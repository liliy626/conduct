# gateway_core.api.openai_compat.chat_pipeline_parts

## 1. 目录职责

- 拆分 chat pipeline 的请求/响应小部件，降低主入口文件负担。

## 2. 输入

- 请求对象、运行时 setup、响应 payload。

## 3. 输出

- 请求解析片段、响应片段和复用函数。

## 4. 核心文件

- `request_parts.py`：请求部件。
- `response_parts.py`：响应部件。

## 5. 数据流

- `chat_pipeline.py` 调用这里的小函数完成准备或响应拼装。

## 6. 不负责什么（Boundary）

- 不做路由决策。
- 不访问数据库。

## 7. 修改这里时的注意事项

- 只放可复用片段；不要新增与主入口平行的 pipeline。
