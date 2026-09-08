"""Strict capability, plan, and widget-result contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from aiplot.visualization.models import ChartSpec, ColumnMetadata, StrictModel

WidgetKind = Literal["kpi", "chart", "table"]
WidgetVisualization = Literal["kpi", "line", "bar", "scatter", "pie", "table"]
Comparison = Literal["none", "previous_period", "previous_month", "previous_year", "year_over_year"]


class CapabilityField(StrictModel):
    name: str
    table: str
    data_type: str
    semantic_type: str | None = None
    description: str | None = None


class BusinessMetric(StrictModel):
    name: str
    description: str
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    columns: list[str] = Field(default_factory=list, max_length=20)
    source: Literal["glossary", "profile", "schema"]


class AnalyticsCapabilities(StrictModel):
    db_id: str
    dialect: Literal["sqlite", "postgres"]
    tables: list[str] = Field(default_factory=list, max_length=100)
    business_entities: list[str] = Field(default_factory=list, max_length=100)
    measures: list[CapabilityField] = Field(default_factory=list, max_length=200)
    dimensions: list[CapabilityField] = Field(default_factory=list, max_length=200)
    time_columns: list[CapabilityField] = Field(default_factory=list, max_length=100)
    available_kpis: list[BusinessMetric] = Field(default_factory=list, max_length=100)


class DashboardWidget(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: WidgetKind
    title: str = Field(min_length=1, max_length=120)
    metric: str | None = Field(default=None, max_length=120)
    dimensions: list[str] = Field(default_factory=list, max_length=4)
    comparison: Comparison = "none"
    visualization: WidgetVisualization
    analytical_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def kind_matches_visualization(self) -> DashboardWidget:
        if self.kind == "kpi" and self.visualization != "kpi":
            raise ValueError("KPI widgets must use KPI visualization.")
        if self.kind == "table" and self.visualization != "table":
            raise ValueError("Table widgets must use table visualization.")
        if self.kind == "chart" and self.visualization in {"kpi", "table"}:
            raise ValueError("Chart widgets require a chart visualization.")
        if self.kind != "table" and self.metric is None:
            raise ValueError("Data-backed KPI and chart widgets require a metric.")
        return self


class AgentDashboardSpec(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    widgets: list[DashboardWidget] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def enforce_widget_limits(self) -> AgentDashboardSpec:
        if len({widget.id for widget in self.widgets}) != len(self.widgets):
            raise ValueError("Dashboard widget ids must be unique.")
        if sum(widget.kind == "kpi" for widget in self.widgets) > 6:
            raise ValueError("Dashboard plans support at most 6 KPI widgets.")
        if sum(widget.kind == "chart" for widget in self.widgets) > 6:
            raise ValueError("Dashboard plans support at most 6 chart widgets.")
        return self


class DashboardPlan(StrictModel):
    spec: AgentDashboardSpec
    unsupported_requirements: list[str] = Field(default_factory=list, max_length=20)


class WidgetDataset(StrictModel):
    widget_id: str
    analytical_question: str
    accepted_sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    status: Literal["accepted", "failed", "budget_exceeded"]
    error: str | None = None
    column_metadata: list[ColumnMetadata] = Field(default_factory=list)
    chart: ChartSpec | None = None
    visualization_note: str | None = None


class DashboardExecution(StrictModel):
    plan: DashboardPlan
    datasets: list[WidgetDataset]
    unique_query_count: int = Field(ge=0, le=8)
    query_budget: int = 8
    max_concurrency: int = 3
