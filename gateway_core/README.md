# gateway_core

## 1. 目录职责

- 网关核心包，承载 OpenAI 兼容接口、Agent、工具、运行时、观测和数据检索能力。
- 维持 OpenWebUI 请求到学校数据/政策/多模态工具的主数据流。

## 2. 输入

- HTTP 层传入的聊天请求、API key 解析结果、模型配置和运行时上下文。
- PostgreSQL、向量索引、工具执行结果和 LangGraph/Agent 事件。

## 3. 输出

- OpenAI 兼容 JSON/SSE 响应。
- 证据、Sources、Artifacts、Trace、监控日志和工具执行结果。

## 4. 核心文件

- `api/openai_compat/chat_pipeline.py`：聊天入口主编排。
- `agents/school_sql/`：学校数据 ReAct 专家。
- `runtime/gateway_config.py`：模型与网关配置加载。
- `tools/`：查库以外的可调用工具。

## 5. 数据流

- `app.py` 进入 `api/openai_compat`，按 key 类型路由到 policy/school/plain 通道。
- 学校数据问题经 DDL/SQL/工具层补证，最终通过适配器转为 OpenAI 兼容输出。

## 6. 不负责什么（Boundary）

- 不保存真实密钥、运行日志或数据库备份。
- 不直接承担部署编排；Docker/脚本只调用这里暴露的入口。

## 7. 修改这里时的注意事项

- 新增能力优先放入对应子目录，不要把业务逻辑塞回根包。
- 跨目录公共规则优先归仓到 `prompts/` 或 `runtime/`，避免在入口层硬编码。
