from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from aiplot.agents.dashboard_agent import DashboardAgent
from aiplot.agents.query_planner import QueryPlanner
from aiplot.dashboard.models import (
    AgentDashboardSpec,
    AnalyticsCapabilities,
    DashboardPlan,
    DashboardWidget,
)
from aiplot.dashboard.service import DashboardService
from aiplot.router import route_request
from aiplot.text2sql_client import TextToSQLError


def capabilities() -> AnalyticsCapabilities:
    return AnalyticsCapabilities.model_validate(
        {
            "db_id": "business",
            "dialect": "postgres",
            "tables": ["sales", "customers"],
            "business_entities": ["customer", "sale"],
            "measures": [],
            "dimensions": [{"name": "segment", "table": "customers", "data_type": "text"}],
            "time_columns": [{"name": "month", "table": "sales", "data_type": "date"}],
            "available_kpis": [
                {
                    "name": "revenue",
                    "description": "Recognized sales revenue",
                    "synonyms": ["sales revenue"],
                    "columns": ["sales.revenue"],
                    "source": "glossary",
                },
                {
                    "name": "active customers",
                    "description": "Customers active in a period",
                    "synonyms": ["active users"],
                    "columns": ["customers.customer_id"],
                    "source": "glossary",
                },
            ],
        }
    )


class FakeClient:
    def __init__(self, fail_on: str | None = None, delay: float = 0) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.delay = delay
        self.active = 0
        self.peak_active = 0

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload["message"])
        self.calls.append(question)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.active -= 1
        if self.fail_on and self.fail_on in question:
            raise TextToSQLError("Metric unavailable")
        if "by month" in question.casefold():
            columns = ["month", "revenue"]
            rows = [["2026-01", 100], ["2026-02", 120]]
        elif "by segment" in question.casefold():
            columns = ["segment", "revenue"]
            rows = [["SME", 100], ["Enterprise", 120]]
        else:
            columns = ["period", "value"]
            rows = [["2026-01", 100], ["2026-02", 120]]
        return {
            "message": "accepted",
            "generation": {
                "accepted": True,
                "execution_status": "ACCEPTED",
                "sql": "SELECT verified_result",
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            },
        }


def test_obvious_chart_request_routes_to_single_analysis() -> None:
    assert route_request("Plot revenue by customer segment") == "single_analysis"


def test_obvious_dashboard_request_routes_to_dashboard() -> None:
    assert route_request("Create a dashboard for monitoring sales") == "dashboard"


def test_dashboard_agent_output_validates() -> None:
    plan = DashboardAgent().plan("Build a business performance dashboard", capabilities())

    assert isinstance(plan.spec, AgentDashboardSpec)
    assert len(plan.spec.widgets) <= 10


def test_dashboard_contract_rejects_arbitrary_sql_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DashboardWidget.model_validate(
            {
                "id": "revenue",
                "kind": "kpi",
                "title": "Revenue",
                "metric": "revenue",
                "visualization": "kpi",
                "sql": "DROP TABLE sales",
            }
        )


def test_explicit_client_kpis_are_preserved() -> None:
    plan = DashboardAgent().plan(
        "Build a dashboard with revenue and active customers.", capabilities()
    )

    metrics = {widget.metric for widget in plan.spec.widgets}
    assert {"revenue", "active customers"} <= metrics


def test_explicit_client_chart_type_is_preserved() -> None:
    plan = DashboardAgent().plan(
        "Build a dashboard with revenue and active customers. Show monthly revenue as a bar chart.",
        capabilities(),
    )

    revenue_charts = [
        widget
        for widget in plan.spec.widgets
        if widget.kind == "chart" and widget.metric == "revenue"
    ]
    assert revenue_charts[0].visualization == "bar"


def test_query_planner_generates_questions_not_sql() -> None:
    widget = DashboardWidget(
        id="revenue_trend",
        kind="chart",
        title="Revenue Trend",
        metric="revenue",
        dimensions=["month"],
        visualization="line",
    )

    planned = QueryPlanner().with_question(widget)

    assert planned.analytical_question == "Show revenue by month."
    assert "select " not in planned.analytical_question.casefold()


