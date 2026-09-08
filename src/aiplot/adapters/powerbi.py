"""Replaceable Power BI manifest and REST service adapters."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from aiplot.dashboard.models import AgentDashboardSpec
from aiplot.persistence.models import PowerBIResult, TransformationAction


class PowerBIAdapter(ABC):
    @abstractmethod
    async def publish(
        self, dashboard: AgentDashboardSpec, transformations: list[TransformationAction]
    ) -> PowerBIResult:
        """Create or update a semantic model and report over stable marts."""

    @abstractmethod
    async def refresh(self, semantic_model_id: str) -> PowerBIResult:
        """Refresh an already-published semantic model without planning calls."""


class PowerBIManifestAdapter(PowerBIAdapter):
    """Writes an MCP-ready manifest when no live Power BI MCP is configured."""

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir.resolve()

    async def publish(
        self, dashboard: AgentDashboardSpec, transformations: list[TransformationAction]
    ) -> PowerBIResult:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        semantic_model_id = _slug(dashboard.title)
        path = self.artifact_dir / f"{semantic_model_id}.json"
        payload = {
            "semantic_model": {
                "id": semantic_model_id,
                "tables": [
                    {
                        "name": action.target_model,
                        "source": f"analytics.{action.target_model}",
                        "grain": action.spec.grain,
                        "dimensions": action.spec.dimensions,
                        "metrics": [metric.model_dump() for metric in action.spec.metrics],
                    }
                    for action in transformations
                ],
            },
            "report": {
                "title": dashboard.title,
                "objective": dashboard.objective,
                "widgets": [widget.model_dump() for widget in dashboard.widgets],
            },
            "adapter_target": "power_bi_mcp",
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return PowerBIResult(
            status="manifest_ready",
            semantic_model_id=semantic_model_id,
            artifact_path=str(path),
        )

    async def refresh(self, semantic_model_id: str) -> PowerBIResult:
        path = self.artifact_dir / f"{semantic_model_id}.json"
        if not path.is_file():
            return PowerBIResult(status="failed", error="Power BI manifest was not found.")
        return PowerBIResult(
            status="manifest_ready",
            semantic_model_id=semantic_model_id,
            artifact_path=str(path),
        )


class PowerBIRestAdapter(PowerBIAdapter):
    """Publishes a versioned PBIX template and refreshes its stable mart dataset."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        workspace_id: str,
        pbix_template: Path,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.workspace_id = workspace_id
        self.pbix_template = pbix_template.resolve()
        self.transport = transport

    async def _token(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://analysis.windows.net/powerbi/api/.default",
            },
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Power BI authentication did not return an access token.")
        return token

    async def publish(
        self, dashboard: AgentDashboardSpec, transformations: list[TransformationAction]
    ) -> PowerBIResult:
        if not self.pbix_template.is_file():
            return PowerBIResult(
                status="failed", error="Configured Power BI PBIX template was not found."
            )
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=60) as client:
                token = await self._token(client)
                headers = {"Authorization": f"Bearer {token}"}
                with self.pbix_template.open("rb") as file_handle:
                    response = await client.post(
                        f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/imports",
                        headers=headers,
                        params={
                            "datasetDisplayName": dashboard.title,
                            "nameConflict": "CreateOrOverwrite",
                        },
                        files={
                            "file": (
                                self.pbix_template.name,
                                file_handle,
                                "application/octet-stream",
                            )
                        },
                    )
                response.raise_for_status()
                import_id = response.json().get("id")
                if not isinstance(import_id, str):
                    raise ValueError("Power BI import did not return an import id.")
                payload: dict[str, object] = {}
                for _ in range(30):
                    poll = await client.get(
                        f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/imports/{import_id}",
                        headers=headers,
                    )
                    poll.raise_for_status()
                    payload = poll.json()
                    if payload.get("importState") in {"Succeeded", "Failed"}:
                        break
                    await asyncio.sleep(1)
                if payload.get("importState") != "Succeeded":
                    import_state = payload.get("importState", "Unknown")
                    return PowerBIResult(
                        status="failed",
                        error=f"Power BI import ended in state {import_state}.",
                    )
                datasets = payload.get("datasets", [])
                semantic_id = (
                    datasets[0].get("id")
                    if isinstance(datasets, list) and datasets and isinstance(datasets[0], dict)
                    else None
                )
                if not isinstance(semantic_id, str):
                    raise ValueError("Power BI import returned no semantic model id.")
                return PowerBIResult(status="published", semantic_model_id=semantic_id)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return PowerBIResult(status="failed", error=str(exc))

    async def refresh(self, semantic_model_id: str) -> PowerBIResult:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=60) as client:
                token = await self._token(client)
                response = await client.post(
                    f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets/{semantic_model_id}/refreshes",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"notifyOption": "NoNotification"},
                )
                response.raise_for_status()
            return PowerBIResult(status="refreshed", semantic_model_id=semantic_model_id)
        except (httpx.HTTPError, ValueError) as exc:
            return PowerBIResult(
                status="failed", semantic_model_id=semantic_model_id, error=str(exc)
            )


def power_bi_adapter_from_environment(artifact_dir: Path) -> PowerBIAdapter:
    values = {
        name: os.environ.get(name)
        for name in (
            "POWERBI_TENANT_ID",
            "POWERBI_CLIENT_ID",
            "POWERBI_CLIENT_SECRET",
            "POWERBI_WORKSPACE_ID",
            "POWERBI_PBIX_TEMPLATE_PATH",
        )
    }
    if all(values.values()):
        return PowerBIRestAdapter(
            values["POWERBI_TENANT_ID"] or "",
            values["POWERBI_CLIENT_ID"] or "",
            values["POWERBI_CLIENT_SECRET"] or "",
            values["POWERBI_WORKSPACE_ID"] or "",
            Path(values["POWERBI_PBIX_TEMPLATE_PATH"] or ""),
        )
    return PowerBIManifestAdapter(artifact_dir)


def _slug(value: str) -> str:
    return "_".join(part for part in value.casefold().replace("-", " ").split() if part)
