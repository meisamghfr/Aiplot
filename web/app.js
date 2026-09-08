"use strict";

const state = { response: null, autoSpec: null, chartSpec: null, config: null, activeView: "chart", threads: [], activeThread: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const palette = ["#176b55", "#e8793e", "#507c91", "#8c6c9c", "#bd9b45", "#9a5550"];

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadConfig();
  loadThreads();
});

function bindEvents() {
  $("#analyzeForm").addEventListener("submit", submitAnalysis);
  $$(".view-tabs button").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  ["#chartTypeSelect", "#xAxisSelect", "#yAxisSelect", "#colorSelect"].forEach((selector) => {
    $(selector).addEventListener("change", applyManualChartSettings);
  });
  $("#resetChartButton").addEventListener("click", resetChart);
  $("#copySqlButton").addEventListener("click", copySql);
  $("#newAnalysisButton").addEventListener("click", () => {
    $("#questionInput").focus();
    window.scrollTo({ top: 200, behavior: "smooth" });
  });
  $("#newDashboardButton").addEventListener("click", focusQuestion);
  $("#approvePersistenceButton").addEventListener("click", approvePersistence);
  $("#refreshPersistenceButton").addEventListener("click", refreshPersistence);
  $("#newThreadButton").addEventListener("click", () => $("#threadDialog").showModal());
  $("#cancelThreadButton").addEventListener("click", () => $("#threadDialog").close());
  $("#closeThreadButton").addEventListener("click", () => selectThread(null));
  $("#threadForm").addEventListener("submit", createThread);
}

async function loadThreads() {
  try {
    const response = await fetch("/api/stakeholders/threads");
    if (!response.ok) throw new Error("Conversation history could not be loaded.");
    state.threads = await response.json(); renderThreadList();
  } catch (error) { showError(error.message); }
}

function renderThreadList() {
  if (!state.threads.length) { $("#threadList").innerHTML = '<p class="thread-empty">No conversations yet.</p>'; return; }
  $("#threadList").replaceChildren(...state.threads.map((thread) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "thread-item";
    if (state.activeThread?.id === thread.id) button.classList.add("active");
    const name = document.createElement("strong"); name.textContent = thread.stakeholder_name;
    const detail = document.createElement("span"); detail.textContent = `${thread.stakeholder_role} · ${thread.messages.length} messages`;
    button.append(name, detail); button.addEventListener("click", () => openThread(thread.id)); return button;
  }));
}

