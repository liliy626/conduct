# Agent / Tool / Prompt 总览

这份文档是仓库级系统地图，用来说明各个 agent 的职责、tool 的边界、
prompt 的归属，以及运行时 contract 的主线。以后新增 agent、tool、
prompt 模块或 handoff contract 时，都要同步更新这里。

## 权威来源

| 范围 | 权威文件 | 用途 |
| --- | --- | --- |
| Agent 注册表 | `gateway_core/agents/universal_hub/registry.py` | 声明 skill 名称、输出归属、默认工具和流式能力。 |
| Workflow contract | `gateway_core/agents/contracts/workflow_contracts.py` | 声明节点顺序、执行器归属、读写字段、工具策略和 trace 策略。 |
| Output contract | `gateway_core/agents/contracts/output_contracts.py` | 声明跨 agent 输出名、tool 输出、handoff 字段和 contract 版本。 |
| Prompt 注册表 | `gateway_core/prompts/prompt_layer.py` 与 `gateway_core/prompts/**` | 按 `prompt_id` 注册带版本的 prompt 模板。 |
| Gateway tool 注册表 | `gateway_core/tools/core/registry.py` 与 `gateway_core/tools/catalog/non_sql.py` | 注册非 SQL 工具、别名、风险等级、标签和权限 scope。 |
| School SQL 工具 | `gateway_core/agents/school_sql/sql_tools.py` | 暴露 School SQL executor 内部使用的 ReAct 工具。 |

更细的分工文档：

- 每个 agent 的运行边界：`docs/agent_responsibility_cards.md`
- 每个 tool 的运行边界：`docs/tool_responsibility_cards.md`
- 每个 prompt 的运行边界：`docs/prompt_responsibility_cards.md`
- 多智能体长期迭代资产包：`docs/multi_agent_iteration_assets.md`

当前 contract 版本：

- `OUTPUT_CONTRACT_VERSION = "2026-06-03.1"`
- `WORKFLOW_CONTRACT_VERSION = "2026-06-04.1"`

## 运行主链路

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

学校数据问答的标准 workflow 是 `SCHOOL_DATA_ANSWER_WORKFLOW`，
workflow id 是 `school_data_answer`。

| Step | Executor | 职责 | 读取 | 写入 |
| --- | --- | --- | --- | --- |
| `route.resolve` | `gateway` | 解析租户、鉴权和路由。 | request headers, messages | route, tenant |
| `context.build_school` | `context_builder` | 选择并压缩学校元数据上下文。 | tenant, question | schema, SQL experience, business prompt context |
| `contract.plan` | `contract_planner` | 规划本轮 route、tools、required outputs 和 answer focus。 | question, conversation, schema, DDL, business prompt context | tool contract, required outputs |
| `school_sql.react_execute` | `school_sql_react` | 通过 DDL 和只读 SQL 工具查询学校数据证据。 | question, tool contract, schema, conversation | data evidence, evidence board, source views |
| `evidence.normalize_inter_agent_state` | `workflow` | 把 SQL 证据标准化成可序列化的 `InterAgentState`。 | data evidence, evidence board, source views, tool contract | inter-agent state |
| `handoff.final_answer` | `workflow` | 从 `InterAgentState` 构造紧凑的 final-answer handoff。 | inter-agent state | handoff payload |
| `answer.compose` | `final_answer` | 基于已验证证据生成最终自然语言答案。 | handoff payload, business prompt context | final answer |

## Agent 清单

