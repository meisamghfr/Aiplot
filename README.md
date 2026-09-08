# AI Plot

AI Plot is a governed analytics application that turns stakeholder conversations into validated analyses, interactive Plotly dashboards, reusable dbt marts, and Power BI refreshes.

It is separate from the authoritative Text-to-SQL service. AI Plot sends natural-language questions upstream and consumes only accepted, executed results. The browser, Dashboard Agent, and persistence planner never author SQL.

## What is implemented

- Persistent stakeholder conversations with role and decision context
- Single-analysis table, chart, KPI, and dashboard views
- Bounded multi-widget dashboard planning and execution
- `ad_hoc` or `persistent` classification
- Strict, reviewable `TransformationSpec` plans
- `REUSE_EXISTING`, `EXTEND_EXISTING`, and `CREATE_NEW` mart decisions
- Human approval before persistent dbt mutation
- Incremental merge, append, delete-insert, and full-refresh strategies
- Late-arriving cohort and retention handling
- dbt compile, build, grain, null, metric-range, and reconciliation checks
- Replaceable dbt and Power BI adapter boundaries
- Deterministic production refresh with zero LLM calls
- Included PostgreSQL demo warehouse with daily-sales and retention marts
- Explicit chatbot tool allowlist: `text_to_sql` and `create_pipeline`

## Architecture

```text
Stakeholder chat / analysis request
  -> AI Plot FastAPI
  -> DashboardSpec + persistence classification
  -> governed Text-to-SQL natural-language request
  -> accepted SQL result and executed rows
  -> deterministic visualization

Persistent dashboard
  -> TransformationSpec
  -> human approval
  -> dbt model reuse, extension, or creation
  -> dbt compile/build/test
  -> stable analytics mart
  -> Power BI semantic model/report publish or refresh
```

Chart controls and result-view changes are browser-local. They do not execute another query or alter accepted SQL.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A compatible running Text-to-SQL API
- PostgreSQL and a valid dbt profile for persistent model execution
- Optional Power BI workspace, service principal, and PBIX template

## Quick start

```bash
git clone https://github.com/meisamghfr/Aiplot.git
cd Aiplot
uv sync --dev
./scripts/bootstrap_warehouse.sh
```

Start the Text-to-SQL service separately, then run AI Plot:

```bash
TEXT2SQL_API_URL=http://127.0.0.1:8000 \
uv run uvicorn aiplot.app:app --reload --host 127.0.0.1 --port 8010
```

