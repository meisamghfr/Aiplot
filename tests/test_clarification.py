from aiplot.dashboard.models import AnalyticsCapabilities
from aiplot.stakeholders.clarification import clarification_for


def test_clarification_options_come_only_from_capabilities() -> None:
    capabilities = AnalyticsCapabilities.model_validate(
        {
            "db_id": "warehouse",
            "dialect": "postgres",
            "tables": ["customers"],
            "business_entities": ["customers"],
            "dimensions": [
                {"name": "customer_id", "table": "customers", "data_type": "bigint"},
                {"name": "segment", "table": "customers", "data_type": "text"},
            ],
            "time_columns": [{"name": "signup_date", "table": "customers", "data_type": "date"}],
        }
    )

    pending = clarification_for("I need a plot for number of clients", capabilities, "text_to_sql")

    assert pending is not None
    prompt = " ".join(pending.questions)
    assert "customer_id" in prompt
    assert "signup_date" in prompt
    assert "segment" in prompt
    assert "app category" not in prompt
