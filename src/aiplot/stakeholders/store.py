"""File-backed stakeholder thread store with atomic updates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from aiplot.stakeholders.models import (
    CreateStakeholderThread,
    PendingClarification,
    StakeholderMessage,
    StakeholderThread,
)


class StakeholderStore:
    def __init__(self, state_dir: Path) -> None:
        self.root = state_dir.resolve() / "stakeholder_threads"

    def create(self, request: CreateStakeholderThread) -> StakeholderThread:
        thread = StakeholderThread(id=uuid4().hex, **request.model_dump())
        self.save(thread)
        return thread

    def list(self) -> list[StakeholderThread]:
        if not self.root.is_dir():
            return []
        threads = []
        for path in self.root.glob("*.json"):
            try:
                threads.append(StakeholderThread.model_validate_json(path.read_text()))
            except (OSError, ValueError):
                continue
        return sorted(threads, key=lambda item: item.updated_at, reverse=True)

    def get(self, thread_id: str) -> StakeholderThread:
        path = self._path(thread_id)
        if not path.is_file():
            raise ValueError("Stakeholder thread was not found.")
        return StakeholderThread.model_validate_json(path.read_text(encoding="utf-8"))

    def append(
        self,
        thread_id: str,
        role: Literal["user", "assistant"],
        content: str,
        analysis: dict[str, object] | None = None,
    ) -> StakeholderMessage:
        thread = self.get(thread_id)
        message = StakeholderMessage(
            id=uuid4().hex,
            role=role,
            content=content,
            analysis=analysis,
        )
        updated = thread.model_copy(
            update={
                "messages": [*thread.messages, message][-100:],
                "updated_at": datetime.now(UTC),
            }
        )
        self.save(updated)
        return message

    def save(self, thread: StakeholderThread) -> None:
        path = self._path(thread.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(thread.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def set_pending(
        self, thread_id: str, pending: PendingClarification | None
    ) -> StakeholderThread:
        thread = self.get(thread_id)
        updated = thread.model_copy(
            update={"pending_clarification": pending, "updated_at": datetime.now(UTC)}
        )
        self.save(updated)
        return updated

    def _path(self, thread_id: str) -> Path:
        if len(thread_id) != 32 or any(char not in "0123456789abcdef" for char in thread_id):
            raise ValueError("Invalid stakeholder thread id.")
        return self.root / f"{thread_id}.json"
