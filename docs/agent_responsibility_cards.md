# Agent 责任卡片

这份文档只回答一个问题：当前仓库里每个 agent 到底负责什么，不能负责什么，
出问题时应该看哪里、测哪里。

权威来源是 `SKILL_REGISTRY`。新增、删除或重命名 agent 时，先改
`gateway_core/agents/universal_hub/registry.py`，再同步更新这份文档和
`tests/test_agent_tool_prompt_inventory.py`。

## 一眼看懂

| Agent | 核心定位 | 归属输出 | 不能越界 |
| --- | --- | --- | --- |
| `school_sql` | 学校数据库证据 agent | `data_evidence` | 不写最终回答，不生成图片/PPT，不绕过 DDL。 |
| `chat` | 普通聊天兜底 agent | 无 | 不编造学校事实，不产出数据证据。 |
| `image_generator` | 基于已审计 SQL lineage 的图片产物 agent | `image_artifact` | 不查库，不写聊天历史，不脱离 SQL hash 生图。 |
| `ppt_generator` | 基于校园分析状态的 PPT 产物 agent | `ppt_artifact` | 不查库，不创建 SQL lineage，不输出未校验 URL。 |

## 共享 Agent Contract

所有注册 skill 都实现：

```text
BaseAgentSkill.astream(state, ctx) -> AsyncIterator[SkillEvent]
```

运行时对象的归属规则：

- `state`：只放可序列化业务状态，例如 messages、required_outputs、meta_context。
- `ctx`：只放运行时对象，例如请求句柄、模型客户端、测试替身、emitter、trace。
- 不要把 request/model/trace 这类对象写进 `UniversalAgentState`。

事件输出规则：

- 所有 agent 都输出标准化 `SkillEvent`。
- artifact 类 agent 继承 `BaseMultimodalAgentSkill`。
- artifact 类 agent 必须返回 `MultimodalOutputContract`，并通过
  `evidence_completed` 输出带 proof 的产物。

## `school_sql`

| 字段 | 值 |
| --- | --- |
| 注册类 | `gateway_core.agents.school_sql.school_sql_skill.SchoolSqlSkill` |
| 默认角色 | `data_agent` |
| 归属输出 | `data_evidence` |
| 声明工具 | `ddl_search`, `sql_db_query`, `sample_table_rows` |
| 支持流式 | 是 |
| 主运行入口 | `gateway_core.agents.school_sql.agent_stream.stream_school_sql_agent_native` |
| Prompt 归属 | `agents.school_sql.system` |
| Workflow node | `school_sql.react_execute` |
| 聚焦测试 | `tests/test_image_generation_skill.py`, `tests/test_agent_stream_workflow_trace.py`, `tests/test_agent_stream_direct_snapshot.py`, `tests/test_workflow_contracts.py` |

定位：

`school_sql` 是学校数据证据入口。它负责把用户的数据问题转成 DDL 约束下的只读
SQL 查询和结构化证据，交给后续 handoff / final answer 使用。

输入：

- 用户当前问题。
- `tool_contract` 和 `required_outputs`。
- schema catalog、DDL vector context、SQL experience context。
- school scope、DSN、model、embedding function 等运行时上下文。

输出：

- `data_evidence`
- `evidence_board`
- `source_views`
- SQL lineage
- 给 final answer 使用的紧凑 handoff 材料

可以做：

- 用 `ddl_search` 找候选表、业务字段、时间字段和 SQL readiness。
- 必要时用 schema/sample/JSONB 工具补齐字段理解。
- 用 `sql_db_query` 执行受限只读 SQL。
- 当问题同时要求图片/PPT/chart 时，剥离多模态措辞，只检索真实数据证据。

不能做：

- 不能负责最终自然语言答案生成。
- 不能负责图片、chart、plot、slide、PPT artifact 生成。
- 不能绕过 DDL evidence 直接猜表猜字段。
- 不能在跨 agent state 中塞入无界 raw rows。
- 不能把工具失败编造成业务事实。

失败策略：

- contract 无效或核心不变量缺失时，带 trace context fast fail。
- tool 失败应进入 tool error 或 caveats。
- 空结果要保留查询口径和候选表线索，不能静默降级成“无数据”。

优先审查点：

- `agent_stream.py` 是否继续承担过多 orchestration。
- `final_handoff.py` 是否保留证据语义、计数和 lineage。
- SQL 工具是否仍然以 `ddl_search` 为第一证据门。

## `chat`

| 字段 | 值 |
| --- | --- |
| 注册类 | `gateway_core.agents.chat.chat_skill.ChatSkill` |
| 默认角色 | `general_chat` |
| 归属输出 | 无 |
| 声明工具 | 无 |
| 支持流式 | 是 |
| 主运行入口 | `ChatSkill.astream` |
| Prompt 归属 | 目前没有生产级 `PromptRegistry` id |
| Workflow node | 不属于 `SCHOOL_DATA_ANSWER_WORKFLOW`；由 route/contract plan 选择 |
| 聚焦测试 | `tests/test_contract_planner.py`, `tests/test_agent_native_flow.py`, `tests/test_pipeline_audit.py` |

定位：

`chat` 是普通聊天兜底 agent。它不拥有任何学校数据输出，也不拥有证据生产能力。

输入：

- 用户当前问题。
- 可选的 `chat_stream_fn`。
- 原始 state 和 ctx。

输出：

