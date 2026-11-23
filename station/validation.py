from fractions import Fraction

from exchange.matrix import characteristic_coefficients
from exchange.polynomials import divide_polynomials, parse_rational, trim_polynomial
from station.models import FrozenRevision, StationModel, StationSource


_ALLOWED_ROOTS = (Fraction(0), Fraction(1, 2), Fraction(1), Fraction(2))


def validate_label(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f'{field} must be a nonempty trimmed string')
    return value


def _vector(values, size):
    if not isinstance(values, (tuple, list)) or len(values) != size:
        raise ValueError('model vector has the wrong size')
    return tuple(parse_rational(value) for value in values)


def validate_model(model: StationModel) -> StationModel:
    if not isinstance(model, StationModel):
        raise ValueError('a StationModel is required')
    if not isinstance(model.A, (tuple, list)) or len(model.A) > 4:
        raise ValueError('A must have zero to four rows')
    size = len(model.A)
    matrix = tuple(_vector(row, size) for row in model.A)
    b = _vector(model.B, size)
    c = _vector(model.C, size)
    direct = parse_rational(model.D)
    initial = model.initial_state if model.initial_state is not None else (0,) * size
    initial = _vector(initial, size)
    if any(initial):
        raise ValueError('the initial state must be zero')
    return StationModel(matrix, b, c, direct, initial)


def validate_source(source: StationSource, size: int) -> StationSource:
    if not isinstance(source, StationSource) or not isinstance(source.topology, str):
        raise ValueError('invalid station source')
    if not isinstance(source.state_ids, (list, tuple)) or not isinstance(source.raw_rows, (list, tuple)):
        raise ValueError('state identifiers and measured rows must be sequences')
    ids = tuple(validate_label(item, 'state identifier') for item in source.state_ids)
    if ids and (len(ids) != size or len(set(ids)) != len(ids)):
        raise ValueError('state identifiers must match the model states')
    if any(not isinstance(row, str) for row in source.raw_rows):
        raise ValueError('station measured rows must be text')
    return StationSource(ids, source.topology, tuple(source.raw_rows))


def validate_parent(parent: FrozenRevision, batch: str, channel: str) -> None:
    if (parent.batch, parent.channel) != (batch, channel):
        raise ValueError('the parent must belong to the same batch and channel')


def validate_data_model(model: StationModel) -> StationModel:
    """Validate model dimensions and exact coefficients without constraining state."""
    if not isinstance(model, StationModel):
        raise ValueError('a StationModel is required')
    if not isinstance(model.A, (tuple, list)) or len(model.A) > 4:
        raise ValueError('A must have zero to four rows')
    size = len(model.A)
    matrix = tuple(_vector(row, size) for row in model.A)
    b = _vector(model.B, size)
    c = _vector(model.C, size)
    direct = parse_rational(model.D)
    return StationModel(matrix, b, c, direct, None)


def validate_free_state(values, size):
    """Validate an exact rational state vector used by the restoration path."""
    if not isinstance(values, (tuple, list)) or len(values) != size:
        raise ValueError('initial state has the wrong size')
    return tuple(parse_rational(value) for value in values)


def validate_basis(base, directions, size):
    base = validate_free_state(base, size)
    if not isinstance(directions, (tuple, list)):
        raise ValueError('directions must be a list or tuple of vectors')
    frozen = tuple(validate_free_state(direction, size) for direction in directions)
    return base, frozen


def _divides(left, right) -> bool:
    """Return True when low-to-high polynomial ``left`` divides ``right``."""
    _, remainder = divide_polynomials(right, left)
    return remainder == (Fraction(0),)


def check_spectrum(matrix) -> None:
    """Reject a matrix whose characteristic roots are not 0, 1/2, 1 or 2.

    Multiplicity is preserved: the characteristic polynomial ``det(I - qA)``
    must factor exactly into the four allowed linear factors, so repeated
    roots are admitted while every other algebraic root is rejected.
    """
    factors = ((Fraction(1), Fraction(-1, 2)), (Fraction(1), Fraction(-1)),
               (Fraction(1), Fraction(-2)))
    coefficients = trim_polynomial(characteristic_coefficients(matrix))
    for factor in factors:
        while len(coefficients) > 1 and _divides(factor, coefficients):
            coefficients, _ = divide_polynomials(coefficients, factor)
    if coefficients != (Fraction(1),):
        raise ValueError('the station spectrum is outside 0, 1/2, 1 and 2')
