"""The tools the agents call to reach data (run a query, search documents, look up a
metric). Plain async functions; the only doorway from an agent to a database.

No tool takes a company, user or outlet argument: the caller comes from the session.
Empty in the setup phase. Design: docs/design/mcp.md §2, docs/design/nl2sql.md.
"""
