# Tool Responsibility Cards

This file records the operational boundary for tools. It separates public
gateway tools from executor-internal School SQL tools.

## Shared Tool Contract

Public gateway tools are registered through `GatewayToolRegistry` and return a
normalized `ToolResult` with:

- `ok`
- `structured_content`
- `evidence`
- `artifacts`
- `sources`
- `lineage`
- `warnings`
- `error`
- `duration_ms`

Legacy `AgentTool` implementations are adapted by `AgentToolGatewayAdapter`.
Planner-visible tools should use canonical names; aliases exist only for
compatibility with older tool-call names.

## Public Gateway Tools

### `time.resolve`

| Field | Value |
| --- | --- |
| Aliases | `time` |
| Implementation | `gateway_core.tools.time_tool.TimeTool` |
| Tags | `time` |
| Risk | `low` |
| Scopes | none |
| Main output | `time_context` evidence and JSON artifact |

Responsibility:

- Resolve current time and natural-language time ranges.
- Provide the authoritative timezone/today/now context for the gateway.

Must not own:

- SQL filtering itself.
- Policy or business interpretation.

### `policy.official_policy_search`

| Field | Value |
| --- | --- |
| Aliases | `official_policy_search` |
| Implementation | `gateway_core.tools.policy_tool.PolicyTool` |
| Tags | `policy`, `evidence` |
| Risk | `medium` |
| Scopes | `policy:read` |
| Main output | `official_policy_evidence`, policy JSON artifact, sources |

Responsibility:

- Retrieve official policy evidence for policy, title evaluation, honors,
  eligibility, review body, continuing education, and one-vote veto questions.
- Require a non-empty `query`.

Must not own:

- School database evidence.
- Final eligibility judgment without explicit policy evidence.

### `web.search`

| Field | Value |
| --- | --- |
| Aliases | `web_search` |
| Implementation | `gateway_core.tools.web_search_tool.WebSearchTool` |
| Tags | `web`, `evidence` |
| Risk | `high` |
| Scopes | `web:search` |
| Main output | `web_search_result` JSON artifact and sources |

Responsibility:

- Run optional external web search after privacy sanitization.
- Return lean source results only when web search is enabled and a provider is
  available.

Must not own:

- Sending student/teacher sensitive context to the web.
- Treating disabled search as factual absence.

### `artifact.chart`

| Field | Value |
| --- | --- |
| Aliases | `chart` |
| Implementation | `gateway_core.tools.chart_tool.ChartTool` |
| Tags | `artifact`, `chart` |
| Risk | `low` |
| Scopes | none |
| Main output | HTML/JSON/SVG chart artifact |

Responsibility:

- Build table/line/bar/stacked-bar/pie chart artifacts from local evidence rows.
- Write chart preview and data artifacts.

Must not own:

- Executing SQL.
- Inventing chart rows when evidence rows are missing.

### `artifact.plot`

| Field | Value |
| --- | --- |
| Aliases | `plot` |
| Implementation | `gateway_core.tools.plot_tool.PlotTool` |
| Tags | `artifact`, `plot` |
| Risk | `low` |
| Scopes | none |
| Main output | PNG plot artifact plus JSON data artifact |

Responsibility:

- Build PNG charts from already queried evidence rows.
- Reject SQL text input and require rows/evidence rows.

Must not own:

- SQL execution.
- Data fabrication for visualization.

### `artifact.image_generate`

| Field | Value |
| --- | --- |
| Aliases | `generate_image_tool`, `image` |
| Implementation | `gateway_core.tools.image_tool.GenerateImageTool` |
| Tags | `artifact`, `image` |
| Risk | `medium` |
| Scopes | none |
| Main output | image artifact evidence and artifact payload |

Responsibility:

- Generate or edit images through the configured image provider.
- Sanitize prompts and discard sensitive non-prompt context.
- Validate supported image sizes and provider capabilities.

Must not own:

- School SQL evidence.
- Raw sensitive personal details in prompts or external image calls.

### `artifact.slide_generate`

| Field | Value |
| --- | --- |
| Aliases | `slide` |
| Implementation | `gateway_core.tools.slide_tool.SlideTool` |
| Tags | `artifact`, `slide` |
| Risk | `low` |
| Scopes | none |
| Main output | PPTX artifact and slide outline evidence |

Responsibility:

- Build PPTX report artifacts from local evidence, chart artifacts, and
  structured sections.
- Use an LLM provider only when configured, otherwise fall back to the basic
  local PPTX generator.

Must not own:

- Sensitive external context transfer.
- Database querying or SQL lineage creation.

## Internal Context Tool

### `business_prompt_context`

| Field | Value |
| --- | --- |
| Implementation | `gateway_core.tools.business_prompt_tool.BusinessPromptContextTool` |
| Visibility | internal context helper |
| Main output | `business_prompt_context` evidence |

Responsibility:

- Provide school business evidence boundaries by domain and user role.
- Feed contract construction and answer guardrails.

Must not own:

- SQL, permissions, policy truth, or data evidence.
- Public gateway tool behavior unless deliberately promoted into
  `GatewayToolRegistry`.

## School SQL ReAct Tools

These tools are internal to `DDLReactTools.as_langchain_tools()` and are used by
the School SQL executor. They should remain bounded by DDL evidence, schema
allowlists, and read-only query rules.

| Tool | Responsibility | Must not own |
| --- | --- | --- |
| `ddl_search` | Find candidate tables, business fields, time fields, latest-row preview, SQL readiness, and evidence packets. | Final prose, artifact generation, or bypassing DDL evidence. |
| `list_available_tables` | List the tables currently available to the executor. | Business interpretation. |
| `inspect_table_schema` | Inspect selected table columns and schema metadata. | Running queries. |
| `sample_table_rows` | Return representative rows for selected tables. | Full data export. |
| `inspect_jsonb_recordset` | Inspect JSONB recordset structure before expansion. | Guessing nested fields without samples. |
| `jsonb_recordset_query` | Execute bounded JSONB recordset queries. | Non-read-only SQL or unbounded expansion. |
| `sql_db_query` | Execute bounded read-only SQL against DDL-vetted tables. | Write SQL, unvetted tables, or hidden fallback data. |
| `sql_experience_search` | Retrieve prior SQL/query patterns relevant to the current question. | Treating old examples as current facts. |
| `suggest_related_queries` | Suggest follow-up query directions from gathered evidence. | Expanding the user question without trigger conditions. |
| `trend_analysis` | Compute trend summaries from already gathered data. | Querying new database facts. |
| `anomaly_detection` | Detect anomalies from already gathered rows or evidence. | Inventing risk causes. |
| `cohort_compare` | Compare cohorts from already gathered rows or evidence. | Treating incomparable cohorts as equivalent. |

## Next Review Order

1. Public tools: verify every `allowed_tools` entry maps to a registered
   canonical name or alias.
2. School SQL tools: verify `ddl_search` remains the first evidence gate for SQL.
3. Artifact tools: verify chart/plot/image/slide all require existing evidence
   or sanitized prompts and never query SQL directly.
