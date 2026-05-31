# gateway_core.agents.jobs

## 1. 目录职责

- 异步 Agent 任务、Redis Stream、任务状态存储和 SSE 事件服务。

## 2. 输入

- 后台任务请求、Redis stream 消息、任务配置。

## 3. 输出

- 任务状态、事件流、worker 执行结果。

## 4. 核心文件

- `service.py`：任务服务。
- `worker.py`：后台 worker。
- `redis_streams.py`：Redis stream 读写。
- `sse.py`：任务事件 SSE。

## 5. 数据流

- HTTP/调度创建 job，写入 store/stream，worker 消费并把事件推给 SSE 客户端。

## 6. 不负责什么（Boundary）

- 不实现具体学校问答逻辑。
- 不替代主 `/v1/chat/completions` 同步链路。

## 7. 修改这里时的注意事项

- 改 Redis key/stream 名称时要兼顾 worker 与 endpoint。
- 任务 payload 只能放可序列化对象。
