"""Local transformation catalog and deterministic reuse decisions."""

from __future__ import annotations

from pathlib import Path

from aiplot.persistence.models import ModelDecision, TransformationSpec


class TransformationCatalog:
    def __init__(self, project_dir: Path) -> None:
        self.root = project_dir / ".aiplot-transformations"

    def list_specs(self) -> list[TransformationSpec]:
        if not self.root.is_dir():
            return []
        specs: list[TransformationSpec] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                specs.append(TransformationSpec.model_validate_json(path.read_text()))
            except (ValueError, OSError):
                continue
        return specs

    def save(self, spec: TransformationSpec) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{spec.model_name}.json"
        path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def decide(
        self, proposed: TransformationSpec
    ) -> tuple[ModelDecision, str, TransformationSpec | None]:
        proposed_sources = {(item.source_name, item.relation_name) for item in proposed.sources}
        for existing in self.list_specs():
            existing_sources = {(item.source_name, item.relation_name) for item in existing.sources}
            if existing_sources != proposed_sources or existing.grain != proposed.grain:
                continue
            existing_metrics = {item.name for item in existing.metrics}
            proposed_metrics = {item.name for item in proposed.metrics}
            existing_dimensions = set(existing.dimensions)
            proposed_dimensions = set(proposed.dimensions)
            if proposed_metrics <= existing_metrics and proposed_dimensions <= existing_dimensions:
                return (
                    "REUSE_EXISTING",
                    "An existing mart covers the requested grain, dimensions, and metrics.",
                    existing,
                )
            return (
                "EXTEND_EXISTING",
                "An existing mart has the same sources and grain but needs additional outputs.",
                existing,
            )
        return "CREATE_NEW", "No compatible analytics mart exists.", None
