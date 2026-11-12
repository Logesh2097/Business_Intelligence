"""The functions AI-written SQL may call. Everything else is refused.

An allow-list, not a block-list: a block-list has to know every dangerous function
PostgreSQL has or will have; this only has to know what a business question needs.
It is what keeps out set_config, current_setting, pg_sleep, pg_read_file, dblink,
lo_import, version(), current_user and the rest.

The list is written as SQL and parsed once, so it follows whatever class names the
installed sqlglot gives each function.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

_ALLOWED_SQL = """
SELECT
  sum(a), count(*), count(DISTINCT a), avg(a), min(a), max(a),
  stddev(a), stddev_pop(a), stddev_samp(a), variance(a), var_pop(a), var_samp(a),
  bool_and(a), bool_or(a), string_agg(a, ','), array_agg(a),
  percentile_cont(0.5) WITHIN GROUP (ORDER BY a), percentile_disc(0.5) WITHIN GROUP (ORDER BY a),
  row_number() OVER (), rank() OVER (), dense_rank() OVER (), ntile(4) OVER (),
  percent_rank() OVER (), cume_dist() OVER (),
  lag(a) OVER (), lead(a) OVER (), first_value(a) OVER (), last_value(a) OVER (),
  round(a, 2), round(a), abs(a), ceil(a), ceiling(a), floor(a), trunc(a), sign(a),
  sqrt(a), power(a, 2), mod(a, 2), ln(a), log(a), exp(a), greatest(a, b), least(a, b),
  coalesce(a, b), nullif(a, b), CASE WHEN a THEN b ELSE c END, CAST(a AS int), a::numeric,
  lower(a), upper(a), initcap(a), length(a), char_length(a), trim(a), ltrim(a), rtrim(a),
  concat(a, b), concat_ws(',', a, b), a || b, substring(a, 1, 2), substr(a, 1, 2),
  left(a, 2), right(a, 2), replace(a, 'x', 'y'), position('x' IN a), strpos(a, 'x'),
  split_part(a, '-', 1), lpad(a, 5, '0'), rpad(a, 5, '0'),
  date_trunc('month', a), date_part('year', a), extract(year FROM a), to_char(a, 'YYYY-MM'),
  to_date(a, 'YYYY-MM-DD'), age(a, b), age(a), make_date(2026, 1, 1), date(a),
  current_date, current_timestamp, now(), localtimestamp,
  a + interval '1 day', a - interval '1 month'
FROM t, generate_series(1, 10) AS g(n)
WHERE a IN (1, 2) AND a BETWEEN 1 AND 2 AND a LIKE 'x' AND a ILIKE 'x' AND a IS NOT NULL
  AND EXISTS (SELECT 1) AND a = ANY (ARRAY[1, 2]) AND NOT (a > 1 OR a < 0)
"""


def _collect() -> tuple[frozenset[str], frozenset[str]]:
    classes: set[str] = set()
    names: set[str] = set()
    for node in sqlglot.parse_one(_ALLOWED_SQL, read="postgres").find_all(exp.Func):
        if isinstance(node, exp.Anonymous):
            names.add(node.name.lower())
        else:
            classes.add(type(node).__name__)
    return frozenset(classes), frozenset(names)


ALLOWED_CLASSES, ALLOWED_NAMES = _collect()


def function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    return node.sql_name().lower() if node.sql_names() else type(node).__name__.lower()


def is_allowed(node: exp.Func) -> bool:
    if isinstance(node, exp.Anonymous):
        return node.name.lower() in ALLOWED_NAMES
    return type(node).__name__ in ALLOWED_CLASSES
