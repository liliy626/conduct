# Agent / Tool / Prompt Inventory

This document is the repo-level map for agent responsibilities, tool surfaces,
prompt ownership, and runtime contracts. Keep it updated whenever a new agent,
tool, prompt module, or handoff contract is added.

## Authoritative Sources

| Area | Source of truth | Purpose |
| --- | --- | --- |
| Agent registry | `gateway_core/agents/universal_hub/registry.py` | Declares skill names, output ownership, default tools, and stream support. |
| Workflow contract | `gateway_core/agents/contracts/workflow_contracts.py` | Declares node order, executor ownership, reads/writes, tool policy, and trace policy. |
| Output contract | `gateway_core/agents/contracts/output_contracts.py` | Declares cross-agent output names, tool outputs, handoff fields, and contract version. |
| Prompt registry | `gateway_core/prompts/prompt_layer.py` plus `gateway_core/prompts/**` | Registers versioned prompt templates by prompt id. |
| Gateway tool registry | `gateway_core/tools/core/registry.py` plus `gateway_core/tools/catalog/non_sql.py` | Registers non-SQL tools, aliases, risk, tags, and scopes. |
| School SQL tools | `gateway_core/agents/school_sql/sql_tools.py` | Exposes the internal ReAct tools used by the School SQL executor. |

Per-agent operating details live in `docs/agent_responsibility_cards.md`.
Per-tool operating details live in `docs/tool_responsibility_cards.md`.
Per-prompt operating details live in `docs/prompt_responsibility_cards.md`.

The current contract versions are:

- `OUTPUT_CONTRACT_VERSION = "2026-06-03.1"`
- `WORKFLOW_CONTRACT_VERSION = "2026-06-04.1"`

## Runtime Route

```text
OpenAI-compatible request
  -> gateway_core.api.openai_compat.chat_pipeline
  -> route/context/contract planning
  -> selected agent or skill
  -> registered tools
  -> InterAgentState / handoff payload
  -> final answer prompt
  -> OpenAI-compatible response
```

For school data answers, the canonical workflow is
`SCHOOL_DATA_ANSWER_WORKFLOW` with workflow id `school_data_answer`.

| Step | Executor | Responsibility | Reads | Writes |
| --- | --- | --- | --- | --- |
| `route.resolve` | `gateway` | Resolve tenant, auth, and route. | request headers, messages | route, tenant |
| `context.build_school` | `context_builder` | Select and compress school metadata context. | tenant, question | schema, SQL experience, business prompt context |
| `contract.plan` | `contract_planner` | Plan per-turn route, tools, required outputs, and answer focus. | question, conversation, schema, DDL, business prompt context | tool contract, required outputs |
| `school_sql.react_execute` | `school_sql_react` | Query school data evidence through DDL and read-only SQL tools. | question, tool contract, schema, conversation | data evidence, evidence board, source views |
| `evidence.normalize_inter_agent_state` | `workflow` | Normalize SQL evidence into serializable `InterAgentState`. | data evidence, evidence board, source views, tool contract | inter-agent state |
| `handoff.final_answer` | `workflow` | Build compact final-answer handoff from `InterAgentState`. | inter-agent state | handoff payload |
| `answer.compose` | `final_answer` | Compose the final natural-language answer from verified evidence. | handoff payload, business prompt context | final answer |

## Agent Inventory

| Skill | Implementation | Owned outputs | Declared tools | Role | Boundary |
| --- | --- | --- | --- | --- | --- |
| `school_sql` | `gateway_core.agents.school_sql.school_sql_skill.SchoolSqlSkill` | `data_evidence` | `ddl_search`, `sql_db_query`, `sample_table_rows` | `data_agent` | Owns school database evidence. It must not own final prose, policy retrieval, or raw row dumping. |
| `chat` | `gateway_core.agents.chat.chat_skill.ChatSkill` | none | none | `general_chat` | Owns general conversational fallback. It must not fabricate data evidence. |
| `image_generator` | `gateway_core.agents.visual.image_generation_skill.ImageGenerationSkill` | `image_artifact` | `image_generation` | `visual_agent` | Owns image artifact generation. It must not answer school SQL questions. |
| `ppt_generator` | `gateway_core.agents.ppt.ppt_generation_skill.PptGenerationSkill` | `ppt_artifact` | `ppt_generation` | `presentation_agent` | Owns presentation artifact generation. It must not own evidence retrieval. |

`policy_only` is intentionally not an agent. Policy lookup is a tool capability
(`policy.official_policy_search`) that can contribute `policy_evidence` to a
turn contract.

## Tool Inventory

