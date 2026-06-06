# Prompt 责任卡片

这份文档记录 prompt 的归属，以及仍然存在的非 registry prompt surface。
已注册 prompt 是当前受治理的生产 prompt layer。inline parts 只允许用于运行时值，
或明确不作为稳定生产规则的兼容片段。

## 共享 Prompt Contract

生产 prompt 应使用：

- `PromptTemplate`
- `PromptRegistry`
- `PromptBuilder`
- 明确的 prompt id
- 明确的 version
- 一个 owner
- 一个 responsibility

当前已注册 prompt 的版本基线：`2026-06-03.1`。

## 已注册 Agent Prompts

### `agents.contract_planner.system`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.contract_planner` |
| Description | 单轮契约规划器 system prompt |
| Tags | `contract`, `planner`, `system` |

职责：

- 定义 planner 角色和非 SQL tool 边界。
- 防止 planner 写最终答案或执行 SQL。

### `agents.contract_planner.user`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.contract_planner` |
| Description | 单轮契约规划器 user prompt |
| Tags | `contract`, `planner`, `user` |

职责：

- 组装 question、conversation context、metadata context、DDL context、
  business prompt context、SQL experience context 和 available tools。
- 要求输出兼容 per-turn contract plan schema。

### `agents.school_sql.system`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.school_sql_agent` |
| Description | 学校 SQL 数据证据 Agent system prompt |
| Tags | `school_sql`, `agent`, `system` |

职责：

- 将 School SQL 定义为 data evidence agent。
- 要求 SQL 前先做 DDL-first evidence lookup。
- 把 image/chart/PPT 生成排除在 SQL executor 之外。

### `agents.final_answer.system`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.final_answer` |
| Description | 最终答案 system prompt |
| Tags | `final_answer`, `system` |

职责：

- 定义基于客观 evidence packet 的最终答案生成规则。
- 禁止在用户可见答案里叙述内部 tool/process。

### `agents.final_answer.user`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.agents.final_answer` |
| Description | 最终答案 user prompt |
| Tags | `final_answer`, `user` |

职责：

- 把最终 evidence packet 交给 answer model。
- 确保最终答案基于 handoff payload。

## 已注册 Answer Context Prompts

### `answers.context.system_data`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.answer_context_prompt` |
| Description | 最终回答前的 system/data context prompt |
| Tags | `answer`, `context`, `system_data` |

职责：

- 通过 `PromptBuilder` 组合最终 data context prompt。
- 记录有序 prompt parts，方便 trace。

### `answers.context.global_base`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答检索结果基础规则 |
| Tags | `answer`, `context`, `rule` |

职责：

- 约束最终回答只能基于证据，并避免错误的“无数据”回答。

### `answers.context.request_protocol`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答请求层级规则 |
| Tags | `answer`, `context`, `request` |

职责：

- 定义当前用户问题、data context、system prompt、历史消息之间的优先级。

### `answers.context.question_type`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答问题类型表达规则 |
| Tags | `answer`, `context`, `question_type` |

职责：

- 按问题类型引导答案结构，但不强行套固定模板。

### `answers.context.citation_format`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答引用格式规则 |
| Tags | `answer`, `context`, `citation` |

职责：

- 对官方/网页证据要求编号引用，同时把来源清单交给 OpenWebUI citation 组件展示。

### `answers.context.teacher_development`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 教师发展成果表达规则 |
| Tags | `answer`, `context`, `teacher_development` |

职责：

- 要求教师发展类回答包含有证据支持的总量、分层、代表性成果和可用于申报/述职的表达。

### `answers.context.title_evaluation`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 职称判断表达规则 |
| Tags | `answer`, `context`, `title_evaluation` |

职责：

- 区分校内数据事实、政策硬条件和待人工核验项。

### `answers.context.audience_reminder`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_context` |
| Description | 最终回答受众视角提醒 |
| Tags | `answer`, `context`, `audience` |

职责：

- 在适用时补充校长视角或年级组长/班主任视角提醒。

## 已注册 Evidence Prompts

### `answers.evidence.board`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_evidence` |
| Description | Evidence Board 回答规则 |
| Tags | `answer`, `evidence`, `board` |

职责：

- 说明最终回答如何使用 Evidence Board tasks。

### `answers.evidence.complex`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_evidence` |
| Description | 复杂多证据回答规则 |
| Tags | `answer`, `evidence`, `complex` |

职责：

- 引导多类证据组合场景下的最终回答。

