# gateway_core.runtime

运行时层负责网关装配、配置加载、HTTP 请求预处理和管理端点。

## 目录职责

| 文件 | 说明 |
|---|---|
| `gateway_runtime.py` | 运行时主装配（模型、数据库、路由执行协同） |
| `gateway_config.py` | 模型与网关配置加载与校验 |
| `runtime_context.py` | 兼容导出层，向旧调用方暴露统一入口 |
| `request_handler.py` | 请求预处理（认证、模型路由等） |
| `admin_endpoints.py` | 管理端接口（health/reload/monitor/school-traces） |

## 运维接口

- `GET /health`
- `POST /admin/reload-config`
- `GET /v1/admin/recent-question-monitor`
- `GET /v1/admin/school-traces/ui`
