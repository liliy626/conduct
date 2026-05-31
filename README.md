# conduct

我自己的测试

## 智慧校园 AI 数据网关

这是当前网关运行代码的精简导出版，用于 OpenWebUI 兼容代理服务。

### 入口

- FastAPI 入口：`app.py`
- 健康检查：`GET /health`
- OpenAI 兼容接口：`POST /v1/chat/completions`
- 默认端口：`8008`

### 主要目录

| 路径 | 说明 |
|---|---|
| `gateway_core/` | 网关核心代码 |
| `gateway_core/agents/` | Agent、Skill、Universal Hub、学校 SQL 专家 |
| `gateway_core/api/openai_compat/` | OpenAI 兼容接口与流式适配 |
| `gateway_core/tools/` | SQL、图表、图片、PPT、时间等工具 |
| `gateway_core/runtime/` | 运行时配置、模型与数据库上下文 |
| `scripts/` | 少量启动和初始化脚本 |

### 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8008 --log-level info
```

### 配置

复制 `.env.example` 为 `.env`，填入数据库连接、模型配置和网关开关。

注意：本仓库不包含 `.env`、日志、artifact、数据备份或任何真实密钥。