### `answers.evidence.policy`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_evidence` |
| Description | 政策证据回答规则 |
| Tags | `answer`, `evidence`, `policy` |

职责：

- 让政策类回答严格基于官方政策证据。

## 已注册 Style Prompts

### `answers.style.json`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_style` |
| Description | 用户要求 JSON 时的最终回答风格约束 |
| Tags | `answer`, `style`, `json` |

职责：

- 当用户明确要求 JSON 时，约束最终输出为合法 JSON。

### `answers.style.with_data`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_style` |
| Description | 有数据上下文时的最终回答风格约束 |
| Tags | `answer`, `style`, `data` |

职责：

- 要求有数据上下文的答案基于证据得出结论，防止无依据数字或判断。

### `answers.style.no_data`

| 字段 | 值 |
| --- | --- |
| Version | `2026-06-03.1` |
| Owner module | `gateway_core.prompts.rules.answer_style` |
| Description | 无数据上下文时的最终回答风格约束 |
| Tags | `answer`, `style`, `no_data` |

职责：

- 在没有数据上下文时防止编造数据。

## PromptBuilder Inline Parts

这些 prompt ids 是 `answers.context.system_data` 的 inline parts。它们允许存在，
因为它们表示运行时值或按条件选择的上下文。

| Inline id | Owner | 用途 |
| --- | --- | --- |
| `answers.context.agent_label` | `answer_context_prompt.py` | 运行时 agent label 行。 |
| `answers.context.domain_layer` | `answer_context_prompt.py` | 已解析 domain layer 行。 |
| `answers.context.expert_playbook` | `answer_context_prompt.py` | 特定 domain 的 expert playbook 文本。 |
| `answers.context.cross_domain` | `answer_context_prompt.py` | 跨域分析层。 |
| `answers.context.final_guide` | `answer_context_prompt.py` | 最终回答 guide 文本。 |
| `answers.context.current_question` | `answer_context_prompt.py` | 当前用户问题。 |
| `answers.context.note` | `answer_context_prompt.py` | 可选运行时 note。 |
| `answers.context.citation_sources` | `answer_context_prompt.py` | 运行时 citation source JSON。 |
| `answers.context.payload_json` | `answer_context_prompt.py` | 运行时 evidence payload JSON。 |

## 非 Registry Prompt Surfaces

| Surface | Owner | 当前状态 | 清理规则 |
| --- | --- | --- | --- |
| `build_data_format_guard` | `gateway_core.prompts.data_format_guard` | 根据问题和数据 flags 动态生成回答格式 guard。 | 保持动态；如果稳定规则被多处复用，再抽到 registered prompt。 |
| `prompt_domains` image matrices | `gateway_core.prompts.prompt_domains` | `TripleAxisPromptSynthesizer` 使用的图片 prompt 矩阵。 | 如果图片 prompt policy 进入生产治理面，就提升到 registry。 |
| `build_class_grade_portrait_note` | `gateway_core.prompts.prompt_registry` | 班级/年级画像 note helper。 | 如果作为稳定生产措辞使用，就迁移成 registered prompt。 |
| `build_student_teacher_portrait_note` | `gateway_core.prompts.prompt_registry` | 学生/教师画像 note helper。 | 如果作为稳定生产措辞使用，就迁移成 registered prompt。 |
| `audience_answer_guard` | `gateway_core.prompts.prompt_registry` | 面向特定受众的 runtime guard。 | 只要仍是逻辑 guard 就保留；如果措辞扩张，再抽到 prompt。 |
| `_stream_contract_plan_chat` user prompt | `gateway_core.agents.school_sql.agent_stream` | 围绕当前问题/历史的动态 chat-route prompt。 | 保持短小；如果行为扩张，就迁移到 registry。 |
| `PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT` | `gateway_core.prompts.output_contracts.per_turn_contract_plan` | 从 runtime contract 生成的面向 prompt 的 schema。 | 和 `output_contracts.py` 保持同步。 |
| `FINAL_ANSWER_HANDOFF_DESCRIPTION` | `gateway_core.prompts.output_contracts.final_handoff` | 面向 prompt 的 handoff tool 描述。 | 和 final handoff contract fields 保持同步。 |

## 下一步审查顺序

1. 保持所有生产 prompt 措辞都在 `PromptRegistry` 之下。
2. 修改回答行为前，先审查非 registry prompt surfaces。
3. 如果某个非 registry surface 变成稳定生产 policy，就给它注册 prompt id 和 version。
4. 任何改变行为的 prompt 修改，都要 bump version 并更新测试。
