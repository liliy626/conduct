# gateway_core

`gateway_core/` 是 OpenWebUI 网关的核心代码。当前线上主链路已经收敛为 **Agent-native ReAct**，旧的直接回答、功能路由、受控 JSON plan 主链路、数据上下文兜底、入口 gate 链路已经移除。

## 当前目录

```text
gateway_core/
├── api/openai_compat/   # /v1/chat/completions 编排；policy-only/school/plain 三个出口
├── agents/school_sql/   # 学校 DDL ReAct Agent、sql_db_query、证据板、SQL 安全边界
├── agents/policy_only/  # 无学校数据权限的政策/联网 ReAct Agent
├── agents/streaming/    # LangGraph/tool 事件转 OpenWebUI 过程流
├── agents/jobs/         # Redis 异步任务、状态存储、SSE 事件
├── tools/               # 联网、政策、图表、图片、PPT、时间、业务提示词工具
├── school/          # 学校 schema 索引、school trace
├── schema_context/  # DDL / schema / 历史 SQL 检索与缓存
├── policy/          # official_policy 向量/关键词检索
├── prompts/         # 业务角度提示词、格式 guard、prompt registry
├── runtime/         # 模型配置、鉴权、运行时单例、admin endpoints
├── conversation/    # OpenWebUI 消息模型与 thread_id
├── observability/   # question monitor、日志策略
├── domain/          # 时间、班级、人名等领域小工具
└── infra/           # DB pool、鉴权辅助、中国模型默认配置
```

## 主链路

```text
OpenWebUI
-> app.py
-> api/openai_compat/chat_pipeline.py
   -> key_policy: policy_only_agent_native
      -> web_search / official_policy_search / time / business_prompt_context
   -> school key: school_agent_native
      -> ddl_search / sample_table_rows / sql_db_query / official_policy_search / web_search / chart / image / slide / time
   -> other: plain LLM chat
-> OpenAI 兼容 SSE / JSON
```

## 关键规则

- `key_policy` 不绑定学校，不允许查学校数据库。
- `policy_only_agent_native` 只服务 `key_type=policy` 的公开政策助手；学校 key 下的政策检索是 `school_agent_native` 的 `official_policy_search` 工具。
- 学校 key 通过 `platform.gateway_api_keys` 定位 `school_id` 和 `schema_name`。
- SQL 只能通过 `sql_db_query` 工具执行，并经过只读安全边界。
- `web_search`、`official_policy_search`、`chart`、`image`、`slide`、`time` 与 `sql_db_query` 处在同一个 LangGraph 工具层。
- Trace 由 `school/trace.py` 记录，可通过 `/v1/admin/school-traces/ui` 查看。
