"""Convert a dashboard plan and advertised capabilities into reusable mart specs."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from aiplot.dashboard.models import AnalyticsCapabilities, DashboardPlan
from aiplot.persistence.catalog import TransformationCatalog
from aiplot.persistence.models import (
    TransformationAction,
    TransformationMetric,
    TransformationSource,
    TransformationSpec,
    TransformationTest,
)


class TransformationPlanner:
    def __init__(self, catalog: TransformationCatalog) -> None:
        self.catalog = catalog

    def plan(
        self,
        dashboard: DashboardPlan,
        capabilities: AnalyticsCapabilities,
        request: str,
    ) -> list[TransformationAction]:
        metrics = _metric_sources(capabilities)
        requested = {
            widget.metric.casefold()
            for widget in dashboard.spec.widgets
            if widget.metric is not None
        }
        grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for name in sorted(requested):
            source = metrics.get(name)
            if source is not None:
                grouped[source[0]].append((name, source[1]))

        actions: list[TransformationAction] = []
        for table, table_metrics in list(grouped.items())[:8]:
            spec = _build_spec(
                dashboard,
                capabilities,
                request,
                table,
                table_metrics,
            )
            decision, reason, existing = self.catalog.decide(spec)
            target = existing.model_name if existing is not None else spec.model_name
            if decision == "REUSE_EXISTING" and existing is not None:
                spec = existing
            elif decision == "EXTEND_EXISTING" and existing is not None:
                spec = _extend(existing, spec)
            actions.append(
                TransformationAction(
                    decision=decision,
                    target_model=target,
                    reason=reason,
                    spec=spec.model_copy(update={"model_name": target}),
                )
            )
        if not actions:
            raise ValueError("No dashboard metric maps to a supported transformation source.")
        return actions


def _metric_sources(capabilities: AnalyticsCapabilities) -> dict[str, tuple[str, str]]:
    measure_lookup = {
        (item.table.casefold(), item.name.casefold()): (item.table, item.name)
        for item in capabilities.measures
    }
    result = {item.name.casefold(): (item.table, item.name) for item in capabilities.measures}
    for metric in capabilities.available_kpis:
        candidates = []
        for qualified in metric.columns:
            if "." not in qualified:
                continue
            table, column = qualified.split(".", 1)
            resolved = measure_lookup.get((table.casefold(), column.casefold()))
            if resolved is not None:
                candidates.append(resolved)
        if len(set(candidates)) == 1:
            table, column = candidates[0]
            result[metric.name.casefold()] = (table, column)
            for synonym in metric.synonyms:
                result.setdefault(synonym.casefold(), (table, column))
    return result


def _build_spec(
    dashboard: DashboardPlan,
    capabilities: AnalyticsCapabilities,
    request: str,
    table: str,
    metrics: list[tuple[str, str]],
) -> TransformationSpec:
    dashboard_dimensions = {
        dimension.casefold() for widget in dashboard.spec.widgets for dimension in widget.dimensions
    }
    dimensions = sorted(
        {
            field.name
            for field in [*capabilities.dimensions, *capabilities.time_columns]
            if field.table == table and field.name.casefold() in dashboard_dimensions
        }
    )
    table_times = [item.name for item in capabilities.time_columns if item.table == table]
    cohort = bool(re.search(r"\b(cohort|retention)\b", request, re.I))
    cohort_columns = [item for item in table_times if "cohort" in item.casefold()]
    updated_columns = [
        item
        for item in table_times
        if any(token in item.casefold() for token in ("updated", "loaded", "modified", "ingested"))
    ]
    if cohort and not cohort_columns:
        raise ValueError(
            f"Retention persistence requires an advertised cohort column on source {table}."
        )
    if cohort_columns and cohort_columns[0] not in dimensions:
        dimensions.insert(0, cohort_columns[0])
    incremental_column = (
        updated_columns[0] if updated_columns else (table_times[-1] if table_times else None)
    )
    if (
        incremental_column
        and incremental_column not in updated_columns
        and incremental_column not in dimensions
    ):
        dimensions.insert(0, incremental_column)
    materialization: Literal["incremental", "table"] = (
        "incremental" if incremental_column and dimensions else "table"
    )
    if materialization == "table":
        incremental_column = None
    strategy: Literal["append", "merge", "delete_insert", "full_refresh"] = (
        "delete_insert" if cohort else ("merge" if incremental_column else "full_refresh")
    )
    unique_key = dimensions.copy() if materialization == "incremental" else []
    metric_specs = [
        TransformationMetric(
            name=_identifier(name),
            source_column=column,
            aggregation="sum",
        )
        for name, column in metrics
    ]
    tests = [
        TransformationTest(kind="not_null", columns=unique_key or [metric_specs[0].name]),
        TransformationTest(kind="unique_combination", columns=unique_key or [metric_specs[0].name]),
        TransformationTest(
            kind="reconciliation", columns=[item.name for item in metric_specs], tolerance=0.001
        ),
    ]
    tests.extend(
        TransformationTest(kind="accepted_range", columns=[item.name], minimum=0)
        for item in metric_specs
    )
    return TransformationSpec(
        model_name=_identifier(f"mart_{dashboard.spec.title}_{table}")[:63],
        sources=[
            TransformationSource(source_name=_identifier(capabilities.db_id), relation_name=table)
        ],
        grain=dimensions,
        dimensions=dimensions,
        metrics=metric_specs,
        materialization=materialization,
        incremental_strategy=strategy,
        unique_key=unique_key,
        incremental_column=incremental_column,
        lookback_days=90 if cohort else (3 if incremental_column else 0),
        purpose="cohort_retention" if cohort else "standard",
        late_arriving_policy=(
            "recompute_affected_cohorts"
            if cohort
            else ("lookback_window" if incremental_column else "not_applicable")
        ),
        tests=tests,
    )


def _extend(existing: TransformationSpec, proposed: TransformationSpec) -> TransformationSpec:
    metrics = {item.name: item for item in existing.metrics}
    metrics.update({item.name: item for item in proposed.metrics})
    tests = {item.model_dump_json(): item for item in [*existing.tests, *proposed.tests]}
    return existing.model_copy(
        update={
            "dimensions": sorted(set(existing.dimensions) | set(proposed.dimensions)),
            "metrics": list(metrics.values()),
            "tests": list(tests.values()),
        }
    )


def _identifier(value: str) -> str:
    result = "_".join(re.findall(r"[a-z0-9]+", value.casefold())).strip("_")
    if not result or not result[0].isalpha():
        result = f"metric_{result}"
    return result
