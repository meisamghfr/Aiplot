"""Validated public contracts for charts and small dashboards."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChartType = Literal["line", "bar", "scatter", "pie", "histogram", "box"]
ColumnKind = Literal["datetime", "numeric", "categorical", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnMetadata(StrictModel):
    name: str
    kind: ColumnKind
    nullable: bool = False


class ChartSpec(StrictModel):
    type: ChartType
    x: str | None = None
    y: str | list[str] | None = None
    color: str | None = None
    aggregation: Literal["sum", "avg", "min", "max", "count"] | None = None
    title: str | None = None

    def referenced_columns(self) -> set[str]:
        y_columns = [self.y] if isinstance(self.y, str) else (self.y or [])
        return {column for column in [self.x, self.color, *y_columns] if column is not None}


class KPIWidget(StrictModel):
    label: str
    column: str
    operation: Literal["sum", "avg", "min", "max", "count", "latest"]


class DashboardSpec(StrictModel):
    title: str
    kpis: list[KPIWidget] = Field(default_factory=list, max_length=4)
    charts: list[ChartSpec] = Field(default_factory=list, max_length=3)
    show_table: bool = True


class QueryResult(StrictModel):
    question: str
    accepted_sql: str
    status: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int = Field(ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def rows_match_columns(self) -> QueryResult:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Result column names must be unique.")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every result row must match the result columns.")
        return self


class VisualizationPlan(StrictModel):
    columns: list[ColumnMetadata]
    chart: ChartSpec | None = None
    dashboard: DashboardSpec
    message: str | None = None
    plotted_rows: list[list[Any]]
    plotting_limited: bool = False
