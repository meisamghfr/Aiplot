"""Approval-gated persistence lifecycle and no-LLM production refresh."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from aiplot.adapters.dbt import DbtAdapter
from aiplot.adapters.powerbi import PowerBIAdapter
from aiplot.dashboard.models import AnalyticsCapabilities, DashboardExecution
from aiplot.persistence.catalog import TransformationCatalog
from aiplot.persistence.models import (
    PersistenceDeployment,
    PersistencePlan,
    PowerBIResult,
    RefreshResult,
)
from aiplot.persistence.planner import TransformationPlanner


class PersistenceService:
    def __init__(
        self,
        state_dir: Path,
        catalog: TransformationCatalog,
        dbt: DbtAdapter,
        power_bi: PowerBIAdapter,
    ) -> None:
        self.state_dir = state_dir.resolve()
        self.catalog = catalog
        self.dbt = dbt
        self.power_bi = power_bi
        self.planner = TransformationPlanner(catalog)

    def propose(
        self,
        dashboard: DashboardExecution,
        capabilities: AnalyticsCapabilities,
        request: str,
    ) -> PersistencePlan:
        actions = self.planner.plan(dashboard.plan, capabilities, request)
        plan = PersistencePlan(
            id=uuid4().hex,
            mode="persistent",
            dashboard=dashboard.plan.spec,
            transformations=actions,
            status="pending_approval",
            requires_approval=True,
        )
        self._save_plan(plan)
        return plan

    async def approve(self, plan_id: str, approved_by: str) -> PersistenceDeployment:
        plan = self._load_plan(plan_id)
        if plan.status != "pending_approval":
            raise ValueError(f"Persistence plan is already {plan.status}.")
        approved = plan.model_copy(update={"status": "approved", "approved_by": approved_by})
        self._save_plan(approved)
        dbt_result = await self.dbt.deploy(approved.transformations)
        if dbt_result.status != "succeeded":
            failed = approved.model_copy(
                update={"status": "failed", "error": dbt_result.error or "dbt failed"}
            )
            self._save_plan(failed)
            deployment = PersistenceDeployment(
                plan=failed,
                dbt=dbt_result,
                power_bi=PowerBIResult(status="not_run"),
            )
            self._save_deployment(deployment)
            return deployment
        power_bi_result = await self.power_bi.publish(approved.dashboard, approved.transformations)
        deployed = approved.model_copy(
            update={
                "status": "deployed" if power_bi_result.status != "failed" else "failed",
                "error": power_bi_result.error,
            }
        )
        for action in approved.transformations:
            self.catalog.save(action.spec)
        self._save_plan(deployed)
        deployment = PersistenceDeployment(plan=deployed, dbt=dbt_result, power_bi=power_bi_result)
        self._save_deployment(deployment)
        return deployment

    async def refresh(self, plan_id: str) -> RefreshResult:
        plan = self._load_plan(plan_id)
        if plan.status != "deployed":
            raise ValueError("Only deployed persistence plans can be refreshed.")
        previous = self._load_deployment(plan_id)
        dbt_result = await self.dbt.refresh(
            [action.target_model for action in plan.transformations]
        )
        if dbt_result.status != "succeeded":
            return RefreshResult(
                plan_id=plan_id,
                dbt=dbt_result,
                power_bi=PowerBIResult(status="not_run"),
            )
        semantic_model_id = previous.power_bi.semantic_model_id
        power_bi_result = (
            await self.power_bi.refresh(semantic_model_id)
            if semantic_model_id
            else PowerBIResult(status="failed", error="Semantic model id is unavailable.")
        )
        return RefreshResult(plan_id=plan_id, dbt=dbt_result, power_bi=power_bi_result, llm_calls=0)

    def get(self, plan_id: str) -> PersistencePlan:
        return self._load_plan(plan_id)

    def _plan_path(self, plan_id: str) -> Path:
        _validate_plan_id(plan_id)
        return self.state_dir / "plans" / f"{plan_id}.json"

    def _deployment_path(self, plan_id: str) -> Path:
        _validate_plan_id(plan_id)
        return self.state_dir / "deployments" / f"{plan_id}.json"

    def _save_plan(self, plan: PersistencePlan) -> None:
        path = self._plan_path(plan.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, plan.model_dump_json(indent=2) + "\n")

    def _load_plan(self, plan_id: str) -> PersistencePlan:
        path = self._plan_path(plan_id)
        if not path.is_file():
            raise ValueError("Persistence plan was not found.")
        return PersistencePlan.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_deployment(self, deployment: PersistenceDeployment) -> None:
        path = self._deployment_path(deployment.plan.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, deployment.model_dump_json(indent=2) + "\n")

    def _load_deployment(self, plan_id: str) -> PersistenceDeployment:
        path = self._deployment_path(plan_id)
        if not path.is_file():
            raise ValueError("Persistence deployment record was not found.")
        return PersistenceDeployment.model_validate_json(path.read_text(encoding="utf-8"))


def _validate_plan_id(plan_id: str) -> None:
    if len(plan_id) != 32 or any(char not in "0123456789abcdef" for char in plan_id):
        raise ValueError("Invalid persistence plan id.")


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
