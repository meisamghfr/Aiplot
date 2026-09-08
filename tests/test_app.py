from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from aiplot.app import create_app
from aiplot.dashboard.models import AnalyticsCapabilities
from aiplot.text2sql_client import TextToSQLError


class FakeTextToSQLClient:
    def __init__(
        self, response: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.chat_calls = 0

    async def databases(self) -> list[dict[str, Any]]:
        return [{"db_id": "business", "dialect": "postgres", "configured": True}]

    async def models(self) -> list[dict[str, Any]]:
        return [{"provider": "ollama", "model": "local", "configured": True, "local": True}]

    async def health(self) -> bool:
        return True

    async def analytics_capabilities(self, db_id: str) -> AnalyticsCapabilities:
        return AnalyticsCapabilities.model_validate(
            {
                "db_id": db_id,
                "dialect": "postgres",
                "tables": ["sales"],
                "business_entities": ["customers"],
                "measures": [
                    {
                        "name": "revenue",
                        "table": "sales",
                        "data_type": "numeric",
                    }
                ],
                "dimensions": [{"name": "segment", "table": "sales", "data_type": "text"}],
                "time_columns": [{"name": "month", "table": "sales", "data_type": "date"}],
                "available_kpis": [],
            }
        )

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_calls += 1
        if self.error:
            raise self.error
        assert payload["execute"] is True
        assert "sql" not in payload
        return self.response or {}


def accepted_response() -> dict[str, Any]:
    return {
        "operation": "NEW_QUERY",
        "resolved_question": "Show revenue by segment",
        "message": "Query accepted.",
        "generation": {
            "sql": "SELECT segment, SUM(revenue) AS revenue FROM sales GROUP BY segment",
            "accepted": True,
            "execution_status": "ACCEPTED",
            "columns": ["segment", "revenue"],
            "rows": [["SME", 12], ["Enterprise", 25]],
            "row_count": 2,
            "truncated": False,
            "termination_reason": "accepted",
            "model_error": None,
        },
    }


def request_body() -> dict[str, str]:
    return {
        "question": "Show revenue by segment",
        "db_id": "business",
        "provider": "ollama",
        "model": "local",
    }


def test_analyze_normalizes_text2sql_result_and_plans_chart() -> None:
    fake = FakeTextToSQLClient(accepted_response())
    response = TestClient(create_app(fake)).post("/api/analyze", json=request_body())  # type: ignore[arg-type]

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["accepted_sql"].startswith("SELECT segment")
    assert body["visualization"]["chart"]["type"] == "bar"
    assert fake.chat_calls == 1


def test_text2sql_api_error_is_returned_for_display() -> None:
    fake = FakeTextToSQLClient(error=TextToSQLError("Text-to-SQL is unavailable."))
    response = TestClient(create_app(fake)).post("/api/analyze", json=request_body())  # type: ignore[arg-type]

    assert response.status_code == 200
    assert response.json()["error_type"] == "api_unavailable"
    assert "unavailable" in response.json()["message"]


def test_database_failure_is_preserved() -> None:
    payload = accepted_response()
    payload["generation"].update(
        accepted=False,
        execution_status="NOT_EXECUTED",
        termination_reason="database_error",
        model_error="read-only execution failed",
    )
    response = TestClient(create_app(FakeTextToSQLClient(payload))).post(
        "/api/analyze", json=request_body()
    )

    assert response.json()["error_type"] == "database_execution_failure"


def test_frontend_view_and_manual_controls_are_local_only() -> None:
    script = TestClient(create_app(FakeTextToSQLClient())).get("/static/app.js").text  # type: ignore[arg-type]

    assert script.count('fetch("/api/analyze"') == 1
    switch_view = script[
        script.index("function switchView") : script.index("function setupControls")
    ]
    manual_settings = script[
        script.index("function applyManualChartSettings") : script.index("function resetChart")
    ]
    assert "fetch(" not in switch_view
    assert "fetch(" not in manual_settings


def test_stakeholder_thread_persists_governed_analysis(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("AIPLOT_STATE_DIR", str(tmp_path))
    fake = FakeTextToSQLClient(accepted_response())
    client = TestClient(create_app(fake))  # type: ignore[arg-type]
    created = client.post(
        "/api/stakeholders/threads",
        json={
            "stakeholder_name": "Maya",
            "stakeholder_role": "Head of Growth",
            "objective": "Decide which segment to prioritize",
            "db_id": "business",
            "provider": "ollama",
            "model": "local",
        },
    )
    thread_id = created.json()["id"]

    response = client.post(
        f"/api/stakeholders/threads/{thread_id}/messages",
        json={"message": "Show revenue by segment"},
    )

    assert response.status_code == 200
    assert response.json()["analysis"]["result"]["accepted_sql"].startswith("SELECT")
    assert response.json()["tool"] == "text_to_sql"
    assert response.json()["tool_status"] == "completed"
    assert [item["role"] for item in response.json()["thread"]["messages"]] == [
        "user",
        "assistant",
    ]
    assert client.get("/api/stakeholders/threads").json()[0]["id"] == thread_id
    assert fake.chat_calls == 1


def test_pipeline_tool_stops_for_human_approval(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("AIPLOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIPLOT_DBT_PROJECT_DIR", str(tmp_path / "dbt"))
    fake = FakeTextToSQLClient(accepted_response())
    client = TestClient(create_app(fake))  # type: ignore[arg-type]
    thread = client.post(
        "/api/stakeholders/threads",
        json={
            "stakeholder_name": "Maya",
            "stakeholder_role": "Head of Growth",
            "objective": "Operational revenue monitoring",
            "db_id": "business",
            "provider": "ollama",
            "model": "local",
        },
    ).json()

    response = client.post(
        f"/api/stakeholders/threads/{thread['id']}/messages",
        json={"message": "Create a daily revenue pipeline by segment"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["tool"] == "create_pipeline"
    assert body["tool_status"] == "awaiting_human_approval"
    assert body["analysis"]["persistence_plan"]["status"] == "pending_approval"
    assert body["approval_url"].endswith("/approve")
    assert not (tmp_path / "dbt" / "models").exists()


def test_vague_plot_is_clarified_before_text_to_sql(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("AIPLOT_STATE_DIR", str(tmp_path))
    fake = FakeTextToSQLClient(accepted_response())
    client = TestClient(create_app(fake))  # type: ignore[arg-type]
    thread = client.post(
        "/api/stakeholders/threads",
        json={
            "stakeholder_name": "Maya",
            "stakeholder_role": "Commercial lead",
            "objective": "Understand the client base",
            "db_id": "business",
            "provider": "ollama",
            "model": "local",
        },
    ).json()

    clarification = client.post(
        f"/api/stakeholders/threads/{thread['id']}/messages",
        json={"message": "I need a plot for number of clients"},
    ).json()

    assert clarification["tool_status"] == "awaiting_clarification"
    assert clarification["analysis"] == {}
    assert len(clarification["clarification_questions"]) >= 3
    assert fake.chat_calls == 0

    result = client.post(
        f"/api/stakeholders/threads/{thread['id']}/messages",
        json={"message": "Use customers, the last 12 months, monthly, by segment."},
    ).json()

    assert result["tool_status"] == "completed"
    assert "Use these stakeholder clarifications" in result["generated_question"]
    assert fake.chat_calls == 1


def test_dashboard_mode_performs_multiple_text2sql_calls() -> None:
    fake = FakeTextToSQLClient(accepted_response())
    body = request_body() | {"question": "Build a business performance dashboard"}

    response = TestClient(create_app(fake)).post("/api/analyze", json=body)  # type: ignore[arg-type]

    assert response.status_code == 200
    assert response.json()["intent"] == "dashboard"
    assert response.json()["dashboard"]["unique_query_count"] > 1
    assert fake.chat_calls == response.json()["dashboard"]["unique_query_count"]
