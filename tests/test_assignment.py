import itertools
import random

import pytest

from app.assignment import hungarian_min_cost


def _assignment_cost(cost: list[list[float]], row_to_col: list[int]) -> float:
    return sum(cost[i][row_to_col[i]] for i in range(len(row_to_col)))


def _brute_force_min_cost(cost: list[list[float]]) -> float:
    n, m = len(cost), len(cost[0])
    best = float("inf")
    for cols in itertools.permutations(range(m), n):
        total = sum(cost[i][cols[i]] for i in range(n))
        best = min(best, total)
    return best


def test_empty_matrix_returns_empty_assignment():
    assert hungarian_min_cost([]) == []


def test_single_row_picks_cheapest_column():
    assert hungarian_min_cost([[5.0, 1.0, 3.0]]) == [1]


def test_two_by_two_prefers_lower_total_cost():
    # Diagonal (1+1=2) beats the cross assignment (2+2=4).
    assert hungarian_min_cost([[1.0, 2.0], [2.0, 1.0]]) == [0, 1]


def test_square_matrix_matches_known_optimal_assignment():
    cost = [[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]]
    assignment = hungarian_min_cost(cost)
    assert len(set(assignment)) == 3  # a valid permutation
    assert _assignment_cost(cost, assignment) == _brute_force_min_cost(cost)


def test_rectangular_more_columns_than_rows():
    cost = [[1.0, 9.0, 9.0, 9.0], [9.0, 1.0, 9.0, 9.0]]
    assignment = hungarian_min_cost(cost)
    assert _assignment_cost(cost, assignment) == pytest.approx(2.0)


def test_more_rows_than_columns_raises():
    with pytest.raises(ValueError):
        hungarian_min_cost([[1.0], [2.0], [3.0]])


def test_ragged_rows_raises():
    with pytest.raises(ValueError):
        hungarian_min_cost([[1.0, 2.0], [1.0]])


@pytest.mark.parametrize("seed", range(20))
def test_matches_brute_force_on_random_small_matrices(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 4)
    m = rng.randint(n, n + 2)
    cost = [[rng.uniform(0, 50) for _ in range(m)] for _ in range(n)]
    assignment = hungarian_min_cost(cost)
    assert len(set(assignment)) == n  # each row gets a distinct column
    assert _assignment_cost(cost, assignment) == pytest.approx(_brute_force_min_cost(cost))
