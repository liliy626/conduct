# gateway_core.observability

## 1. 目录职责

- 问题监控、日志策略和观测辅助。

## 2. 输入

- 请求问题、回答摘要、错误、trace/monitor 配置。

## 3. 输出

- question monitor 记录、日志策略结果、监控读取结果。

## 4. 核心文件

- `question_monitor.py`：问题监控落盘和读取。
- `log_policy.py`：日志策略。
- `langfuse_exporter.py`：把 SchoolTrace 导出到 Langfuse。
- `phoenix_exporter.py`：把 SchoolTrace 通过 OTLP/HTTP 导出到 Phoenix。

## 5. 数据流

- 入口和运行时把监控事件交给 observability，按策略落盘或读取。
- `finish_trace()` 会在 Langfuse 和 Phoenix 都启用时同时导出同一条 SchoolTrace，便于对比两套观测 UI。

## 5.1 Phoenix 对比配置

- 启动本地 Phoenix：`docker compose -f ops/phoenix/docker-compose.yml up -d`
- Gateway 容器内推荐地址：`http://conduct-phoenix:6006/v1/traces`
- 宿主机 Gateway 推荐地址：`http://127.0.0.1:16006/v1/traces`
- 宿主机 Phoenix UI 地址：`http://127.0.0.1:16006`
- 启用变量：
  - `PHOENIX_ENABLED=1`
  - `PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:16006/v1/traces`
  - `PHOENIX_PROJECT_NAME=conduct-gateway`

## 6. 不负责什么（Boundary）

- 不参与回答生成。
- 不改变业务路由。

## 7. 修改这里时的注意事项

- 监控文本要脱敏和限长。
- 不要把完整敏感数据直接写入日志。