| Skill | 实现 | 归属输出 | 声明工具 | 角色 | 边界 |
| --- | --- | --- | --- | --- | --- |
| `school_sql` | `gateway_core.agents.school_sql.school_sql_skill.SchoolSqlSkill` | `data_evidence` | `ddl_search`, `sql_db_query`, `sample_table_rows` | `data_agent` | 负责学校数据库证据；不负责最终表述、政策检索或原始行无界倾倒。 |
| `chat` | `gateway_core.agents.chat.chat_skill.ChatSkill` | 无 | 无 | `general_chat` | 负责普通对话兜底；不能伪造数据证据。 |
| `image_generator` | `gateway_core.agents.visual.image_generation_skill.ImageGenerationSkill` | `image_artifact` | `image_generation` | `visual_agent` | 负责图片产物生成；不回答 School SQL 问题。 |
| `ppt_generator` | `gateway_core.agents.ppt.ppt_generation_skill.PptGenerationSkill` | `ppt_artifact` | `ppt_generation` | `presentation_agent` | 负责 PPT 产物生成；不负责证据检索。 |

`policy_only` 已经不再是 agent。政策检索是 tool 能力
（`policy.official_policy_search`），可在 turn contract 中贡献
`policy_evidence`。

## Tool 清单

### Gateway Tools

这些工具通过 `GatewayToolRegistry` 注册，适合被跨 agent 规划使用。

| Canonical name | Aliases | 实现 | 输出边界 |
| --- | --- | --- | --- |
| `time.resolve` | `time` | `gateway_core.tools.time_tool.TimeTool` | 解析时间表达和当前日期上下文。 |
| `policy.official_policy_search` | `official_policy_search` | `gateway_core.tools.policy_tool.PolicyTool` | 返回官方政策证据；中风险；scope 为 `policy:read`。 |
| `web.search` | `web_search` | `gateway_core.tools.web_search_tool.WebSearchTool` | 返回网页证据；高风险；scope 为 `web:search`。 |
| `artifact.chart` | `chart` | `gateway_core.tools.chart_tool.ChartTool` | 产出 `chart_artifact`。 |
| `artifact.plot` | `plot` | `gateway_core.tools.plot_tool.PlotTool` | 产出 `plot_artifact`。 |
| `artifact.image_generate` | `generate_image_tool`, `image` | `gateway_core.tools.image_tool.GenerateImageTool` | 产出 `image_artifact`。 |
| `artifact.slide_generate` | `slide` | `gateway_core.tools.slide_tool.SlideTool` | 产出 `slide_artifact`。 |

### School SQL ReAct Tools

这些工具只属于 School SQL executor 内部。它们更靠近数据库层，因此必须受
schema allowlist、DDL 证据和只读查询规则约束。

| Tool | 职责 |
| --- | --- |
| `ddl_search` | SQL 前置第一步：查候选表、业务字段、时间字段、最新行预览和 SQL readiness。 |
| `list_available_tables` | 列出当前 School SQL executor 可用表。 |
| `inspect_table_schema` | 检查选中表的字段和 schema 元数据。 |
| `sample_table_rows` | 当 schema 不足以判断时抽样代表性行。 |
| `inspect_jsonb_recordset` | JSONB 展开前检查 recordset 结构。 |
| `jsonb_recordset_query` | 执行受限的 JSONB recordset 查询。 |
| `sql_db_query` | 对 DDL 验证过的表执行受限只读 SQL。 |
| `sql_experience_search` | 检索与当前问题相关的历史 SQL/查询模式。 |
| `suggest_related_queries` | 基于已有证据建议后续查询方向。 |
| `trend_analysis` | 基于已收集数据计算趋势摘要。 |
| `anomaly_detection` | 基于已收集行或证据检测异常。 |
| `cohort_compare` | 基于已收集行或证据做群组对比。 |

`BusinessPromptContextTool` 是 School SQL contract 构造时使用的内部上下文
助手。除非明确提升到 gateway tool registry，否则它应保持为 context feeder，
不是公共证据工具。

## Prompt 清单

### Prompt Layer

Prompt 层的基础对象：

- `PromptTemplate`：带 `prompt_id`、`version`、tags、description 的版本化 renderer。
- `PromptRegistry`：统一 prompt 查找和版本注册。
- `PromptBuilder`：组合已注册 prompt 片段和 inline literal。
- `PromptRender`：记录渲染后的文本、元数据和 part lineage。

