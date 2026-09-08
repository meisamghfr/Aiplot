"""Deterministic result profiling, validation, and visualization planning."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from aiplot.visualization.models import (
    ChartSpec,
    ColumnKind,
    ColumnMetadata,
    DashboardSpec,
    KPIWidget,
    QueryResult,
    VisualizationPlan,
)

_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DATE_NAME_RE = re.compile(r"(^|_)(date|time|month|year|week|day|quarter)($|_)", re.I)
_PART_TO_WHOLE_RE = re.compile(
    r"\b(share|percent|percentage|composition|distribution|breakdown)\b", re.I
)


class VisualizationValidationError(ValueError):
    """Raised when a visualization spec references unavailable result fields."""


def validate_chart_spec(spec: ChartSpec, columns: list[str]) -> ChartSpec:
    missing = sorted(spec.referenced_columns() - set(columns))
    if missing:
        raise VisualizationValidationError(
            f"Chart references columns not returned by SQL: {', '.join(missing)}"
        )
    if spec.type in {"line", "bar", "scatter"} and (spec.x is None or spec.y is None):
        raise VisualizationValidationError(f"{spec.type.title()} charts require x and y fields.")
    if spec.type == "pie" and (spec.x is None or not isinstance(spec.y, str)):
        raise VisualizationValidationError(
            "Pie charts require one label field and one value field."
        )
    if spec.type in {"histogram", "box"} and spec.x is None and spec.y is None:
        raise VisualizationValidationError(f"{spec.type.title()} charts require a numeric field.")
    return spec


def validate_dashboard_spec(spec: DashboardSpec, columns: list[str]) -> DashboardSpec:
    available = set(columns)
    missing_kpis = sorted({kpi.column for kpi in spec.kpis} - available)
    if missing_kpis:
        raise VisualizationValidationError(
            f"Dashboard KPI references columns not returned by SQL: {', '.join(missing_kpis)}"
        )
    for chart in spec.charts:
        validate_chart_spec(chart, columns)
    return spec


def infer_columns(result: QueryResult) -> list[ColumnMetadata]:
    metadata: list[ColumnMetadata] = []
    for index, name in enumerate(result.columns):
        raw_values = [row[index] for row in result.rows]
        values = [value for value in raw_values if value is not None]
        kind = _infer_kind(name, values)
        metadata.append(
            ColumnMetadata(name=name, kind=kind, nullable=len(values) != len(raw_values))
        )
    return metadata


def plan_visualization(result: QueryResult, max_plot_rows: int = 5_000) -> VisualizationPlan:
    metadata = infer_columns(result)
    chart = _choose_chart(result, metadata)
    if chart is not None:
        validate_chart_spec(chart, result.columns)
    dashboard = _build_dashboard(result, metadata, chart)
    validate_dashboard_spec(dashboard, result.columns)
    plotting_limited = len(result.rows) > max_plot_rows
    plotted_rows = _sample_rows(result.rows, max_plot_rows)
    message = _result_message(result, chart, plotting_limited, max_plot_rows)
    return VisualizationPlan(
        columns=metadata,
        chart=chart,
        dashboard=dashboard,
        message=message,
        plotted_rows=plotted_rows,
        plotting_limited=plotting_limited,
    )


def _infer_kind(name: str, values: list[Any]) -> ColumnKind:
    if not values:
        return "unknown"
    if _DATE_NAME_RE.search(name) and _mostly(values, _is_date):
        return "datetime"
    if _mostly(values, _is_numeric):
        return "numeric"
    if _mostly(values, _is_date):
        return "datetime"
    return "categorical"


def _mostly(values: list[Any], predicate: Any) -> bool:
    return sum(bool(predicate(value)) for value in values) / len(values) >= 0.8


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not isinstance(value, float) or math.isfinite(value)
    return isinstance(value, str) and bool(_NUMERIC_RE.fullmatch(value.strip()))


def _is_date(value: Any) -> bool:
    if isinstance(value, (datetime,)):
        return True
    if not isinstance(value, str) or len(value.strip()) < 4:
        return False
    candidate = value.strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return bool(re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", candidate))


def _choose_chart(result: QueryResult, metadata: list[ColumnMetadata]) -> ChartSpec | None:
    if not result.rows or not result.columns or len(result.rows) == 1:
        return None
    by_kind = {item.name: item.kind for item in metadata}
    dates = [name for name in result.columns if by_kind[name] == "datetime"]
    numerics = [name for name in result.columns if by_kind[name] == "numeric"]
    categories = [name for name in result.columns if by_kind[name] == "categorical"]

    if dates and numerics:
        return ChartSpec(
            type="line",
            x=dates[0],
            y=numerics[0],
            color=categories[0] if categories else None,
            title=_title(result.question),
        )
    if categories and numerics:
        category = categories[0]
        numeric = numerics[0]
        distinct = len({row[result.columns.index(category)] for row in result.rows})
        values = [row[result.columns.index(numeric)] for row in result.rows]
        pie_ready = (
            distinct <= 6
            and _PART_TO_WHOLE_RE.search(result.question) is not None
            and all(value is None or float(value) >= 0 for value in values if _is_numeric(value))
        )
        return ChartSpec(
            type="pie" if pie_ready else "bar",
            x=category,
            y=numeric,
            title=_title(result.question),
        )
    if len(numerics) >= 2:
        return ChartSpec(
            type="scatter", x=numerics[0], y=numerics[1], title=_title(result.question)
        )
    if len(numerics) == 1 and len(result.rows) >= 10:
        return ChartSpec(type="histogram", x=numerics[0], title=_title(result.question))
    return None


def _build_dashboard(
    result: QueryResult,
    metadata: list[ColumnMetadata],
    primary_chart: ChartSpec | None,
) -> DashboardSpec:
    numeric = [column.name for column in metadata if column.kind == "numeric"]
    categorical = [column.name for column in metadata if column.kind == "categorical"]
    datetime_columns = [column.name for column in metadata if column.kind == "datetime"]
    kpis: list[KPIWidget] = []
    if len(result.rows) == 1:
        for column in numeric[:4]:
            kpis.append(KPIWidget(label=_label(column), column=column, operation="latest"))
    elif numeric:
        kpis.append(
            KPIWidget(label=f"Total {_label(numeric[0])}", column=numeric[0], operation="sum")
        )
        if categorical:
            kpis.append(
                KPIWidget(
                    label=f"Latest {_label(categorical[0])}",
                    column=categorical[0],
                    operation="latest",
                )
            )
        kpis.append(KPIWidget(label="Rows", column=result.columns[0], operation="count"))

    charts = [primary_chart] if primary_chart is not None else []
    if datetime_columns and categorical and numeric and len(charts) < 3:
        charts.append(
            ChartSpec(
                type="bar",
                x=categorical[0],
                y=numeric[0],
                aggregation="sum",
                title=f"{_label(numeric[0])} by {_label(categorical[0])}",
            )
        )
    return DashboardSpec(
        title=_title(result.question), kpis=kpis[:4], charts=charts[:3], show_table=True
    )


def _sample_rows(rows: list[list[Any]], limit: int) -> list[list[Any]]:
    if len(rows) <= limit:
        return rows
    if limit <= 1:
        return rows[:limit]
    return [rows[round(index * (len(rows) - 1) / (limit - 1))] for index in range(limit)]


def _result_message(
    result: QueryResult, chart: ChartSpec | None, plotting_limited: bool, max_plot_rows: int
) -> str | None:
    if not result.rows:
        return "The query returned no rows. The accepted SQL is available below."
    if len(result.rows) == 1:
        return "A single-row result is summarized as KPI values and remains available as a table."
    if chart is None:
        return (
            "This result is best represented as a table. "
            "You can still choose compatible fields manually."
        )
    if plotting_limited:
        return (
            f"The chart uses an evenly spaced sample of {max_plot_rows:,} rows; "
            "the SQL was not changed."
        )
    return None


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _title(question: str) -> str:
    return question.strip().rstrip(".?!")[:120] or "Analysis"
