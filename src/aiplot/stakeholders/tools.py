"""Deterministic tool selection for stakeholder conversations."""

from __future__ import annotations

import re
from typing import Literal

StakeholderTool = Literal["text_to_sql", "create_pipeline"]

_PIPELINE = re.compile(
    r"\b(create|build|deploy|schedule|persist|production|ongoing|refresh)\b.*"
    r"\b(pipeline|dashboard|mart|monitor(?:ing)?)\b|"
    r"\b(pipeline|dashboard|mart)\b.*\b(daily|weekly|production|ongoing|refresh)\b",
    re.I,
)


def select_tool(message: str) -> StakeholderTool:
    """Select from an allowlist; tool choice never accepts caller-authored SQL."""
    return "create_pipeline" if _PIPELINE.search(message) else "text_to_sql"


def pipeline_request(message: str) -> str:
    """Make implicit pipeline intent explicit for the existing dashboard planner."""
    if re.search(r"\bdashboard\b", message, re.I):
        return message
    return f"Build an ongoing production dashboard for this request: {message}"
