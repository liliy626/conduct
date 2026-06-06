# Tool 责任卡片

这份文档只回答一个问题：每个 tool 到底能做什么、不能做什么，以及它应该怎样
被 agent 调用。

权威来源：

- 公共工具：`gateway_core/tools/catalog/non_sql.py`
- 工具注册表：`gateway_core/tools/core/registry.py`
- 旧工具适配层：`gateway_core/tools/core/agent_tool_adapter.py`
- School SQL 内部工具：`gateway_core/agents/school_sql/sql_tools.py`

## 一眼看懂

| Tool 类型 | 代表工具 | 谁能用 | 核心边界 |
| --- | --- | --- | --- |
| 时间工具 | `time.resolve` | contract planner / answer 链路 | 只解析时间口径，不查业务数据。 |
| 政策工具 | `policy.official_policy_search` | 需要官方政策证据的回合 | 只查官方政策证据，不替代校内事实。 |
| Web 工具 | `web.search` | 需要公开网页证据的回合 | 必须隐私清洗；禁传学生/教师敏感上下文。 |
| Artifact 工具 | `artifact.chart`, `artifact.plot`, `artifact.image_generate`, `artifact.slide_generate` | 多模态/产物回合 | 只基于已有证据或清洗 prompt 生成产物，不查 SQL。 |
| 内部 context 工具 | `business_prompt_context` | School SQL contract/context 构造 | 只给业务证据边界，不给事实真值。 |
| School SQL ReAct 工具 | `ddl_search`, `sql_db_query` 等 | `school_sql` executor 内部 | 只读、DDL-first、受 schema allowlist 约束。 |

## 共享 Tool Contract

公共 gateway tools 统一通过 `GatewayToolRegistry` 暴露，并返回 `ToolResult`。

`ToolResult` 主要字段：`ok`、`structured_content`、`evidence`、
`artifacts`、`sources`、`lineage`、`warnings`、`error`、`duration_ms`。

旧版 `AgentTool` 通过 `AgentToolGatewayAdapter` 接入公共 registry。planner 可见工具
应使用 canonical name；alias 只用于兼容旧调用名。

## 公共 Gateway Tools

### `time.resolve`

| 字段 | 值 |
| --- | --- |
| Aliases | `time` |
| 实现 | `gateway_core.tools.time_tool.TimeTool` |
| Tags | `time` |
| Risk | `low` |
| Scopes | 无 |
| 主要输出 | `time_context` evidence 和 JSON artifact |

输入：

- `question` / `text`
- 可选 `timezone`
- 可选 `reference_date`

输出：

- timezone、today、now。
- start_date、end_date、granularity、confidence。
- 面向 SQL/回答链路的时间口径 instruction。

可以做：

- 解析“今天、昨天、本周、最近一个月、近三年”等时间表达。
- 提供 gateway 当前时间上下文。

不能做：

- 不能查询业务数据。
- 不能替 SQL 自动加过滤条件。
- 不能解释政策或业务结论。

失败边界：

- 空问题返回无时间范围的 time context。
- 非法 timezone 回退到默认 `Asia/Shanghai`。

### `policy.official_policy_search`

| 字段 | 值 |
| --- | --- |
| Aliases | `official_policy_search` |
| 实现 | `gateway_core.tools.policy_tool.PolicyTool` |
| Tags | `policy`, `evidence` |
| Risk | `medium` |
| Scopes | `policy:read` |
| 主要输出 | `official_policy_evidence`、policy JSON artifact、sources |

输入：

- `query` / `question` / `input`

输出：

- policy evidence chunk count。
- official policy JSON artifact。
- 可引用 sources 和 official URLs。

可以做：

- 检索职称、荣誉、申报条件、年限、评审主体、一票否决、继续教育等官方政策证据。
- 为 final answer 提供政策依据。

不能做：

- 不能替代校内数据库事实。
- 不能在缺少政策证据时判断“满足/不满足”。
- 不能编造政策名称、文号或链接。

失败边界：

- `query` 为空时返回 `ok=False`。
- provider 报错时返回明确 `error`，不返回假证据。

### `web.search`

| 字段 | 值 |
| --- | --- |
| Aliases | `web_search` |
| 实现 | `gateway_core.tools.web_search_tool.WebSearchTool` |
| Tags | `web`, `evidence` |
| Risk | `high` |
| Scopes | `web:search` |
| 主要输出 | `web_search_result` JSON artifact 和 sources |

输入：

- `query` / `input`
- 可选 `locale`、`date`、`topic`、`policy_domain`

输出：

- 成功时返回 `web_search_status` evidence、`web_search_result` JSON artifact 和 sources。
- 失败时仍返回 `web_search_status` evidence，说明是否调用了外部 provider。

可以做：

- 在需要最新公开网页或出处链接时执行外部搜索。
- 在 provider 可用且开关启用时返回网页证据。

