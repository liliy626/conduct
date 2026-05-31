# gateway_core.api

## 1. 目录职责

- HTTP API 层命名空间，承载 OpenAI 兼容接口。

## 2. 输入

- FastAPI request、headers、JSON payload。

## 3. 输出

- JSONResponse、StreamingResponse、管理端响应。

## 4. 核心文件

- `openai_compat/`：OpenAI 兼容聊天接口。

## 5. 数据流

- `app.py` 将请求路由到 API 子模块，子模块调用 runtime/agents/tools。

## 6. 不负责什么（Boundary）

- 不实现工具业务。
- 不直接执行 SQL。

## 7. 修改这里时的注意事项

- 新增 API 时保持薄入口，业务下沉到 runtime/agents。
