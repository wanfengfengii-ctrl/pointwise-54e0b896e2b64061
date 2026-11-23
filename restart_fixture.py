"""Run the existing V1 pulse workflow and the V2 restoration conclusions on the fixture."""
import json
from contextlib import ExitStack, closing
from fractions import Fraction
from pathlib import Path

from exchange.issue import issue_packet, issue_restart
from exchange.issued_catalog import IssuedCatalog
from receiving.catalog import ReceiptCatalog
from receiving.intake import accept
from reports.history import read_report, read_restart_report
from reports.repository import ReportRepository
from review.request import RestartRequest, ReviewRequest, compare, compare_restart
from station.models import StationModel, StationSource
from station.repository import StationRepository
from station.revisions import revise


_SAMPLE = Path(__file__).resolve().parent / "samples" / "restart.json"


def pulse_reference(fixture):
    batch = fixture["batch"]
    with closing(ReceiptCatalog(":memory:")) as receipts, closing(ReportRepository(":memory:")) as reports:
        with ExitStack() as station_lifetime:
            stations = {}
            revisions = {}
            receipt_ids = {}
            for name, source in fixture["revisions"].items():
                site = source["station"]
                if site not in stations:
                    station = station_lifetime.enter_context(closing(StationRepository(":memory:", site)))
                    stations[site] = station, IssuedCatalog(station.connection)
                station, issued = stations[site]
                model = StationModel(source["A"], [0] * len(source["A"]), source["C"], 0)
                revision = revise(station, batch, source["channel"], model,
                                  StationSource(raw_rows=(f"source/{name}",)),
                                  parent_id=revisions.get(source.get("parent")))
                revisions[name] = revision
                packet = issue_packet(station, issued, revision)
                receipt_ids[name] = accept(receipts, packet.wire, packet.credential)

        result = {}
        for case in fixture["reviews"]:
            pair = tuple(receipt_ids[name] for name in case["pair"])
            report = compare(receipts, reports, ReviewRequest(batch, *pair, 0))
            saved = read_report(reports, report.report_id)
            if saved != report or tuple(item.receipt_id for item in saved.sources) != pair:
                raise AssertionError(f"wrong V1 report or sources for {case}")
            if (saved.energy_status, saved.energy, saved.verdict) != ("finite", 0, "release"):
                raise AssertionError(f"unexpected V1 pulse result for {case}: {saved}")
            result[case["case"]] = {"energy": str(saved.energy), "verdict": saved.verdict}
        return result


def restoration_reference(fixture):
    """Issue V2 packets for every revision and assert each review's expected status."""
    batch = fixture["batch"]
    with closing(ReceiptCatalog(":memory:")) as receipts, closing(ReportRepository(":memory:")) as reports:
        with ExitStack() as station_lifetime:
            stations = {}
            revisions = {}
            receipt_ids = {}
            for name, source in fixture["revisions"].items():
                site = source["station"]
                if site not in stations:
                    station = station_lifetime.enter_context(closing(StationRepository(":memory:", site)))
                    stations[site] = station, IssuedCatalog(station.connection)
                station, issued = stations[site]
                matrix = [[Fraction(value) for value in row] for row in source["A"]]
                model = StationModel(matrix, [0] * len(matrix),
                                     [Fraction(value) for value in source["C"]], 0)
                revision = revise(station, batch, source["channel"], model,
                                  StationSource(raw_rows=(f"source/{name}",)),
                                  parent_id=revisions.get(source.get("parent")))
                revisions[name] = revision
                packet = issue_restart(station, issued, revision,
                                       tuple(Fraction(value) for value in source["base"]),
                                       tuple(tuple(Fraction(value) for value in row)
                                             for row in source["directions"]))
                receipt_ids[name] = accept(receipts, packet.wire, packet.credential)

        result = {}
        for case in fixture["reviews"]:
            pair = tuple(receipt_ids[name] for name in case["pair"])
            report = compare_restart(receipts, reports,
                                     RestartRequest(batch, *pair, case["observations"]))
            saved = read_restart_report(reports, report.report_id)
            if saved != report or tuple(item.receipt_id for item in saved.sources) != pair:
                raise AssertionError(f"wrong V2 report or sources for {case}")
            if saved.status != case["status"]:
                raise AssertionError(f"unexpected V2 status for {case}: {saved.status}")
            result[case["case"]] = saved.status
        return result


if __name__ == "__main__":
    fixture = json.loads(_SAMPLE.read_text(encoding="utf-8"))
    # Original V1 reference output, unchanged.
    print(json.dumps(pulse_reference(fixture), sort_keys=True))
    # Restoration cross-check printed on a separate line so the V1 output stays intact.
    print("POINTWISE_RESTART_FIXTURE "
          + json.dumps(restoration_reference(fixture), sort_keys=True))
