"""Strict persistent analytics contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from aiplot.dashboard.models import AgentDashboardSpec
from aiplot.visualization.models import StrictModel

PersistenceMode = Literal["ad_hoc", "persistent"]
ModelDecision = Literal["REUSE_EXISTING", "EXTEND_EXISTING", "CREATE_NEW"]
IncrementalStrategy = Literal["append", "merge", "delete_insert", "full_refresh"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]


class TransformationSource(StrictModel):
    source_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    relation_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class TransformationMetric(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    source_column: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    aggregation: Literal["sum", "avg", "min", "max", "count", "count_distinct"]


class TransformationTest(StrictModel):
    kind: Literal[
        "not_null",
        "unique_combination",
        "accepted_range",
        "relationships",
        "reconciliation",
    ]
    columns: list[Identifier] = Field(default_factory=list, max_length=20)
    minimum: float | None = None
    maximum: float | None = None
    tolerance: float | None = Field(default=None, ge=0, le=1)


class TransformationSpec(StrictModel):
    model_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    sources: list[TransformationSource] = Field(min_length=1, max_length=8)
    grain: list[Identifier] = Field(default_factory=list, max_length=20)
    dimensions: list[Identifier] = Field(default_factory=list, max_length=40)
    metrics: list[TransformationMetric] = Field(min_length=1, max_length=40)
    materialization: Literal["incremental", "table"]
    incremental_strategy: IncrementalStrategy
    unique_key: list[Identifier] = Field(default_factory=list, max_length=20)
    incremental_column: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    lookback_days: int = Field(default=0, ge=0, le=365)
    purpose: Literal["standard", "cohort_retention"] = "standard"
    late_arriving_policy: Literal[
        "not_applicable", "lookback_window", "recompute_affected_cohorts"
    ] = "not_applicable"
    tests: list[TransformationTest] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_incremental_contract(self) -> TransformationSpec:
        fields = {metric.name for metric in self.metrics} | set(self.dimensions)
        if not set(self.grain) <= fields:
            raise ValueError("Transformation grain must reference dimensions or metric outputs.")
        if self.materialization == "incremental":
            if self.incremental_strategy == "full_refresh":
                raise ValueError("Incremental models cannot use full_refresh strategy.")
            if not self.incremental_column or not self.unique_key:
                raise ValueError("Incremental models require an incremental column and unique key.")
            if self.incremental_strategy == "append" and (
                self.lookback_days != 0 or self.late_arriving_policy != "not_applicable"
            ):
                raise ValueError(
                    "Append is allowed only for immutable streams without "
                    "late-arriving reprocessing."
                )
        elif self.incremental_strategy != "full_refresh":
            raise ValueError("Table models must use full_refresh strategy.")
        if self.purpose == "cohort_retention":
            if self.incremental_strategy not in {"merge", "delete_insert"}:
                raise ValueError("Cohort retention models must reprocess affected keys.")
            if self.late_arriving_policy != "recompute_affected_cohorts":
                raise ValueError(
                    "Cohort retention models must recompute affected cohorts for late arrivals."
                )
            if self.lookback_days < 1:
                raise ValueError("Cohort retention models require a positive lookback window.")
        return self


class TransformationAction(StrictModel):
    decision: ModelDecision
    target_model: str
    reason: str
    spec: TransformationSpec


class PersistencePlan(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    mode: PersistenceMode
    dashboard: AgentDashboardSpec
    transformations: list[TransformationAction] = Field(default_factory=list, max_length=8)
    status: Literal["pending_approval", "approved", "deployed", "failed"]
    requires_approval: bool = True
    approved_by: str | None = Field(default=None, max_length=200)
    error: str | None = None


class DbtCommandResult(StrictModel):
    command: list[str]
    return_code: int
    stdout: str = ""
    stderr: str = ""


class DbtDeploymentResult(StrictModel):
    status: Literal["succeeded", "failed", "not_run"]
    model_names: list[str] = Field(default_factory=list)
    commands: list[DbtCommandResult] = Field(default_factory=list)
    error: str | None = None


class PowerBIResult(StrictModel):
    status: Literal["published", "refreshed", "manifest_ready", "failed", "not_run"]
    semantic_model_id: str | None = None
    report_id: str | None = None
    artifact_path: str | None = None
    error: str | None = None


class PersistenceDeployment(StrictModel):
    plan: PersistencePlan
    dbt: DbtDeploymentResult
    power_bi: PowerBIResult


class ApprovalRequest(StrictModel):
    approved: bool
    approved_by: str = Field(min_length=1, max_length=200)


class RefreshResult(StrictModel):
    plan_id: str
    dbt: DbtDeploymentResult
    power_bi: PowerBIResult
    llm_calls: int = 0
