"""Turn the chosen tables and metrics into the text the model reads."""

from __future__ import annotations

from app.semantic.model import Metric, Table


def schema_text(tables: tuple[Table, ...]) -> str:
    blocks = []
    for table in tables:
        lines = [f"TABLE {table.table}: {table.purpose}"]
        for column in table.columns:
            detail = column.meaning
            if column.values:
                detail = (detail + ". " if detail else "") + "One of: " + ", ".join(column.values)
            lines.append(f"  {column.name} ({column.type})" + (f": {detail}" if detail else ""))
        shown = {t.table for t in tables}
        for join in table.joins:
            if join.table in shown:
                lines.append(f"  JOIN {table.table}.{join.column} = {join.to}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def metrics_text(metrics: tuple[Metric, ...]) -> str:
    if not metrics:
        return "(no defined metric matched; use the columns as described)"
    lines = []
    for metric in metrics:
        line = f"- {metric.metric} ({metric.unit}): {metric.sql}"
        if metric.where:
            line += f"  WHERE {metric.where}"
        if metric.note:
            line += f"  NOTE: {metric.note}"
        lines.append(line)
    return "\n".join(lines)
