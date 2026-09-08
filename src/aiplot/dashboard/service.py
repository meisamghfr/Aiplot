"""One-shot bounded dashboard orchestration."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import uuid4

from aiplot.agents.dashboard_agent import DashboardAgent
from aiplot.agents.query_planner import QueryPlanner
from aiplot.dashboard.models import (
    AnalyticsCapabilities,
    DashboardExecution,
    DashboardPlan,
    DashboardWidget,
    WidgetDataset,
)
from aiplot.text2sql_client import TextToSQLClient, TextToSQLError
from aiplot.visualization.models import ChartSpec, QueryResult
from aiplot.visualization.planner import plan_visualization


class DashboardService:
    def __init__(
        self,
        client: TextToSQLClient,
        *,
        query_budget: int = 8,
        max_concurrency: int = 3,
        max_plot_rows: int = 300,
    ) -> None:
        self.client = client
        self.query_budget = min(query_budget, 8)
        self.max_concurrency = min(max_concurrency, 3)
        self.max_plot_rows = max_plot_rows
        self.agent = DashboardAgent()
        self.query_planner = QueryPlanner()

    async def build(
        self,
        request: str,
        capabilities: AnalyticsCapabilities,
        *,
        db_id: str,
        provider: str,
        model: str,
        context_mode: str,
        context_provider: str | None = None,
        context_model: str | None = None,
    ) -> DashboardExecution:
        initial = self.agent.plan(request, capabilities)
        spec = self.query_planner.add_questions(initial.spec)
        plan = DashboardPlan(spec=spec, unsupported_requirements=initial.unsupported_requirements)
        unique: dict[str, DashboardWidget] = {}
        normalized_by_widget: dict[str, str] = {}
        for widget in spec.widgets:
            assert widget.analytical_question is not None
            normalized = _normalize_question(widget.analytical_question)
            normalized_by_widget[widget.id] = normalized
            unique.setdefault(normalized, widget)

        executable = list(unique.items())[: self.query_budget]
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def execute(item: tuple[str, DashboardWidget]) -> tuple[str, WidgetDataset]:
            normalized, widget = item
            async with semaphore:
                dataset = await self._execute_widget(
                    widget,
                    db_id=db_id,
                    provider=provider,
                    model=model,
                    context_mode=context_mode,
                    context_provider=context_provider,
                    context_model=context_model,
                )
            return normalized, dataset

        completed = dict(await asyncio.gather(*(execute(item) for item in executable)))
        datasets: list[WidgetDataset] = []
        for widget in spec.widgets:
            normalized = normalized_by_widget[widget.id]
            existing = completed.get(normalized)
            if existing is None:
                datasets.append(
                    WidgetDataset(
                        widget_id=widget.id,
                        analytical_question=widget.analytical_question or "",
                        status="budget_exceeded",
                        error="Dashboard query budget was reached before this widget executed.",
                    )
                )
                continue
            datasets.append(
                existing.model_copy(
                    update={
                        "widget_id": widget.id,
                        "analytical_question": widget.analytical_question,
                    }
                )
            )
        return DashboardExecution(
            plan=plan,
            datasets=datasets,
            unique_query_count=len(executable),
            query_budget=self.query_budget,
            max_concurrency=self.max_concurrency,
        )

    async def _execute_widget(
        self,
        widget: DashboardWidget,
        *,
        db_id: str,
        provider: str,
        model: str,
        context_mode: str,
        context_provider: str | None,
        context_model: str | None,
    ) -> WidgetDataset:
        assert widget.analytical_question is not None
        payload: dict[str, Any] = {
            "session_id": f"aiplot-dashboard-{uuid4().hex}",
            "db_id": db_id,
            "message": widget.analytical_question,
            "provider": provider,
            "model": model,
            "context_mode": context_mode,
            "execute": True,
            "max_rows": 500,
        }
        if context_mode == "model1":
            payload["context_provider"] = context_provider or provider
            payload["context_model"] = context_model or model
        try:
            response = await self.client.chat(payload)
        except TextToSQLError as exc:
            return _failed_dataset(widget, str(exc))
        generation = response.get("generation")
        if not isinstance(generation, dict):
            return _failed_dataset(
                widget, str(response.get("message") or "No query result was returned.")
            )
        if not generation.get("accepted") or generation.get("execution_status") != "ACCEPTED":
            return _failed_dataset(
                widget,
                str(
                    response.get("message")
                    or generation.get("model_error")
                    or "The widget query was not accepted."
                ),
            )
        columns = generation.get("columns")
        raw_rows = generation.get("rows")
        sql = generation.get("sql")
        if (
            not isinstance(columns, list)
            or not isinstance(raw_rows, list)
            or not isinstance(sql, str)
        ):
            return _failed_dataset(widget, "Text-to-SQL returned malformed widget data.")
        column_names = [str(column) for column in columns]
        result = QueryResult(
            question=widget.analytical_question,
            accepted_sql=sql,
            status="ACCEPTED",
            columns=column_names,
            rows=raw_rows,
            row_count=int(generation.get("row_count", len(raw_rows))),
            truncated=bool(generation.get("truncated", False)),
        )
        visualization = plan_visualization(result, max_plot_rows=self.max_plot_rows)
        chart, note = _compatible_chart(widget, visualization.chart, visualization.columns)
        rows = [dict(zip(column_names, row, strict=True)) for row in raw_rows]
        return WidgetDataset(
            widget_id=widget.id,
            analytical_question=widget.analytical_question,
            accepted_sql=sql,
            columns=column_names,
            rows=rows,
            row_count=result.row_count,
            status="accepted",
            column_metadata=visualization.columns,
            chart=chart,
            visualization_note=note,
        )


def _compatible_chart(
    widget: DashboardWidget,
    automatic: ChartSpec | None,
    metadata: list[Any],
) -> tuple[ChartSpec | None, str | None]:
    if widget.kind != "chart":
        return None, None
    if automatic is None:
        return None, "The returned fields are not chart-compatible; showing a table instead."
    requested = widget.visualization
    kinds = {item.name: item.kind for item in metadata}
    compatible = False
    if requested == "bar":
        compatible = automatic.x is not None and automatic.y is not None
    elif requested == "line":
        compatible = automatic.x is not None and kinds.get(automatic.x) == "datetime"
    elif requested == "scatter":
        compatible = automatic.type == "scatter"
    elif requested == "pie":
        compatible = automatic.type in {"bar", "pie"}
    if compatible:
        return automatic.model_copy(update={"type": requested, "title": widget.title}), None
    return (
        automatic.model_copy(update={"title": widget.title}),
        f"{requested.title()} was incompatible with the returned fields; using {automatic.type}.",
    )


def _failed_dataset(widget: DashboardWidget, error: str) -> WidgetDataset:
    return WidgetDataset(
        widget_id=widget.id,
        analytical_question=widget.analytical_question or "",
        status="failed",
        error=error,
    )


def _normalize_question(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.casefold()))