### Gateway Tools

These tools are registered through `GatewayToolRegistry` and are suitable for
cross-agent planning.

| Canonical name | Aliases | Implementation | Output boundary |
| --- | --- | --- | --- |
| `time.resolve` | `time` | `gateway_core.tools.time_tool.TimeTool` | Resolves temporal expressions and current date context. |
| `policy.official_policy_search` | `official_policy_search` | `gateway_core.tools.policy_tool.PolicyTool` | Returns official-policy evidence; medium risk; scope `policy:read`. |
| `web.search` | `web_search` | `gateway_core.tools.web_search_tool.WebSearchTool` | Returns web evidence; high risk; scope `web:search`. |
| `artifact.chart` | `chart` | `gateway_core.tools.chart_tool.ChartTool` | Produces `chart_artifact`. |
| `artifact.plot` | `plot` | `gateway_core.tools.plot_tool.PlotTool` | Produces `plot_artifact`. |
| `artifact.image_generate` | `generate_image_tool`, `image` | `gateway_core.tools.image_tool.GenerateImageTool` | Produces `image_artifact`. |
| `artifact.slide_generate` | `slide` | `gateway_core.tools.slide_tool.SlideTool` | Produces `slide_artifact`. |

### School SQL ReAct Tools

These are internal to the School SQL executor. They are intentionally closer to
the database and should remain bounded by schema allowlists, DDL evidence, and
read-only query rules.

| Tool | Responsibility |
| --- | --- |
| `ddl_search` | Mandatory first pass for candidate tables, business fields, time fields, latest-row preview, and SQL readiness. |
| `list_available_tables` | Lists tables currently available to the School SQL executor. |
| `inspect_table_schema` | Inspects columns and schema metadata for selected tables. |
| `sample_table_rows` | Samples representative rows when schema alone is not enough. |
| `inspect_jsonb_recordset` | Inspects JSONB recordset structure before JSONB expansion. |
| `jsonb_recordset_query` | Executes bounded JSONB recordset queries. |
| `sql_db_query` | Executes bounded read-only SQL against DDL-vetted tables. |
| `sql_experience_search` | Retrieves prior SQL/query patterns relevant to the question. |
| `suggest_related_queries` | Suggests follow-up query directions from gathered evidence. |
| `trend_analysis` | Computes trend summaries from already gathered data. |
| `anomaly_detection` | Detects anomalies from already gathered rows or evidence. |
| `cohort_compare` | Compares cohorts from already gathered rows or evidence. |

`BusinessPromptContextTool` is an internal context helper for School SQL
contract construction. It should stay a context feeder, not a public evidence
tool, unless it is deliberately promoted into the gateway tool registry.

## Prompt Inventory

### Prompt Layer

The prompt layer primitives are:

- `PromptTemplate`: versioned renderer with `prompt_id`, `version`, tags, and description.
- `PromptRegistry`: single lookup and version registry.
- `PromptBuilder`: composes registered prompt parts and inline literals.
- `PromptRender`: records rendered text plus metadata and part lineage.

New prompt behavior should be registered as a versioned `PromptTemplate` rather
than hidden inside a long inline string.

### Agent Prompts

| Prompt id | Module | Purpose |
| --- | --- | --- |
| `agents.contract_planner.system` | `gateway_core.prompts.agents.contract_planner` | System rules for per-turn contract planning. |
| `agents.contract_planner.user` | `gateway_core.prompts.agents.contract_planner` | User/context payload for contract planning. |
| `agents.school_sql.system` | `gateway_core.prompts.agents.school_sql_agent` | System rules for School SQL evidence retrieval. |
| `agents.final_answer.system` | `gateway_core.prompts.agents.final_answer` | System rules for final answer composition. |
| `agents.final_answer.user` | `gateway_core.prompts.agents.final_answer` | Handoff/context payload for final answer composition. |

### Answer Rule Prompts

| Prompt family | Prompt ids | Boundary |
| --- | --- | --- |
| Context | `answers.context.global_base`, `answers.context.request_protocol`, `answers.context.question_type`, `answers.context.citation_format`, `answers.context.system_data`, `answers.context.teacher_development`, `answers.context.title_evaluation`, `answers.context.audience_reminder` | Request hierarchy, audience, citation, system/data context, teacher/title special cases. |
| Evidence | `answers.evidence.board`, `answers.evidence.complex`, `answers.evidence.policy` | Evidence board, complex evidence, and policy evidence presentation rules. |
| Style | `answers.style.json`, `answers.style.with_data`, `answers.style.no_data` | Output formatting for JSON, no-data, and data-backed answers. |

