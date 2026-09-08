from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aiplot.adapters.dbt import DbtAdapter, LocalDbtCliAdapter
from aiplot.adapters.powerbi import PowerBIAdapter, PowerBIManifestAdapter
from aiplot.app import create_app
from aiplot.dashboard.models import (
    AgentDashboardSpec,
    AnalyticsCapabilities,
    DashboardPlan,
    DashboardWidget,
)
from aiplot.persistence.catalog import TransformationCatalog
from aiplot.persistence.classifier import classify_persistence
from aiplot.persistence.models import (
    DbtDeploymentResult,
    PowerBIResult,
    TransformationAction,
    TransformationMetric,
    TransformationSource,
    TransformationSpec,
    TransformationTest,
)
from aiplot.persistence.planner import TransformationPlanner


def capabilities(cohort: bool = False) -> AnalyticsCapabilities:
    time_columns = [
        {"name": "month", "table": "sales", "data_type": "date"},
    ]
    if cohort:
        time_columns = [
            {"name": "cohort_month", "table": "sales", "data_type": "date"},
            {"name": "activity_month", "table": "sales", "data_type": "date"},
            {"name": "updated_at", "table": "sales", "data_type": "timestamp"},
        ]
    return AnalyticsCapabilities.model_validate(
        {
            "db_id": "business",
            "dialect": "postgres",
            "tables": ["sales"],
            "business_entities": ["sale"],
            "measures": [
                {"name": "revenue", "table": "sales", "data_type": "numeric"},
                {"name": "customers", "table": "sales", "data_type": "integer"},
            ],
            "dimensions": [{"name": "segment", "table": "sales", "data_type": "text"}],
            "time_columns": time_columns,
            "available_kpis": [],
        }
    )


def dashboard_plan(*metrics: str) -> DashboardPlan:
    return DashboardPlan(
        spec=AgentDashboardSpec(
            title="Sales Performance",
            objective="Monitor sales performance.",
            widgets=[
                DashboardWidget(
                    id=f"metric_{index}",
                    kind="kpi",
                    title=metric.title(),
                    metric=metric,
                    visualization="kpi",
                )
                for index, metric in enumerate(metrics)
            ],
        )
    )


def transformation(
    name: str = "mart_sales",
    metrics: tuple[str, ...] = ("revenue",),
    *,
    cohort: bool = False,
) -> TransformationSpec:
    dimensions = ["cohort_month", "activity_month"] if cohort else ["month"]
    return TransformationSpec(
        model_name=name,
        sources=[TransformationSource(source_name="business", relation_name="sales")],
        grain=dimensions,
        dimensions=dimensions,
        metrics=[
            TransformationMetric(name=metric, source_column=metric, aggregation="sum")
            for metric in metrics
        ],
        materialization="incremental",
        incremental_strategy="delete_insert" if cohort else "merge",
        unique_key=dimensions,
        incremental_column="updated_at" if cohort else "month",
        lookback_days=90 if cohort else 3,
        purpose="cohort_retention" if cohort else "standard",
        late_arriving_policy=("recompute_affected_cohorts" if cohort else "lookback_window"),
        tests=[
            TransformationTest(kind="not_null", columns=dimensions),
            TransformationTest(kind="unique_combination", columns=dimensions),
            TransformationTest(kind="accepted_range", columns=list(metrics), minimum=0),
            TransformationTest(kind="reconciliation", columns=list(metrics), tolerance=0.001),
        ],
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a dashboard that refreshes daily",
        "Create a weekly monitoring dashboard",
        "Create a production dashboard",
        "Build an ongoing dashboard",
    ],
)
def test_persistent_requests_are_classified(prompt: str) -> None:
    assert classify_persistence(prompt) == "persistent"


def test_normal_dashboard_is_ad_hoc() -> None:
    assert classify_persistence("Build a sales dashboard") == "ad_hoc"


def test_transformation_spec_rejects_unsafe_incremental_contract() -> None:
    payload = transformation().model_dump()
    payload["unique_key"] = []

    with pytest.raises(ValidationError, match="unique key"):
        TransformationSpec.model_validate(payload)


