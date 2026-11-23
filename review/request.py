from dataclasses import dataclass

from exchange.envelope import unpack_v1, unpack_packet
from exchange.polynomials import parse_rational
from receiving.catalog import ReceiptCatalog
from receiving.receipt import Receipt
from reports.publish import publish_report, publish_restart
from reports.repository import ReportRepository, RestartReport, SignedReport
from review.difference import compose_difference
from review.energy import compute_energy
from review.restart_model import analyse_restart
from review.verdict import decide_verdict


@dataclass(frozen=True)
class ReviewRequest:
    batch: str
    primary_receipt: int
    reference_receipt: int
    threshold: object


@dataclass(frozen=True)
class RestartRequest:
    batch: str
    primary_receipt: int
    reference_receipt: int
    observations: object


def _evidence_for_batch(receipt: Receipt, batch: str):
    packet = unpack_v1(receipt.wire)
    source = receipt.source
    if (packet.issue_id, packet.digest, packet.origin) != (source.issue_id, source.digest, source.origin):
        raise ValueError('received packet and receipt origin do not match')
    if source.origin.batch != batch:
        raise ValueError('both receipts must belong to the requested batch')
    return packet.evidence


def compare(receipts: ReceiptCatalog, reports: ReportRepository, request: ReviewRequest) -> SignedReport:
    if not isinstance(request, ReviewRequest):
        raise ValueError('a ReviewRequest is required')
    batch = request.batch
    if not isinstance(batch, str) or not batch or batch != batch.strip():
        raise ValueError('invalid review batch')
    threshold = parse_rational(request.threshold)
    if threshold < 0:
        raise ValueError('energy threshold must be nonnegative')
    primary = receipts.lookup(request.primary_receipt)
    reference = receipts.lookup(request.reference_receipt)
    difference = compose_difference(_evidence_for_batch(primary, batch), _evidence_for_batch(reference, batch))
    result = compute_energy(difference)
    verdict = decide_verdict(result, threshold)
    return publish_report(reports, batch, threshold, result, verdict, (primary.source, reference.source))


def _family_for_batch(receipt: Receipt, batch: str):
    packet = unpack_packet(receipt.wire)
    if packet.version != 2:
        raise ValueError('restoration review requires V2 receipts')
    source = receipt.source
    if (packet.issue_id, packet.digest, packet.origin) != (source.issue_id, source.digest, source.origin):
        raise ValueError('received packet and receipt origin do not match')
    if source.origin.batch != batch:
        raise ValueError('both receipts must belong to the requested batch')
    return packet.family


def _parse_observations(observations):
    if isinstance(observations, (str, bytes)) or not isinstance(observations, (list, tuple)):
        raise ValueError('observations must be a nonempty list or tuple')
    if not observations:
        raise ValueError('observations must be nonempty')
    parsed = []
    seen = set()
    for item in observations:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('each observation must be a (tick, difference) pair')
        tick, difference = item
        if type(tick) is not int or tick < 0:
            raise ValueError('observation ticks must be nonnegative integers')
        if tick in seen:
            raise ValueError('observation ticks must be distinct')
        seen.add(tick)
        parsed.append((tick, parse_rational(difference)))
    return tuple(sorted(parsed))


def compare_restart(receipts: ReceiptCatalog, reports: ReportRepository,
                   request: RestartRequest) -> RestartReport:
    if not isinstance(request, RestartRequest):
        raise ValueError('a RestartRequest is required')
    batch = request.batch
    if not isinstance(batch, str) or not batch or batch != batch.strip():
        raise ValueError('invalid review batch')
    observations = _parse_observations(request.observations)
    primary = receipts.lookup(request.primary_receipt)
    reference = receipts.lookup(request.reference_receipt)
    primary_family = _family_for_batch(primary, batch)
    reference_family = _family_for_batch(reference, batch)
    analysis = analyse_restart(primary_family, reference_family, observations)
    return publish_restart(reports, batch, observations, analysis.status,
                           analysis.finite_witness, analysis.divergent_witness,
                           (primary.source, reference.source))
