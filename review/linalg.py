"""Small exact linear-algebra helpers for rational constraint systems."""

from fractions import Fraction


def solve_affine(equations, variables: int):
    """Solve ``rows · x = rhs`` exactly.

    ``equations`` is a sequence of ``(row, rhs)`` pairs.  Returns ``None`` when
    inconsistent, else ``(particular, basis)`` where every solution is
    ``particular + sum(weight * direction for direction in basis)``.
    """
    matrix = [[Fraction(value) for value in row] + [Fraction(rhs)]
              for row, rhs in equations]
    if any(len(row) != variables + 1 for row in matrix):
        raise ValueError('constraint rows have the wrong width')
    pivot_columns = []
    rank = 0
    for column in range(variables):
        pivot = next((index for index in range(rank, len(matrix)) if matrix[index][column]), None)
        if pivot is None:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        divisor = matrix[rank][column]
        matrix[rank] = [value / divisor for value in matrix[rank]]
        for index, row in enumerate(matrix):
            if index != rank and row[column]:
                factor = row[column]
                matrix[index] = [value - factor * entry for value, entry in zip(row, matrix[rank])]
        pivot_columns.append(column)
        rank += 1
        if rank == len(matrix):
            break
    for row in matrix:
        if all(value == 0 for value in row[:variables]) and row[variables] != 0:
            return None
    particular = [Fraction(0)] * variables
    for row, column in enumerate(pivot_columns):
        particular[column] = matrix[row][variables]
    free_columns = [column for column in range(variables) if column not in pivot_columns]
    basis = []
    for free in free_columns:
        direction = [Fraction(0)] * variables
        direction[free] = Fraction(1)
        for row, column in enumerate(pivot_columns):
            direction[column] = -matrix[row][free]
        basis.append(tuple(direction))
    return tuple(particular), tuple(basis)


def evaluate_expression(expression, parameters) -> Fraction:
    total = Fraction(expression[0])
    for weight, coefficient in zip(parameters, expression[1:]):
        total += weight * coefficient
    return total