不能做：

- 不能把学生姓名、教师姓名、敏感上下文发到外网。
- 不能把“搜索关闭/ provider 不可用”解释为“事实不存在”。
- 不能返回大量原始 provider payload。

失败边界：

- `invalid_input`：`query/input` 为空，`ok=False`，不调用 provider。
- `privacy_blocked`：检测到敏感上下文，`ok=False`，不调用 provider。
- `disabled` / `provider_unavailable`：工具未启用或 provider 缺失，`ok=False`，不能解释成“没有搜索结果”。
- `provider_error`：provider 调用失败，`ok=False`，保留明确 error。

### `artifact.chart`

| 字段 | 值 |
| --- | --- |
| Aliases | `chart` |
| 实现 | `gateway_core.tools.chart_tool.ChartTool` |
| Tags | `artifact`, `chart` |
| Risk | `low` |
| Scopes | 无 |
| 主要输出 | HTML/JSON/SVG chart artifact |

输入：

- `rows` / `evidence_rows`
- `chart_type`
- 可选 `title`、`x_field`、`y_field`、`series`

输出：

- HTML preview。
- JSON data artifact。
- 非 table 类型可输出 SVG。

可以做：

- 基于已有 evidence rows 生成表格、折线图、柱状图、堆叠柱状图、饼图。
- 自动推断常见 label/value 字段。

不能做：

- 不能执行 SQL。
- 不能接受 `sql` / `sql_query` / `query_sql` / `query` / `input` 中的 SQL 文本。
- 不能在没有 rows 时编造图表。
- 不能在非 table 图缺少数值字段时生成全 0 假图。
- 不能替代 `plot` 生成 PNG。

失败边界：

- 传入 SQL 文本时直接拒绝。
- rows 缺失或非 table 图无法解析数值字段时返回 `ok=False`。
- 不支持的 `chart_type` 返回错误和支持列表。

### `artifact.plot`

| 字段 | 值 |
| --- | --- |
| Aliases | `plot` |
| 实现 | `gateway_core.tools.plot_tool.PlotTool` |
| Tags | `artifact`, `plot` |
| Risk | `low` |
| Scopes | 无 |
| 主要输出 | PNG plot artifact 和 JSON data artifact |

输入：

- `rows` / `evidence_rows`
- `chart_type`
- 可选 `title`、`x_field`、`y_field`

输出：

- PNG 图片。
- JSON 数据副本。
- plot evidence。

可以做：

- 基于已有 evidence rows 生成 PNG 图表。
- 在字段明确或可推断时渲染 bar/line/scatter/pie。

不能做：

- 不能执行 SQL。
- 不能接受 `sql` / `sql_query` / `query_sql` / `query` / `input` 中的 SQL 文本。
- 不能在无数据时伪造图。

失败边界：

- 传入 SQL 文本时直接拒绝。
- rows 缺失时返回 `ok=False`。
- matplotlib 不可用时提示安装或改用 `chart`。

### `artifact.image_generate`

| 字段 | 值 |
| --- | --- |
| Aliases | `generate_image_tool`, `image` |
| 实现 | `gateway_core.tools.image_tool.GenerateImageTool` |
| Tags | `artifact`, `image` |
| Risk | `medium` |
| Scopes | `artifact:image_generate` |
| 主要输出 | image artifact evidence 和 artifact payload |

输入：

- `prompt`
- 可选 `style`
- 可选 `size`
- 可选 image refs / mask refs

输出：

- image artifact。
- provider/model/size/mode evidence。
- warnings 或 error。

可以做：

- 通过配置的 image provider 生成或编辑图片。
- 清洗视觉 prompt。
- 丢弃敏感的非 prompt 上下文。
- 校验图片尺寸和 provider 能力。

不能做：

- 不能查 SQL。
- 不能替代 `image_generator` 的 SQL lineage lock。
- 不能把原始敏感个人明细发送给外部图片服务。
- 不能在 provider 未启用时假装生成成功。

失败边界：

- prompt 为空时返回 `ok=False`。
- size 不支持时返回错误和支持列表。
- image edit 但 provider 不是 OpenAI 时直接拒绝。
- provider 无可用图片 artifact 时返回 `ok=False`。

### `artifact.slide_generate`

| 字段 | 值 |
| --- | --- |
| Aliases | `slide` |
| 实现 | `gateway_core.tools.slide_tool.SlideTool` |
| Tags | `artifact`, `slide` |
| Risk | `low` |
| Scopes | 无 |
| 主要输出 | PPTX artifact 和 slide outline evidence |

输入：

- `title`
- `sections`
- 可选 `charts`
- 可选 `evidence_by_task`

输出：

- PPTX artifact。
- slide outline evidence。
- warnings 或 error。

可以做：