- 普通 content/process 事件。
- 不产出 `data_evidence`、`policy_evidence` 或 artifact。

可以做：

- 处理 contract-planned `chat` route。
- 如果注入了 `chat_stream_fn`，把它的事件转成标准 `SkillEvent`。
- 如果没有 stream function，只输出最新用户问题，保持测试/运行时安全。

不能做：

- 不能编造学校事实。
- 不能回答需要学校数据的问题。
- 不能调用 SQL、policy、web、artifact tools。
- 不能把兜底回答伪装成已验证证据。

失败策略：

- 没有 stream function 时保持极简，不补事实。
- 未来如果接入生产 chat prompt，需要先注册 prompt id 和测试。

优先审查点：

- 是否需要给生产 chat 增加 `PromptRegistry` id。
- `chat` route 是否被错误用于数据问题。

## `image_generator`

| 字段 | 值 |
| --- | --- |
| 注册类 | `gateway_core.agents.visual.image_generation_skill.ImageGenerationSkill` |
| 默认角色 | `visual_agent` |
| 归属输出 | `image_artifact` |
| 声明工具 | `image_generation` |
| 支持流式 | 是 |
| 主运行入口 | `ImageGenerationSkill.astream` |
| Prompt 归属 | `TripleAxisPromptSynthesizer`，通过 `gateway_core.prompts.prompt_domains` 生成 |
| Workflow node | universal hub 中 `data_evidence` 之后的多模态 worker |
| 聚焦测试 | `tests/test_image_generation_skill.py`, `tests/test_universal_hub_graph.py` |

定位：

`image_generator` 是后置多模态 agent。它只把已经审计过的 SQL lineage 转成图片
artifact，不负责查数据。

输入：

- `meta_context.executed_sql_lineage` 或 `meta_context.sql_lineage`。
- 最新 answer context 或 lineage snapshot。
- 用户历史消息，用于合成图片 prompt 的场景目标。

输出：

- `image_artifact`
- `image_md5_proof`
- CDN URL
- `linked_sql_hash`
- `prompt_used`
- markdown render payload

可以做：

- 选择有效 SQL lineage，优先使用有 row_count 和 tables_used 的记录。
- 用 `TripleAxisPromptSynthesizer` 合成图片 prompt。
- 调用图片工具生成 artifact。
- 校验 artifact URL 后再输出。

不能做：

- 不能查询数据库。
- 不能创建或修改 SQL lineage。
- 不能把图片 URL 写回聊天历史。
- 不能在没有有效 64 字符 SQL hash 时生成图片。
- 不能把敏感个人明细放进图片 prompt。

失败策略：

- 缺少 SQL lineage：跳过生成。
- SQL hash 不完整：跳过生成。
- timeout、缺 URL、tool error、URL 校验失败：记录 multimodal error，并输出 process event。

优先审查点：

- `TripleAxisPromptSynthesizer` 是否已经需要纳入正式 PromptRegistry。
- 图片 prompt 是否严格绑定真实数据快照和 SQL hash。

## `ppt_generator`

| 字段 | 值 |
| --- | --- |
| 注册类 | `gateway_core.agents.ppt.ppt_generation_skill.PptGenerationSkill` |
| 默认角色 | `presentation_agent` |
| 归属输出 | `ppt_artifact` |
| 声明工具 | `ppt_generation` |
| 支持流式 | 是 |
| 主运行入口 | `PptGenerationSkill.astream` |
| Prompt 归属 | 来自 SQL lineage 的 provider payload；目前没有生产级 `PromptRegistry` id |
| Workflow node | universal hub 中 `data_evidence` 之后的多模态 worker |
| 聚焦测试 | `tests/test_image_generation_skill.py`, `tests/test_universal_hub_graph.py` |

定位：

`ppt_generator` 是后置汇报产物 agent。它基于当前校园分析状态生成 PPT artifact，
不负责查数据，也不负责写最终回答正文。

输入：

- 当前 messages。
- school id / tenant id。
- 最近 SQL lineage。
- 可选的 Bailian/provider 调用。

输出：

- `ppt_artifact`
- `ppt_sha256`
- CDN URL
- PPT title
- page count
- pages preview
- render engine

可以做：

- 构造紧凑 provider payload。
- 使用注入 provider 生成 PPT。
- provider 不可用时，回退到 `SlideTool` 本地生成 PPTX。
- 校验 artifact URL 后输出 proof-bearing artifact。

不能做：

- 不能查询数据库。
- 不能创建 SQL lineage。
- 不能生成最终回答正文。
- 不能输出未校验或不可信的外部 artifact URL。

失败策略：

- provider 或本地生成失败时转为 process event。
- 不可信 URL 直接拒绝。
- 产物缺失下载地址时明确失败，不静默补假 URL。

优先审查点：

- PPT outline 规则是否已经复杂到需要注册 prompt。
- provider payload 是否继续保持 compact，不塞原始大 payload。

## Agent 整理顺序

1. 先整理 `school_sql`，因为它连接数据库、tool、handoff、answer，是最大责任面。
2. 再整理 `image_generator` / `ppt_generator`，重点看 lineage lock 和 artifact proof。
3. 最后整理 `chat`，决定它是否继续极简，还是升级成正式生产 chat agent。

## 标准验证

```bash
python3 -m pytest tests/test_agent_tool_prompt_inventory.py tests/test_universal_hub_graph.py tests/test_image_generation_skill.py tests/test_workflow_contracts.py
```
