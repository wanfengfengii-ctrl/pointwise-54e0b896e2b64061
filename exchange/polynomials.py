import re
from fractions import Fraction


_RATIONAL = re.compile(r'[+-]?[0-9]+(?:/[0-9]+)?')
_ZERO = (Fraction(0),)
_ONE = (Fraction(1),)


def parse_rational(value) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, str) and _RATIONAL.fullmatch(value):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError('invalid rational coefficient') from error
    raise ValueError('expected an exact rational number')


def _trim(values):
    values = list(values)
    while values and values[-1] == 0:
        values.pop()
    return tuple(values) or _ZERO


def trim_polynomial(values):
    """Return the canonical low-to-high coefficient tuple with zero tail removed."""
    return _trim(map(parse_rational, values))


def add_polynomials(left, right):
    left, right = _trim(left), _trim(right)
    result = [Fraction(0)] * max(len(left), len(right))
    for i, value in enumerate(left):
        result[i] += value
    for i, value in enumerate(right):
        result[i] += value
    return _trim(result)


def scale_polynomial(values, factor):
    factor = parse_rational(factor)
    return _trim(value * factor for value in values)


def evaluate_polynomial(values, point):
    point = parse_rational(point)
    result = Fraction(0)
    for value in reversed(_trim(values)):
        result = result * point + value
    return result


def multiply_polynomials(left, right):
    left, right = _trim(left), _trim(right)
    result = [Fraction(0)] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            result[i + j] += a * b
    return _trim(result)


def subtract_polynomials(left, right):
    left, right = tuple(left), tuple(right)
    result = [Fraction(0)] * max(len(left), len(right))
    for i, value in enumerate(left):
        result[i] += value
    for i, value in enumerate(right):
        result[i] -= value
    return _trim(result)


def _divide(dividend, divisor):
    remainder = list(_trim(dividend))
    quotient = [Fraction(0)] * max(0, len(remainder) - len(divisor) + 1)
    while remainder and len(remainder) >= len(divisor):
        offset = len(remainder) - len(divisor)
        factor = remainder[-1] / divisor[-1]
        quotient[offset] = factor
        for index, value in enumerate(divisor):
            remainder[index + offset] -= factor * value
        while remainder and remainder[-1] == 0:
            remainder.pop()
    return _trim(quotient), _trim(remainder)


def divide_polynomials(dividend, divisor):
    """Return the exact low-to-high quotient and remainder of two polynomials."""
    return _divide(trim_polynomial(dividend), trim_polynomial(divisor))


def polynomial_gcd(left, right):
    """Return the monic greatest common divisor of two rational polynomials."""
    left, right = trim_polynomial(left), trim_polynomial(right)
    while right != _ZERO:
        _, remainder = _divide(left, right)
        left, right = right, remainder
    if left == _ZERO:
        return _ONE
    leading = left[-1]
    return tuple(value / leading for value in left)


def _normalize_constant(values):
    values = trim_polynomial(values)
    if values[0] == 0:
        raise ValueError('a polynomial with a nonzero constant term is required')
    return tuple(value / values[0] for value in values)


def polynomial_lcm(left, right):
    """Return the constant-one normalized least common multiple."""
    left, right = trim_polynomial(left), trim_polynomial(right)
    common = polynomial_gcd(left, right)
    product = multiply_polynomials(left, right)
    quotient, remainder = _divide(product, common)
    if remainder != _ZERO:
        raise ValueError('polynomial gcd does not divide the product')
    return _normalize_constant(quotient)


def polynomial_power(base, exponent: int):
    if type(exponent) is not int or exponent < 0:
        raise ValueError('a nonnegative integer exponent is required')
    result = _ONE
    for _ in range(exponent):
        result = multiply_polynomials(result, base)
    return result


def polynomial_remainder(dividend, divisor):
    return _divide(trim_polynomial(dividend), trim_polynomial(divisor))[1]


def rational_root_multiplicity(coefficients, factor) -> int:
    """Count how many times the linear ``factor`` divides ``coefficients``."""
    remaining = trim_polynomial(coefficients)
    factor = trim_polynomial(factor)
    count = 0
    while len(remaining) > 1:
        quotient, remainder = _divide(remaining, factor)
        if remainder != _ZERO:
            break
        remaining = quotient
        count += 1
    return count


def reduce_rational(numerator, denominator):
    """Cancel the polynomial gcd and normalize the denominator at q=0."""
    numerator = _trim(map(parse_rational, numerator))
    denominator = _trim(map(parse_rational, denominator))
    if not denominator[0]:
        raise ValueError('tail denominator must be nonzero at q=0')
    if numerator == _ZERO:
        return _ZERO, _ONE
    left, right = denominator, numerator
    while right != _ZERO:
        _, remainder = _divide(left, right)
        left, right = right, remainder
    numerator, _ = _divide(numerator, left)
    denominator, _ = _divide(denominator, left)
    scale = denominator[0]
    return tuple(value / scale for value in numerator), tuple(value / scale for value in denominator)
