import json
from dataclasses import replace
from fractions import Fraction

from exchange.envelope import pack_v2, unpack_v1, unpack_v2
from exchange.issue import issue_packet, issue_restart
from exchange.issued_catalog import IssuedCatalog
from receiving.intake import accept
from reports.history import read_restart_report
from review.request import RestartRequest, compare_restart
from station.models import StationModel, StationSource
from station.repository import StationRepository
from station.revisions import read_revision
from tests import LabCase, scalar, scenario


def fam_case(values):
    matrix = [[Fraction(value) for value in row] for row in values['A']]
    model = StationModel(matrix, [0] * len(matrix), [Fraction(value) for value in values['C']], 0)
    base = tuple(Fraction(value) for value in values['base'])
    directions = tuple(tuple(Fraction(value) for value in row) for row in values['directions'])
    return model, base, directions


class RestartIssueTests(LabCase):
    def freeze_and_issue(self, channel, model, base, directions, *, batch='B-01', parent_id=None):
        revision = self.freeze(channel, model, batch=batch, parent_id=parent_id)
        packet = issue_restart(self.station, self.issued, revision, base, directions)
        receipt = accept(self.receipts, packet.wire, packet.credential)
        return revision, packet, receipt

    def _counts(self):
        return (self.station.connection.execute('SELECT count(*) FROM revisions').fetchone()[0],
                self.station.connection.execute('SELECT count(*) FROM issued_packets').fetchone()[0],
                self.station.connection.execute('SELECT count(*) FROM restoration_issues').fetchone()[0])

    def test_same_revision_v1_and_v2_distinct(self):
        model = StationModel((('1/2',),), (1,), (1,), 0)
        revision = self.freeze('primary', model)
        first = issue_packet(self.station, self.issued, revision)
        second = issue_restart(self.station, self.issued, revision, (0,), ((1,),))
        self.assertNotEqual(first.credential.issue_id, second.credential.issue_id)
        self.assertEqual(first.credential.origin, second.credential.origin)
        self.assertEqual(first.wire[:60] != second.wire[:60], True)
        self.assertEqual(unpack_v1(first.wire).version, 1)
        self.assertEqual(unpack_v2(second.wire).version, 2)
        self.assertTrue(second.credential.issue_id.endswith(':v2'))
        first_receipt = accept(self.receipts, first.wire, first.credential)
        second_receipt = accept(self.receipts, second.wire, second.credential)
        self.assertNotEqual(first_receipt, second_receipt)
        self.assertEqual(self.receipts.lookup(first_receipt).wire, first.wire)
        self.assertEqual(self.receipts.lookup(second_receipt).wire, second.wire)
        self.assertEqual(self.receipts.connection.execute(
            'SELECT count(*) FROM received_packets').fetchone()[0], 2)
        self.assertEqual(self._counts(), (1, 1, 1))

    def test_v2_reissue_returns_original(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        revision, packet, receipt = self.freeze_and_issue('primary', model, (1,), ())
        replay = issue_restart(self.station, self.issued, revision, (1,), ())
        self.assertEqual(replay.wire, packet.wire)
        self.assertEqual(replay.credential, packet.credential)
        self.assertEqual(accept(self.receipts, replay.wire, replay.credential), receipt)
        self.assertEqual(self.receipts.connection.execute(
            'SELECT count(*) FROM received_packets').fetchone()[0], 1)

    def test_changed_license_rejected(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        revision, packet, receipt = self.freeze_and_issue('primary', model, (1,), ())
        for base, directions in (((2,), ()), ((1,), ((1,),)), ((Fraction(1),), ((0,),))):
            with self.subTest(base=base, directions=directions), self.assertRaises(ValueError):
                issue_restart(self.station, self.issued, revision, base, directions)
        self.assertEqual(self._counts(), (1, 0, 1))
        replay = issue_restart(self.station, self.issued, revision, [Fraction(1)], [])
        self.assertEqual(replay.wire, packet.wire)
        self.assertEqual(replay.directions, ())

    def test_spectrum_gate(self):
        for bad in (((Fraction(3),),), ((Fraction(0), Fraction(1)), (Fraction(1), Fraction(0)),),
                    ((Fraction(-1, 2),),), ((Fraction(1, 3),),)):
            with self.subTest(bad=bad):
                model = StationModel(bad, (0,) * len(bad), (1,) + (0,) * (len(bad) - 1), 0)
                revision = self.freeze('primary', model)
                with self.assertRaises(ValueError):
                    issue_restart(self.station, self.issued, revision, (0,) * len(bad), ())
        # Allowed roots with multiplicity and a nontrivial Jordan block.
        for good in (((Fraction(1),),), ((Fraction(2),),), ((Fraction(1, 2),),), (),
                     ((Fraction(1), Fraction(1)), (Fraction(0), Fraction(1)),),
                     ((Fraction(2), Fraction(1), Fraction(0), Fraction(0)),
                      (Fraction(0), Fraction(2), Fraction(1), Fraction(0)),
                      (Fraction(0), Fraction(0), Fraction(2), Fraction(1)),
                      (Fraction(0), Fraction(0), Fraction(0), Fraction(2)),)):
            with self.subTest(good=good):
                size = len(good)
                c = ((1,) + (0,) * (size - 1)) if size else ()
                model = StationModel(good, (0,) * size, c, 0)
                revision = self.freeze('primary', model)
                issue_restart(self.station, self.issued, revision, (0,) * size, ())
        self.assertEqual(self.station.connection.execute(
            'SELECT count(*) FROM restoration_issues').fetchone()[0], 6)

    def test_private_state_stays_station_side(self):
        model = StationModel((('1/2',),), (0,), (7,), 0)
        source = StationSource(('hidden-state',), 'internal-topology', ('raw-measurement-row',))
        revision = self.freeze('primary', model, source=source)
        packet = issue_restart(self.station, self.issued, revision, (1,), ((1,),))
        stored = read_revision(self.station, revision)
        self.assertEqual(stored.source, source)
        for secret in (b'hidden-state', b'internal-topology', b'raw-measurement-row'):
            self.assertNotIn(secret, packet.wire)
        family = unpack_v2(packet.wire).family
        self.assertEqual(family.base.direct, 7)

    def test_basis_validation(self):
        model = StationModel((('1/2', 0), (0, 2)), (0, 0), (1, 1), 0)
        revision = self.freeze('primary', model)
        for base, directions in (((1,), ()), ((0, 0), ((1,),)), ((0, 0), 'nope'),
                                 ((0, 0), ([1, 2, 3],)), ((True, 0), ()), ((0.5, 0), ())):
            with self.subTest(base=base, directions=directions), self.assertRaises(ValueError):
                issue_restart(self.station, self.issued, revision, base, directions)
        self.assertEqual(self._counts(), (1, 0, 0))

    def test_wrong_ledger_rejected(self):
        other = StationRepository(self.root / 'other.sqlite', 'other')
        self.addCleanup(other.close)
        other_issued = IssuedCatalog(other.connection)
        revision = self.freeze('primary', scalar('1/2', 0))
        with self.assertRaises(ValueError):
            issue_restart(self.station, other_issued, revision, (0,), ())


class RestartIntakeTests(LabCase):
    def test_tampered_v2_rejected(self):
        revision = self.freeze('primary', StationModel((('1/2',),), (0,), (1,), 0))
        packet = issue_restart(self.station, self.issued, revision, (1,), ())
        receipt = accept(self.receipts, packet.wire, packet.credential)
        raw = json.loads(packet.wire)
        raw['body']['base']['direct'] = '3'
        tampered = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            accept(self.receipts, tampered, packet.credential)
        self.assertEqual(self.receipts.lookup(receipt).wire, packet.wire)

    def test_conflicting_identity_rejected(self):
        revision = self.freeze('primary', StationModel((('1/2',),), (0,), (1,), 0))
        packet = issue_restart(self.station, self.issued, revision, (1,), ())
        receipt = accept(self.receipts, packet.wire, packet.credential)
        family = unpack_v2(packet.wire).family
        changed = replace(family, base=replace(family.base, direct=Fraction(9)))
        origin = packet.credential.origin
        conflicting = pack_v2(packet.credential.issue_id, origin, changed)
        with self.assertRaisesRegex(ValueError, 'original issuance credential'):
            accept(self.receipts, conflicting, packet.credential)
        with self.assertRaisesRegex(ValueError, 'different receipt'):
            accept(self.receipts, conflicting, unpack_v2(conflicting).credential)
        self.assertEqual(self.receipts.lookup(receipt).wire, packet.wire)

    def test_v2_wire_cannot_be_claimed_as_v1(self):
        revision = self.freeze('primary', StationModel((('1/2',),), (0,), (1,), 0))
        packet = issue_restart(self.station, self.issued, revision, (1,), ())
        with self.assertRaises(ValueError):
            accept(self.receipts, packet.wire, unpack_v1)
        raw = json.loads(packet.wire)
        raw['version'] = 1
        forged = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        with self.assertRaises(ValueError):
            accept(self.receipts, forged, packet.credential)
        self.assertEqual(self.receipts.connection.execute(
            'SELECT count(*) FROM received_packets').fetchone()[0], 0)

    def test_cross_batch_v2_review_rejected(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        left = self.freeze('primary', model)
        right = self.freeze('reference', model, batch='B-02')
        left_packet = issue_restart(self.station, self.issued, left, (1,), ())
        right_packet = issue_restart(self.station, self.issued, right, (0,), ())
        left_receipt = accept(self.receipts, left_packet.wire, left_packet.credential)
        right_receipt = accept(self.receipts, right_packet.wire, right_packet.credential)
        with self.assertRaises(ValueError):
            compare_restart(self.receipts, self.reports,
                            RestartRequest('B-01', left_receipt, right_receipt, [(0, 1)]))
        self.assertEqual(self.report_counts(), (0, 0))

    def test_v1_receipt_rejected_in_restart(self):
        revision = self.freeze('primary', StationModel((('1/2',),), (1,), (1,), 0))
        packet = issue_packet(self.station, self.issued, revision)
        receipt = accept(self.receipts, packet.wire, packet.credential)
        with self.assertRaises(ValueError):
            compare_restart(self.receipts, self.reports,
                            RestartRequest('B-01', receipt, receipt, [(0, 0)]))
        self.assertEqual(self.report_counts(), (0, 0))

    def test_mixed_version_pair_rejected(self):
        model = StationModel((('1/2',),), (1,), (1,), 0)
        revision = self.freeze('primary', model)
        v2 = issue_restart(self.station, self.issued, revision, (1,), ())
        v2_receipt = accept(self.receipts, v2.wire, v2.credential)
        other = self.freeze('reference', model)
        v1 = issue_packet(self.station, self.issued, other)
        v1_receipt = accept(self.receipts, v1.wire, v1.credential)
        for pair in ((v2_receipt, v1_receipt), (v1_receipt, v2_receipt)):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                compare_restart(self.receipts, self.reports,
                                RestartRequest('B-01', *pair, [(0, 1)]))
        self.assertEqual(self.report_counts(), (0, 0))


class RestartReviewTests(LabCase):
    def _pair(self, left, right, observations, batch='B-01'):
        left_revision = self.freeze('primary', left[0])
        right_revision = self.freeze('reference', right[0])
        self._last_primary, self._last_reference = left_revision, right_revision
        left_packet = issue_restart(self.station, self.issued, left_revision, left[1], left[2])
        right_packet = issue_restart(self.station, self.issued, right_revision, right[1], right[2])
        left_receipt = accept(self.receipts, left_packet.wire, left_packet.credential)
        right_receipt = accept(self.receipts, right_packet.wire, right_packet.credential)
        report = compare_restart(self.receipts, self.reports,
                                 RestartRequest(batch, left_receipt, right_receipt, observations))
        return report, (left_packet, right_packet), (left_receipt, right_receipt)

    def test_linked_growth_diverges(self):
        model = StationModel((('1/2', 0), (0, 2)), (0, 0), (1, 1), 0)
        zero = StationModel((), (), (), 0)
        report, packets, receipts = self._pair((model, (0, 0), ((1, 1),)),
                                               (zero, (), ()), [(0, 2)])
        self.assertEqual(report.status, 'all_divergent')
        self.assertIsNone(report.finite_witness)
        self.assertIsNone(report.divergent_witness)
        self.assertEqual(report.observations, ((0, Fraction(2)),))
        self.assertEqual(tuple(item.receipt_id for item in report.sources), receipts)
        self.assertEqual(read_restart_report(self.reports, report.report_id), report)
        scenario('v9', {'status_expected': 'all_divergent', 'observation': [[0, 2]]},
                 {'status': 'all_divergent'}, {'status': report.status},
                 {'witnesses': [None, None]},
                 {'witnesses': [report.finite_witness, report.divergent_witness]},
                 ['exchange/issue.py', 'receiving/intake.py', 'review/restart_model.py'],
                 'Coupled growth direction forces an unstable component, so the difference cannot be square-summable.')

    def test_decay_is_finite(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        zero = StationModel((), (), (), 0)
        report, _, _ = self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 2), (3, Fraction(1, 4))])
        self.assertEqual(report.status, 'all_finite')

    def test_mixed_witnesses(self):
        model = StationModel((('1/2', 0), (0, 2)), (0, 0), (1, 1), 0)
        zero = StationModel((), (), (), 0)
        report, packets, _ = self._pair((model, (0, 0), ((1, 0), (0, 1))),
                                        (zero, (), ()), [(0, 2)])
        self.assertEqual(report.status, 'mixed')
        finite, divergent = report.finite_witness, report.divergent_witness
        self.assertIsNotNone(finite)
        self.assertIsNotNone(divergent)
        self.assertEqual(len(finite[0]), 4)
        self.assertEqual(len(divergent[0]), 4)
        self.assertEqual(finite[1], (0, 0, 0, 0))
        self.assertEqual(finite[0][0] - finite[1][0], 2)
        self.assertEqual(divergent[0][0] - divergent[1][0], 2)
        primary_family = unpack_v2(packets[0].wire).family
        reference_family = unpack_v2(packets[1].wire).family
        self._check_witness_explains(primary_family, reference_family, finite, expected_finite=True)
        self._check_witness_explains(primary_family, reference_family, divergent, expected_finite=False)

    def _check_witness_explains(self, primary, reference, witness, expected_finite):
        from review.linalg import solve_affine
        from review.restart_model import family_tick

        def parameters(family, reads):
            count = family.parameters
            if count == 0:
                return ()
            zero = tuple(Fraction(0) for _ in range(count))
            start = family_tick(family, zero, 0)
            equations = []
            for tick, value in enumerate(reads):
                row = [family_tick(family, tuple(Fraction(int(i == j)) for i in range(count)), tick)
                       - family_tick(family, zero, tick) for j in range(count)]
                equations.append((row, value - family_tick(family, zero, tick)))
            solution = solve_affine(equations, count)
            self.assertIsNotNone(solution)
            return solution[0]

        left = parameters(primary, witness[0])
        right = parameters(reference, witness[1])
        for tick, value in enumerate(witness[0]):
            self.assertEqual(family_tick(primary, left, tick), value)
        for tick, value in enumerate(witness[1]):
            self.assertEqual(family_tick(reference, right, tick), value)
        sequence = [family_tick(primary, left, tick) - family_tick(reference, right, tick)
                    for tick in range(60)]
        tail = [abs(value) for value in sequence[30:60]]
        if expected_finite:
            self.assertLess(max(tail), Fraction(1, 1000))
        else:
            self.assertGreater(max(tail), 1)

    def test_inconsistent_rejected_but_signed(self):
        model = StationModel(((2,),), (0,), (1,), 0)
        report, _, _ = self._pair((model, (0,), ((1,),)),
                                  (model, (0,), ((1,),)), [(0, 0), (1, 1)])
        self.assertEqual(report.status, 'inconsistent')
        self.assertIsNone(report.finite_witness)
        self.assertIsNone(report.divergent_witness)
        self.assertEqual(read_restart_report(self.reports, report.report_id), report)

    def test_observation_order_and_validation(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        zero = StationModel((), (), (), 0)
        report, _, _ = self._pair((model, (0,), ((1,),)), (zero, (), ()),
                                  [(3, Fraction(1, 8)), (0, 2), (1, 1)])
        self.assertEqual(tuple(tick for tick, _ in report.observations), (0, 1, 3))
        with self.assertRaises(ValueError):
            self._pair((model, (0,), ((1,),)), (zero, (), ()), [])
        with self.assertRaises(ValueError):
            self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 0), (0, 1)])
        with self.assertRaises(ValueError):
            self._pair((model, (0,), ((1,),)), (zero, (), ()), [(-1, 0)])
        with self.assertRaises(ValueError):
            self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 'x')])

    def test_unknown_receipt_and_missing_report(self):
        with self.assertRaises(KeyError):
            compare_restart(self.receipts, self.reports, RestartRequest('B-01', 1, 2, [(0, 0)]))
        with self.assertRaises(KeyError):
            read_restart_report(self.reports, 999)

    def test_same_receipt_allowed(self):
        model = StationModel(((1,),), (0,), (1,), 0)
        revision = self.freeze('primary', model)
        packet = issue_restart(self.station, self.issued, revision, (1,), ())
        receipt = accept(self.receipts, packet.wire, packet.credential)
        report = compare_restart(self.receipts, self.reports,
                                 RestartRequest('B-01', receipt, receipt, [(0, 0)]))
        self.assertEqual(report.status, 'all_finite')
        self.assertEqual(tuple(source.receipt_id for source in report.sources), (receipt, receipt))

    def test_commit_failure_does_not_add_records(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        zero = StationModel((), (), (), 0)
        report, _, _ = self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 2)])
        self.reports.connection.executescript('''
            CREATE TABLE restart_gate(id INTEGER PRIMARY KEY);
            CREATE TABLE restart_commit(required INTEGER REFERENCES restart_gate(id)
                                        DEFERRABLE INITIALLY DEFERRED);
            CREATE TRIGGER fail_restart_commit AFTER INSERT ON restart_sources WHEN NEW.slot = 1
            BEGIN
                INSERT INTO restart_commit VALUES (1);
            END;
        ''')
        import sqlite3
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 2)])
        self.assertFalse(self.reports.connection.in_transaction)
        self.assertEqual(self.reports.connection.execute(
            'SELECT count(*) FROM restart_reports').fetchone()[0], 1)
        self.assertEqual(self.reports.connection.execute(
            'SELECT count(*) FROM restart_observations').fetchone()[0], 1)
        self.assertEqual(read_restart_report(self.reports, report.report_id), report)

    def test_reopen_preserves_restart_report(self):
        model = StationModel((('1/2',),), (0,), (1,), 0)
        zero = StationModel((), (), (), 0)
        report, _, _ = self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 2)])
        self.reports.close()
        self.reports = self.open_reports()
        self.assertEqual(read_restart_report(self.reports, report.report_id), report)

    def test_errata_keeps_original_report_number(self):
        model = StationModel(((2,),), (0,), (1,), 0)
        zero = StationModel((), (), (), 0)
        first, _, _ = self._pair((model, (0,), ((1,),)), (zero, (), ()), [(0, 1)])
        self.assertEqual(first.status, 'all_divergent')
        amendment = self.freeze('reference', zero, parent_id=self._last_reference)
        amended_packet = issue_restart(self.station, self.issued, amendment, (), ())
        amended_receipt = accept(self.receipts, amended_packet.wire, amended_packet.credential)
        corrected = compare_restart(self.receipts, self.reports,
                                    RestartRequest('B-01', self._last_primary, amended_receipt, [(0, 1)]))
        self.reports.close()
        self.reports = self.open_reports()
        self.assertEqual(read_restart_report(self.reports, first.report_id), first)
        self.assertEqual(read_restart_report(self.reports, corrected.report_id), corrected)
        self.assertNotEqual(first.report_id, corrected.report_id)

    def test_finite_support_and_direct_only(self):
        # A nilpotent chain yields a finitely supported pulse: square-summable.
        pulse = StationModel(((0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1), (0, 0, 0, 0)),
                             (0, 0, 0, 0), (1, 0, 0, 0), 0)
        zero = StationModel((), (), (), 0)
        report, _, _ = self._pair((pulse, (0, 0, 0, 1), ()), (zero, (), ()),
                                  [(0, 0), (3, 1), (7, 0)])
        self.assertEqual(report.status, 'all_finite')
        # A zero-spectrum state responds only at tick 0, so the difference is one spike.
        spike = StationModel(((0,),), (0,), (1,), 0)
        spike_report, _, _ = self._pair((spike, (2,), ()), (zero, (), ()),
                                        [(0, 2), (1, 0), (5, 0)])
        self.assertEqual(spike_report.status, 'all_finite')
        # The same fixed state cannot produce a zero tick-0 difference.
        impossible, _, _ = self._pair((spike, (2,), ()), (zero, (), ()), [(0, 0)])
        self.assertEqual(impossible.status, 'inconsistent')
