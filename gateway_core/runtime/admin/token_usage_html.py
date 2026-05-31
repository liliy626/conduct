from __future__ import annotations


def build_token_usage_dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Token Usage</title>
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
    h2 { font-size: 15px; margin: 0 0 10px; }
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
    main {
      display: grid;
      gap: 16px;
      padding: 16px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
      overflow: hidden;
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      background: #fff;
    }
    .metric-label { color: var(--muted); font-size: 12px; }
    .metric-value { margin-top: 4px; font-size: 22px; font-weight: 750; }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }
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
    th { color: var(--muted); font-weight: 650; background: #f8fafc; }
    .muted { color: var(--muted); }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .question {
      max-width: 520px;
      line-height: 1.35;
    }
    .error { color: var(--danger); }
    @media (max-width: 980px) {
      header { height: auto; padding: 12px; flex-wrap: wrap; }
      .toolbar { width: 100%; margin-left: 0; }
      nav { order: 2; width: 100%; margin-left: 0; }
      input { flex: 1; min-width: 160px; }
      .metrics-grid, .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Token Usage</h1>
    <nav aria-label="Admin navigation">
      <a href="/v1/admin/school-traces/ui">Trace 流程</a>
      <a class="active" href="/v1/admin/token-usage/ui">Token 用量</a>
    </nav>
    <div class="toolbar">
      <input id="apiKey" type="password" placeholder="Gateway API Key" autocomplete="off" />
      <input id="limit" type="number" min="1" max="5000" value="1000" />
      <button id="refresh">刷新</button>
    </div>
  </header>
  <main>
    <section id="overall">加载中...</section>
    <div class="grid">
      <section><h2>按 API Key</h2><div id="byApi"></div></section>
      <section><h2>按用户</h2><div id="byUser"></div></section>
      <section><h2>按模型</h2><div id="byModel"></div></section>
      <section><h2>按路由</h2><div id="byRoute"></div></section>
    </div>
    <section><h2>最近问题明细</h2><div id="recent"></div></section>
  </main>
  <script>
    const apiKeyInput = document.getElementById("apiKey");
    const limitInput = document.getElementById("limit");
    const refreshButton = document.getElementById("refresh");
    apiKeyInput.value = localStorage.getItem("token_usage_api_key") || localStorage.getItem("school_trace_api_key") || "";
    refreshButton.addEventListener("click", loadUsage);
    loadUsage();

    function headers() {
      const key = apiKeyInput.value.trim();
      if (key) {
        localStorage.setItem("token_usage_api_key", key);
        localStorage.setItem("school_trace_api_key", key);
      }
      return key ? { "Authorization": `Bearer ${key}` } : {};
    }

    async function loadUsage() {
      try {
        const limit = encodeURIComponent(limitInput.value || "1000");
        const res = await fetch(`/v1/admin/token-usage?limit=${limit}`, { headers: headers() });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.statusText);
        render(data);
      } catch (err) {
        document.getElementById("overall").innerHTML = `<div class="error">${escapeHtml(String(err.message || err))}</div>`;
      }
    }

    function render(data) {
      const overall = data.overall || {};
      document.getElementById("overall").innerHTML = `
        <div class="metrics-grid">
          ${metricCell("Requests", overall.request_count)}
          ${metricCell("Total Tokens", overall.total_tokens)}
          ${metricCell("Prompt/Input", overall.prompt_tokens)}
          ${metricCell("Completion/Output", overall.completion_tokens)}
          ${metricCell("Avg Tokens", overall.avg_total_tokens)}
        </div>
      `;
      document.getElementById("byApi").innerHTML = renderAgg(data.by_api || [], "API Key Hash");
      document.getElementById("byUser").innerHTML = renderAgg(data.by_user || [], "User ID");
      document.getElementById("byModel").innerHTML = renderAgg(data.by_model || [], "Model");
      document.getElementById("byRoute").innerHTML = renderAgg(data.by_route || [], "Route");
      document.getElementById("recent").innerHTML = renderRecent(data.recent || []);
    }

    function metricCell(label, value) {
      return `<div class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${formatNumber(value)}</div></div>`;
    }

    function renderAgg(rows, keyLabel) {
      if (!rows.length) return '<div class="muted">暂无数据。</div>';
      return `<table><thead><tr><th>${escapeHtml(keyLabel)}</th><th>请求</th><th>Total</th><th>Prompt</th><th>Completion</th><th>Avg</th></tr></thead><tbody>${
        rows.slice(0, 100).map(row => `<tr>
          <td class="mono">${escapeHtml(row.key || "-")}</td>
          <td>${formatNumber(row.request_count)}</td>
          <td>${formatNumber(row.total_tokens)}</td>
          <td>${formatNumber(row.prompt_tokens)}</td>
          <td>${formatNumber(row.completion_tokens)}</td>
          <td>${formatNumber(row.avg_total_tokens)}</td>
        </tr>`).join("")
      }</tbody></table>`;
    }

    function renderRecent(rows) {
      if (!rows.length) return '<div class="muted">暂无数据。</div>';
      return `<table><thead><tr><th>时间</th><th>用户</th><th>API</th><th>模型/路由</th><th>Token</th><th>问题</th></tr></thead><tbody>${
        rows.map(row => `<tr>
          <td>${formatTime(row.ts)}</td>
          <td class="mono">${escapeHtml(row.user_id || "-")}</td>
          <td class="mono">${escapeHtml(row.token_hash || "-")}</td>
          <td>${escapeHtml(row.model_id || "-")}<br><span class="muted">${escapeHtml(row.route_name || "-")}</span></td>
          <td>${formatNumber((row.usage || {}).total_tokens)}<br><span class="muted">${formatNumber((row.usage || {}).prompt_tokens)} / ${formatNumber((row.usage || {}).completion_tokens)}</span></td>
          <td class="question">${escapeHtml(row.question || "")}</td>
        </tr>`).join("")
      }</tbody></table>`;
    }

    function formatTime(value) {
      if (!value) return "-";
      return new Date(Number(value) * 1000).toLocaleString();
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
