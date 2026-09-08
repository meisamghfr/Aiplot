"""FastAPI entrypoint for Aiplot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from aiplot.adapters.dbt import DbtAdapter, LocalDbtCliAdapter
from aiplot.adapters.powerbi import PowerBIAdapter, power_bi_adapter_from_environment
from aiplot.dashboard.models import DashboardExecution
from aiplot.dashboard.service import DashboardService
from aiplot.persistence.catalog import TransformationCatalog
from aiplot.persistence.classifier import classify_persistence
from aiplot.persistence.models import (
    ApprovalRequest,
    PersistenceDeployment,
    PersistenceMode,
    PersistencePlan,
    RefreshResult,
)
from aiplot.persistence.service import PersistenceService
from aiplot.router import RequestIntent, route_request
from aiplot.stakeholders.clarification import (
    clarification_for,
    clarification_message,
    generated_question,
)
from aiplot.stakeholders.models import (
    CreateStakeholderThread,
    StakeholderChatRequest,
    StakeholderChatResponse,
    StakeholderThread,
)
from aiplot.stakeholders.store import StakeholderStore
from aiplot.stakeholders.tools import pipeline_request, select_tool
from aiplot.text2sql_client import TextToSQLClient, TextToSQLError
from aiplot.visualization.models import QueryResult, StrictModel, VisualizationPlan
from aiplot.visualization.planner import plan_visualization


class AnalyzeRequest(StrictModel):
    question: str = Field(min_length=1, max_length=4_000)
    db_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    provider: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=200)
    context_mode: str = Field(default="retrieval", pattern=r"^(retrieval|model1)$")
    context_provider: str | None = Field(default=None, max_length=30)
    context_model: str | None = Field(default=None, max_length=200)
    session_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class AnalyzeResponse(StrictModel):
    intent: RequestIntent = "single_analysis"
    persistence: PersistenceMode = "ad_hoc"
    result: QueryResult | None = None
    visualization: VisualizationPlan | None = None
    dashboard: DashboardExecution | None = None
    persistence_plan: PersistencePlan | None = None
    message: str
    error_type: str | None = None
    raw_status: dict[str, Any] = Field(default_factory=dict)
    provider_used: str | None = None
    model_used: str | None = None
    provider_notice: str | None = None


def create_app(
    client: TextToSQLClient | None = None,
    dbt_adapter: DbtAdapter | None = None,
    power_bi_adapter: PowerBIAdapter | None = None,
) -> FastAPI:
    api_url = os.environ.get("TEXT2SQL_API_URL", "http://127.0.0.1:8000")
    timeout = float(os.environ.get("AIPLOT_TEXT2SQL_TIMEOUT_SECONDS", "180"))
    max_plot_rows = int(os.environ.get("AIPLOT_MAX_PLOT_ROWS", "300"))
    text2sql = client or TextToSQLClient(api_url, timeout)
    dashboard_service = DashboardService(text2sql, max_plot_rows=max_plot_rows)
    dbt_project = Path(os.environ.get("AIPLOT_DBT_PROJECT_DIR", "dbt_analytics"))
    state_dir = Path(os.environ.get("AIPLOT_STATE_DIR", ".aiplot"))
    power_bi_dir = Path(os.environ.get("AIPLOT_POWERBI_ARTIFACT_DIR", "powerbi_artifacts"))
    catalog = TransformationCatalog(dbt_project)
    persistence_service = PersistenceService(
        state_dir,
        catalog,
        dbt_adapter or LocalDbtCliAdapter(dbt_project),
        power_bi_adapter or power_bi_adapter_from_environment(power_bi_dir),
    )
    stakeholder_store = StakeholderStore(state_dir)
    app = FastAPI(title="AI Plot", version="0.4.0")
    web_root = Path(__file__).resolve().parents[2] / "web"
    app.mount("/static", StaticFiles(directory=web_root), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        try:
            databases, models = await text2sql.databases(), await text2sql.models()
        except TextToSQLError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return {
            "databases": databases,
            "models": models,
            "text2sql_online": await text2sql.health(),
        }

    @app.post("/api/analyze", response_model=AnalyzeResponse)
    async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        intent = route_request(request.question)
        if intent == "dashboard":
            persistence = classify_persistence(request.question)
            try:
                capabilities = await text2sql.analytics_capabilities(request.db_id)
            except TextToSQLError as exc:
                return AnalyzeResponse(
                    intent="dashboard", message=str(exc), error_type="capability_discovery_failure"
                )
            try:
                provider, model, provider_notice = await _dashboard_model(
                    text2sql, request.provider, request.model
                )
                dashboard = await dashboard_service.build(
                    request.question,
                    capabilities,
                    db_id=request.db_id,
                    provider=provider,
                    model=model,
                    context_mode=request.context_mode,
                    context_provider=request.context_provider,
                    context_model=request.context_model,
                )
            except ValueError as exc:
                return AnalyzeResponse(
                    intent="dashboard", message=str(exc), error_type="dashboard_planning_failure"
                )
            accepted = sum(item.status == "accepted" for item in dashboard.datasets)
            persistence_plan = None
            if persistence == "persistent":
                try:
                    persistence_plan = persistence_service.propose(
                        dashboard, capabilities, request.question
                    )
                except ValueError as exc:
                    return AnalyzeResponse(
                        intent="dashboard",
                        persistence="persistent",
                        dashboard=dashboard,
                        message=str(exc),
                        error_type="transformation_planning_failure",
                    )
            return AnalyzeResponse(
                intent="dashboard",
                persistence=persistence,
                dashboard=dashboard,
                persistence_plan=persistence_plan,
                provider_used=provider,
                model_used=model,
                provider_notice=provider_notice,
                message=(
                    f"Dashboard built with {accepted} available widgets. "
                    "Review and approve the persistence plan before dbt changes are made."
                    if persistence_plan
                    else f"Dashboard built with {accepted} available widgets."
                ),
            )
        payload: dict[str, Any] = {
            "session_id": request.session_id or f"aiplot-{uuid4().hex}",
            "db_id": request.db_id,
            "message": request.question,
            "provider": request.provider,
            "model": request.model,
            "context_mode": request.context_mode,
            "execute": True,
            "max_rows": 500,
        }
        if request.context_mode == "model1":
            payload["context_provider"] = request.context_provider or request.provider
            payload["context_model"] = request.context_model or request.model
        try:
            response = await text2sql.chat(payload)
        except TextToSQLError as exc:
            return AnalyzeResponse(message=str(exc), error_type="api_unavailable")
        generation = response.get("generation")
        if not isinstance(generation, dict):
            return AnalyzeResponse(
                message=str(
                    response.get("message") or "Text-to-SQL did not return a query result."
                ),
                error_type="sql_generation_failure",
                raw_status=_status(response, None),
            )
        if not generation.get("accepted") or generation.get("execution_status") != "ACCEPTED":
            return AnalyzeResponse(
                message=str(response.get("message") or _generation_error(generation)),
                error_type=_error_type(generation),
                raw_status=_status(response, generation),
            )
        sql = generation.get("sql")
        columns = generation.get("columns")
        rows = generation.get("rows")
        if not isinstance(sql, str) or not isinstance(columns, list) or not isinstance(rows, list):
            return AnalyzeResponse(
                message="Text-to-SQL accepted the query but returned malformed result metadata.",
                error_type="invalid_response",
                raw_status=_status(response, generation),
            )
        result = QueryResult(
            question=str(response.get("resolved_question") or request.question),
            accepted_sql=sql,
            status=str(generation.get("execution_status")),
            columns=[str(column) for column in columns],
            rows=rows,
            row_count=int(generation.get("row_count", len(rows))),
            truncated=bool(generation.get("truncated", False)),
        )
        visualization = plan_visualization(result, max_plot_rows=max_plot_rows)
        return AnalyzeResponse(
            result=result,
            visualization=visualization,
            message=str(response.get("message") or "Query completed."),
            raw_status=_status(response, generation),
            provider_used=request.provider,
            model_used=request.model,
        )

    @app.get("/api/persistence/plans/{plan_id}", response_model=PersistencePlan)
    async def get_persistence_plan(plan_id: str) -> PersistencePlan:
        try:
            return persistence_service.get(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/persistence/plans/{plan_id}/approve",
        response_model=PersistenceDeployment,
    )
    async def approve_persistence_plan(
        plan_id: str, request: ApprovalRequest
    ) -> PersistenceDeployment:
        if not request.approved:
            raise HTTPException(
                status_code=400,
                detail="Explicit approval is required before persistent model mutation.",
            )
        try:
            return await persistence_service.approve(plan_id, request.approved_by)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/persistence/plans/{plan_id}/refresh", response_model=RefreshResult)
    async def refresh_persistence_plan(plan_id: str) -> RefreshResult:
        try:
            return await persistence_service.refresh(plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/stakeholders/threads", response_model=list[StakeholderThread])
    async def list_stakeholder_threads() -> list[StakeholderThread]:
        return stakeholder_store.list()

    @app.post(
        "/api/stakeholders/threads",
        response_model=StakeholderThread,
        status_code=201,
    )
    async def create_stakeholder_thread(
        request: CreateStakeholderThread,
    ) -> StakeholderThread:
        return stakeholder_store.create(request)

    @app.get("/api/stakeholders/threads/{thread_id}", response_model=StakeholderThread)
    async def get_stakeholder_thread(thread_id: str) -> StakeholderThread:
        try:
            return stakeholder_store.get(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/stakeholders/threads/{thread_id}/messages",
        response_model=StakeholderChatResponse,
    )
    async def stakeholder_message(
        thread_id: str, request: StakeholderChatRequest
    ) -> StakeholderChatResponse:
        try:
            thread = stakeholder_store.get(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        stakeholder_store.append(thread_id, "user", request.message)
        generated: str | None = None
        if thread.pending_clarification is not None:
            pending = thread.pending_clarification
            tool = pending.tool
            generated = generated_question(pending, request.message)
            stakeholder_store.set_pending(thread_id, None)
        else:
            tool = select_tool(request.message)
            try:
                capabilities = await text2sql.analytics_capabilities(thread.db_id)
            except TextToSQLError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            clarification = clarification_for(request.message, capabilities, tool)
            if clarification is not None:
                stakeholder_store.set_pending(thread_id, clarification)
                assistant = stakeholder_store.append(
                    thread_id, "assistant", clarification_message(clarification)
                )
                return StakeholderChatResponse(
                    thread=stakeholder_store.get(thread_id),
                    assistant_message=assistant,
                    analysis={},
                    tool=tool,
                    tool_status="awaiting_clarification",
                    clarification_questions=clarification.questions,
                )
        tool_input = generated or request.message
        effective_request = (
            pipeline_request(tool_input) if tool == "create_pipeline" else tool_input
        )
        analysis = await analyze(
            AnalyzeRequest(
                question=effective_request,
                db_id=thread.db_id,
                provider=thread.provider,
                model=thread.model,
                context_mode=thread.context_mode,
                session_id=thread.id,
            )
        )
        analysis_payload = analysis.model_dump(mode="json")
        assistant = stakeholder_store.append(
            thread_id,
            "assistant",
            _stakeholder_answer(analysis),
            analysis_payload,
        )
        return StakeholderChatResponse(
            thread=stakeholder_store.get(thread_id),
            assistant_message=assistant,
            analysis=analysis_payload,
            tool=tool,
            tool_status=(
                "failed"
                if analysis.error_type
                else (
                    "awaiting_human_approval"
                    if analysis.persistence_plan is not None
                    else "completed"
                )
            ),
            approval_url=(
                f"/api/persistence/plans/{analysis.persistence_plan.id}/approve"
                if analysis.persistence_plan is not None
                else None
            ),
            generated_question=effective_request,
        )

    return app


def _status(response: dict[str, Any], generation: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "operation": response.get("operation"),
        "clarification_required": response.get("clarification_required", False),
        "termination_reason": generation.get("termination_reason") if generation else None,
        "execution_status": generation.get("execution_status") if generation else None,
        "model_error": generation.get("model_error") if generation else None,
    }


def _error_type(generation: dict[str, Any]) -> str:
    reason = generation.get("termination_reason")
    if reason == "database_error":
        return "database_execution_failure"
    if reason == "model_error":
        return "sql_generation_failure"
    return "sql_validation_failure"


def _generation_error(generation: dict[str, Any]) -> str:
    return str(generation.get("model_error") or "Text-to-SQL could not produce an accepted query.")


def _stakeholder_answer(analysis: AnalyzeResponse) -> str:
    if analysis.error_type:
        return f"I could not complete that analysis: {analysis.message}"
    if analysis.dashboard:
        available = sum(item.status == "accepted" for item in analysis.dashboard.datasets)
        total = len(analysis.dashboard.datasets)
        suffix = " A persistence plan is ready for approval." if analysis.persistence_plan else ""
        return f"I built the dashboard with {available} of {total} widgets available.{suffix}"
    if analysis.result:
        return (
            f"I completed the analysis and returned {analysis.result.row_count} "
            f"{'row' if analysis.result.row_count == 1 else 'rows'}."
        )
    return analysis.message


async def _dashboard_model(
    text2sql: TextToSQLClient, provider: str, model: str
) -> tuple[str, str, str | None]:
    if provider != "groq":
        return provider, model, None
    try:
        models = await text2sql.models()
    except TextToSQLError:
        return provider, model, None
    fallback = next(
        (
            item
            for item in models
            if item.get("provider") == "justdowork" and item.get("configured") is True
        ),
        None,
    )
    fallback_model = fallback.get("model") if fallback else None
    if not isinstance(fallback_model, str):
        return provider, model, None
    return (
        "justdowork",
        fallback_model,
        f"Dashboard execution used {fallback_model} because the configured Groq tier has a "
        "1,000 output-token-per-minute limit.",
    )


app = create_app()
