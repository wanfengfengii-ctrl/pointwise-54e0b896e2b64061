from exchange.envelope import unpack_packet
from exchange.provenance import IssueCredential


def validate_incoming(wire: bytes, credential: IssueCredential):
    packet = unpack_packet(wire)
    if not isinstance(credential, IssueCredential) or packet.credential != credential:
        raise ValueError('packet conflicts with the original issuance credential')
    return packet
