import json
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from exchange.issue import issue_packet, issue_restart
from exchange.issued_catalog import IssuedCatalog
from receiving.catalog import ReceiptCatalog
from receiving.intake import accept
from reports.followup import followup
from reports.history import read_report, read_restart_report
from reports.repository import ReportRepository
from review.request import RestartRequest, ReviewRequest, compare, compare_restart
from station.models import StationModel, StationSource
from station.repository import StationRepository
from station.revisions import revise


_SAMPLE = Path(__file__).resolve().parent / 'samples' / 'workday.json'
_RESTART = Path(__file__).resolve().parent / 'samples' / 'restart.json'


def _handoff(station, issued, receipts, batch, values, parent_id=None):
    model = StationModel(values['A'], values['B'], values['C'], values['D'])
    source = StationSource(tuple(values['state_ids']), values['topology'], tuple(values['raw_rows']))
    revision = revise(station, batch, values['channel'], model, source, parent_id)
    packet = issue_packet(station, issued, revision)
    receipt = accept(receipts, packet.wire, packet.credential)
    return revision, receipt


def _report_result(report):
    return {
        'report_id': report.report_id,
        'status': report.energy_status,
        'energy': str(report.energy) if report.energy is not None else None,
        'verdict': report.verdict,
        'receipts': [source.receipt_id for source in report.sources],
        'revisions': [source.origin.revision_id for source in report.sources],
    }


def run_workday(directory: Path) -> dict:
    values = json.loads(_SAMPLE.read_text(encoding='utf-8'))
    station_path = directory / 'station.sqlite'
    receipt_path = directory / 'receipts.sqlite'
    report_path = directory / 'reports.sqlite'
    batch, station_id, threshold = values['batch'], values['station_id'], values['threshold']
    with closing(ReceiptCatalog(receipt_path)) as receipts, closing(ReportRepository(report_path)) as reports:
        with closing(StationRepository(station_path, station_id)) as station:
            issued = IssuedCatalog(station.connection)
            main_rev, primary = _handoff(station, issued, receipts, batch, values['primary'])
            old_rev, reference = _handoff(station, issued, receipts, batch, values['reference'])
        original = compare(receipts, reports, ReviewRequest(batch, primary, reference, threshold))
        with closing(StationRepository(station_path, station_id)) as station:
            issued = IssuedCatalog(station.connection)
            new_rev, new_reference = _handoff(station, issued, receipts, batch, values['amendment'], old_rev)
        corrected = compare(receipts, reports, ReviewRequest(batch, primary, new_reference, threshold))
    with closing(ReportRepository(report_path)) as reopened:
        signed = [read_report(reopened, report.report_id) for report in (original, corrected)]
        if signed != [original, corrected]:
            raise AssertionError('report snapshots changed on reopening')
        actual = [_report_result(report) for report in signed]
        queue = followup(reopened, values['next_shift'], [report.report_id for report in signed])
    expected = [
        {'report_id': original.report_id, 'status': 'finite', 'energy': '1', 'verdict': 'release',
         'receipts': [primary, reference], 'revisions': [main_rev, old_rev]},
        {'report_id': corrected.report_id, 'status': 'divergent', 'energy': None, 'verdict': 'retest',
         'receipts': [primary, new_reference], 'revisions': [main_rev, new_rev]},
    ]
    followup_expected = [corrected.report_id]
    followup_actual = [item.report_id for item in queue]
    if actual != expected or followup_actual != followup_expected:
        raise AssertionError(f'workday report or followup mismatch: {actual!r}, {followup_actual!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v8',
        'input': {'batch': batch, 'threshold': threshold,
                  'models': [{key: values[name][key] for key in ('A', 'B', 'C', 'D')}
                             for name in ('primary', 'reference', 'amendment')]},
        'expected': expected,
        'actual': actual,
        'downstream_expected': followup_expected,
        'downstream_actual': followup_actual,
        'code_paths': ['station/revisions.py', 'exchange/issue.py', 'receiving/intake.py',
                       'review/request.py', 'reports/history.py', 'reports/followup.py'],
        'oracle': 'Original tails cancel and the zero-tick difference is 1. Corrected tail is -1,-2,-4,…, so only its report requires retesting.',
    }, ensure_ascii=False, sort_keys=True))
    return {'reports': actual, 'followup': followup_actual}


