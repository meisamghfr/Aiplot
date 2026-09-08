"""Convert dashboard widgets into analytical questions, never SQL."""

from __future__ import annotations

from aiplot.dashboard.models import AgentDashboardSpec, DashboardWidget


class QueryPlanner:
    def add_questions(self, spec: AgentDashboardSpec) -> AgentDashboardSpec:
        return spec.model_copy(
            update={"widgets": [self.with_question(widget) for widget in spec.widgets]}
        )

    def with_question(self, widget: DashboardWidget) -> DashboardWidget:
        if widget.analytical_question:
            return widget
        assert widget.metric is not None
        if widget.kind == "kpi":
            if widget.comparison == "previous_month":
                question = (
                    f"Return {widget.metric} for the latest complete month and the previous month."
                )
            elif widget.comparison == "previous_year":
                question = (
                    f"Return {widget.metric} for the latest complete year and the previous year."
                )
            else:
                question = f"Return the current total {widget.metric}."
        elif widget.kind == "chart":
            dimensions = " and ".join(widget.dimensions)
            question = f"Show {widget.metric} by {dimensions}."
        else:
            question = f"Show the records needed for {widget.title}."
        return widget.model_copy(update={"analytical_question": question})
