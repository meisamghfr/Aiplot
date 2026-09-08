from __future__ import annotations

import pytest
from pydantic import ValidationError

from aiplot.visualization.models import ChartSpec, DashboardSpec, KPIWidget, QueryResult
from aiplot.visualization.planner import (
    VisualizationValidationError,
    plan_visualization,
    validate_chart_spec,
    validate_dashboard_spec,
)


def result(
    columns: list[str], rows: list[list[object]], question: str = "Analyze this"
) -> QueryResult:
    return QueryResult(
        question=question,
        accepted_sql="SELECT 1",
        status="ACCEPTED",
        columns=columns,
        rows=rows,
        row_count=len(rows),
    )


def test_time_and_numeric_becomes_line() -> None:
    plan = plan_visualization(
        result(["month", "revenue"], [["2025-01", "10.2"], ["2025-02", "12.4"]])
    )

    assert plan.chart is not None
    assert plan.chart.type == "line"
    assert (plan.chart.x, plan.chart.y) == ("month", "revenue")


def test_category_and_numeric_becomes_bar() -> None:
    plan = plan_visualization(result(["segment", "revenue"], [["SME", 10], ["Enterprise", 20]]))

    assert plan.chart is not None
    assert plan.chart.type == "bar"


def test_two_numeric_columns_becomes_scatter() -> None:
    plan = plan_visualization(result(["price", "quantity"], [[10, 2], [20, 5]]))

    assert plan.chart is not None
    assert plan.chart.type == "scatter"


def test_invalid_chart_column_is_rejected() -> None:
    with pytest.raises(VisualizationValidationError, match="profit"):
        validate_chart_spec(ChartSpec(type="line", x="month", y="profit"), ["month", "revenue"])


def test_empty_result_uses_table_message() -> None:
    plan = plan_visualization(result(["segment", "revenue"], []))

    assert plan.chart is None
    assert "no rows" in (plan.message or "").lower()


def test_single_scalar_result_becomes_kpi() -> None:
    plan = plan_visualization(result(["customer_count"], [[42]], "Count customers"))

    assert plan.chart is None
    assert plan.dashboard.kpis == [
        KPIWidget(label="Customer Count", column="customer_count", operation="latest")
    ]


def test_dashboard_kpi_is_restricted_to_result_columns() -> None:
    dashboard = DashboardSpec(
        title="Revenue", kpis=[KPIWidget(label="Profit", column="profit", operation="sum")]
    )

    with pytest.raises(VisualizationValidationError, match="profit"):
        validate_dashboard_spec(dashboard, ["revenue"])


def test_result_rows_must_match_columns() -> None:
    with pytest.raises(ValidationError, match="match the result columns"):
        result(["one", "two"], [[1]])


def test_large_result_is_evenly_sampled_without_changing_source_rows() -> None:
    source = result(["category", "value"], [[f"row-{index}", index] for index in range(20)])
    plan = plan_visualization(source, max_plot_rows=5)

    assert plan.plotting_limited is True
    assert len(plan.plotted_rows) == 5
    assert plan.plotted_rows[0] == ["row-0", 0]
    assert plan.plotted_rows[-1] == ["row-19", 19]
    assert len(source.rows) == 20
