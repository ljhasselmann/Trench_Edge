"""Small stdlib-only stats helpers shared by this repo's reporting-only
analysis scripts (analyze_ats_correlation.py, analyze_ol_rank_correlation.py).
No numpy/pandas in requirements.txt, so this is hand-rolled rather than a
library call -- extracted here instead of duplicated a second time.
"""

from __future__ import annotations


def pearson_correlation(xs: list, ys: list) -> "float | None":
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)
