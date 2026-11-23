import hashlib
import json
from dataclasses import dataclass

from exchange.evidence import ResponseEvidence, decode_evidence, encode_evidence
from exchange.family import ResponseFamily, decode_family, encode_family
from exchange.provenance import (IssueCredential, IssueOrigin, decode_origin, encode_origin,
                                 issue_identity, issue_identity_v2)


@dataclass(frozen=True)
class V1Packet:
    issue_id: str
    origin: IssueOrigin
    evidence: ResponseEvidence
    digest: str

    @property
    def version(self) -> int:
        return 1

    @property
    def credential(self) -> IssueCredential:
        return IssueCredential(self.issue_id, self.origin, self.digest)


@dataclass(frozen=True)
class V2Packet:
    issue_id: str
    origin: IssueOrigin
    family: ResponseFamily
    digest: str

    @property
    def version(self) -> int:
        return 2

    @property
    def credential(self) -> IssueCredential:
        return IssueCredential(self.issue_id, self.origin, self.digest)


def _canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _payload(issue_id, origin, evidence):
    return {'version': 1, 'issue_id': issue_id, 'origin': encode_origin(origin),
            'body': encode_evidence(evidence)}


def packet_digest(issue_id: str, origin: IssueOrigin, evidence: ResponseEvidence) -> str:
    return hashlib.sha256(_canonical(_payload(issue_id, origin, evidence))).hexdigest()


def pack_v1(issue_id: str, origin: IssueOrigin, evidence: ResponseEvidence) -> bytes:
    payload = _payload(issue_id, origin, evidence)
    decode_origin(payload['origin'])
    decode_evidence(payload['body'])
    if issue_id != issue_identity(origin):
        raise ValueError('issuance identity does not match its origin')
    return _canonical({**payload, 'digest': packet_digest(issue_id, origin, evidence)})


def _payload_v2(issue_id, origin, family):
    return {'version': 2, 'issue_id': issue_id, 'origin': encode_origin(origin),
            'body': encode_family(family)}


def packet_digest_v2(issue_id: str, origin: IssueOrigin, family: ResponseFamily) -> str:
    return hashlib.sha256(_canonical(_payload_v2(issue_id, origin, family))).hexdigest()


def pack_v2(issue_id: str, origin: IssueOrigin, family: ResponseFamily) -> bytes:
    payload = _payload_v2(issue_id, origin, family)
    decode_origin(payload['origin'])
    decode_family(payload['body'])
    if issue_id != issue_identity_v2(origin):
        raise ValueError('restoration identity does not match its origin')
    return _canonical({**payload, 'digest': packet_digest_v2(issue_id, origin, family)})


def _unpack_envelope(wire: bytes, version: int, allowed_keys: set[str]):
    if not isinstance(wire, bytes):
        raise ValueError(f'a V{version} packet must be bytes')
    try:
        raw = json.loads(wire.decode('utf-8'))
    except (ValueError, RecursionError) as error:
        raise ValueError(f'invalid V{version} JSON') from error
    if not isinstance(raw, dict) or set(raw) != allowed_keys:
        raise ValueError(f'invalid V{version} envelope')
    if type(raw['version']) is not int or raw['version'] != version:
        raise ValueError('unsupported packet version')
    if _canonical(raw) != wire:
        raise ValueError(f'packet is not canonical V{version} JSON')
    return raw


_V1_KEYS = {'version', 'issue_id', 'origin', 'body', 'digest'}
_V2_KEYS = {'version', 'issue_id', 'origin', 'body', 'digest'}


def unpack_v1(wire: bytes) -> V1Packet:
    raw = _unpack_envelope(wire, 1, _V1_KEYS)
    origin = decode_origin(raw['origin'])
    evidence = decode_evidence(raw['body'])
    issue_id = raw['issue_id']
    if issue_id != issue_identity(origin):
        raise ValueError('issuance identity does not match its origin')
    digest = packet_digest(issue_id, origin, evidence)
    if raw['digest'] != digest:
        raise ValueError('packet digest mismatch')
    return V1Packet(issue_id, origin, evidence, digest)


def unpack_v2(wire: bytes) -> V2Packet:
    raw = _unpack_envelope(wire, 2, _V2_KEYS)
    origin = decode_origin(raw['origin'])
    family = decode_family(raw['body'])
    issue_id = raw['issue_id']
    if issue_id != issue_identity_v2(origin):
        raise ValueError('restoration identity does not match its origin')
    digest = packet_digest_v2(issue_id, origin, family)
    if raw['digest'] != digest:
        raise ValueError('packet digest mismatch')
    return V2Packet(issue_id, origin, family, digest)


def packet_version(wire: bytes) -> int:
    if not isinstance(wire, bytes):
        raise ValueError('a packet must be bytes')
    try:
        raw = json.loads(wire.decode('utf-8'))
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid packet JSON') from error
    if not isinstance(raw, dict) or type(raw.get('version')) is not int:
        raise ValueError('invalid packet envelope')
    if raw['version'] not in (1, 2):
        raise ValueError('unsupported packet version')
    return raw['version']


def unpack_packet(wire: bytes):
    version = packet_version(wire)
    return unpack_v1(wire) if version == 1 else unpack_v2(wire)
