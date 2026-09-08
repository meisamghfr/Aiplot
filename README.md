# AI Plot

AI Plot is a separate visualization application for
`semantic_text2sql_ideal`. It does not generate, validate, or execute SQL.
It sends a natural-language question to the existing Text-to-SQL API, accepts
only its validated and executed result, then builds a deterministic Plotly
chart or compact dashboard from those returned rows.

## Architecture

```text
Browser -> Aiplot FastAPI -> semantic_text2sql_ideal /api/chat
                                -> accepted SQL + executed rows
        <- validated ChartSpec / DashboardSpec + bounded plotting rows
Browser -> local Plotly rendering and local view/chart controls
```

Changing the result view, chart type, axes, or grouping is browser-local. It
does not call the Text-to-SQL API again and never changes the accepted SQL.

## Run

Start the authoritative Text-to-SQL application in its own terminal:

```bash
cd /Users/meisam/Documents/text-to-sql/semantic_text2sql_ideal
uv sync
uv run uvicorn semantic_text2sql.api:create_app --factory --host 127.0.0.1 --port 8000
```

Start Aiplot in a second terminal:

```bash
cd /Users/meisam/Documents/Aiplot
uv sync --dev
TEXT2SQL_API_URL=http://127.0.0.1:8000 uv run uvicorn aiplot.app:app --reload --port 8010
```

Open `http://127.0.0.1:8010`, choose one of the databases and configured
models reported by Text-to-SQL, then ask a question appropriate to that data.
For a revenue database, an example is: `Show monthly revenue by customer segment.`

## Environment

- `TEXT2SQL_API_URL`: Text-to-SQL origin; default `http://127.0.0.1:8000`
- `AIPLOT_MAX_PLOT_ROWS`: maximum rows sent to Plotly; default `300`. Sampling
  affects only the chart and never alters SQL or the result table.
- `AIPLOT_TEXT2SQL_TIMEOUT_SECONDS`: upstream request timeout; default `180`
- `AIPLOT_DBT_PROJECT_DIR`: dbt project/catalog directory; default `dbt_analytics`
- `AIPLOT_STATE_DIR`: plan and deployment records; default `.aiplot`
- `AIPLOT_POWERBI_ARTIFACT_DIR`: fallback Power BI manifests; default
  `powerbi_artifacts`

## Tests

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

Plotly.js and the interface fonts are loaded from public CDNs. An offline
deployment should vendor those static assets. The first version supports one
accepted result at a time, up to three dashboard charts, four KPIs, and a
500-row result table. It intentionally does not offer arbitrary SQL input,
database credentials, unrestricted dashboard composition, or LLM-authored
JavaScript.

## Agent-driven dashboards

Requests containing an explicit dashboard intent follow a separate bounded
path. Aiplot retrieves compact analytical capabilities from
`GET /api/databases/{db_id}/analytics-capabilities`, selects only advertised
metrics, produces a strict dashboard plan, converts each widget into a natural
language question, and sends each unique question through Text-to-SQL.

Dashboard planning is currently deterministic. It never emits SQL, code, or
Plotly configuration. Explicit supported metrics and chart types in the user
request take priority over automatic choices. Unsupported requested KPIs are
reported in the dashboard plan and are not silently substituted.

Each dashboard is limited to 10 widgets, including at most 6 KPIs and 6 charts.
Execution is limited to 8 unique Text-to-SQL calls with at most 3 running
concurrently. A failed widget remains visible as unavailable while successful
widgets continue rendering. Query provenance is available under each widget's
details section.

## Persistent analytics

Dashboard requests containing operational language such as `refresh daily`,
`weekly monitoring`, `production dashboard`, or `ongoing dashboard` are
classified as persistent. A normal dashboard remains ad hoc.

Persistent requests return a strict, reviewable transformation plan. They do
not immediately create or modify dbt models. The browser exposes a separate
approval action backed by:

```text
POST /api/persistence/plans/{plan_id}/approve
```

Only an explicitly approved plan reaches the configured `DbtAdapter`. The
default adapter uses the local dbt CLI, creates or updates grouped analytics
marts, then runs `dbt compile`, `dbt build`, and `dbt test`. It generates
not-null, unique-grain, accepted-range, and source-reconciliation tests.

The planner selects `REUSE_EXISTING`, `EXTEND_EXISTING`, or `CREATE_NEW` by
comparing sources, grain, dimensions, and metrics with the transformation
catalog. Widgets using the same source and grain share a mart instead of
producing one model per widget.

Standard incremental marts use merge and a bounded lookback. Retention/cohort
marts require delete-insert or merge behavior and recompute the complete
history of cohorts affected by late-arriving activity inside the lookback.
Sources without a usable incremental/time key use table/full-refresh behavior.

After successful dbt validation, the replaceable `PowerBIAdapter` receives
stable mart names. Without a live Power BI MCP connection, the default adapter
writes an MCP-ready semantic-model/report manifest under `powerbi_artifacts/`;
it never points Power BI at raw generated SQL.

Production refresh is deterministic:

```text
POST /api/persistence/plans/{plan_id}/refresh
```

It performs only `dbt build` followed by Power BI refresh and reports zero LLM
calls. Scheduling is deliberately out of scope. The local adapter requires a
working `dbt` executable and profile; actual Power BI publication requires a
live MCP-backed `PowerBIAdapter` implementation.
## Stakeholder conversations

The web application now supports persistent stakeholder threads. Each thread records the stakeholder role, decision objective, governed database/model configuration, messages, and the complete analysis response. Messages still enter the existing single-analysis or dashboard route; the browser never authors SQL.

Thread state is stored under `${AIPLOT_STATE_DIR}/stakeholder_threads` and survives application restarts.

## dbt and Power BI

`dbt-core` and `dbt-postgres` are project dependencies, so `uv sync --dev` installs a local `dbt` CLI used by the existing replaceable `DbtAdapter`.

Power BI has two modes behind the same adapter boundary:

- With all `POWERBI_*` variables in `.env.example` configured, Aiplot authenticates with a service principal, imports/overwrites the configured PBIX template in the workspace, captures its semantic-model id, and requests refreshes after successful dbt builds.
- Without live credentials or a PBIX template, it writes the existing portable manifest without contacting Power BI.

The PBIX template must already connect to the stable analytics mart. Credentials stay server-side. Power BI Desktop report authoring is a Windows workflow; on macOS, publishing and refresh are handled through the Power BI service API.