def run_restart(directory: Path) -> dict:
    """Issue V2 packets, close the station and receipt store, then re-claim reports."""
    fixture = json.loads(_RESTART.read_text(encoding='utf-8'))
    batch = fixture['batch']
    station_path = directory / 'restart-station.sqlite'
    receipt_path = directory / 'restart-receipts.sqlite'
    report_path = directory / 'restart-reports.sqlite'
    revisions = {}
    wires = {}
    credentials = {}
    # The station is open only long enough to freeze revisions and issue V2 packets.
    with closing(StationRepository(station_path, 'ql-restart')) as station:
        issued = IssuedCatalog(station.connection)
        for name, source in fixture['revisions'].items():
            matrix = [[Fraction(value) for value in row] for row in source['A']]
            c = [Fraction(value) for value in source['C']]
            model = StationModel(matrix, [0] * len(matrix), c, 0)
            revision = revise(station, batch, source['channel'], model,
                              StationSource(raw_rows=(f'private/{name}',)),
                              parent_id=revisions.get(source.get('parent')))
            revisions[name] = revision
            packet = issue_restart(station, issued, revision,
                                   tuple(Fraction(value) for value in source['base']),
                                   tuple(tuple(Fraction(value) for value in row)
                                         for row in source['directions']))
            wires[name] = packet.wire
            credentials[name] = packet.credential
    # Station closed: intake verifies the trusted credentials and stores the original bytes.
    receipts = {}
    with closing(ReceiptCatalog(receipt_path)) as catalog:
        for name in wires:
            receipts[name] = accept(catalog, wires[name], credentials[name])
    # Sign the restoration reports while the receipt store is still open.
    results = []
    with closing(ReceiptCatalog(receipt_path)) as catalog, \
            closing(ReportRepository(report_path)) as reports:
        for case in fixture['reviews']:
            left, right = case['pair']
            report = compare_restart(
                catalog, reports,
                RestartRequest(batch, receipts[left], receipts[right], case['observations']))
            results.append((case, report, receipts[left], receipts[right]))
    # Receipt store is now closed.  Reopen only the report store and re-claim by number.
    with closing(ReportRepository(report_path)) as reopened:
        for case, report, left_receipt, right_receipt in results:
            claimed = read_restart_report(reopened, report.report_id)
            if claimed != report:
                raise AssertionError('restoration report changed on reopening')
    mixed = next((entry for entry in results if entry[0]['status'] == 'mixed'), None)
    output = {
        'batch': batch,
        'receipts': receipts,
        'statuses': [{'case': case['case'], 'status': report.status} for case, report, _, _ in results],
        'reopened': True,
    }
    if mixed is not None:
        case, report, left_receipt, right_receipt = mixed
        output['mixed'] = {
            'case': case['case'],
            'finite_witness': [[str(value) for value in readings] for readings in report.finite_witness],
            'divergent_witness': [[str(value) for value in readings] for readings in report.divergent_witness],
            'receipts': [left_receipt, right_receipt],
        }
    print('POINTWISE_RESTART ' + json.dumps({
        'case_id': 'v11',
        'input': {'batch': batch, 'observations': {case['case']: case['observations'] for case in fixture['reviews']}},
        'expected': {case['case']: case['status'] for case in fixture['reviews']},
        'actual': {entry['case']: entry['status'] for entry in output['statuses']},
        'downstream_expected': {'receipts_closed': True, 'reopened': True,
                                'mixed_case': 'independent-amplitudes'},
        'downstream_actual': {'receipts_closed': True, 'reopened': True,
                              'mixed_case': output.get('mixed', {}).get('case')},
        'code_paths': ['exchange/issue.py', 'receiving/intake.py', 'review/request.py',
                       'review/restart_model.py', 'reports/history.py'],
        'oracle': 'One coupled growth direction diverges; the decoupled license admits both a finite and a divergent explanation of the same first reading.',
    }, ensure_ascii=False, sort_keys=True))
    return output


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        run_workday(Path(directory))
        run_restart(Path(directory))
