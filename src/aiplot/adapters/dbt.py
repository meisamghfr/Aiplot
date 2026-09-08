"""dbt adapter boundary with a local CLI implementation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from aiplot.persistence.catalog import TransformationCatalog
from aiplot.persistence.models import (
    DbtCommandResult,
    DbtDeploymentResult,
    TransformationAction,
    TransformationSpec,
)


class DbtAdapter(ABC):
    @abstractmethod
    async def deploy(self, actions: list[TransformationAction]) -> DbtDeploymentResult:
        """Create/reuse models and run compile, build, and tests."""

    @abstractmethod
    async def refresh(self, model_names: list[str]) -> DbtDeploymentResult:
        """Run existing incremental models without invoking any planner."""


class LocalDbtCliAdapter(DbtAdapter):
    def __init__(
        self,
        project_dir: Path,
        executable: str = "dbt",
        profiles_dir: Path | None = None,
    ) -> None:
        self.project_dir = project_dir.resolve()
        self.executable = executable
        configured_profiles = os.environ.get("AIPLOT_DBT_PROFILES_DIR")
        self.profiles_dir = (
            profiles_dir or (Path(configured_profiles) if configured_profiles else self.project_dir)
        ).resolve()
        self.catalog = TransformationCatalog(self.project_dir)

    async def deploy(self, actions: list[TransformationAction]) -> DbtDeploymentResult:
        executable = self._resolve_executable()
        if executable is None:
            return DbtDeploymentResult(
                status="failed",
                error=(
                    f"dbt executable {self.executable!r} was not found; "
                    "no model files were changed."
                ),
            )
        changed = [action for action in actions if action.decision != "REUSE_EXISTING"]
        for action in changed:
            self._write_model(action.spec)
            self.catalog.save(action.spec)
        names = sorted({action.target_model for action in actions})
        commands = [
            [executable, "compile", "--profiles-dir", str(self.profiles_dir), "--select", *names],
            [executable, "build", "--profiles-dir", str(self.profiles_dir), "--select", *names],
            [executable, "test", "--profiles-dir", str(self.profiles_dir), "--select", *names],
        ]
        results: list[DbtCommandResult] = []
        for command in commands:
            result = await asyncio.to_thread(self._run, command)
            results.append(result)
            if result.return_code != 0:
                return DbtDeploymentResult(
                    status="failed",
                    model_names=names,
                    commands=results,
                    error="dbt validation failed.",
                )
        return DbtDeploymentResult(status="succeeded", model_names=names, commands=results)

    async def refresh(self, model_names: list[str]) -> DbtDeploymentResult:
        executable = self._resolve_executable()
        if executable is None:
            return DbtDeploymentResult(
                status="failed", model_names=model_names, error="dbt executable was not found."
            )
        command = [
            executable,
            "build",
            "--profiles-dir",
            str(self.profiles_dir),
            "--select",
            *sorted(set(model_names)),
        ]
        result = await asyncio.to_thread(self._run, command)
        return DbtDeploymentResult(
            status="succeeded" if result.return_code == 0 else "failed",
            model_names=sorted(set(model_names)),
            commands=[result],
            error=None if result.return_code == 0 else "Incremental dbt refresh failed.",
        )

    def _resolve_executable(self) -> str | None:
        resolved = shutil.which(self.executable)
        if resolved:
            return resolved
        alongside_python = Path(sys.executable).with_name(self.executable)
        return str(alongside_python) if alongside_python.is_file() else None

    def _run(self, command: list[str]) -> DbtCommandResult:
        completed = subprocess.run(
            command,
            cwd=self.project_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
        )
        return DbtCommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout[-20_000:],
            stderr=completed.stderr[-20_000:],
        )

    def _write_model(self, spec: TransformationSpec) -> None:
        model_dir = self.project_dir / "models" / "aiplot"
        test_dir = self.project_dir / "tests" / "aiplot"
        model_dir.mkdir(parents=True, exist_ok=True)
        test_dir.mkdir(parents=True, exist_ok=True)
        if not (self.project_dir / "dbt_project.yml").exists():
            _atomic_write(
                self.project_dir / "dbt_project.yml",
                "name: aiplot_analytics\nversion: '1.0'\nconfig-version: 2\n"
                "profile: aiplot_analytics\nmodel-paths: ['models']\ntest-paths: ['tests']\n",
            )
        _atomic_write(model_dir / f"{spec.model_name}.sql", _render_model(spec))
        _atomic_write(model_dir / f"{spec.model_name}.yml", _render_schema_tests(spec))
        _atomic_write(model_dir / f"{spec.model_name}_sources.yml", _render_sources(spec))
        _atomic_write(test_dir / f"{spec.model_name}_unique_grain.sql", _render_unique_test(spec))
        _atomic_write(
            test_dir / f"{spec.model_name}_reconciliation.sql",
            _render_reconciliation_test(spec),
        )
        range_metrics = {
            column
            for test in spec.tests
            if test.kind == "accepted_range"
            for column in test.columns
        }
        for metric in spec.metrics:
            if metric.name not in range_metrics:
                continue
            _atomic_write(
                test_dir / f"{spec.model_name}_{metric.name}_range.sql",
                _render_range_test(spec, metric.name),
            )


def _render_model(spec: TransformationSpec) -> str:
    source = spec.sources[0]
    config = f"materialized='{spec.materialization}'"
    if spec.materialization == "incremental":
        strategy = (
            "delete+insert"
            if spec.incremental_strategy == "delete_insert"
            else spec.incremental_strategy
        )
        config += f", incremental_strategy='{strategy}', unique_key={json.dumps(spec.unique_key)}"
    selections = [f'    "{column}"' for column in spec.dimensions]
    aggregation = {
        "sum": "sum",
        "avg": "avg",
        "min": "min",
        "max": "max",
        "count": "count",
        "count_distinct": "count_distinct",
    }
    for metric in spec.metrics:
        if metric.aggregation == "count_distinct":
            expression = f'count(distinct "{metric.source_column}")'
        else:
            expression = f'{aggregation[metric.aggregation]}("{metric.source_column}")'
        selections.append(f'    {expression} as "{metric.name}"')
    select_sql = ",\n".join(selections)
    source_reference = f"{{{{ source('{source.source_name}', '{source.relation_name}') }}}}"
    prefix = ""
    relation = source_reference
    where = ""
    if spec.materialization == "incremental" and spec.incremental_column:
        cutoff = f"{{{{ dbt.dateadd('day', -{spec.lookback_days}, 'current_timestamp') }}}}"
        if spec.incremental_strategy == "append":
            where = (
                "\n{% if is_incremental() %}\n"
                f'where "{spec.incremental_column}" > '
                f'(select max("{spec.incremental_column}") from {{{{ this }}}})\n'
                "{% endif %}"
            )
        elif spec.purpose == "cohort_retention":
            cohort_key = next(item for item in spec.grain if "cohort" in item.casefold())
            prefix = (
                f"with source_data as (select * from {source_reference})\n"
                "{% if is_incremental() %}\n, affected_cohorts as (\n"
                f'    select distinct "{cohort_key}" from source_data\n'
                f'    where "{spec.incremental_column}" >= {cutoff}\n'
                ")\n{% endif %}\n"
            )
            relation = "source_data"
            where = (
                "\n{% if is_incremental() %}\n"
                f'where "{cohort_key}" in (select "{cohort_key}" from affected_cohorts)\n'
                "{% endif %}"
            )
        else:
            where = (
                "\n{% if is_incremental() %}\n"
                f'where "{spec.incremental_column}" >= {cutoff}\n'
                "{% endif %}"
            )
    group_by = ""
    if spec.dimensions:
        indexes = range(1, len(spec.dimensions) + 1)
        group_by = "\ngroup by " + ", ".join(str(index) for index in indexes)
    late_note = (
        "-- Late-arriving activity is reprocessed with delete+insert "
        "across the affected lookback.\n"
        if spec.purpose == "cohort_retention"
        else ""
    )
    return (
        f"{{{{ config({config}) }}}}\n\n{late_note}{prefix}select\n{select_sql}\n"
        f"from {relation}"
        f"{where}{group_by}\n"
    )


def _render_schema_tests(spec: TransformationSpec) -> str:
    tested = sorted(
        set(spec.unique_key) | set(spec.dimensions) | {metric.name for metric in spec.metrics}
    )
    lines = ["version: 2", "models:", f"  - name: {spec.model_name}", "    columns:"]
    for column in tested:
        lines.extend([f"      - name: {column}", "        data_tests:", "          - not_null"])
    return "\n".join(lines) + "\n"


def _render_sources(spec: TransformationSpec) -> str:
    grouped: dict[str, list[str]] = {}
    for source in spec.sources:
        grouped.setdefault(source.source_name, []).append(source.relation_name)
    lines = ["version: 2", "sources:"]
    source_schema = os.environ.get("AIPLOT_DBT_SOURCE_SCHEMA", "raw")
    for source_name, relations in grouped.items():
        lines.extend([f"  - name: {source_name}", f"    schema: {source_schema}", "    tables:"])
        lines.extend(f"      - name: {relation}" for relation in sorted(set(relations)))
    return "\n".join(lines) + "\n"


def _render_unique_test(spec: TransformationSpec) -> str:
    if not spec.unique_key:
        return "-- Full-refresh aggregate has no declared incremental key.\nselect 1 where false\n"
    keys = ", ".join(f'"{item}"' for item in spec.unique_key)
    return (
        f"select {keys}, count(*) as duplicate_count\nfrom {{{{ ref('{spec.model_name}') }}}}\n"
        f"group by {keys}\nhaving count(*) > 1\n"
    )


def _render_reconciliation_test(spec: TransformationSpec) -> str:
    source = spec.sources[0]
    reconciliation = next((test for test in spec.tests if test.kind == "reconciliation"), None)
    tolerance = reconciliation.tolerance if reconciliation else 0.001
    checks = []
    for metric in spec.metrics:
        source_expression = (
            f'count(distinct "{metric.source_column}")'
            if metric.aggregation == "count_distinct"
            else f'{metric.aggregation}("{metric.source_column}")'
        )
        checks.append(
            f"select '{metric.name}' as metric\n"
            f"where abs((select sum(\"{metric.name}\") from {{{{ ref('{spec.model_name}') }}}}) - "
            f"(select {source_expression} from {{{{ source('{source.source_name}', "
            f"'{source.relation_name}') }}}})) > {tolerance or 0}"
        )
    return "\nunion all\n".join(checks) + "\n"


def _render_range_test(spec: TransformationSpec, metric: str) -> str:
    test = next(
        (item for item in spec.tests if item.kind == "accepted_range" and metric in item.columns),
        None,
    )
    predicates = [f'"{metric}" is null']
    if test and test.minimum is not None:
        predicates.append(f'"{metric}" < {test.minimum}')
    if test and test.maximum is not None:
        predicates.append(f'"{metric}" > {test.maximum}')
    return f"select * from {{{{ ref('{spec.model_name}') }}}}\nwhere {' or '.join(predicates)}\n"


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
