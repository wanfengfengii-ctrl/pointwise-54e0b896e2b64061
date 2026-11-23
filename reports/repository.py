import json
import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from exchange.provenance import IssueOrigin, decode_origin, encode_origin


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS signed_reports (
    report_id INTEGER PRIMARY KEY,
    batch TEXT NOT NULL,
    threshold TEXT NOT NULL,
    energy TEXT,
    verdict TEXT NOT NULL CHECK (verdict IN ('release', 'retest')),
    CHECK (energy IS NOT NULL OR verdict = 'retest')
);
CREATE TABLE IF NOT EXISTS report_sources (
    report_id INTEGER NOT NULL REFERENCES signed_reports(report_id),
    slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
    receipt_id INTEGER NOT NULL,
    issue_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY(report_id, slot)
);
CREATE TABLE IF NOT EXISTS restart_reports (
    report_id INTEGER PRIMARY KEY,
    batch TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('inconsistent', 'all_finite', 'all_divergent', 'mixed'))
);
CREATE TABLE IF NOT EXISTS restart_observations (
    report_id INTEGER NOT NULL REFERENCES restart_reports(report_id),
    tick INTEGER NOT NULL CHECK (tick >= 0),
    difference TEXT NOT NULL,
    PRIMARY KEY(report_id, tick)
);
CREATE TABLE IF NOT EXISTS restart_witnesses (
    report_id INTEGER NOT NULL REFERENCES restart_reports(report_id),
    kind TEXT NOT NULL CHECK (kind IN ('finite', 'divergent')),
    slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
    tick INTEGER NOT NULL CHECK (tick BETWEEN 0 AND 3),
    reading TEXT NOT NULL,
    PRIMARY KEY(report_id, kind, slot, tick)
);
CREATE TABLE IF NOT EXISTS restart_sources (
    report_id INTEGER NOT NULL REFERENCES restart_reports(report_id),
    slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
    receipt_id INTEGER NOT NULL,
    issue_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY(report_id, slot)
);
'''


@dataclass(frozen=True)
class ReportSource:
    receipt_id: int
    issue_id: str
    digest: str
    origin: IssueOrigin


@dataclass(frozen=True)
class SignedReport:
    report_id: int
    batch: str
    threshold: Fraction
    energy: Fraction | None
    verdict: str
    sources: tuple[ReportSource, ReportSource]

    @property
    def energy_status(self) -> str:
        return 'divergent' if self.energy is None else 'finite'


Witness = tuple[tuple[Fraction, ...], tuple[Fraction, ...]]


@dataclass(frozen=True)
class RestartReport:
    report_id: int
    batch: str
    observations: tuple[tuple[int, Fraction], ...]
    status: str
    finite_witness: Witness | None
    divergent_witness: Witness | None
    sources: tuple[ReportSource, ReportSource]


class ReportRepository:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        try:
            self.connection.execute('PRAGMA foreign_keys = ON')
            self.connection.executescript(_SCHEMA)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.connection.close()

    def commit_report(self, batch: str, threshold: Fraction, energy: Fraction | None,
                      verdict: str, sources: tuple[ReportSource, ReportSource]) -> SignedReport:
        if len(sources) != 2 or any(source.origin.batch != batch for source in sources):
            raise ValueError('a report needs two ordered sources from its batch')
        energy_text = str(energy) if energy is not None else None
        with self.connection:
            cursor = self.connection.execute('''
                INSERT INTO signed_reports(batch, threshold, energy, verdict) VALUES (?, ?, ?, ?)
            ''', (batch, str(threshold), energy_text, verdict))
            report_id = cursor.lastrowid
            self.connection.executemany('''
                INSERT INTO report_sources(report_id, slot, receipt_id, issue_id, digest, origin)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ((report_id, slot, source.receipt_id, source.issue_id, source.digest,
                   json.dumps(encode_origin(source.origin), sort_keys=True, ensure_ascii=False))
                  for slot, source in enumerate(sources)))
        return SignedReport(report_id, batch, threshold, energy, verdict, sources)

    def get_report(self, report_id: int) -> SignedReport | None:
        if type(report_id) is not int or not 0 < report_id < 2**63:
            return None
        rows = self.connection.execute('''
            SELECT r.batch, r.threshold, r.energy, r.verdict,
                   s.slot, s.receipt_id, s.issue_id, s.digest, s.origin
            FROM signed_reports AS r LEFT JOIN report_sources AS s ON s.report_id = r.report_id
            WHERE r.report_id = ? ORDER BY s.slot
        ''', (report_id,)).fetchall()
        if not rows:
            return None
        if len(rows) != 2 or [row[4] for row in rows] != [0, 1]:
            raise sqlite3.DatabaseError('signed report has an incomplete source snapshot')
        batch, threshold, energy, verdict = rows[0][:4]
        sources = tuple(ReportSource(receipt_id, issue_id, digest, decode_origin(json.loads(origin)))
                        for _, receipt_id, issue_id, digest, origin in (row[4:] for row in rows))
        return SignedReport(report_id, batch, Fraction(threshold),
                            None if energy is None else Fraction(energy), verdict, sources)

    def get_reports(self, report_ids) -> tuple[SignedReport, ...]:
        reports = []
        for report_id in report_ids:
            report = self.get_report(report_id)
            if report is None:
                raise KeyError(report_id)
            reports.append(report)
        return tuple(reports)

    def commit_restart(self, batch: str, observations, status: str,
                       finite_witness: Witness | None, divergent_witness: Witness | None,
                       sources: tuple[ReportSource, ReportSource]) -> RestartReport:
        if status not in ('inconsistent', 'all_finite', 'all_divergent', 'mixed'):
            raise ValueError('invalid restoration status')
        if len(sources) != 2 or any(source.origin.batch != batch for source in sources):
            raise ValueError('a restoration report needs two ordered sources from its batch')
        if status == 'mixed':
            if finite_witness is None or divergent_witness is None:
                raise ValueError('a mixed restoration report needs both witnesses')
        elif finite_witness is not None or divergent_witness is not None:
            raise ValueError('only a mixed restoration report carries witnesses')
        observations = tuple(observations)
        with self.connection:
            cursor = self.connection.execute(
                'INSERT INTO restart_reports(batch, status) VALUES (?, ?)', (batch, status))
            report_id = cursor.lastrowid
            self.connection.executemany('''
                INSERT INTO restart_observations(report_id, tick, difference) VALUES (?, ?, ?)
            ''', ((report_id, tick, str(difference)) for tick, difference in observations))
            self.connection.executemany('''
                INSERT INTO restart_sources(report_id, slot, receipt_id, issue_id, digest, origin)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ((report_id, slot, source.receipt_id, source.issue_id, source.digest,
                   json.dumps(encode_origin(source.origin), sort_keys=True, ensure_ascii=False))
                  for slot, source in enumerate(sources)))
            for kind, witness in (('finite', finite_witness), ('divergent', divergent_witness)):
                if witness is None:
                    continue
                for slot, readings in enumerate(witness):
                    self.connection.executemany('''
                        INSERT INTO restart_witnesses(report_id, kind, slot, tick, reading)
                        VALUES (?, ?, ?, ?, ?)
                    ''', ((report_id, kind, slot, tick, str(value)) for tick, value in enumerate(readings)))
        return RestartReport(report_id, batch, tuple(observations), status,
                             finite_witness, divergent_witness, sources)

    def get_restart_report(self, report_id: int) -> RestartReport | None:
        if type(report_id) is not int or not 0 < report_id < 2**63:
            return None
        row = self.connection.execute(
            'SELECT batch, status FROM restart_reports WHERE report_id = ?', (report_id,)
        ).fetchone()
        if row is None:
            return None
        batch, status = row
        observations = tuple(
            (tick, Fraction(difference)) for tick, difference in self.connection.execute(
                'SELECT tick, difference FROM restart_observations WHERE report_id = ? ORDER BY tick',
                (report_id,)).fetchall())
        source_rows = self.connection.execute('''
            SELECT slot, receipt_id, issue_id, digest, origin FROM restart_sources
            WHERE report_id = ? ORDER BY slot
        ''', (report_id,)).fetchall()
        if len(source_rows) != 2 or [entry[0] for entry in source_rows] != [0, 1]:
            raise sqlite3.DatabaseError('restoration report has an incomplete source snapshot')
        sources = tuple(ReportSource(receipt_id, issue_id, digest, decode_origin(json.loads(origin)))
                        for _, receipt_id, issue_id, digest, origin in source_rows)
        witnesses = {}
        for kind in ('finite', 'divergent'):
            rows = self.connection.execute('''
                SELECT slot, tick, reading FROM restart_witnesses
                WHERE report_id = ? AND kind = ? ORDER BY slot, tick
            ''', (report_id, kind)).fetchall()
            if not rows:
                witnesses[kind] = None
                continue
            grouped = ([], [])
            for slot, _, reading in rows:
                grouped[slot].append(Fraction(reading))
            if any(len(readings) != 4 for readings in grouped):
                raise sqlite3.DatabaseError('restoration witness is incomplete')
            witnesses[kind] = (tuple(grouped[0]), tuple(grouped[1]))
        return RestartReport(report_id, batch, observations, status,
                             witnesses['finite'], witnesses['divergent'], sources)