新的稳定 prompt 行为应该注册成带版本的 `PromptTemplate`，不要藏在很长的
inline 字符串里。

### Agent Prompts

| Prompt id | 模块 | 用途 |
| --- | --- | --- |
| `agents.contract_planner.system` | `gateway_core.prompts.agents.contract_planner` | 单轮 contract planning 的 system 规则。 |
| `agents.contract_planner.user` | `gateway_core.prompts.agents.contract_planner` | 单轮 contract planning 的 user/context payload。 |
| `agents.school_sql.system` | `gateway_core.prompts.agents.school_sql_agent` | School SQL 证据检索的 system 规则。 |
| `agents.final_answer.system` | `gateway_core.prompts.agents.final_answer` | 最终答案生成的 system 规则。 |
| `agents.final_answer.user` | `gateway_core.prompts.agents.final_answer` | 最终答案生成的 handoff/context payload。 |

### Answer Rule Prompts

| Prompt family | Prompt ids | 边界 |
| --- | --- | --- |
| Context | `answers.context.global_base`, `answers.context.request_protocol`, `answers.context.question_type`, `answers.context.citation_format`, `answers.context.system_data`, `answers.context.teacher_development`, `answers.context.title_evaluation`, `answers.context.audience_reminder` | 请求层级、受众、引用、system/data context、教师发展和职称特殊规则。 |
| Evidence | `answers.evidence.board`, `answers.evidence.complex`, `answers.evidence.policy` | Evidence Board、多证据和政策证据表达规则。 |
| Style | `answers.style.json`, `answers.style.with_data`, `answers.style.no_data` | JSON、无数据、有数据上下文时的最终回答风格约束。 |

### Output Contract Prompts

| 文件 | 用途 |
| --- | --- |
| `gateway_core/prompts/output_contracts/per_turn_contract_plan.py` | 面向 prompt 的 per-turn contract plan schema 描述。 |
| `gateway_core/prompts/output_contracts/final_handoff.py` | 面向 prompt 的 final-answer handoff 字段描述。 |

真正的运行时动态片段仍然可以 inline，例如当前用户问题、选中的 schema context、
business prompt context、handoff JSON、citation sources JSON。稳定规则应迁移
到带版本的 prompt 模块。

## Contract 边界

- `ToolContract` 记录本轮 route、allowed tools、required outputs、
  completed outputs 和 handoff blocks。
- `InterAgentState` 是跨 agent 证据状态。它应该携带紧凑证据引用、样本、
  lineage、caveats 和 source views；不要嵌入无界 raw rows。
- `FinalAnswerHandoff` 是进入最终回答生成的紧凑桥梁。它必须保留证据语义，
  不能把结构化证据降级成普通 text digest。
- contract 无效、必填字段缺失、核心不变量破坏时，应带上下文 fast fail。
  broad exception swallowing 或 silent fallback 不是可接受的恢复策略。

### `data_evidence` 字段级门禁

`RawDataEvidencePayload` 是 SQL 工具产物进入 `InterAgentState` 前的硬门禁。
它由 `gateway_core/agents/contracts/inter_agent_state.py` 中的 Pydantic 模型校验。

必填字段：

- `task_id`
- `allowed`，且必须为 `True`
- `intent`
- `dataset_label`
- `row_count`
- `sql_lineage`
- `evidence_summary`
- `raw_sql_handle`

样本和截断规则：

- `row_count > 0` 时，必须提供 `row_sample` 或 `display_rows`。
- 样本截断只能使用原生切片，并必须在 `RawDataPolicy` 中保留
  `original_count`、`included_count`、`truncated`。
- `ToolContract` 只有在 `validate_data_evidence_payload()` 通过后，才能标记
  `data_evidence` completed。
- `InterAgentState` 保留 ref/sample/lineage/counts，不嵌入无界 `raw_rows`。

