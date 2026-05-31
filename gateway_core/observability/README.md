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

## 5. 数据流

- 入口和运行时把监控事件交给 observability，按策略落盘或读取。

## 6. 不负责什么（Boundary）

- 不参与回答生成。
- 不改变业务路由。

## 7. 修改这里时的注意事项

- 监控文本要脱敏和限长。
- 不要把完整敏感数据直接写入日志。
