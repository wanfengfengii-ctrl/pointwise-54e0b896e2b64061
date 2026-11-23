from exchange.envelope import pack_v1, pack_v2
from exchange.issued_catalog import IssuedCatalog, IssuedPacket, IssuedRestoration
from exchange.provenance import IssueOrigin, issue_identity, issue_identity_v2
from exchange.response import FreeStateBasis, build_family, build_response
from station.repository import StationRepository
from station.revisions import read_revision
from station.validation import check_spectrum, validate_basis, validate_data_model


def _origin(station: StationRepository, revision) -> IssueOrigin:
    return IssueOrigin(station.station_id, revision.batch, revision.channel,
                       revision.revision_id, f'revision/{revision.revision_id}')


def issue_packet(station: StationRepository, issued: IssuedCatalog, revision_id: int) -> IssuedPacket:
    if issued.connection is not station.connection:
        raise ValueError('the issue catalog must use the selected station ledger')
    revision = read_revision(station, revision_id)
    prior = issued.find_by_revision(revision_id)
    if prior is not None:
        return prior
    origin = _origin(station, revision)
    evidence = build_response(revision.model)
    wire = pack_v1(issue_identity(origin), origin, evidence)
    return issued.register_once(revision_id, wire)


def issue_restart(station: StationRepository, issued: IssuedCatalog, revision_id: int,
                  base, directions) -> IssuedRestoration:
    if issued.connection is not station.connection:
        raise ValueError('the issue catalog must use the selected station ledger')
    revision = read_revision(station, revision_id)
    model = validate_data_model(revision.model)
    base, directions = validate_basis(base, directions, len(model.A))
    check_spectrum(model.A)
    origin = _origin(station, revision)
    prior = issued.find_restoration_by_revision(revision_id)
    if prior is not None:
        if prior.base != base or prior.directions != directions:
            raise ValueError('a frozen revision can carry only its original restoration license')
        return prior
    family = build_family(model, FreeStateBasis(base, directions))
    wire = pack_v2(issue_identity_v2(origin), origin, family)
    return issued.register_restoration_once(revision_id, base, directions, wire)
