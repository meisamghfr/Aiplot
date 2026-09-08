"""Capability-grounded deterministic dashboard agent."""

from __future__ import annotations

import re

from aiplot.dashboard.models import (
    AgentDashboardSpec,
    AnalyticsCapabilities,
    BusinessMetric,
    DashboardPlan,
    DashboardWidget,
)

_TOKEN = re.compile(r"[a-z0-9]+")
_CHART_TYPES = ("line", "bar", "scatter", "pie")
_REQUESTED_ITEM = re.compile(r"\b(?:with|include|including)\s+([^.;]+)", re.I)
_GENERIC = {"business", "performance", "sales", "monitor", "monitoring", "dashboard"}


class DashboardAgent:
    """Selects only capabilities advertised by the authoritative service."""

    def plan(self, request: str, capabilities: AnalyticsCapabilities) -> DashboardPlan:
        catalog = _metric_catalog(capabilities)
        explicit = _explicit_metrics(request, catalog)
        requested_phrases = _requested_phrases(request)
        unsupported = [
            phrase
            for phrase in requested_phrases
            if not _phrase_supported(phrase, catalog) and _tokens(phrase) - _GENERIC
        ]
        selected = explicit or list(catalog.values())[:3]
        if not selected:
            raise ValueError("No supported measures or KPIs are available for dashboard planning.")

        widgets: list[DashboardWidget] = []
        used_ids: set[str] = set()
        for metric in selected[:6]:
            metric_id = _unique_id(_slug(metric.name), used_ids)
            widgets.append(
                DashboardWidget(
                    id=metric_id,
                    kind="kpi",
                    title=_title(metric.name),
                    metric=metric.name,
                    comparison=("previous_month" if capabilities.time_columns else "none"),
                    visualization="kpi",
                )
            )

        chart_requests = _explicit_chart_requests(request, selected, capabilities)
        if chart_requests:
            for metric, dimension, chart_type in chart_requests:
                if len(widgets) >= 10:
                    break
                widgets.append(
                    DashboardWidget(
                        id=_unique_id(f"{_slug(metric.name)}_{chart_type}", used_ids),
                        kind="chart",
                        title=f"{_title(metric.name)} by {_title(dimension)}",
                        metric=metric.name,
                        dimensions=[dimension],
                        visualization=chart_type,  # type: ignore[arg-type]
                    )
                )
        elif not explicit:
            primary = selected[0]
            if capabilities.time_columns:
                dimension = capabilities.time_columns[0].name
                widgets.append(
                    DashboardWidget(
                        id=_unique_id(f"{_slug(primary.name)}_trend", used_ids),
                        kind="chart",
                        title=f"{_title(primary.name)} Trend",
                        metric=primary.name,
                        dimensions=[dimension],
                        visualization="line",
                    )
                )
            if capabilities.dimensions and len(widgets) < 10:
                dimension = capabilities.dimensions[0].name
                widgets.append(
                    DashboardWidget(
                        id=_unique_id(f"{_slug(primary.name)}_by_{_slug(dimension)}", used_ids),
                        kind="chart",
                        title=f"{_title(primary.name)} by {_title(dimension)}",
                        metric=primary.name,
                        dimensions=[dimension],
                        visualization="bar",
                    )
                )

        title = _dashboard_title(request)
        return DashboardPlan(
            spec=AgentDashboardSpec(
                title=title,
                objective=f"Monitor supported indicators for {title.casefold()}.",
                widgets=widgets,
            ),
            unsupported_requirements=unsupported,
        )


def _metric_catalog(capabilities: AnalyticsCapabilities) -> dict[str, BusinessMetric]:
    result: dict[str, BusinessMetric] = {}
    for item in capabilities.available_kpis:
        result[item.name.casefold()] = item
    for field in capabilities.measures:
        key = field.name.casefold()
        result.setdefault(
            key,
            BusinessMetric(
                name=field.name,
                description=field.description or f"Numeric measure {field.name}",
                columns=[f"{field.table}.{field.name}"],
                source="profile" if field.semantic_type else "schema",
            ),
        )
    return result


def _explicit_metrics(request: str, catalog: dict[str, BusinessMetric]) -> list[BusinessMetric]:
    normalized = " ".join(_TOKEN.findall(request.casefold()))
    matches: list[BusinessMetric] = []
    for metric in catalog.values():
        names = [metric.name, *metric.synonyms]
        if any(_contains_phrase(normalized, name) for name in names):
            matches.append(metric)
    return matches


def _explicit_chart_requests(
    request: str,
    metrics: list[BusinessMetric],
    capabilities: AnalyticsCapabilities,
) -> list[tuple[BusinessMetric, str, str]]:
    lowered = request.casefold()
    chart_type = next((item for item in _CHART_TYPES if f"{item} chart" in lowered), None)
    time_requested = any(item in lowered for item in ("monthly", "month", "trend", "over time"))
    if chart_type is None and not time_requested:
        return []
    dimension = None
    if time_requested and capabilities.time_columns:
        dimension = capabilities.time_columns[0].name
    if dimension is None:
        dimension = next(
            (item.name for item in capabilities.dimensions if item.name.casefold() in lowered),
            None,
        )
    if dimension is None:
        return []
    target = next(
        (metric for metric in metrics if _contains_phrase(lowered, metric.name)), metrics[0]
    )
    return [(target, dimension, chart_type or "line")]


def _requested_phrases(request: str) -> list[str]:
    match = _REQUESTED_ITEM.search(request)
    if not match:
        return []
    cleaned = re.sub(
        r"\b(?:as a|in a)\s+(?:line|bar|scatter|pie)\s+chart\b",
        "",
        match.group(1),
        flags=re.I,
    )
    cleaned = re.sub(r"\b(?:monthly|yearly|weekly|daily)\b", "", cleaned, flags=re.I)
    return [item.strip(" ,") for item in re.split(r",|\band\b", cleaned) if item.strip(" ,")]


def _phrase_supported(phrase: str, catalog: dict[str, BusinessMetric]) -> bool:
    return any(
        _contains_phrase(phrase, name) or _contains_phrase(name, phrase)
        for metric in catalog.values()
        for name in [metric.name, *metric.synonyms]
    )


def _contains_phrase(haystack: str, needle: str) -> bool:
    normalized = " ".join(_TOKEN.findall(needle.casefold()))
    return bool(normalized) and f" {normalized} " in f" {haystack} "


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(value.casefold()))


def _slug(value: str) -> str:
    result = "_".join(_TOKEN.findall(value.casefold()))[:64]
    return result if result and result[0].isalpha() else f"metric_{result}"[:64]


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base[:64]
    suffix = 2
    while candidate in used:
        marker = f"_{suffix}"
        candidate = f"{base[: 64 - len(marker)]}{marker}"
        suffix += 1
    used.add(candidate)
    return candidate


def _title(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _dashboard_title(request: str) -> str:
    lowered = request.casefold()
    if "sales" in lowered:
        return "Sales Dashboard"
    if "business performance" in lowered:
        return "Business Performance"
    return "Performance Dashboard"
