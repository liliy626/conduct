# gateway_core.agents.streaming

## 1. 目录职责

- 将 LangGraph/工具运行事件转换为可观测过程流和 trace 步骤。

## 2. 输入

- LangGraph event、工具 start/end/error 事件、模型流事件。

## 3. 输出

- `SchoolTraceStep`、使用量统计、过程事件摘要。

## 4. 核心文件

- `langgraph_event_stream.py`：事件到 trace 的转换。
- `models.py`：流式模型。
- `context.py`：流式上下文。

## 5. 数据流

- 运行时事件进入转换器，脱敏/摘要后写入 school trace 或向前端过程流展示。

## 6. 不负责什么（Boundary）

- 不修改已经产生的 SkillEvent。
- 不缓存大正文或图片数据。

## 7. 修改这里时的注意事项

- 新增事件类型时优先保持事件不可变，避免引入跨事件缓冲区。