class FixedAgent:
    def __init__(self, widgets: list[DashboardWidget]) -> None:
        self.widgets = widgets

    def plan(self, request: str, capabilities: AnalyticsCapabilities) -> DashboardPlan:
        return DashboardPlan(
            spec=AgentDashboardSpec(title="Test", objective="Test dashboard", widgets=self.widgets)
        )


def build_service(client: FakeClient, widgets: list[DashboardWidget]) -> DashboardService:
    service = DashboardService(client)  # type: ignore[arg-type]
    service.agent = FixedAgent(widgets)  # type: ignore[assignment]
    return service


def execute(service: DashboardService) -> Any:
    return asyncio.run(
        service.build(
            "Build dashboard",
            capabilities(),
            db_id="business",
            provider="ollama",
            model="local",
            context_mode="retrieval",
        )
    )


def test_duplicate_questions_are_deduplicated() -> None:
    widgets = [
        DashboardWidget(
            id=f"revenue_{index}",
            kind="kpi",
            title=f"Revenue {index}",
            metric="revenue",
            visualization="kpi",
            analytical_question="Return current revenue.",
        )
        for index in range(2)
    ]
    client = FakeClient()
    result = execute(build_service(client, widgets))

    assert result.unique_query_count == 1
    assert len(client.calls) == 1
    assert len(result.datasets) == 2


def test_dashboard_query_budget_is_enforced() -> None:
    widgets = [
        DashboardWidget(
            id=f"metric_{index}",
            kind="kpi",
            title=f"Metric {index}",
            metric=f"metric {index}",
            visualization="kpi",
            analytical_question=f"Return metric {index}.",
        )
        for index in range(5)
    ]
    widgets.extend(
        DashboardWidget(
            id=f"chart_{index}",
            kind="chart",
            title=f"Chart {index}",
            metric=f"chart metric {index}",
            dimensions=["month"],
            visualization="line",
            analytical_question=f"Show chart metric {index} by month.",
        )
        for index in range(4)
    )
    client = FakeClient()
    result = execute(build_service(client, widgets))

    assert result.unique_query_count == 8
    assert len(client.calls) == 8
    assert result.datasets[-1].status == "budget_exceeded"


def test_dashboard_concurrency_is_limited_to_three() -> None:
    widgets = [
        DashboardWidget(
            id=f"metric_{index}",
            kind="kpi",
            title=f"Metric {index}",
            metric=f"metric {index}",
            visualization="kpi",
            analytical_question=f"Return metric {index}.",
        )
        for index in range(6)
    ]
    client = FakeClient(delay=0.01)

    execute(build_service(client, widgets))

    assert client.peak_active == 3


def test_one_widget_failure_does_not_fail_dashboard() -> None:
    widgets = [
        DashboardWidget(
            id="revenue",
            kind="kpi",
            title="Revenue",
            metric="revenue",
            visualization="kpi",
        ),
        DashboardWidget(
            id="customers",
            kind="kpi",
            title="Customers",
            metric="active customers",
            visualization="kpi",
        ),
    ]
    result = execute(build_service(FakeClient(fail_on="customers"), widgets))

    assert [dataset.status for dataset in result.datasets] == ["accepted", "failed"]


def test_incompatible_line_chart_falls_back_safely() -> None:
    widget = DashboardWidget(
        id="revenue_trend",
        kind="chart",
        title="Revenue Trend",
        metric="revenue",
        dimensions=["segment"],
        visualization="line",
        analytical_question="Show revenue by segment.",
    )
    result = execute(build_service(FakeClient(), [widget]))

    dataset = result.datasets[0]
    assert dataset.status == "accepted"
    assert dataset.chart is not None
    assert dataset.chart.type != "line"
    assert "incompatible" in (dataset.visualization_note or "")


def test_unsupported_kpi_is_not_fabricated() -> None:
    plan = DashboardAgent().plan(
        "Build a dashboard with revenue, churn, and active customers.", capabilities()
    )

    assert "churn" not in {widget.metric for widget in plan.spec.widgets}
    assert "churn" in plan.unsupported_requirements
