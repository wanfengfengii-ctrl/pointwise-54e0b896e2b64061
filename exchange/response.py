"""Exact free-decay response of a station model.

The restoration path never exposes the model or the private state.  The
station computes the public response ``C A**k x`` of a chosen initial state,
and the base/direction responses of the affine family of allowed initials.

A :class:`ResponseEvidence` here uses the same convention as the V1 pulse
evidence: ``direct`` is tick 0, and the rational tail ``numerator/denominator``
has its constant term at tick 1.
"""

from dataclasses import dataclass
from fractions import Fraction

from exchange.evidence import ResponseEvidence
from exchange.family import ResponseFamily
from exchange.matrix import characteristic_coefficients
from exchange.polynomials import multiply_polynomials, reduce_rational
from station.models import StationModel
from station.validation import validate_data_model, validate_model


def build_response(model: StationModel) -> ResponseEvidence:
    """Pulse response used by the V1 path: ``direct = D`` and tail ``C A^(k-1) B``."""
    model = validate_model(model)
    denominator = characteristic_coefficients(model.A)
    states = model.B
    tail = []
    for _ in model.A:
        tail.append(sum(coefficient * value for coefficient, value in zip(model.C, states)))
        states = tuple(sum(a * b for a, b in zip(row, states)) for row in model.A)
    # Cayley–Hamilton makes every coefficient at q**size or above vanish.
    numerator = multiply_polynomials(denominator, tail)[:len(model.A)]
    numerator, denominator = reduce_rational(numerator, denominator)
    return ResponseEvidence(model.D, numerator, denominator)


@dataclass(frozen=True)
class FreeStateBasis:
    base: tuple[Fraction, ...]
    directions: tuple[tuple[Fraction, ...], ...]

    def state(self, parameters) -> tuple[Fraction, ...]:
        parameters = tuple(parameters)
        if len(parameters) != len(self.directions):
            raise ValueError('parameter vector has the wrong size')
        combined = list(self.base)
        for weight, direction in zip(parameters, self.directions):
            weight = Fraction(weight)
            for index, value in enumerate(direction):
                combined[index] += weight * value
        return tuple(combined)


def _advance(model: StationModel, state):
    return tuple(sum(a * b for a, b in zip(row, state)) for row in model.A)


def _observe(model: StationModel, state) -> Fraction:
    return sum(coefficient * value for coefficient, value in zip(model.C, state))


def free_response(model: StationModel, state) -> ResponseEvidence:
    """Return the full free response, ``direct = C x`` and tail ticks 1 onward."""
    model = validate_data_model(model)
    state = tuple(Fraction(value) for value in state)
    if len(state) != len(model.A):
        raise ValueError('initial state has the wrong size')
    direct = _observe(model, state)
    denominator = characteristic_coefficients(model.A)
    samples = []
    current = state
    for _ in range(len(model.A)):
        current = _advance(model, current)
        samples.append(_observe(model, current))
    # The tail sequence begins at tick 1; Cayley-Hamilton makes the first len(A)
    # tail samples determine every later tick, so the generating function equals
    # the truncation of denominator * samples.
    numerator = multiply_polynomials(denominator, samples)[:len(model.A)]
    numerator, denominator = reduce_rational(numerator, denominator)
    return ResponseEvidence(direct, numerator, denominator)


def build_family(model: StationModel, basis: FreeStateBasis) -> ResponseFamily:
    model = validate_data_model(model)
    if len(basis.base) != len(model.A):
        raise ValueError('initial state has the wrong size')
    if any(len(direction) != len(model.A) for direction in basis.directions):
        raise ValueError('direction has the wrong size')
    base = free_response(model, basis.base)
    directions = tuple(free_response(model, direction) for direction in basis.directions)
    return ResponseFamily(base, directions)
