from __future__ import annotations


def build_school_trace_dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>School Trace</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --border: #d8dee8;
      --text: #172033;
      --muted: #667085;
      --accent: #2458d3;
      --danger: #b42318;
      --ok: #067647;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      height: 64px;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 20px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }
    h1 { font-size: 18px; margin: 0; }
    nav {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-left: 12px;
    }
    nav a {
      height: 34px;
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0 10px;
      color: var(--muted);
      text-decoration: none;
      background: #fff;
      font-size: 13px;
      font-weight: 650;
    }
    nav a.active {
      color: #fff;
      border-color: var(--accent);
      background: var(--accent);
    }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      min-height: calc(100vh - 64px);
    }
    .sidebar {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 12px;
      min-width: 0;
      max-height: calc(100vh - 96px);
    }
    .toolbar {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    input, button {
      height: 36px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      background: #fff;
    }
    button {
      cursor: pointer;
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      min-width: 0;
      overflow: hidden;
    }
    .list {
      overflow: auto;
      min-height: 0;
    }
    .metrics {
      padding: 12px;
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .metric {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      min-width: 0;
      background: #fff;
    }
    .metric-label {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .metric-value {
      margin-top: 2px;
      font-size: 18px;
      font-weight: 700;
      line-height: 1.2;
    }
    .school-metrics {
      margin-top: 8px;
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    .item {
      display: grid;
      gap: 6px;
      width: 100%;
      height: auto;
      min-height: 92px;
      align-content: start;
      padding: 12px;
      border: 0;
      border-bottom: 1px solid var(--border);
      background: white;
      color: var(--text);
      text-align: left;
      overflow: hidden;
    }
    .item:hover, .item.active { background: #eef4ff; }
    .question {
      min-width: 0;
      font-weight: 650;
      line-height: 1.35;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .meta {
      min-width: 0;
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }
    .pill {
      max-width: 100%;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      background: #fff;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .trace-id {
      min-width: 0;
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      line-height: 1.35;
    }
    .detail {
      padding: 16px;
      overflow: auto;
      max-height: calc(100vh - 96px);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .block {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      margin-bottom: 12px;
    }
    h2 { font-size: 16px; margin: 0 0 10px; }
    h3 { font-size: 14px; margin: 0 0 8px; color: var(--muted); }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--border);
      padding: 8px;
      text-align: left;
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 600; background: #f8fafc; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.45;
      background: #0f172a;
      color: #e5edf7;
      padding: 12px;
      border-radius: 6px;
      max-height: 360px;
      overflow: auto;
    }
    .empty {
      padding: 24px;
      color: var(--muted);
    }
    .error { color: var(--danger); }
    .ok { color: var(--ok); }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      .toolbar { width: 100%; margin-left: 0; }
      nav { order: 2; width: 100%; margin-left: 0; }
      header { height: auto; padding: 12px; flex-wrap: wrap; }
      input { flex: 1; min-width: 180px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>School Trace</h1>
    <nav aria-label="Admin navigation">
      <a class="active" href="/v1/admin/school-traces/ui">Trace 流程</a>
      <a href="/v1/admin/token-usage/ui">Token 用量</a>
    </nav>
    <div class="toolbar">
      <input id="apiKey" type="password" placeholder="Gateway API Key" autocomplete="off" />
      <input id="limit" type="number" min="1" max="200" value="50" />
      <button id="refresh">刷新</button>
    </div>
  </header>
  <main>
    <div class="sidebar">
      <section class="metrics" id="traceMetrics"><div class="empty">刷新后显示指标。</div></section>
      <section class="list" id="traceList"><div class="empty">输入 key 后点击刷新。</div></section>
    </div>
    <section class="detail" id="traceDetail"><div class="empty">选择一条 trace 查看详情。</div></section>
  </main>
  <script>
    const apiKeyInput = document.getElementById("apiKey");
    const limitInput = document.getElementById("limit");
    const metricsEl = document.getElementById("traceMetrics");
    const listEl = document.getElementById("traceList");
    const detailEl = document.getElementById("traceDetail");
    const refreshButton = document.getElementById("refresh");
    let activeTraceId = "";

    apiKeyInput.value = localStorage.getItem("school_trace_api_key") || "";
    refreshButton.addEventListener("click", loadRecent);

    function headers() {
      const key = apiKeyInput.value.trim();
      if (key) localStorage.setItem("school_trace_api_key", key);
      return key ? { "Authorization": `Bearer ${key}` } : {};
    }

    async function loadRecent() {
      metricsEl.innerHTML = '<div class="empty">加载中...</div>';
      listEl.innerHTML = '<div class="empty">加载中...</div>';
      detailEl.innerHTML = '<div class="empty">选择一条 trace 查看详情。</div>';
      try {
        const limit = encodeURIComponent(limitInput.value || "50");
        const res = await fetch(`/v1/admin/school-traces/recent?limit=${limit}`, { headers: headers() });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.statusText);
        renderMetrics(data.metrics || {}, data.api_usage_metrics || []);
        renderList(data.items || []);
      } catch (err) {
        metricsEl.innerHTML = '<div class="empty error">指标加载失败。</div>';
        listEl.innerHTML = `<div class="empty error">${escapeHtml(String(err.message || err))}</div>`;
      }
    }

    function renderMetrics(metrics, apiUsage) {
      const overall = metrics.overall || {};
      const schools = metrics.schools || [];
      metricsEl.innerHTML = `
        <h3>Metrics</h3>
        <div class="metrics-grid">
          ${metricCell("Total", overall.total || 0)}
          ${metricCell("Success Rate", `${formatRate(overall.success_rate)}%`)}
          ${metricCell("Avg ms", formatNumber(overall.avg_duration_ms))}
          ${metricCell("SQL Fail", overall.sql_failure_count || 0)}
          ${metricCell("Empty", overall.empty_result_count || 0)}
          ${metricCell("Policy", overall.policy_task_count || 0)}
          ${metricCell("Tools", overall.tool_call_count || 0)}
          ${metricCell("Tokens", formatNumber(overall.total_tokens))}
          ${metricCell("LLM Calls", overall.llm_call_count || 0)}
        </div>
        <div class="school-metrics">
          ${schools.slice(0, 4).map(item => `
            <div title="${escapeHtml(item.school_id || "")}">
              ${escapeHtml(item.school_id || "-")} · ${Number(item.total || 0)} traces · ${formatRate(item.success_rate)}% · ${formatNumber(item.total_tokens)} tokens
            </div>
          `).join("") || '<div>无学校指标。</div>'}
        </div>
        <h3 style="margin-top:12px;">API Token Usage</h3>
        <div class="school-metrics">
          ${(apiUsage || []).slice(0, 6).map(item => `
            <div title="${escapeHtml(item.token_hash || "")}">
              ${escapeHtml(item.token_hash || "-")} · ${Number(item.request_count || 0)} req · ${formatNumber(item.total_tokens)} tokens
            </div>
          `).join("") || '<div>暂无 token 用量。</div>'}
        </div>
      `;
    }

    function metricCell(label, value) {
      return `<div class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div></div>`;
    }

    function renderList(items) {
      if (!items.length) {
        listEl.innerHTML = '<div class="empty">没有 trace。</div>';
        return;
      }
      listEl.innerHTML = "";
      for (const item of items) {
        const btn = document.createElement("button");
        btn.className = `item ${item.trace_id === activeTraceId ? "active" : ""}`;
        btn.innerHTML = `
          <div class="question">${escapeHtml(item.question || "(空问题)")}</div>
          <div class="meta">
            <span class="pill">${escapeHtml(item.school_id || "-")}</span>
            <span class="pill">${formatTime(item.created_at)}</span>
            <span class="pill">${Number(item.step_count || 0)} steps</span>
            <span class="pill">${formatNumber((item.token_usage || {}).total_tokens)} tokens</span>
            <span class="${item.has_error ? "error" : "ok"}">${item.has_error ? "error" : "ok"}</span>
          </div>
          <span class="trace-id" title="${escapeHtml(item.trace_id || "")}">${escapeHtml(item.trace_id || "")}</span>
        `;
        btn.addEventListener("click", () => loadDetail(item.trace_id));
        listEl.appendChild(btn);
      }
    }

    async function loadDetail(traceId) {
      activeTraceId = traceId;
      detailEl.innerHTML = '<div class="empty">加载中...</div>';
      try {
        const res = await fetch(`/v1/admin/school-traces/${encodeURIComponent(traceId)}`, { headers: headers() });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.statusText);
        if (!data.found) {
          detailEl.innerHTML = '<div class="empty">没有找到这条 trace，或当前 key 无权查看。</div>';
          return;
        }
        renderDetail(data);
      } catch (err) {
        detailEl.innerHTML = `<div class="empty error">${escapeHtml(String(err.message || err))}</div>`;
      }
    }

    function renderDetail(trace) {
      const steps = trace.steps || [];
      const tokenUsage = trace.token_usage || {};
      const packageIndex = findStep(trace, "school_schema.index").name ? findStep(trace, "school_schema.index") : findStep(trace, "package.index");
      const agentStart = findStep(trace, "agent_native.start").name
        ? findStep(trace, "agent_native.start")
        : (findStep(trace, "policy_only_agent.start").name ? findStep(trace, "policy_only_agent.start") : findStep(trace, "policy_agent.start"));
      const sqlSteps = steps.filter(step => step.name === "ddl_react.tool.sql_db_query");
      const ddlSteps = steps.filter(step => step.name === "ddl_react.tool.ddl_search" || step.name === "ddl_react.tool.inspect_table_schema" || step.name === "ddl_react.tool.sample_table_rows");
      const policySteps = steps.filter(step => String(step.name || "").includes("official_policy_search"));
      const webSteps = steps.filter(step => String(step.name || "").includes("web_search"));
      const toolSteps = steps.filter(step => String(step.name || "").includes(".tool."));
      const output = packageIndex.output || {};
      detailEl.innerHTML = `
        <div class="block">
          <h2>${escapeHtml(trace.question || "")}</h2>
          <div class="meta">
            <span class="pill">${escapeHtml(trace.school_id || "")}</span>
            <span class="pill">${formatTime(trace.created_at)}</span>
            <span class="pill">${escapeHtml(trace.trace_id || "")}</span>
          </div>
        </div>
        <div class="grid">
          <div class="block">
            <h3>Token Usage</h3>
            <table><tbody>
              <tr><th>Total</th><td>${formatNumber(tokenUsage.total_tokens)}</td></tr>
              <tr><th>Prompt/Input</th><td>${formatNumber(tokenUsage.prompt_tokens)}</td></tr>
              <tr><th>Completion/Output</th><td>${formatNumber(tokenUsage.completion_tokens)}</td></tr>
              <tr><th>LLM Calls</th><td>${Number(tokenUsage.llm_call_count || 0)}</td></tr>
            </tbody></table>
            <pre style="margin-top:8px;">${escapeHtml(JSON.stringify(tokenUsage.by_model || {}, null, 2))}</pre>
          </div>
          <div class="block">
            <h3>Agent</h3>
            <pre>${escapeHtml(JSON.stringify(agentStart.output || {}, null, 2))}</pre>
          </div>
          <div class="block">
            <h3>Schema</h3>
            <pre>${escapeHtml(JSON.stringify({
              datasets_count: output.datasets_count,
              fields_count: output.fields_count,
              sample_datasets: output.sample_datasets || []
            }, null, 2))}</pre>
          </div>
          <div class="block">
            <h3>Timing</h3>
            ${renderSteps(steps)}
          </div>
        </div>
        <div class="block">
          <h3>DDL / Schema Tools</h3>
          ${renderToolOutputs(ddlSteps)}
        </div>
        <div class="block">
          <h3>SQL Queries</h3>
          ${renderToolOutputs(sqlSteps)}
        </div>
        <div class="block">
          <h3>Policy Evidence</h3>
          ${renderToolOutputs(policySteps)}
        </div>
        <div class="block">
          <h3>Web Search</h3>
          ${renderToolOutputs(webSteps)}
        </div>
        <div class="block">
          <h3>All Tool Calls</h3>
          ${renderToolOutputs(toolSteps)}
        </div>
      `;
    }

    function findStep(trace, name) {
      return (trace.steps || []).find(step => step.name === name) || { output: {} };
    }

    function renderArray(items) {
      if (!items.length) return '<div class="empty">无。</div>';
      return `<table><tbody>${items.map(item => `<tr><td>${escapeHtml(String(item))}</td></tr>`).join("")}</tbody></table>`;
    }

    function renderSteps(steps) {
      return `<table><thead><tr><th>Step</th><th>Status</th><th>ms</th></tr></thead><tbody>${
        steps.map(step => `<tr><td>${escapeHtml(step.name || "")}</td><td>${escapeHtml(step.status || "")}</td><td>${Number(step.duration_ms || 0)}</td></tr>`).join("")
      }</tbody></table>`;
    }

    function renderToolOutputs(steps) {
      if (!steps.length) return '<div class="empty">无。</div>';
      return steps.map(step => `
        <details open>
          <summary>${escapeHtml(step.name || "")} · ${escapeHtml(step.status || "")} · ${Number(step.duration_ms || 0)}ms</summary>
          <pre>${escapeHtml(JSON.stringify({
            input: step.input || {},
            output: step.output || {},
            error: step.error || ""
          }, null, 2))}</pre>
        </details>
      `).join("");
    }

    function renderCandidates(items) {
      if (!items.length) return '<div class="empty">无候选数据。</div>';
      return `<table><thead><tr><th>Dataset</th><th>Score</th><th>Matched</th><th>Reason</th></tr></thead><tbody>${
        items.map(item => `<tr><td>${escapeHtml(item.dataset_id || "")}<br><span class="meta">${escapeHtml(item.label || "")}</span></td><td>${Number(item.score || 0)}</td><td>${escapeHtml((item.matched_terms || []).join("、"))}</td><td>${escapeHtml(item.reason || "")}</td></tr>`).join("")
      }</tbody></table>`;
    }

    function renderRejected(items) {
      if (!items.length) return '<div class="empty">无拒绝项。</div>';
      return `<table><thead><tr><th>Dataset</th><th>Reason</th></tr></thead><tbody>${
        items.map(item => `<tr><td>${escapeHtml(item.dataset_id || "")}</td><td>${escapeHtml(item.reason || "")}</td></tr>`).join("")
      }</tbody></table>`;
    }

    function formatTime(value) {
      if (!value) return "-";
      return new Date(Number(value) * 1000).toLocaleString();
    }

    function formatRate(value) {
      return formatNumber(Number(value || 0) * 100);
    }

    function formatNumber(value) {
      const number = Number(value || 0);
      return Number.isInteger(number) ? String(number) : number.toFixed(1);
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[ch]));
    }
  </script>
</body>
</html>"""
