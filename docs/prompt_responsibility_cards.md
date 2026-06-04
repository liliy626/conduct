# Prompt Responsibility Cards

This file records prompt ownership and remaining prompt surfaces. Registered
prompts are the governed production prompt layer. Inline parts are allowed only
for runtime values or compatibility fragments that are intentionally not stable
production rules.

## Shared Prompt Contract

Production prompts should use:

- `PromptTemplate`
- `PromptRegistry`
- `PromptBuilder`
- explicit prompt id
- explicit version
- one owner
- one responsibility

Current registered prompt version baseline: `2026-06-03.1`.

## Registered Agent Prompts

### `agents.contract_planner.system`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.contract_planner` |
| Description | 单轮契约规划器 system prompt |
| Tags | `contract`, `planner`, `system` |

Responsibility:

- Define the planner role and non-SQL tool boundary.
- Prevent final answer writing and SQL execution inside the planner.

### `agents.contract_planner.user`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.contract_planner` |
| Description | 单轮契约规划器 user prompt |
| Tags | `contract`, `planner`, `user` |

Responsibility:

- Assemble question, conversation context, metadata context, DDL context,
  business prompt context, SQL experience context, and available tools.
- Require output compatible with the per-turn contract plan schema.

### `agents.school_sql.system`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.school_sql_agent` |
| Description | 学校 SQL 数据证据 Agent system prompt |
| Tags | `school_sql`, `agent`, `system` |

Responsibility:

- Define School SQL as a data evidence agent.
- Require DDL-first evidence lookup before SQL.
- Keep image/chart/PPT generation out of the SQL executor.

### `agents.final_answer.system`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.final_answer` |
| Description | 最终答案 system prompt |
| Tags | `final_answer`, `system` |

Responsibility:

- Define final answer composition from objective evidence packets.
- Ban internal tool/process narration in user-facing answers.

### `agents.final_answer.user`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.final_answer` |
| Description | 最终答案 user prompt |
| Tags | `final_answer`, `user` |

Responsibility:

- Provide the final evidence packet to the answer model.
- Keep the final answer grounded in the handoff payload.

## Registered Answer Context Prompts

### `answers.context.system_data`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.answer_context_prompt` |
| Description | 最终回答前的 system/data context prompt |
| Tags | `answer`, `context`, `system_data` |

Responsibility:

- Compose the final data context prompt through `PromptBuilder`.
- Record ordered prompt parts for traceability.

### `answers.context.global_base`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答检索结果基础规则 |
| Tags | `answer`, `context`, `rule` |

Responsibility:

- Enforce evidence-only final answering and prevent false no-data answers.

### `answers.context.request_protocol`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答请求层级规则 |
| Tags | `answer`, `context`, `request` |

Responsibility:

- Define precedence among current user question, data context, system prompt,
  and historical messages.

### `answers.context.question_type`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答问题类型表达规则 |
| Tags | `answer`, `context`, `question_type` |

Responsibility:

- Guide answer shape by question type without forcing rigid templates.

### `answers.context.citation_format`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答引用格式规则 |
| Tags | `answer`, `context`, `citation` |

Responsibility:

- Require numbered citations for official/web evidence while leaving source
  lists to the OpenWebUI citation component.

### `answers.context.teacher_development`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 教师发展成果表达规则 |
| Tags | `answer`, `context`, `teacher_development` |

Responsibility:

- Force teacher development answers to include supported totals, layers,
  representative achievements, and application-ready wording.

### `answers.context.title_evaluation`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 职称判断表达规则 |
| Tags | `answer`, `context`, `title_evaluation` |

Responsibility:

- Separate internal data facts, policy hard conditions, and manual verification
  items for title evaluation.

### `answers.context.audience_reminder`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答受众视角提醒 |
| Tags | `answer`, `context`, `audience` |

Responsibility:

- Add principal or grade/homeroom audience reminders when applicable.

## Registered Evidence Prompts

### `answers.evidence.board`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_evidence` |
| Description | Evidence Board 回答规则 |
| Tags | `answer`, `evidence`, `board` |

Responsibility:

- Explain how to use Evidence Board tasks in final answers.

