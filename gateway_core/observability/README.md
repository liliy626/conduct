# gateway_core.observability

可观测层负责记录问答链路状态，支持线上排障与路由质量回归。

## 目录职责

| 文件 | 说明 |
|---|---|
| `question_monitor.py` | 问题级日志（问题、路由、耗时、答案预览、状态） |

## 常用日志

- `logs/question_monitor/`：问题监控日志。
- 学校数据流 trace：由 `gateway_core/school/trace.py` 写入，管理页面为 `/v1/admin/school-traces/ui`。
- 默认自动仅保留最近 7 天（可通过 `OBSERVABILITY_LOG_RETENTION_DAYS` 调整）。

## 管理端接口

- `/v1/admin/school-traces/recent`
- `/v1/admin/school-traces/ui`
- `/v1/admin/school-traces/{trace_id}`
