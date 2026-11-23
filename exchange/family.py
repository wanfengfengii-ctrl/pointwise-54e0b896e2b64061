"""Public response family carried by a V2 restoration packet.

A frozen revision together with a rational initial-state base and direction
table defines an affine set of allowed initial states.  The packet never
carries those state coordinates, the model matrices or the measured rows;
it only carries the observable response of the base and of every direction.
The output at tick ``k`` for parameter vector ``u`` is ``base(k) + sum(u_i *
direction_i(k))``, which is exactly ``C A**k (base + sum(u_i * direction_i))``.
"""

from dataclasses import dataclass

from exchange.evidence import ResponseEvidence, decode_evidence, encode_evidence


@dataclass(frozen=True)
class ResponseFamily:
    base: ResponseEvidence
    directions: tuple[ResponseEvidence, ...]

    @property
    def parameters(self) -> int:
        return len(self.directions)


def encode_family(family: ResponseFamily) -> dict:
    return {
        'parameters': len(family.directions),
        'base': encode_evidence(family.base),
        'directions': [encode_evidence(entry) for entry in family.directions],
    }


def decode_family(raw: dict) -> ResponseFamily:
    if not isinstance(raw, dict) or set(raw) != {'parameters', 'base', 'directions'}:
        raise ValueError('invalid V2 response family')
    parameters = raw['parameters']
    if type(parameters) is not int or not 0 <= parameters < 2**31:
        raise ValueError('invalid V2 parameter count')
    if not isinstance(raw['directions'], list) or len(raw['directions']) != parameters:
        raise ValueError('V2 direction table does not match its parameter count')
    base = decode_evidence(raw['base'])
    directions = tuple(decode_evidence(entry) for entry in raw['directions'])
    return ResponseFamily(base, directions)
