# gateway_core.runtime

## 1. 目录职责

- 运行时装配层：模型配置、鉴权上下文、请求缓存、RAG embedding 和 trace context。

## 2. 输入

- `model_config.yaml`、环境变量、HTTP 请求、API key、模型调用参数。

## 3. 输出

- 模型客户端、请求上下文、运行时单例、usage/trace context。

## 4. 核心文件

- `gateway_config.py`：模型配置和客户端工厂。
- `gateway_runtime.py`：运行时导出和单例。
- `runtime_rag_embedding.py`：RAG embedding。
- `runtime_trace_context.py`：trace/usage contextvars。

## 5. 数据流

- API 层请求进入 runtime，解析模型/鉴权/缓存，再把 ctx 交给 Agent。

## 6. 不负责什么（Boundary）

- 不写业务 SQL。
- 不处理具体工具输出格式。

## 7. 修改这里时的注意事项

- 新增环境开关优先封装为配置读取器。
- 不要在业务层散落 `os.getenv`。
