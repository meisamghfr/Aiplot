"""Deterministic request intent routing."""

from __future__ import annotations

import re
from typing import Literal

RequestIntent = Literal["single_analysis", "dashboard"]

_DASHBOARD = re.compile(
    r"\b(dashboard|scorecard|executive overview|performance monitor(?:ing)?)\b", re.I
)


def route_request(message: str) -> RequestIntent:
    """Route obvious dashboard requests without spending a model call."""
    return "dashboard" if _DASHBOARD.search(message) else "single_analysis"
