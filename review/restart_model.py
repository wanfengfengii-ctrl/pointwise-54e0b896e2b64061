"""Exact analysis of free-response observation tables.

Given two received V2 families and a table of primary-minus-reference
readings, decide whether any allowed initial state on each side can explain
every reading, and whether the *full* difference (all ticks 0, 1, 2, ...)
is square-summable for every, none, or only some of those explanations.

Everything is exact rational arithmetic.  The sequence attached to a family
is eventually a combination of ``k**m * lambda**k`` with ``lambda`` in
``{0, 1/2, 1, 2}``.  Its generating function therefore has poles only at
``q = 1`` (lambda 1), ``q = 1/2`` (lambda 2), ``q = 2`` (lambda 1/2), or no
finite pole (lambda 0).  The difference is square-summable exactly when its
reduced generating function has no pole at ``q = 1`` and none at ``q = 1/2``.
"""

from dataclasses import dataclass
from fractions import Fraction

from exchange.family import ResponseFamily
from exchange.polynomials import (add_polynomials, divide_polynomials, multiply_polynomials,
                                  polynomial_lcm, rational_root_multiplicity,
                                  scale_polynomial, subtract_polynomials)
from review.linalg import solve_affine


_ZERO = Fraction(0)
_ONE = Fraction(1)


# --------------------------------------------------------------------------- #
# Exact tick evaluation                                                       #
# --------------------------------------------------------------------------- #

def _klam_combine(left, right, coefficients):
    size = len(coefficients)
    product = [Fraction(0)] * (2 * size - 1)
    for i, first in enumerate(left):
        if first:
            for j, second in enumerate(right):
                if second:
                    product[i + j] += first * second
    for power in range(2 * size - 2, size - 1, -1):
        value = product[power]
        if value:
            for lag in range(1, size + 1):
                product[power - lag] += value * coefficients[lag - 1]
    return product[:size]


def _linear_recurrence(coefficients, initial, index):
    """Return term ``index`` of ``a[n] = sum coefficients[i] * a[n-1-i]``."""
    size = len(coefficients)
    if index < size:
        return initial[index]
    result = [Fraction(0)] * size
    result[0] = _ONE
    base = [Fraction(0)] * size
    if size == 1:
        base[0] = coefficients[0]
    else:
        base[1] = _ONE
    power = index
    while power:
        if power & 1:
            result = _klam_combine(result, base, coefficients)
        base = _klam_combine(base, base, coefficients)
        power >>= 1
    return sum((coefficient * value for coefficient, value in zip(result, initial)), _ZERO)


def _tail_prefix(numerator, denominator, count):
    depth = len(denominator) - 1
    values = []
    for position in range(count):
        feed = numerator[position] if position < len(numerator) else _ZERO
        carried = sum((denominator[lag] * values[position - lag]
                       for lag in range(1, min(position, depth) + 1)), _ZERO)
        values.append(feed - carried)
    return values


def _tail_value(numerator, denominator, index):
    depth = len(denominator) - 1
    if depth == 0:
        return numerator[index] if index < len(numerator) else _ZERO
    start = max(len(numerator), depth)
    window = start + depth
    limit = min(index, window - 1)
    values = _tail_prefix(numerator, denominator, limit + 1)
    if index <= limit:
        return values[index]
    initial = values[start:start + depth]
    coefficients = tuple(-denominator[lag] for lag in range(1, depth + 1))
    return _linear_recurrence(coefficients, initial, index - start)


def tick_value(evidence, tick: int) -> Fraction:
    if type(tick) is not int or tick < 0:
        raise ValueError('a tick must be a nonnegative integer')
    if tick == 0:
        return evidence.direct
    return _tail_value(evidence.numerator, evidence.denominator, tick - 1)


def family_tick(family: ResponseFamily, parameters, tick: int) -> Fraction:
    total = tick_value(family.base, tick)
    for weight, direction in zip(parameters, family.directions):
        if weight:
            total += weight * tick_value(direction, tick)
    return total


def _readings(family: ResponseFamily, parameters) -> tuple[Fraction, ...]:
    return tuple(family_tick(family, parameters, tick) for tick in range(4))


# --------------------------------------------------------------------------- #
# Rational generating functions                                               #
# --------------------------------------------------------------------------- #

def _common_denominator(evidences) -> tuple[Fraction, ...]:
    result = (_ONE,)
    for evidence in evidences:
        result = polynomial_lcm(result, evidence.denominator)
    return result


def _numerator_over(evidence, denominator) -> tuple[Fraction, ...]:
    quotient, remainder = divide_polynomials(denominator, evidence.denominator)
    if remainder != (_ZERO,):
        raise ValueError('a family denominator does not divide the common denominator')
    tail = multiply_polynomials(evidence.numerator, quotient)
    tail = (Fraction(0),) + tuple(tail)
    return add_polynomials(scale_polynomial(denominator, evidence.direct), tail)


