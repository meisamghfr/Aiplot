"""Schema-grounded clarification without querying warehouse rows."""

from __future__ import annotations

import re
from collections.abc import Sequence

from aiplot.dashboard.models import AnalyticsCapabilities, CapabilityField
from aiplot.stakeholders.models import PendingClarification
from aiplot.stakeholders.tools import StakeholderTool

_VISUAL_REQUEST = re.compile(r"\b(plot|chart|dashboard|visuali[sz]e|graph|trend)\b", re.I)
_CLIENTS = re.compile(r"\b(client|clients|customer|customers|user|users)\b", re.I)
_TIME_RANGE = re.compile(r"\b(last|past|between|from|since|until|through|20\d{2}|q[1-4])\b", re.I)
_GRAIN = re.compile(r"\b(daily|weekly|monthly|quarterly|yearly|annual)\b", re.I)


def clarification_for(
    message: str, capabilities: AnalyticsCapabilities, tool: StakeholderTool
) -> PendingClarification | None:
    """Ask only about choices supported by advertised metadata."""
    if not _VISUAL_REQUEST.search(message):
        return None
    dimensions = _names(capabilities.dimensions, exclude={"order_id", "customer_id"})
    time_columns = _names(capabilities.time_columns)
    entity_columns = _names([item for item in capabilities.dimensions if item.name.endswith("_id")])
    questions: list[str] = []
    notes: list[str] = []
    if _CLIENTS.search(message):
        customer_key = next((item for item in entity_columns if "customer" in item), None)
        if customer_key:
            questions.append(f"Should 'clients' mean distinct `{customer_key}` values?")
        else:
            available = ", ".join(capabilities.business_entities) or "no named entities"
            questions.append(
                "The data has no client field. Which available business entity should be counted: "
                f"{available}?"
            )
    if time_columns and not _TIME_RANGE.search(message):
        questions.append(
            "What date window should be used, and which available date field applies: "
            f"{', '.join(f'`{item}`' for item in time_columns)}?"
        )
    if time_columns and not _GRAIN.search(message):
        questions.append("Should the time grain be daily, weekly, or monthly?")
    mentioned_breakdown = any(
        re.search(rf"\bby\s+{re.escape(item.replace('_', ' '))}\b", message, re.I)
        for item in dimensions
    )
    if dimensions and not mentioned_breakdown:
        questions.append(
            "Choose an optional breakdown supported by the data: "
            f"{', '.join(f'`{item}`' for item in dimensions)}, or no breakdown."
        )
    notes.append(f"Available entities: {', '.join(capabilities.business_entities) or 'none'}.")
    notes.append(f"Available measures: {', '.join(_names(capabilities.measures)) or 'none'}.")
    if not questions:
        return None
    return PendingClarification(
        original_request=message,
        tool=tool,
        questions=questions,
        capability_context=" ".join(notes),
    )


def generated_question(pending: PendingClarification, answer: str) -> str:
    """Create the tool input from stakeholder intent plus explicit clarification."""
    return (
        f"{pending.original_request.rstrip('. ')}. "
        f"Use these stakeholder clarifications: {answer.rstrip('. ')}. "
        "Use only fields and entities available in the database capabilities."
    )


def clarification_message(pending: PendingClarification) -> str:
    lines = [
        "I can create that, but I need to define the analytical question first.",
        pending.capability_context,
    ]
    lines.extend(f"{index}. {question}" for index, question in enumerate(pending.questions, 1))
    lines.append("Reply once with your choices; I will then call Text-to-SQL as a tool.")
    return "\n".join(lines)


def _names(fields: Sequence[CapabilityField], exclude: set[str] | None = None) -> list[str]:
    excluded = exclude or set()
    return sorted({str(item.name) for item in fields if item.name not in excluded})