## 维护规则

新增或修改 agent 时：

1. 更新 `SKILL_REGISTRY`。
2. 声明归属输出和默认工具。
3. 更新本 inventory。
4. 增加或更新路由、输出归属、stream 行为相关测试。

新增或修改 tool 时：

1. 如果是共享工具，优先通过 `GatewayToolRegistry` 管理；只有严格属于单个
   executor 的工具才保留为内部工具。
2. 声明 aliases、tags、risk level 和 scopes。
3. 如果 tool 输出会进入最终答案证据或 artifact，映射到 output contract 字段。
4. 更新本 inventory 和 registry 测试。

新增或修改 prompt 时：

1. 优先注册 `PromptTemplate`，不要新增 inline 长字符串。
2. 维护明确的 prompt id 和版本。
3. 输出 schema 放在 `gateway_core/agents/contracts`，面向 prompt 的 schema
   文本放在 `gateway_core/prompts/output_contracts`。
4. 更新 prompt layer 测试和本 inventory。

修改 School SQL handoff 时：

1. 从 `SCHOOL_DATA_ANSWER_WORKFLOW` 开始看。
2. 保留 `InterAgentState` lineage 和紧凑证据语义。
3. 最终自然语言表达留在 `answer.compose`，不要塞进 SQL executor。
4. 交付前运行 contract、prompt、handoff 相关聚焦测试。

## 清理路线

当目标是把所有 agent、tool、prompt 都显式化时，按这个顺序走。

### Pass 1：Agents

1. 从 `SKILL_REGISTRY` 开始。
2. 对每个 skill 写清楚归属输出、默认工具、stream 行为，以及它不能负责什么。
3. 和 workflow nodes 对照。如果某个 skill 实际是 tool，就移除 agent 形态；
   如果某个 workflow node 持有状态，就声明 contract。
4. 用 `tests/test_agent_tool_prompt_inventory.py` 和 agent 专项测试验证。

### Pass 2：Tools

1. 从 `build_non_sql_tool_registry()` 和 `DDLReactTools.as_langchain_tools()` 开始。
2. 区分公共 gateway tools 与 executor 内部工具。
3. 每个公共 tool 都要声明 canonical name、aliases、risk、scopes 和 output field。
4. 用 `tests/test_gateway_tool_registry.py`、`tests/test_langchain_tool_policy.py`
   和 `tests/test_agent_tool_prompt_inventory.py` 验证。

### Pass 3：Prompts

1. 从 `PROMPT_REGISTRY` 开始。
2. 每个生产 prompt 都要有稳定 prompt id、明确 version、一个 owner、一个 responsibility。
3. 稳定规则放在 `gateway_core/prompts/rules`；schema 文本放在
   `gateway_core/prompts/output_contracts`；运行时值可以保留 inline。
4. 用 `tests/test_prompt_layer.py` 和 `tests/test_agent_tool_prompt_inventory.py` 验证。

### Pass 4：Contracts And Handoffs

1. 从 `output_contracts.py`、`workflow_contracts.py`、`tool_contract.py`
   和 `inter_agent_state.py` 开始。
2. 检查每个 output 是否只有一个 owner 和一条明确下游消费路径。
3. 检查紧凑 handoff 是否保留 lineage、counts、samples、caveats、
   source views，且不嵌入无界 raw rows。
4. 用 `tests/test_output_contracts.py`、`tests/test_tool_contract.py`、
   `tests/test_workflow_contracts.py` 和 handoff 专项测试验证。

### 标准验证

```bash
python3 scripts/check_file_size.py docs/agent_tool_prompt_inventory.md tests/test_agent_tool_prompt_inventory.py --verbose
python3 -m pytest tests/test_agent_tool_prompt_inventory.py tests/test_prompt_layer.py tests/test_gateway_tool_registry.py tests/test_workflow_contracts.py
```
