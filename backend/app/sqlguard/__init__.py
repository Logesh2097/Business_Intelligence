"""The SQL guard: text in, checked SQL out, or a reason for refusing it.

Pure: it imports nothing from the rest of the app and opens no connection, so its
rules can be tested in milliseconds. It is the first lock on AI-written SQL, not the
only one: behind it are the read-only database user, the column grants, row security
and the statement timeout (docs/design/nl2sql.md §3, docs/design/security.md §5).
"""

from app.sqlguard.aggregation import Passed, check_aggregation
from app.sqlguard.correctness import Facts, Reviewed, check_correctness
from app.sqlguard.guard import Catalog, Checked, Refused, check

__all__ = [
    "Catalog",
    "Checked",
    "Facts",
    "Passed",
    "Refused",
    "Reviewed",
    "check",
    "check_aggregation",
    "check_correctness",
]
