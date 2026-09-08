"""Deterministic ad-hoc versus persistent analytics classification."""

from __future__ import annotations

import re

from aiplot.persistence.models import PersistenceMode

_PERSISTENT = re.compile(
    r"\b(refresh(?:es|ed)?\s+(?:daily|weekly|monthly)|daily\s+refresh|weekly\s+monitoring|"
    r"ongoing|production(?:-ready)?|scheduled|recurring|keep\s+(?:this|it)\s+updated)\b",
    re.I,
)


def classify_persistence(request: str) -> PersistenceMode:
    return "persistent" if _PERSISTENT.search(request) else "ad_hoc"
