from aiplot.stakeholders.tools import pipeline_request, select_tool


def test_tool_selection_is_allowlisted_and_deterministic() -> None:
    assert select_tool("Show revenue by segment") == "text_to_sql"
    assert select_tool("Create a daily revenue pipeline") == "create_pipeline"
    assert select_tool("Build an ongoing dashboard") == "create_pipeline"


def test_pipeline_request_routes_through_existing_dashboard_path() -> None:
    request = pipeline_request("Create a daily revenue pipeline")
    assert "dashboard" in request
    assert "Create a daily revenue pipeline" in request