def test_cohort_model_rejects_append_only_strategy() -> None:
    payload = transformation(cohort=True).model_dump()
    payload["incremental_strategy"] = "append"

    with pytest.raises(ValidationError, match="immutable streams"):
        TransformationSpec.model_validate(payload)


def test_append_rejects_late_arriving_lookback() -> None:
    payload = transformation().model_dump()
    payload.update(
        incremental_strategy="append",
        lookback_days=3,
        late_arriving_policy="lookback_window",
    )

    with pytest.raises(ValidationError, match="immutable streams"):
        TransformationSpec.model_validate(payload)


def test_widgets_on_same_source_share_one_mart(tmp_path: Path) -> None:
    planner = TransformationPlanner(TransformationCatalog(tmp_path))

    actions = planner.plan(
        dashboard_plan("revenue", "customers"), capabilities(), "Production dashboard"
    )

    assert len(actions) == 1
    assert {metric.name for metric in actions[0].spec.metrics} == {"revenue", "customers"}


def test_reuse_extend_create_decisions(tmp_path: Path) -> None:
    catalog = TransformationCatalog(tmp_path)
    existing = transformation(metrics=("revenue",))
    catalog.save(existing)

    reuse, _, _ = catalog.decide(existing)
    extend, _, _ = catalog.decide(transformation(metrics=("revenue", "customers")))
    create, _, _ = catalog.decide(
        transformation(name="mart_other").model_copy(
            update={
                "sources": [TransformationSource(source_name="business", relation_name="other")]
            }
        )
    )

    assert (reuse, extend, create) == (
        "REUSE_EXISTING",
        "EXTEND_EXISTING",
        "CREATE_NEW",
    )


def test_cohort_plan_recomputes_affected_cohorts(tmp_path: Path) -> None:
    planner = TransformationPlanner(TransformationCatalog(tmp_path))

    action = planner.plan(
        dashboard_plan("revenue"),
        capabilities(cohort=True),
        "Production retention cohort dashboard refreshed daily",
    )[0]

    assert action.spec.incremental_strategy == "delete_insert"
    assert action.spec.late_arriving_policy == "recompute_affected_cohorts"
    assert action.spec.incremental_column == "updated_at"


def test_local_dbt_adapter_generates_and_validates_models(tmp_path: Path) -> None:
    executable = tmp_path / "fake-dbt"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    spec = transformation(cohort=True)
    action = TransformationAction(
        decision="CREATE_NEW", target_model=spec.model_name, reason="test", spec=spec
    )

    result = asyncio.run(LocalDbtCliAdapter(tmp_path, str(executable)).deploy([action]))

    assert result.status == "succeeded"
    assert [item.command[1] for item in result.commands] == ["compile", "build", "test"]
    sql = (tmp_path / "models/aiplot/mart_sales.sql").read_text()
    assert "affected_cohorts" in sql
    assert "delete+insert" in sql
    assert (tmp_path / "tests/aiplot/mart_sales_unique_grain.sql").is_file()
    assert (tmp_path / "tests/aiplot/mart_sales_revenue_range.sql").is_file()
    assert (tmp_path / "tests/aiplot/mart_sales_reconciliation.sql").is_file()


def test_missing_dbt_cli_does_not_mutate_project(tmp_path: Path) -> None:
    spec = transformation()
    action = TransformationAction(
        decision="CREATE_NEW", target_model=spec.model_name, reason="test", spec=spec
    )

    result = asyncio.run(
        LocalDbtCliAdapter(tmp_path, "definitely-not-a-dbt-command").deploy([action])
    )

    assert result.status == "failed"
    assert not (tmp_path / "models").exists()


def test_power_bi_manifest_uses_stable_mart_not_raw_sql(tmp_path: Path) -> None:
    spec = transformation()
    action = TransformationAction(
        decision="CREATE_NEW", target_model=spec.model_name, reason="test", spec=spec
    )
    dashboard = dashboard_plan("revenue").spec

    result = asyncio.run(PowerBIManifestAdapter(tmp_path).publish(dashboard, [action]))
    payload = json.loads(Path(result.artifact_path or "").read_text())

    assert result.status == "manifest_ready"
    assert payload["semantic_model"]["tables"][0]["source"] == "analytics.mart_sales"
    assert "sql" not in json.dumps(payload).casefold()