- 基于本地 evidence、chart artifacts、结构化 sections 生成 PPTX。
- 当配置了 LLM provider 时生成更完整 slide outline。
- provider 不可用时回退到基础本地 PPTX generator。

不能做：

- 不能查询 SQL。
- 不能创建 SQL lineage。
- 不能发送敏感 external context。
- 不能在无 section 时编造完整汇报。

失败边界：

- sensitive external context 触发 privacy block。
- 没有有效 slide section 时返回 `ok=False`。
- LLM provider 配置不完整时返回 error；允许配置后 fallback 到 basic。

## 内部 Context Tool

### `business_prompt_context`

| 字段 | 值 |
| --- | --- |
| 实现 | `gateway_core.tools.business_prompt_tool.BusinessPromptContextTool` |
| 可见性 | 内部 context helper |
| 主要输出 | `business_prompt_context` evidence |

输入：

- `question` / `input`
- 可选 `domain` / `domain_hint`
- 可选 `evidence_types`
- 可选 `user_role`

输出：

- domain。
- role_context。
- evidence_boundaries。
- data_query_guidance。
- usage_instruction。

可以做：

- 按业务域和用户角色提供证据边界。
- 为 contract planning 和 final answer guardrails 提供上下文。

不能做：

- 不能提供数据库事实。
- 不能替代权限、policy truth 或 SQL evidence。
- 不能作为公共 gateway evidence tool 暴露，除非明确提升到 `GatewayToolRegistry`。

失败边界：

- domain 不明确时回退到 `general_school_business`。
- 始终提示它只是 evidence boundary，不覆盖 SQL、权限或数据证据。

## School SQL ReAct Tools

这些工具属于 `DDLReactTools.as_langchain_tools()`，只供 `school_sql` executor 使用。
它们必须满足三个条件：DDL-first、只读、schema allowlist；只有 `ddl_search` 能扩大 SQL 白名单。

| Tool | 主要职责 | 不能负责 |
| --- | --- | --- |
| `ddl_search` | 查候选表、业务字段、时间字段、最新行预览、SQL readiness 和 evidence packets；写入 SQL 白名单。 | 最终正文、artifact 生成、绕过 DDL 证据。 |
| `list_available_tables` | 列出 executor 当前可用表。 | 扩大 SQL 白名单、业务解释、证据判断。 |
| `inspect_table_schema` | 检查已授权表字段和 schema 元数据。 | 执行查询、扩大 SQL 白名单。 |
| `sample_table_rows` | 返回已授权表代表性行。 | 扩大 SQL 白名单、全量数据导出、隐私明细倾倒。 |
| `inspect_jsonb_recordset` | JSONB 展开前检查 recordset 结构。 | 在没有样本时猜测嵌套字段。 |
| `jsonb_recordset_query` | 执行受限 JSONB recordset 查询。 | 未 inspect、record_schema 不匹配、非只读 SQL、无界展开。 |
| `sql_db_query` | 对 DDL 验证过的表执行受限只读 SQL。 | 写 SQL、未验证表、隐藏 fallback 数据。 |
| `sql_experience_search` | 检索与当前问题相关的历史 SQL/查询模式。 | 把旧示例当作当前事实。 |
| `suggest_related_queries` | 基于已有证据建议后续查询方向。 | 在无触发条件时扩大用户问题。 |
| `trend_analysis` | 基于已收集 evidence rows 计算趋势摘要。 | 接受输入 rows 伪造证据、查询新的数据库事实。 |
| `anomaly_detection` | 基于已收集 evidence rows 检测异常。 | 接受输入 rows 伪造证据、编造风险原因。 |
| `cohort_compare` | 基于已收集 evidence rows 做群组对比。 | 接受输入 rows 伪造证据、把不可比群组强行视为等价。 |

调用顺序建议：

1. 先 `ddl_search`。
2. 字段不清楚再 `inspect_table_schema` 或 `sample_table_rows`。
3. JSONB 结构不清楚先 `inspect_jsonb_recordset`，再 `jsonb_recordset_query`。
4. 只有 DDL/evidence 足够明确时才 `sql_db_query`。
5. 趋势、异常、群组对比只基于已经拿到的 rows/evidence。

## Tool 整理顺序

1. 先整理公共 Gateway tools，确认每个 `allowed_tools` 都能映射到 canonical name 或 alias。
2. 再整理 School SQL ReAct tools，确认 `ddl_search` 仍然是 SQL 第一证据门。
3. 最后整理 artifact tools，确认它们都依赖已有证据或清洗后的 prompt，不直接查询 SQL。

## 标准验证

```bash
python3 -m pytest tests/test_agent_tool_prompt_inventory.py tests/test_gateway_tool_registry.py tests/test_langchain_tool_policy.py tests/test_business_prompt_tool.py tests/test_mcp_server.py tests/test_ddl_retriever_indexing.py
```
