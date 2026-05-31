# gateway_core.tools

## 1. 目录职责

- Agent 可调用工具集合：时间、联网、政策、图表、图片、PPT、artifact 和工具注册。

## 2. 输入

- `AgentToolInput`、`ToolExecutionContext`、外部 API/文件/证据数据。

## 3. 输出

- `AgentToolOutput`、evidence、artifacts、sources、warnings。

## 4. 核心文件

- `tool_core.py`：工具接口与 Registry。
- `web_search_tool.py`：联网。
- `policy_tool.py`：政策。
- `image_tool.py`：图片。
- `slide_tool.py`：PPT。
- `chart_tool.py`/`plot_tool.py`：可视化。

## 5. 数据流

- Agent 通过 `langchain_tools.py` 包装工具，工具执行后返回结构化结果给 Agent/adapter。

## 6. 不负责什么（Boundary）

- 工具不决定主路由。
- 工具类不应持有跨请求业务状态。

## 7. 修改这里时的注意事项

- 异常边界优先放 `ToolRegistry` 或工具共用 helper。
- 大文件/Base64 资产必须落盘或转 URL，不进 messages。
