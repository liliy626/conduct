# gateway_core.api.openai_compat

本目录负责 OpenAI 兼容接口适配。旧 `answer/` 目录已经下线；当前只保留问答入口、Agent 流式适配和响应构造。

| 文件 | 说明 |
|---|---|
| `chat_pipeline.py` | `/v1/chat/completions` 主入口，按 key 进入 policy Agent、school Agent 或普通聊天 |
| `agent_native_flow.py` | 把 Agent 事件转换成 OpenAI/OpenWebUI 流式片段 |
| `pipeline_setup_flow.py` | 鉴权、模型、会话、有效问题初始化 |
| `pipeline_response_tools.py` | 监控落盘、错误文本、统一响应小工具 |
| `runtime_response_builders.py` | OpenAI 兼容 JSON/SSE 响应构造 |
| `policy_evidence_search.py` | `official_policy_search` 的检索提供器 |
| `citation_formatter.py` | 从证据 payload 里提取政策/网页引用来源 |
