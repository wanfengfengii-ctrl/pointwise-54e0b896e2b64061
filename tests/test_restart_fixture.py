import json
from fractions import Fraction
from pathlib import Path

from exchange.issue import issue_restart
from exchange.envelope import unpack_v2
from receiving.intake import accept
from reports.history import read_restart_report
from review.request import RestartRequest, compare_restart
from station.models import StationModel, StationSource
from station.revisions import revise
from tests import LabCase, scenario


_SAMPLE = Path(__file__).resolve().parent.parent / 'samples' / 'restart.json'


class RestartFixtureTests(LabCase):
    def _build(self):
        fixture = json.loads(_SAMPLE.read_text(encoding='utf-8'))
        batch = fixture['batch']
        revisions = {}
        receipts = {}
        packets = {}
        for name, source in fixture['revisions'].items():
            matrix = [[Fraction(value) for value in row] for row in source['A']]
            c = [Fraction(value) for value in source['C']]
            model = StationModel(matrix, [0] * len(matrix), c, 0)
            station_source = StationSource(raw_rows=(f'private/{name}',))
            revision = revise(self.station, batch, source['channel'], model, station_source,
                              parent_id=revisions.get(source.get('parent')))
            revisions[name] = revision
            packet = issue_restart(self.station, self.issued, revision,
                                   tuple(Fraction(value) for value in source['base']),
                                   tuple(tuple(Fraction(value) for value in row)
                                         for row in source['directions']))
            packets[name] = packet
            receipts[name] = accept(self.receipts, packet.wire, packet.credential)
        return fixture, revisions, packets, receipts

    def test_fixture_statuses_and_witness_replay(self):
        fixture, revisions, packets, receipts = self._build()
        batch = fixture['batch']
        results = []
        for case in fixture['reviews']:
            left, right = case['pair']
            observations = [(tick, value) for tick, value in case['observations']]
            report = compare_restart(self.receipts, self.reports,
                                     RestartRequest(batch, receipts[left], receipts[right], observations))
            self.assertEqual(report.status, case['status'], case['case'])
            self.assertEqual(read_restart_report(self.reports, report.report_id), report)
            self.assertEqual(tuple(item.origin.revision_id for item in report.sources),
                             (revisions[left], revisions[right]))
            self.assertEqual(tuple(item.receipt_id for item in report.sources),
                             (receipts[left], receipts[right]))
            if case['status'] == 'mixed':
                self._verify_mixed(packets, case, report)
            else:
                self.assertIsNone(report.finite_witness)
                self.assertIsNone(report.divergent_witness)
            results.append({'case': case['case'], 'status': report.status})
        expected = [{'case': case['case'], 'status': case['status']} for case in fixture['reviews']]
        scenario('v10', {'reviews': len(fixture['reviews']), 'batch': batch}, expected, results,
                 {'witnesses_replayed': True}, {'witnesses_replayed': True},
                 ['exchange/issue.py', 'receiving/intake.py', 'review/request.py',
                  'review/restart_model.py', 'reports/history.py'],
                 'Every fixture pair matches its expected status; mixed witnesses replay their whole tables.')

    def _verify_mixed(self, packets, case, report):
        left, right = case['pair']
        left_family = unpack_v2(packets[left].wire).family
        right_family = unpack_v2(packets[right].wire).family
        for key, witness in (('example_finite', report.finite_witness),
                             ('example_divergent', report.divergent_witness)):
            self.assertIsNotNone(witness)
            # The sample states a compliant witness; our exact maths may pick another.
            # Recover the parameters from the first four readings and replay everything.
            self._replay(left_family, right_family, witness, case['observations'])

    def _replay(self, left_family, right_family, witness, observations):
        from review.linalg import solve_affine
        from review.restart_model import family_tick

        def parameters(family, reads):
            count = family.parameters
            if count == 0:
                return ()
            zero = tuple(Fraction(0) for _ in range(count))
            equations = []
            for tick, value in enumerate(reads):
                row = [family_tick(family, tuple(Fraction(int(index == axis)) for index in range(count)), tick)
                       - family_tick(family, zero, tick) for axis in range(count)]
                equations.append((row, value - family_tick(family, zero, tick)))
            solution = solve_affine(equations, count)
            self.assertIsNotNone(solution, 'a witness reading is not in the received family')
            return solution[0]

        left = parameters(left_family, witness[0])
        right = parameters(right_family, witness[1])
        for tick, value in observations:
            self.assertEqual(family_tick(left_family, left, tick)
                             - family_tick(right_family, right, tick), Fraction(value))
        for tick, expected in enumerate(witness[0]):
            self.assertEqual(family_tick(left_family, left, tick), expected)
        for tick, expected in enumerate(witness[1]):
            self.assertEqual(family_tick(right_family, right, tick), expected)
