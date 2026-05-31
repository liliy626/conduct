# gateway_core.runtime.admin

## 1. 目录职责

- 运行时管理端点和 HTML 诊断页面。

## 2. 输入

- 管理员请求、monitor/trace 数据、模型配置状态。

## 3. 输出

- health/reload/trace/usage HTML 或 JSON。

## 4. 核心文件

- `endpoints.py`：管理端聚合。
- `basic_endpoints.py`：基础 admin。
- `school_trace_html.py`：trace 页面。
- `school_trace_metrics.py`：trace 指标。
- `token_usage_html.py`：token 页面。

## 5. 数据流

- FastAPI 挂载 admin endpoint，读取 runtime/observability/school trace 后返回。

## 6. 不负责什么（Boundary）

- 不修改业务数据。
- 不绕过鉴权暴露敏感内容。

## 7. 修改这里时的注意事项

- 管理页面输出必须脱敏。
- 新增 endpoint 要考虑 admin 权限。
