"""The domain agents by name. A new agent is one module and one line here."""

from __future__ import annotations

from app.agents import customer_agent, inventory_agent, sales_agent
from app.agents.domain import DomainAgent

AGENTS: dict[str, DomainAgent] = {
    agent.name: agent for agent in (sales_agent.AGENT, inventory_agent.AGENT, customer_agent.AGENT)
}
