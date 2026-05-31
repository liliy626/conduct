# gateway_core.conversation

## 1. 目录职责

- 处理 OpenWebUI 消息、会话线程、短期会话记忆和消息格式转换。

## 2. 输入

- OpenAI messages、metadata、headers、历史问答。

## 3. 输出

- LangChain messages、thread/chat id、上下文摘要。

## 4. 核心文件

- `messages.py`：消息转换。
- `threading.py`：thread/chat id 提取。
- `session_memory.py`：短期记忆。
- `manager.py`：会话管理。

## 5. 数据流

- 入口 payload -> 消息标准化 -> thread 标识 -> 可选历史上下文 -> Agent。

## 6. 不负责什么（Boundary）

- 不决定业务路由。
- 不保存长期审计证据。

## 7. 修改这里时的注意事项

- 多轮压缩要避免破坏问答对边界。
- 不要把大型 artifact 放进 messages。