async function createThread(event) {
  event.preventDefault();
  const [provider, model] = $("#modelSelect").value.split("|");
  const payload = { stakeholder_name: $("#stakeholderName").value.trim(), stakeholder_role: $("#stakeholderRole").value.trim(), objective: $("#stakeholderObjective").value.trim(), db_id: $("#databaseSelect").value, provider, model, context_mode: $("#contextSelect").value };
  try {
    const response = await fetch("/api/stakeholders/threads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const body = await response.json(); if (!response.ok) throw new Error(body.detail || "Conversation could not be created.");
    state.threads.unshift(body); $("#threadDialog").close(); $("#threadForm").reset(); selectThread(body); $("#questionInput").focus();
  } catch (error) { showError(error.message); }
}

async function openThread(id) {
  try { const response = await fetch(`/api/stakeholders/threads/${id}`); const body = await response.json(); if (!response.ok) throw new Error(body.detail || "Conversation could not be opened."); selectThread(body); }
  catch (error) { showError(error.message); }
}

function selectThread(thread) {
  state.activeThread = thread; renderThreadList();
  $("#conversationName").textContent = thread ? thread.stakeholder_name : "Direct analysis";
  $("#conversationContext").textContent = thread ? `${thread.stakeholder_role} · ${thread.objective}` : "Start a stakeholder thread or ask a one-off question below.";
  $("#closeThreadButton").hidden = !thread; $("#composerKicker").textContent = thread ? "Conversation message" : "New analysis";
  $("#queryTitle").textContent = thread ? `Ask with ${thread.stakeholder_name}'s context` : "What do you want to understand?";
  $("#submitLabel").textContent = thread ? "Send" : "Analyze";
  ["#databaseSelect", "#modelSelect", "#contextSelect"].forEach((selector) => $(selector).disabled = Boolean(thread));
  renderConversation(thread?.messages || []);
}

function renderConversation(messages) {
  if (!messages.length) { $("#conversationMessages").innerHTML = '<div class="conversation-placeholder">No messages yet. Ask the first analytical question below.</div>'; return; }
  $("#conversationMessages").replaceChildren(...messages.map((message) => { const item = document.createElement("article"); item.className = `message ${message.role}`; const role = document.createElement("span"); role.textContent = message.role === "user" ? "Stakeholder" : "AI Plot"; const content = document.createElement("p"); content.textContent = message.content; item.append(role, content); return item; }));
  $("#conversationMessages").scrollTop = $("#conversationMessages").scrollHeight;
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Configuration could not be loaded.");
    state.config = body;
    fillSelect($("#databaseSelect"), body.databases.filter((item) => item.configured), (item) => item.db_id, (item) => `${item.db_id} · ${item.dialect}`);
    fillSelect($("#modelSelect"), body.models.filter((item) => item.configured), (item) => `${item.provider}|${item.model}`, (item) => item.model);
    const ready = $("#databaseSelect").options.length > 0 && $("#modelSelect").options.length > 0;
    $(".analyze-button").disabled = !ready;
    setServiceStatus(body.text2sql_online ? "online" : "offline", body.text2sql_online ? "SQL engine online" : "SQL engine unavailable");
  } catch (error) {
    setServiceStatus("offline", "SQL engine unavailable");
    showError(error.message);
  }
}

function fillSelect(select, items, value, label) {
  select.replaceChildren(...items.map((item) => new Option(label(item), value(item))));
  select.disabled = items.length === 0;
}