### `answers.evidence.complex`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_evidence` |
| Description | 复杂多证据回答规则 |
| Tags | `answer`, `evidence`, `complex` |

Responsibility:

- Guide answers that combine multiple evidence families.

### `answers.evidence.policy`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_evidence` |
| Description | 政策证据回答规则 |
| Tags | `answer`, `evidence`, `policy` |

Responsibility:

- Keep policy answers grounded in official policy evidence.

## Registered Style Prompts

### `answers.style.json`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_style` |
| Description | 用户要求 JSON 时的最终回答风格约束 |
| Tags | `answer`, `style`, `json` |

Responsibility:

- Enforce valid JSON when the user explicitly asks for JSON.

### `answers.style.with_data`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_style` |
| Description | 有数据上下文时的最终回答风格约束 |
| Tags | `answer`, `style`, `data` |

Responsibility:

- Require data-backed conclusions and prevent unsupported values.

### `answers.style.no_data`

| Field | Value |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_style` |
| Description | 无数据上下文时的最终回答风格约束 |
| Tags | `answer`, `style`, `no_data` |

Responsibility:

- Prevent fabricated data when no data context is present.

## PromptBuilder Inline Parts

These prompt ids are inline parts of `answers.context.system_data`. They are
allowed because they represent runtime values or conditionally selected context.

| Inline id | Owner | Purpose |
| --- | --- | --- |
| `answers.context.agent_label` | `answer_context_prompt.py` | Runtime agent label line. |
| `answers.context.domain_layer` | `answer_context_prompt.py` | Resolved domain layer line. |
| `answers.context.expert_playbook` | `answer_context_prompt.py` | Domain-specific expert playbook text. |
| `answers.context.cross_domain` | `answer_context_prompt.py` | Cross-domain analysis layer. |
| `answers.context.final_guide` | `answer_context_prompt.py` | Final answer guide text. |
| `answers.context.current_question` | `answer_context_prompt.py` | Current user question. |
| `answers.context.note` | `answer_context_prompt.py` | Optional runtime note. |
| `answers.context.citation_sources` | `answer_context_prompt.py` | Runtime citation source JSON. |
| `answers.context.payload_json` | `answer_context_prompt.py` | Runtime evidence payload JSON. |

## Non-Registry Prompt Surfaces

| Surface | Owner | Current status | Cleanup rule |
| --- | --- | --- | --- |
| `build_data_format_guard` | `gateway_core.prompts.data_format_guard` | Dynamic answer format guard based on question/data flags. | Keep dynamic, but extract stable repeated rules into registered prompts if reused elsewhere. |
| `prompt_domains` image matrices | `gateway_core.prompts.prompt_domains` | Prompt matrices used by `TripleAxisPromptSynthesizer`. | Promote to registry if image prompt policy becomes production-governed. |
| `build_class_grade_portrait_note` | `gateway_core.prompts.prompt_registry` | Portrait note helper. | Move to registered prompt if used as stable production wording. |
| `build_student_teacher_portrait_note` | `gateway_core.prompts.prompt_registry` | Student/teacher portrait note helper. | Move to registered prompt if used as stable production wording. |
| `audience_answer_guard` | `gateway_core.prompts.prompt_registry` | Runtime guard for audience-specific direct style. | Keep as logic guard unless wording grows. |
| `_stream_contract_plan_chat` user prompt | `gateway_core.agents.school_sql.agent_stream` | Dynamic chat-route prompt around current question/history. | Keep short; move to registry if behavior expands. |
| `PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT` | `gateway_core.prompts.output_contracts.per_turn_contract_plan` | Prompt-facing schema generated from runtime contract. | Keep synced with `output_contracts.py`. |
| `FINAL_ANSWER_HANDOFF_DESCRIPTION` | `gateway_core.prompts.output_contracts.final_handoff` | Prompt-facing handoff tool description. | Keep synced with final handoff contract fields. |

## Next Review Order

1. Keep all production prompt wording under `PromptRegistry`.
2. Review non-registry prompt surfaces before changing answer behavior.
3. If a non-registry surface becomes stable production policy, register it with
   a prompt id and version.
4. For any behavior-changing prompt edit, bump version and update tests.