Open [http://127.0.0.1:8010](http://127.0.0.1:8010).

Configure the separate Text-to-SQL process to expose the warehouse:

```bash
TEXT2SQL_POSTGRES_DATABASES='{"warehouse":"postgresql://localhost/aiplot_warehouse"}' \
uv run uvicorn semantic_text2sql.api:create_app --factory --port 8000
```

Example requests:

```text
Show monthly revenue by customer segment.
Build a customer retention cohort dashboard.
Create a production revenue dashboard that refreshes daily.
```

## Configuration

`.env.example` documents the supported settings. AI Plot does not automatically load `.env` files.

| Variable | Purpose | Default |
|---|---|---|
| `TEXT2SQL_API_URL` | Authoritative Text-to-SQL origin | `http://127.0.0.1:8000` |
| `AIPLOT_MAX_PLOT_ROWS` | Maximum rows passed to Plotly | `300` |
| `AIPLOT_TEXT2SQL_TIMEOUT_SECONDS` | Upstream timeout | `180` |
| `AIPLOT_DBT_PROJECT_DIR` | dbt project and transformation catalog | `dbt_analytics` |
| `AIPLOT_STATE_DIR` | Conversations, plans, and deployment state | `.aiplot` |
| `AIPLOT_POWERBI_ARTIFACT_DIR` | Fallback Power BI manifests | `powerbi_artifacts` |

Sampling affects only plotted rows. It never changes accepted query results or stored SQL provenance.

## Stakeholder conversations

Each thread stores the stakeholder name, role, decision objective, governed database/model configuration, messages, and analysis responses. Threads survive restarts under `${AIPLOT_STATE_DIR}/stakeholder_threads`.

A message enters the same single-analysis or dashboard route as a direct request. It does not introduce another SQL-generation path.

The conversation runtime chooses only from two tools. `text_to_sql` executes governed read-only analysis. `create_pipeline` prepares a persistent dashboard and transformation plan, then stops with `awaiting_human_approval`. Only the separate approval request can invoke dbt mutation.

For an underspecified request such as “plot the number of clients,” the agent first reads capability metadata—not warehouse rows—and pauses with `awaiting_clarification`. It maps business terms to available entities, offers only existing date fields and dimensions, and asks for the date window, time grain, and optional breakdown. The stakeholder's reply is combined with the original intent into the analytical question passed to the Text-to-SQL tool.

## Included PostgreSQL warehouse

`warehouse/bootstrap.sql` creates an isolated `aiplot_warehouse` layout with `raw`, `staging`, and `analytics` schemas. The repeatable demo contains 120 customers and 1,500 orders. The dbt project builds:

- `analytics.mart_daily_sales`
- `analytics.mart_retention_cohort`

The retention model reprocesses complete cohorts affected by activity arriving inside a 90-day lookback. Run `scripts/bootstrap_warehouse.sh` to create or reload the demo and execute every dbt model and test.

## Dashboard safeguards

- Maximum 10 widgets, including at most 6 KPIs and 6 charts
- Maximum 8 unique Text-to-SQL requests and 3 concurrent calls
- Unsupported requirements are reported rather than silently substituted
- Failed widgets remain visible while successful widgets continue rendering
- Accepted SQL and execution provenance remain inspectable per widget

## Persistent analytics and dbt

Language such as `refresh daily`, `weekly monitoring`, `production dashboard`, or `ongoing dashboard` classifies a dashboard as persistent. Normal analysis remains ad hoc.

Persistent requests produce a strict `TransformationSpec` containing model name, sources, grain, dimensions, metrics, materialization, incremental strategy, unique key, incremental column, late-arriving policy, and tests.

Before mutation, the planner compares the specification with the catalog and chooses `REUSE_EXISTING`, `EXTEND_EXISTING`, or `CREATE_NEW`. Compatible widgets share a mart; AI Plot does not create one model per widget.

No model is written until a person approves the plan:

```text
POST /api/persistence/plans/{plan_id}/approve
```

The default `LocalDbtCliAdapter` renders approved models and executes `dbt compile`, `dbt build`, and `dbt test`. Tests cover nulls, unique grain, metric ranges, and source reconciliation.

Standard incremental marts use bounded-lookback append or merge behavior. Cohort and retention marts use merge or delete-insert and recompute cohorts affected by late-arriving activity. Sources without safe incremental keys fall back to table/full-refresh behavior.

Production refresh requires no planning or LLM call:

```text
new data -> dbt incremental build -> analytics mart -> Power BI refresh
POST /api/persistence/plans/{plan_id}/refresh
```

Scheduling and Airflow are intentionally out of scope.

## Power BI

Both integrations implement `PowerBIAdapter`:

- `PowerBIRestAdapter` uses a service principal to import or replace a PBIX template, records its semantic-model ID, and requests dataset refreshes.
- `PowerBIManifestAdapter` writes a portable semantic-model/report manifest when live configuration is unavailable.

For live service integration, configure:

```text
POWERBI_TENANT_ID
POWERBI_CLIENT_ID
POWERBI_CLIENT_SECRET
POWERBI_WORKSPACE_ID
POWERBI_PBIX_TEMPLATE_PATH
```

The PBIX template must connect to stable analytics marts. Credentials remain server-side. Power BI Desktop authoring requires Windows; macOS execution uses the Power BI service API.

## Development and verification

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src/aiplot
deno check web/app.js
uv run dbt --version
```

Verified baseline: **44 tests passing**, Ruff clean, strict mypy clean, frontend JavaScript validation clean, and dbt CLI available.

## Current limitations

- Text-to-SQL and configured databases run separately.
- Live dbt execution requires a profile and reachable PostgreSQL target.
- Live Power BI publication requires tenant credentials, permissions, and a PBIX template.
- Plotly.js and interface fonts load from public CDNs.
- Conversation state uses atomic local JSON rather than a shared multi-instance database.
- Production scheduling, Airflow, and Windows Power BI Desktop authoring are not included.

## Security boundaries

- No arbitrary SQL input in the browser
- No browser-side database or Power BI credentials
- No Dashboard Agent-generated SQL
- No persistent mutation before explicit approval
- No LLM dependency during production refresh
- Runtime state, environment files, virtual environments, and build artifacts are excluded from Git

## License

No open-source license has been selected. Until one is added, the repository remains all rights reserved.