class FakeDbtAdapter(DbtAdapter):
    def __init__(self) -> None:
        self.deploy_calls = 0
        self.refresh_calls = 0

    async def deploy(self, actions: list[TransformationAction]) -> DbtDeploymentResult:
        self.deploy_calls += 1
        return DbtDeploymentResult(
            status="succeeded", model_names=[action.target_model for action in actions]
        )

    async def refresh(self, model_names: list[str]) -> DbtDeploymentResult:
        self.refresh_calls += 1
        return DbtDeploymentResult(status="succeeded", model_names=model_names)


class FakePowerBIAdapter(PowerBIAdapter):
    def __init__(self) -> None:
        self.publish_calls = 0
        self.refresh_calls = 0

    async def publish(
        self, dashboard: AgentDashboardSpec, transformations: list[TransformationAction]
    ) -> PowerBIResult:
        self.publish_calls += 1
        return PowerBIResult(
            status="published", semantic_model_id="sales_model", report_id="sales_report"
        )

    async def refresh(self, semantic_model_id: str) -> PowerBIResult:
        self.refresh_calls += 1
        return PowerBIResult(status="refreshed", semantic_model_id=semantic_model_id)


class FakeTextToSQL:
    def __init__(self) -> None:
        self.chat_calls = 0

    async def databases(self) -> list[dict[str, Any]]:
        return [{"db_id": "business", "dialect": "postgres", "configured": True}]

    async def models(self) -> list[dict[str, Any]]:
        return [{"provider": "ollama", "model": "local", "configured": True}]

    async def health(self) -> bool:
        return True

    async def analytics_capabilities(self, db_id: str) -> AnalyticsCapabilities:
        return capabilities()

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_calls += 1
        return {
            "message": "accepted",
            "generation": {
                "accepted": True,
                "execution_status": "ACCEPTED",
                "sql": "SELECT verified_result",
                "columns": ["month", "revenue"],
                "rows": [["2026-01", 10], ["2026-02", 12]],
                "row_count": 2,
            },
        }


def persistent_request() -> dict[str, str]:
    return {
        "question": "Build a production dashboard with revenue that refreshes daily",
        "db_id": "business",
        "provider": "ollama",
        "model": "local",
    }


def test_persistent_api_requires_separate_human_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIPLOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIPLOT_DBT_PROJECT_DIR", str(tmp_path / "dbt"))
    dbt = FakeDbtAdapter()
    power_bi = FakePowerBIAdapter()
    client = TestClient(create_app(FakeTextToSQL(), dbt, power_bi))  # type: ignore[arg-type]

    proposed = client.post("/api/analyze", json=persistent_request()).json()

    assert proposed["persistence"] == "persistent"
    assert proposed["persistence_plan"]["status"] == "pending_approval"
    assert dbt.deploy_calls == 0
    assert power_bi.publish_calls == 0

    plan_id = proposed["persistence_plan"]["id"]
    deployment = client.post(
        f"/api/persistence/plans/{plan_id}/approve",
        json={"approved": True, "approved_by": "Analytics Owner"},
    )

    assert deployment.status_code == 200
    assert deployment.json()["plan"]["status"] == "deployed"
    assert dbt.deploy_calls == 1
    assert power_bi.publish_calls == 1


def test_production_refresh_uses_no_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIPLOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIPLOT_DBT_PROJECT_DIR", str(tmp_path / "dbt"))
    text2sql = FakeTextToSQL()
    dbt = FakeDbtAdapter()
    power_bi = FakePowerBIAdapter()
    client = TestClient(create_app(text2sql, dbt, power_bi))  # type: ignore[arg-type]
    proposed = client.post("/api/analyze", json=persistent_request()).json()
    plan_id = proposed["persistence_plan"]["id"]
    client.post(
        f"/api/persistence/plans/{plan_id}/approve",
        json={"approved": True, "approved_by": "Analytics Owner"},
    )
    planning_calls = text2sql.chat_calls

    refresh = client.post(f"/api/persistence/plans/{plan_id}/refresh")

    assert refresh.status_code == 200
    assert refresh.json()["llm_calls"] == 0
    assert text2sql.chat_calls == planning_calls
    assert dbt.refresh_calls == 1
    assert power_bi.refresh_calls == 1