def _family_numerators(family: ResponseFamily):
    vector = (family.base, *family.directions)
    denominator = _common_denominator(vector)
    numerators = tuple(_numerator_over(evidence, denominator) for evidence in vector)
    return denominator, numerators


def _derivative(poly, point, order):
    total = _ZERO
    for index in range(order, len(poly)):
        factor = 1
        for step in range(order):
            factor *= index - step
        total += poly[index] * factor * point ** (index - order)
    return total


def _finiteness_equations(constant, primary_terms, reference_terms, multiplicities):
    equations = []
    for point, order_count in multiplicities:
        for order in range(order_count):
            coefficients = [_derivative(term, point, order) for term in primary_terms]
            coefficients += [_derivative(term, point, order) for term in reference_terms]
            right = -_derivative(constant, point, order)
            equations.append((coefficients, right))
    return equations


def _satisfies(constant, primary_terms, reference_terms, parameters, multiplicities):
    total = list(constant)
    for weight, term in zip(parameters, (*primary_terms, *reference_terms)):
        if weight:
            total = add_polynomials(total, scale_polynomial(term, weight))
    for point, order_count in multiplicities:
        for order in range(order_count):
            if _derivative(total, point, order) != 0:
                return False
    return True


@dataclass(frozen=True)
class RestartAnalysis:
    status: str
    finite_witness: tuple[tuple[Fraction, ...], tuple[Fraction, ...]] | None
    divergent_witness: tuple[tuple[Fraction, ...], tuple[Fraction, ...]] | None


def _witness(primary: ResponseFamily, reference: ResponseFamily, parameters):
    primary_parameters = parameters[:primary.parameters]
    reference_parameters = parameters[primary.parameters:]
    return _readings(primary, primary_parameters), _readings(reference, reference_parameters)


def analyse_restart(primary: ResponseFamily, reference: ResponseFamily, observations) -> RestartAnalysis:
    primary_count = primary.parameters
    variables = primary_count + reference.parameters
    primary_denominator, primary_numerators = _family_numerators(primary)
    reference_denominator, reference_numerators = _family_numerators(reference)

    mp_base, *mp_directions = primary_numerators
    mr_base, *mr_directions = reference_numerators
    constant = subtract_polynomials(multiply_polynomials(mp_base, reference_denominator),
                                    multiply_polynomials(mr_base, primary_denominator))
    primary_terms = tuple(multiply_polynomials(term, reference_denominator) for term in mp_directions)
    reference_terms = tuple(scale_polynomial(multiply_polynomials(term, primary_denominator), -1)
                            for term in mr_directions)

    total_denominator = multiply_polynomials(primary_denominator, reference_denominator)
    multiplicities = (
        (Fraction(1), rational_root_multiplicity(total_denominator, (Fraction(1), Fraction(-1)))),
        (Fraction(1, 2), rational_root_multiplicity(total_denominator, (Fraction(1), Fraction(-2)))),
    )

    equations = []
    for tick, difference in observations:
        row = [tick_value(direction, tick) for direction in primary.directions]
        row += [-tick_value(direction, tick) for direction in reference.directions]
        right = difference - tick_value(primary.base, tick) + tick_value(reference.base, tick)
        equations.append((row, right))

    allowed = solve_affine(equations, variables)
    if allowed is None:
        return RestartAnalysis('inconsistent', None, None)

    finite_equations = _finiteness_equations(constant, primary_terms, reference_terms, multiplicities)
    restricted = solve_affine([*equations, *finite_equations], variables)
    if restricted is None:
        return RestartAnalysis('all_divergent', None, None)

    if len(restricted[1]) == len(allowed[1]):
        return RestartAnalysis('all_finite', None, None)

    finite_point = restricted[0]
    divergent_point = None
    for direction in allowed[1]:
        candidate = tuple(base + step for base, step in zip(finite_point, direction))
        if not _satisfies(constant, primary_terms, reference_terms, candidate, multiplicities):
            divergent_point = candidate
            break
    if divergent_point is None:
        # Fall back to a scaled direction, which cannot stay in the finite affine set.
        direction = allowed[1][0]
        divergent_point = tuple(base + 2 * step for base, step in zip(finite_point, direction))
    finite_witness = _witness(primary, reference, finite_point)
    divergent_witness = _witness(primary, reference, divergent_point)
    if divergent_witness == finite_witness:
        # Prefer a divergent explanation whose first four readings identify it.
        for scale in (2, 3, -1, Fraction(1, 2)):
            for direction in allowed[1]:
                candidate = tuple(base + scale * step for base, step in zip(finite_point, direction))
                if _satisfies(constant, primary_terms, reference_terms, candidate, multiplicities):
                    continue
                witness = _witness(primary, reference, candidate)
                if witness != finite_witness:
                    divergent_point, divergent_witness = candidate, witness
                    break
            if divergent_witness != finite_witness:
                break
    return RestartAnalysis('mixed', finite_witness, divergent_witness)
