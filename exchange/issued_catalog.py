import json
import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from exchange.envelope import unpack_v1, unpack_v2
from exchange.provenance import IssueCredential


@dataclass(frozen=True)
class IssuedPacket:
    wire: bytes
    credential: IssueCredential


@dataclass(frozen=True)
class IssuedRestoration(IssuedPacket):
    base: tuple[Fraction, ...]
    directions: tuple[tuple[Fraction, ...], ...]


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS issued_packets (
    revision_id INTEGER PRIMARY KEY REFERENCES revisions(revision_id),
    issue_id TEXT NOT NULL UNIQUE,
    wire BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS restoration_issues (
    revision_id INTEGER PRIMARY KEY REFERENCES revisions(revision_id),
    issue_id TEXT NOT NULL UNIQUE,
    base TEXT NOT NULL,
    directions TEXT NOT NULL,
    wire BLOB NOT NULL
);
'''


def _encode_vector(values):
    return [str(value) for value in values]


def _decode_vector(values):
    return tuple(Fraction(value) for value in values)


class IssuedCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        with connection:
            connection.executescript(_SCHEMA)

    def find_by_revision(self, revision_id: int) -> IssuedPacket | None:
        row = self.connection.execute(
            'SELECT issue_id, wire FROM issued_packets WHERE revision_id = ?', (revision_id,)
        ).fetchone()
        if row is None:
            return None
        issue_id, wire = row
        packet = unpack_v1(wire)
        if packet.issue_id != issue_id or packet.origin.revision_id != revision_id:
            raise sqlite3.DatabaseError('issued packet does not match the ledger')
        return IssuedPacket(wire, packet.credential)

    def register_once(self, revision_id: int, wire: bytes) -> IssuedPacket:
        packet = unpack_v1(wire)
        if packet.origin.revision_id != revision_id:
            raise ValueError('issuance revision mismatch')
        with self.connection:
            self.connection.execute('''
                INSERT INTO issued_packets(revision_id, issue_id, wire) VALUES (?, ?, ?)
                ON CONFLICT(revision_id) DO NOTHING
            ''', (revision_id, packet.issue_id, wire))
            stored = self.find_by_revision(revision_id)
            if stored is None:
                raise sqlite3.DatabaseError('issuance was not registered')
        return stored

    def find_restoration_by_revision(self, revision_id: int) -> IssuedRestoration | None:
        row = self.connection.execute(
            'SELECT issue_id, base, directions, wire FROM restoration_issues WHERE revision_id = ?',
            (revision_id,),
        ).fetchone()
        if row is None:
            return None
        issue_id, base, directions, wire = row
        packet = unpack_v2(wire)
        if packet.issue_id != issue_id or packet.origin.revision_id != revision_id:
            raise sqlite3.DatabaseError('issued restoration does not match the ledger')
        raw_base = _decode_vector(json.loads(base))
        raw_directions = tuple(_decode_vector(entry) for entry in json.loads(directions))
        return IssuedRestoration(wire, packet.credential, raw_base, raw_directions)

    def register_restoration_once(self, revision_id: int, base, directions, wire: bytes) -> IssuedRestoration:
        packet = unpack_v2(wire)
        if packet.origin.revision_id != revision_id:
            raise ValueError('restoration issuance revision mismatch')
        with self.connection:
            self.connection.execute('''
                INSERT INTO restoration_issues(revision_id, issue_id, base, directions, wire)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(revision_id) DO NOTHING
            ''', (revision_id, packet.issue_id,
                  json.dumps(_encode_vector(base)), json.dumps([_encode_vector(row) for row in directions]),
                  wire))
            stored = self.find_restoration_by_revision(revision_id)
            if stored is None:
                raise sqlite3.DatabaseError('restoration issuance was not registered')
        return stored