async function submitAnalysis(event) {
  event.preventDefault();
  const [provider, model] = $("#modelSelect").value.split("|");
  const payload = {
    question: $("#questionInput").value.trim(), db_id: $("#databaseSelect").value,
    provider, model, context_mode: $("#contextSelect").value,
  };
  if (!payload.question) return;
  setLoading(true);
  try {
    // This is the only result-producing request in the frontend. View and chart controls never call it.
    const requestBody = state.activeThread ? { message: payload.question } : payload;
    const options = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody) };
    const response = state.activeThread
      ? await fetch(`/api/stakeholders/threads/${state.activeThread.id}/messages`, options)
      : await fetch("/api/analyze", options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Aiplot could not process the request.");
    if (state.activeThread) { state.activeThread = body.thread; const index = state.threads.findIndex((item) => item.id === body.thread.id); if (index >= 0) state.threads.splice(index, 1); state.threads.unshift(body.thread); renderThreadList(); renderConversation(body.thread.messages); $("#questionInput").value = ""; }
    const analysis = state.activeThread ? body.analysis : body;
    if (analysis.intent === "dashboard") {
      if (!analysis.dashboard) throw new Error(analysis.message || "The dashboard could not be planned.");
      state.response = analysis;
      renderAgentDashboard();
      return;
    }
    if (!analysis.result || !analysis.visualization) throw new Error(analysis.message || "No executable result was returned.");
    state.response = analysis;
    state.autoSpec = analysis.visualization.chart;
    state.chartSpec = state.autoSpec ? structuredClone(state.autoSpec) : createFallbackSpec(analysis.result, analysis.visualization.columns);
    renderWorkspace();
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

function renderWorkspace() {
  const { result, visualization, message } = state.response;
  $("#errorPanel").hidden = true;
  $("#agentDashboard").hidden = true;
  $("#workspace").hidden = false;
  $("#resultQuestion").textContent = result.question;
  $("#resultStatus").textContent = result.status === "ACCEPTED" ? "Query accepted" : result.status;
  $("#rowCount").textContent = `${formatInteger(result.row_count)} ${result.row_count === 1 ? "row" : "rows"}${result.truncated ? " · truncated by source" : ""}`;
  $("#sqlCode").textContent = result.accepted_sql;
  const notice = visualization.message || message;
  $("#resultMessage").textContent = notice || "";
  $("#resultMessage").hidden = !notice;
  renderTable($("#resultTable"), result.columns, result.rows);
  renderTable($("#dashboardTable"), result.columns, result.rows);
  setupControls();
  renderDashboard();
  const initialView = state.autoSpec ? "chart" : (result.rows.length === 1 ? "dashboard" : "table");
  switchView(initialView);
  $("#workspace").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderAgentDashboard() {
  const execution = state.response.dashboard;
  const { spec, unsupported_requirements: unsupported } = execution.plan;
  $("#errorPanel").hidden = true;
  $("#workspace").hidden = true;
  $("#agentDashboard").hidden = false;
  $("#agentDashboardTitle").textContent = spec.title;
  $("#agentDashboardObjective").textContent = spec.objective;
  $("#dashboardQueryCount").textContent = `${execution.unique_query_count} of ${execution.query_budget} query budget used`;
  $("#dashboardPlanList").replaceChildren(...spec.widgets.map((widget) => {
    const item = document.createElement("li");
    item.textContent = `${widget.title} · ${label(widget.visualization)}`;
    return item;
  }));
  const unsupportedBox = $("#unsupportedRequirements");
  unsupportedBox.hidden = unsupported.length === 0;
  unsupportedBox.textContent = unsupported.length ? `Not added because the database did not advertise support: ${unsupported.join(", ")}.` : "";
  renderPersistenceReview(state.response.persistence_plan);
  const datasets = Object.fromEntries(execution.datasets.map((item) => [item.widget_id, item]));
  const cards = spec.widgets.map((widget) => buildAgentWidget(widget, datasets[widget.id]));
  $("#agentWidgetGrid").replaceChildren(...cards);
  spec.widgets.forEach((widget) => {
    const dataset = datasets[widget.id];
    if (widget.kind === "chart" && dataset?.status === "accepted" && dataset.chart) {
      const rows = dataset.rows.map((row) => dataset.columns.map((column) => row[column]));
      drawChart(`agentPlot-${widget.id}`, dataset.chart, dataset.columns, rows);
    }
  });
  $("#agentDashboard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderPersistenceReview(plan) {
  const review = $("#persistenceReview");
  review.hidden = !plan;
  if (!plan) return;
  $("#persistenceState").textContent = label(plan.status);
  $("#transformationList").replaceChildren(...plan.transformations.map((action) => {
    const item = document.createElement("div"); item.className = "transformation-item";
    const name = document.createElement("strong"); name.textContent = action.target_model;
    const detail = document.createElement("span"); detail.textContent = `${label(action.decision)} · ${label(action.spec.materialization)} · ${label(action.spec.incremental_strategy)}`;
    item.append(name, detail); return item;
  }));
  $("#approvalRow").hidden = plan.status !== "pending_approval";
  $("#refreshPersistenceButton").hidden = plan.status !== "deployed";
}

async function approvePersistence() {
  const plan = state.response?.persistence_plan;
  const approvedBy = $("#approverInput").value.trim();
  if (!plan || !approvedBy) { showDeploymentMessage("Enter the approver name before deployment.", true); return; }
  const button = $("#approvePersistenceButton"); button.disabled = true; button.textContent = "Deploying…";
  try {
    const response = await fetch(`/api/persistence/plans/${plan.id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: true, approved_by: approvedBy }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Persistence deployment failed.");
    state.response.persistence_plan = body.plan;
    renderPersistenceReview(body.plan);
    const dbt = body.dbt.status === "succeeded" ? "dbt validation succeeded" : `dbt failed: ${body.dbt.error || "unknown error"}`;
    const powerBi = body.power_bi.status === "manifest_ready" ? "Power BI MCP-ready manifest created" : label(body.power_bi.status);
    showDeploymentMessage(`${dbt}. ${powerBi}.`, body.plan.status === "failed");
  } catch (error) {
    showDeploymentMessage(error.message, true);
  } finally {
    button.disabled = false; button.textContent = "Approve and deploy";
  }
}

async function refreshPersistence() {
  const plan = state.response?.persistence_plan;
  if (!plan) return;
  const button = $("#refreshPersistenceButton"); button.disabled = true; button.textContent = "Refreshing…";
  try {
    const response = await fetch(`/api/persistence/plans/${plan.id}/refresh`, { method: "POST" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Production refresh failed.");
    showDeploymentMessage(`Refresh completed with ${body.llm_calls} LLM calls. Power BI: ${label(body.power_bi.status)}.`, body.dbt.status !== "succeeded");
  } catch (error) {
    showDeploymentMessage(error.message, true);
  } finally {
    button.disabled = false; button.textContent = "Run production refresh";
  }
}

function showDeploymentMessage(message, failed) {
  const element = $("#deploymentMessage"); element.hidden = false; element.textContent = message;
  element.style.color = failed ? "var(--red)" : "var(--green-deep)";
}

function buildAgentWidget(widget, dataset) {
  const article = document.createElement("article");
  article.className = `agent-widget ${widget.kind === "kpi" ? "kpi-widget" : ""}`;
  const head = document.createElement("div"); head.className = "agent-widget-head";
  const title = document.createElement("h3"); title.textContent = widget.title;
  const status = document.createElement("span");
  status.className = `widget-status ${dataset?.status === "accepted" ? "" : "failed"}`;
  status.textContent = dataset?.status === "accepted" ? "Available" : "Unavailable";
  head.append(title, status); article.append(head);
  if (!dataset || dataset.status !== "accepted") {
    const error = document.createElement("div"); error.className = "widget-error";
    error.textContent = dataset?.error || "This widget did not return data."; article.append(error);
  } else if (widget.kind === "kpi") {
    const metric = kpiFromDataset(dataset);
    const strong = document.createElement("strong"); strong.className = "agent-kpi-value"; strong.textContent = metric.value;
    const delta = document.createElement("span"); delta.className = "agent-kpi-delta"; delta.textContent = metric.delta;
    article.append(strong, delta);
  } else if (dataset.chart) {
    if (dataset.visualization_note) { const note = document.createElement("p"); note.className = "widget-fallback"; note.textContent = dataset.visualization_note; article.append(note); }
    const plot = document.createElement("div"); plot.id = `agentPlot-${widget.id}`; plot.className = "agent-widget-plot"; plot.setAttribute("role", "img"); plot.setAttribute("aria-label", widget.title); article.append(plot);
  } else {
    const note = document.createElement("p"); note.className = "widget-fallback"; note.textContent = dataset.visualization_note || "Showing the verified result as a table."; article.append(note);
    const wrap = document.createElement("div"); wrap.className = "table-wrap"; const table = document.createElement("table"); wrap.append(table); article.append(wrap);
    renderTable(table, dataset.columns, dataset.rows.map((row) => dataset.columns.map((column) => row[column])));
  }
  article.append(buildWidgetDetails(dataset));
  return article;
}

function buildWidgetDetails(dataset) {
  const details = document.createElement("details"); details.className = "widget-details";
  const summary = document.createElement("summary"); summary.textContent = "Query details"; details.append(summary);
  const list = document.createElement("dl");
  [["Question", dataset?.analytical_question || "—"], ["Status", dataset?.status || "failed"], ["Rows", String(dataset?.row_count || 0)]].forEach(([term, description]) => {
    const dt = document.createElement("dt"); dt.textContent = term; const dd = document.createElement("dd"); dd.textContent = description; list.append(dt, dd);
  });
  details.append(list);
  if (dataset?.accepted_sql) { const pre = document.createElement("pre"); const code = document.createElement("code"); code.textContent = dataset.accepted_sql; pre.append(code); details.append(pre); }
  return details;
}

function kpiFromDataset(dataset) {
  const numeric = dataset.column_metadata.find((item) => item.kind === "numeric")?.name;
  if (!numeric || dataset.rows.length === 0) return { value: "—", delta: "No numeric result" };
  const values = dataset.rows.map((row) => number(row[numeric])).filter((item) => item !== null);
  if (!values.length) return { value: "—", delta: "No numeric result" };
  const current = values[values.length - 1]; const previous = values.length > 1 ? values[values.length - 2] : null;
  const delta = previous && previous !== 0 ? `${((current - previous) / Math.abs(previous) * 100).toFixed(1)}% vs previous period` : "Current verified value";
  return { value: formatValue(current), delta };
}

function focusQuestion() {
  $("#questionInput").focus();
  window.scrollTo({ top: 200, behavior: "smooth" });
}

function switchView(view) {
  // Deliberately local: switching presentation never fetches, executes SQL, or mutates query state.
  state.activeView = view;
  ["table", "chart", "dashboard"].forEach((name) => {
    $(`#${name}View`).hidden = name !== view;
    const button = $(`.view-tabs button[data-view="${name}"]`);
    button.classList.toggle("active", name === view);
    if (name === view) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  if (view === "chart") renderPrimaryChart();
  if (view === "dashboard") resizeDashboardCharts();
}

function setupControls() {
  const { result, visualization } = state.response;
  const kinds = Object.fromEntries(visualization.columns.map((item) => [item.name, item.kind]));
  const numeric = result.columns.filter((column) => kinds[column] === "numeric");
  fillOptions($("#chartTypeSelect"), ["bar", "line", "scatter", "pie", "histogram", "box"], state.chartSpec?.type);
  fillOptions($("#xAxisSelect"), result.columns, state.chartSpec?.x, false);
  fillOptions($("#yAxisSelect"), numeric, typeof state.chartSpec?.y === "string" ? state.chartSpec.y : numeric[0], true);
  fillOptions($("#colorSelect"), result.columns, state.chartSpec?.color, true);
}

function fillOptions(select, values, selected, allowNone = false) {
  const options = allowNone ? [new Option("None", "")] : [];
  options.push(...values.map((value) => new Option(label(value), value)));
  select.replaceChildren(...options);
  if (selected && values.includes(selected)) select.value = selected;
}

function applyManualChartSettings() {
  state.chartSpec = {
    type: $("#chartTypeSelect").value,
    x: $("#xAxisSelect").value || null,
    y: $("#yAxisSelect").value || null,
    color: $("#colorSelect").value || null,
    title: state.response.result.question.replace(/[.?!]+$/, ""),
  };
  renderPrimaryChart();
}

function resetChart() {
  state.chartSpec = state.autoSpec ? structuredClone(state.autoSpec) : createFallbackSpec(state.response.result, state.response.visualization.columns);
  setupControls();
  renderPrimaryChart();
}

function renderPrimaryChart() {
  if (!state.response || state.activeView !== "chart") return;
  const { result, visualization } = state.response;
  $("#sampleBadge").hidden = !visualization.plotting_limited;
  $("#chartTitle").textContent = state.chartSpec?.title || "Result visualization";
  const validation = validateSpec(state.chartSpec, result.columns, visualization.columns);
  $("#chartEmpty").hidden = !validation;
  $("#plotlyChart").hidden = Boolean(validation);
  if (validation) {
    $("#chartEmpty").textContent = validation;
    Plotly.purge("plotlyChart");
    return;
  }
  drawChart("plotlyChart", state.chartSpec, result.columns, visualization.plotted_rows);
}

function validateSpec(spec, columns, metadata) {
  if (!spec) return "This result is best represented as a table. Choose compatible chart fields to explore it.";
  const referenced = [spec.x, spec.y, spec.color].filter(Boolean);
  if (referenced.some((column) => !columns.includes(column))) return "A selected field is not present in the SQL result.";
  const numeric = new Set(metadata.filter((item) => item.kind === "numeric").map((item) => item.name));
  if (["line", "bar", "scatter", "pie"].includes(spec.type) && (!spec.x || !spec.y)) return "Choose both an X/label field and a numeric Y/value field.";
  if (["line", "bar", "scatter", "pie"].includes(spec.type) && !numeric.has(spec.y)) return "The Y/value field must be numeric.";
  const measure = spec.x || spec.y;
  if (["histogram", "box"].includes(spec.type) && !numeric.has(measure)) return "Choose a numeric field for this chart type.";
  return null;
}

function drawChart(elementId, spec, columns, rows) {
  const records = rows.map((row) => Object.fromEntries(columns.map((column, index) => [column, row[index]])));
  const data = tracesFor(spec, records);
  const layout = {
    margin: { l: 62, r: 25, t: 45, b: 62 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "DM Sans, sans-serif", color: "#26332f", size: 12 }, colorway: palette,
    hoverlabel: { bgcolor: "#17211f", bordercolor: "#17211f", font: { color: "#ffffff" } },
    xaxis: { title: label(spec.x || spec.y), gridcolor: "#ece8df", zeroline: false, automargin: true },
    yaxis: { title: spec.type === "histogram" ? "Count" : label(spec.y), gridcolor: "#ece8df", zeroline: false, automargin: true },
    legend: { orientation: "h", y: 1.12, x: 0 }, showlegend: data.length > 1,
  };
  if (spec.type === "pie") delete layout.xaxis, delete layout.yaxis;
  Plotly.react(elementId, data, layout, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] });
}

function tracesFor(spec, records) {
  if (spec.aggregation && ["bar", "line"].includes(spec.type)) records = aggregate(records, spec.x, spec.y, spec.color, spec.aggregation);
  if (spec.type === "histogram") return [{ type: "histogram", x: records.map((row) => number(row[spec.x || spec.y])), marker: { color: palette[0] }, name: label(spec.x || spec.y) }];
  if (spec.type === "box") return [{ type: "box", y: records.map((row) => number(row[spec.x || spec.y])), marker: { color: palette[0] }, boxpoints: "outliers", name: label(spec.x || spec.y) }];
  if (spec.type === "pie") return [{ type: "pie", labels: records.map((row) => value(row[spec.x])), values: records.map((row) => number(row[spec.y])), hole: .48, textinfo: "label+percent", marker: { colors: palette } }];
  const groups = spec.color ? [...new Set(records.map((row) => value(row[spec.color])))] : [null];
  return groups.map((group, index) => {
    const subset = group === null ? records : records.filter((row) => value(row[spec.color]) === group);
    return {
      type: spec.type === "bar" ? "bar" : "scatter", mode: spec.type === "line" ? "lines+markers" : "markers",
      x: subset.map((row) => value(row[spec.x])), y: subset.map((row) => number(row[spec.y])),
      name: group || label(spec.y), marker: { color: palette[index % palette.length], size: spec.type === "scatter" ? 8 : undefined },
      line: { color: palette[index % palette.length], width: 2.5 }, connectgaps: false,
    };
  });
}

function aggregate(records, x, y, color, operation) {
  const groups = new Map();
  records.forEach((row) => {
    const key = JSON.stringify([row[x], color ? row[color] : null]);
    const current = groups.get(key) || { ...row, __values: [] };
    const parsed = number(row[y]); if (parsed !== null) current.__values.push(parsed);
    groups.set(key, current);
  });
  return [...groups.values()].map((row) => {
    const values = row.__values; let calculated = null;
    if (operation === "sum") calculated = values.reduce((sum, item) => sum + item, 0);
    if (operation === "avg") calculated = values.length ? values.reduce((sum, item) => sum + item, 0) / values.length : null;
    if (operation === "min") calculated = values.length ? Math.min(...values) : null;
    if (operation === "max") calculated = values.length ? Math.max(...values) : null;
    if (operation === "count") calculated = values.length;
    return { ...row, [y]: calculated };
  });
}

function renderDashboard() {
  const { result, visualization } = state.response;
  const dashboard = visualization.dashboard;
  $("#dashboardTitle").textContent = dashboard.title;
  $("#kpiGrid").replaceChildren(...dashboard.kpis.map((kpi) => {
    const card = document.createElement("div"); card.className = "kpi";
    const caption = document.createElement("span"); caption.textContent = kpi.label;
    const metric = document.createElement("strong"); metric.textContent = calculateKpi(kpi, result.columns, result.rows);
    card.append(caption, metric); return card;
  }));
  const charts = dashboard.charts.map((spec, index) => {
    const chart = document.createElement("div"); chart.className = "dashboard-chart"; chart.id = `dashboardChart${index}`; return chart;
  });
  $("#dashboardCharts").replaceChildren(...charts);
  dashboard.charts.forEach((spec, index) => drawChart(`dashboardChart${index}`, spec, result.columns, visualization.plotted_rows));
  $(".dashboard-data").hidden = !dashboard.show_table;
}

function calculateKpi(kpi, columns, rows) {
  const index = columns.indexOf(kpi.column); const raw = rows.map((row) => row[index]).filter((item) => item !== null);
  if (kpi.operation === "count") return formatInteger(raw.length);
  if (kpi.operation === "latest") return raw.length ? formatValue(raw[raw.length - 1]) : "—";
  const values = raw.map(number).filter((item) => item !== null); if (!values.length) return "—";
  const operations = { sum: () => values.reduce((a, b) => a + b, 0), avg: () => values.reduce((a, b) => a + b, 0) / values.length, min: () => Math.min(...values), max: () => Math.max(...values) };
  return formatValue(operations[kpi.operation]());
}

function renderTable(table, columns, rows) {
  const head = document.createElement("thead"); const headerRow = document.createElement("tr");
  columns.forEach((column) => { const th = document.createElement("th"); th.textContent = label(column); headerRow.append(th); }); head.append(headerRow);
  const body = document.createElement("tbody");
  rows.slice(0, 500).forEach((row) => { const tr = document.createElement("tr"); row.forEach((item) => { const td = document.createElement("td"); td.textContent = item === null ? "NULL" : formatValue(item); if (item === null) td.className = "null"; tr.append(td); }); body.append(tr); });
  table.replaceChildren(head, body);
}

function createFallbackSpec(result, metadata) {
  const numeric = metadata.find((item) => item.kind === "numeric")?.name;
  if (!numeric || !result.columns.length || result.rows.length < 2) return null;
  return { type: "bar", x: result.columns.find((column) => column !== numeric) || numeric, y: numeric, color: null, title: result.question.replace(/[.?!]+$/, "") };
}

function setLoading(loading) { $("#loadingPanel").hidden = !loading; $(".analyze-button").disabled = loading || !state.config; if (loading) { $("#errorPanel").hidden = true; $("#workspace").hidden = true; $("#agentDashboard").hidden = true; } }
function showError(message) { $("#errorMessage").textContent = message; $("#errorPanel").hidden = false; }
function setServiceStatus(status, text) { const element = $("#serviceStatus"); element.className = `service-status ${status}`; element.lastElementChild.textContent = text; }
function resizeDashboardCharts() { $$(".dashboard-chart").forEach((chart) => Plotly.Plots.resize(chart)); }
async function copySql() { await navigator.clipboard.writeText(state.response.result.accepted_sql); const button = $("#copySqlButton"); button.textContent = "Copied"; setTimeout(() => button.textContent = "Copy SQL", 1200); }
function label(text) { return text ? String(text).replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase()) : ""; }
function value(item) { return item === null ? "NULL" : item; }
function number(item) { if (item === null || item === "") return null; const parsed = Number(item); return Number.isFinite(parsed) ? parsed : null; }
function formatInteger(item) { return new Intl.NumberFormat().format(item); }
function formatValue(item) { if (typeof item === "number") return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, notation: Math.abs(item) >= 1000000 ? "compact" : "standard" }).format(item); return String(item); }

if (typeof module !== "undefined") module.exports = { validateSpec, createFallbackSpec, aggregate };
