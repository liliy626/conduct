# 智慧校园 AI 数据网关

智慧校园数据问答网关。项目作为 OpenWebUI 的 LLM 代理层，把自然语言问题交给 Agent-native ReAct 主链路，由模型在同一层工具中选择查库、政策、联网、图表、图片、PPT 和时间工具。

## 当前状态

- FastAPI 入口：`app.py`
- 健康检查：`GET /health`
- 默认端口：`8008`
- 主运行方式：Docker + 本地 `uvicorn`
- 学校作用域：当前按网关 API key 映射到对应学校数据作用域

## 架构一览

```text
OpenWebUI / 客户端
  -> app.py
  -> gateway_core.api.openai_compat.chat_pipeline
     -> key_policy: policy_only_agent_native
        -> web_search / official_policy_search / time
     -> school key: school_agent_native
        -> DDL 检索 / sql_db_query / 政策 / 联网 / 图表 / 图片 / PPT / 时间
     -> other key: plain LLM chat
     -> observability (question_monitor + school trace)
  -> PostgreSQL / 业务视图 / 向量索引
```

## 近期能力（2026-04）

- 学校问题默认进入 Agent-native streaming ReAct。
- Agent 先检索当前学校 DDL，再在同一层工具中选择 `sql_db_query`、`official_policy_search`、`web_search`、`chart`、`image`、`slide`、`time` 等工具。
- `policy_only_agent_native` 只服务 `key_type=policy` 的公开政策助手；学校 key 下的政策检索只是 `school_agent_native` 的一个工具，不是单独路由。
- `sql_db_query` 负责受控查库并把事实写入证据板（`EvidenceBoard`）；其他工具负责政策/联网/图表/视觉/PPT/时间补证。
- School Trace Dashboard 支持 `/v1/admin/school-traces/ui`，用于查看每题的数据流、SQL、工具调用和最终答案。
- 复杂业务问题由 Agent 自主补证，再由 LLM 结合学校数据、政策证据和工具结果整合回答。

## 目录速览

| 路径 | 说明 |
|---|---|
| `app.py` | FastAPI 薄入口 |
| `gateway_core/` | 网关核心包（Agent、工具、运行时、回答、观测） |
| `gateway_core/domain/` | Agent 工具复用的时间/班级/人名等通用领域小工具 |
| `gateway_core/school/` | 学校 schema 索引、API key 归属、数据流 trace |
| `gateway_core/schema_context/` | DDL / schema / 历史 SQL 检索与缓存 |
| `gateway_core/agents/school_sql/` | Agent-native SQL 查询、证据板（`EvidenceBoard`）、SQL 安全边界 |
| `scripts/` | 数据导入、评测、运维脚本 |
| `data/` | 配置数据和测试辅助数据 |
| `sql/` | 迁移、schema、报表 SQL |
| `logs/` | question_monitor、school trace / batch eval 输出 |
| `tests/` | 回归测试 |
| `analysis/` | 分析产物与诊断结果 |

## 关键配置文件

- `model_config.yaml`：模型路由与默认模型
- `platform.gateway_api_keys`：api_key、key_type、school_id、schema_name 的权威映射
- `data/intent_route_examples.yaml`：入口导航与澄清样例
- `.env`：数据库连接、模型 key、功能开关

常见开关：

```env
GATEWAY_AUTH_ENABLED=1
GATEWAY_API_KEY_DB_ENABLED=1
SCHOOL_SCHEMA_INDEX_ENABLED=1
POLICY_VECTOR_ENABLED=1
GATEWAY_WEB_SEARCH_ENABLED=1
```

## Python 虚拟环境（本地跑测试 / 脚本 / 非 Docker 起服务）

项目根目录已忽略 `.venv`，请各自本地创建，勿提交到 Git。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

Windows（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
```

退出虚拟环境：`deactivate`。

## 本地启动

```bash
docker compose up -d
curl http://127.0.0.1:8008/health
```

仅启动网关：

```bash
docker compose up -d gateway
```

本地开发：

```bash
uvicorn app:app --host 0.0.0.0 --port 8008 --log-level info
```

## 服务器部署

服务器部署、`.env` 必填项、调度器默认时间和手工触发命令，见：

- [`SERVER_DEPLOY_CHECKLIST.md`](SERVER_DEPLOY_CHECKLIST.md)

## 文档约定

- README 仅描述“当前有效”的结构与运行方式。
- 临时分析、排查结果、历史快照放 `analysis/`。
- 口径真相以数据库当前对象与 SQL 迁移为准。