### Output Contract Prompts

| File | Purpose |
| --- | --- |
| `gateway_core/prompts/output_contracts/per_turn_contract_plan.py` | Prompt-facing description of the per-turn contract plan schema. |
| `gateway_core/prompts/output_contracts/final_handoff.py` | Prompt-facing description of final-answer handoff fields. |

Dynamic inline fragments are still acceptable for true runtime values such as
the current user question, selected schema context, business prompt context,
handoff JSON, and citation source JSON. Stable rules should move into versioned
prompt modules.

## Contract Boundaries

- `ToolContract` tracks the planned route, allowed tools, required outputs,
  completed outputs, and handoff blocks.
- `InterAgentState` is the cross-agent evidence state. It should carry compact
  evidence references, samples, lineage, caveats, and source views; raw rows
  should not be embedded as an unbounded payload.
- `FinalAnswerHandoff` is the compact bridge into final answer composition.
  It must preserve evidence semantics instead of degrading structured evidence
  into a generic text digest.
- Invalid contracts, missing required fields, and broken invariants should fail
  fast with useful context. Broad exception swallowing or silent fallback is not
  an acceptable recovery strategy.

## Maintenance Rules

When adding or changing an agent:

1. Update `SKILL_REGISTRY`.
2. Declare the owned outputs and default tools.
3. Update this inventory.
4. Add or update focused tests for routing, output ownership, and stream behavior.

When adding or changing a tool:

1. Put shared tool behavior behind `GatewayToolRegistry` unless it is strictly
   internal to one executor.
2. Declare aliases, tags, risk level, and scopes.
3. Map tool output to an output contract field when it contributes to final
   answer evidence or artifacts.
4. Update this inventory and registry tests.

When adding or changing a prompt:

1. Prefer `PromptTemplate` registration over inline strings.
2. Version the prompt id.
3. Keep output schemas in `gateway_core/agents/contracts` and prompt-facing
   schema text in `gateway_core/prompts/output_contracts`.
4. Update prompt layer tests and this inventory.

When changing the School SQL handoff:

1. Start from `SCHOOL_DATA_ANSWER_WORKFLOW`.
2. Preserve `InterAgentState` lineage and compact evidence semantics.
3. Keep final prose generation in `answer.compose`, not inside the SQL executor.
4. Run focused contract, prompt, and handoff tests before delivery.

## Cleanup Route

Use this route when the goal is to make every agent, tool, and prompt explicit.

### Pass 1: Agents

1. Start from `SKILL_REGISTRY`.
2. For each skill, write down owned outputs, default tools, stream behavior, and
   what it must not own.
3. Compare each skill with workflow nodes. If a skill is actually a tool, remove
   the agent shape; if a workflow node owns state, declare the contract.
4. Verify with `tests/test_agent_tool_prompt_inventory.py` and agent-specific tests.

### Pass 2: Tools

1. Start from `build_non_sql_tool_registry()` and `DDLReactTools.as_langchain_tools()`.
2. Separate public gateway tools from executor-internal tools.
3. For every public tool, declare canonical name, aliases, risk, scopes, and output field.
4. Verify with `tests/test_gateway_tool_registry.py`,
   `tests/test_langchain_tool_policy.py`, and `tests/test_agent_tool_prompt_inventory.py`.

### Pass 3: Prompts

1. Start from `PROMPT_REGISTRY`.
2. Every production prompt needs a stable prompt id, explicit version, one owner,
   and one responsibility.
3. Stable rules belong in `gateway_core/prompts/rules`; schema text belongs in
   `gateway_core/prompts/output_contracts`; runtime values may remain inline.
4. Verify with `tests/test_prompt_layer.py` and `tests/test_agent_tool_prompt_inventory.py`.

### Pass 4: Contracts And Handoffs

1. Start from `output_contracts.py`, `workflow_contracts.py`,
   `tool_contract.py`, and `inter_agent_state.py`.
2. Check that each output has exactly one owner and one downstream consumer path.
3. Check that compact handoffs preserve lineage, counts, samples, caveats, and
   source views without embedding unbounded raw rows.
4. Verify with `tests/test_output_contracts.py`, `tests/test_tool_contract.py`,
   `tests/test_workflow_contracts.py`, and handoff-focused tests.

### Standard Verification

```bash
python3 scripts/check_file_size.py docs/agent_tool_prompt_inventory.md tests/test_agent_tool_prompt_inventory.py --verbose
python3 -m pytest tests/test_agent_tool_prompt_inventory.py tests/test_prompt_layer.py tests/test_gateway_tool_registry.py tests/test_workflow_contracts.py
```
