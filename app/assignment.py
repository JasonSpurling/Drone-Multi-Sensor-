"""Hungarian algorithm (Kuhn-Munkres) for the rectangular minimum-cost
assignment problem -- used by app.tracking's batch association to jointly
resolve a batch of simultaneous detections against active tracks (global
nearest neighbor), instead of resolving each detection greedy-first-come.

No numpy/scipy dependency: cost matrices here are always small (a handful
of detections against a handful of tracks per batch), so a plain O(n^2 m)
Python implementation is more than fast enough and keeps the project's
dependency footprint small, consistent with app/kalman.py.
"""

from __future__ import annotations

INF = float("inf")


def hungarian_min_cost(cost: list[list[float]]) -> list[int]:
    """Minimum-cost perfect assignment of rows to columns (n rows, m
    columns, n <= m) that minimizes total cost. Returns, for each row i,
    its assigned column index -- every row is always assigned to some
    column (this is a *dense* assignment; callers that need a "decline to
    match" option should augment the matrix with dummy columns carrying
    the cost of declining, rather than relying on this returning -1).

    Raises ValueError if there are more rows than columns.
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    if n > m:
        raise ValueError(f"more rows ({n}) than columns ({m}) -- pad with dummy columns first")
    if any(len(row) != m for row in cost):
        raise ValueError("all rows must have the same length")

    # Classic O(n^2 m) Hungarian algorithm with potentials (1-indexed
    # internally, as is traditional for this formulation -- row/column 0
    # are sentinels).
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)  # p[j] = row currently assigned to column j (1-indexed), 0 = none
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    row_to_col = [-1] * n
    for j in range(1, m + 1):
        if p[j] != 0:
            row_to_col[p[j] - 1] = j - 1
    return row_to_col
