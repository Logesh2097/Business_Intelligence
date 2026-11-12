"""The LangGraph workflows: the shared state, the graph that connects the agents, the
routes and the limits.

Kept apart from `app.agents` so that *what an agent does* and *how agents are wired
together* change independently. Empty in the setup phase. Design: docs/design/agents.md §3-4.
"""
