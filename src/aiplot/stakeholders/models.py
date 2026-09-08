"""Strict stakeholder conversation contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from aiplot.visualization.models import StrictModel


class StakeholderMessage(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    analysis: dict[str, Any] | None = None


class StakeholderThread(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    stakeholder_name: str = Field(min_length=1, max_length=120)
    stakeholder_role: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    db_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    provider: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=200)
    context_mode: Literal["retrieval", "model1"] = "retrieval"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    messages: list[StakeholderMessage] = Field(default_factory=list, max_length=100)


class CreateStakeholderThread(StrictModel):
    stakeholder_name: str = Field(min_length=1, max_length=120)
    stakeholder_role: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    db_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    provider: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=200)
    context_mode: Literal["retrieval", "model1"] = "retrieval"


class StakeholderChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4_000)


class StakeholderChatResponse(StrictModel):
    thread: StakeholderThread
    assistant_message: StakeholderMessage
    analysis: dict[str, Any]
