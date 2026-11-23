"""Exact rational matrix helpers shared by the pulse and restoration paths."""

from fractions import Fraction


def matrix_product(left, right):
    columns = tuple(zip(*right))
    return tuple(
        tuple(sum(a * b for a, b in zip(row, column)) for column in columns)
        for row in left
    )


def matrix_power(matrix, exponent: int):
    size = len(matrix)
    if type(exponent) is not int or exponent < 0:
        raise ValueError('a nonnegative integer power is required')
    result = tuple(tuple(Fraction(i == j) for j in range(size)) for i in range(size))
    base = tuple(tuple(Fraction(value) for value in row) for row in matrix)
    while exponent:
        if exponent & 1:
            result = matrix_product(result, base)
        exponent >>= 1
        if exponent:
            base = matrix_product(base, base)
    return result


def characteristic_coefficients(matrix):
    """Return det(I - qA) as low-to-high coefficients by the trace recurrence."""
    size = len(matrix)
    work = tuple(tuple(Fraction(i == j) for j in range(size)) for i in range(size))
    coefficients = [Fraction(1)]
    for step in range(1, size + 1):
        advanced = matrix_product(matrix, work)
        coefficient = -sum(advanced[i][i] for i in range(size)) / step
        coefficients.append(coefficient)
        work = tuple(
            tuple(value + (coefficient if i == j else 0) for j, value in enumerate(row))
            for i, row in enumerate(advanced)
        )
    return tuple(coefficients)


def char_poly_high_to_low(matrix):
    """Return the characteristic polynomial det(lambda*I - A) in descending powers."""
    return tuple(reversed(characteristic_coefficients(matrix)))
