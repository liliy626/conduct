# Agent 异步任务

异步 Agent 任务模块，负责把长耗时问答从 HTTP 请求生命周期里拆出来。

## 运行形态

```text
POST /v1/agent/jobs
-> PostgreSQL platform.agent_jobs
-> Redis Stream agent_jobs:queue
-> scripts/run_agent_worker.py
-> 内部调用 /v1/chat/completions stream=true
-> Redis Stream agent_job:{job_id}:events
-> GET /v1/agent/jobs/{job_id}/events
```

主问答链路仍然是现有 `/v1/chat/completions`。worker 只是复用它，并把上游
OpenAI 兼容 SSE token 增量转换成 job 事件：

- `job_queued`
- `job_started`
- `answer_delta`
- `upstream_done`
- `job_succeeded`
- `job_failed`
- `job_cancelled`

## 必填环境变量

```bash
AGENT_JOB_QUEUE_ENABLED=1
REDIS_URL=redis://127.0.0.1:6379/0
POSTGRES_DSN=postgresql://user:pass@127.0.0.1:5432/yili
AGENT_JOB_GATEWAY_BASE_URL=http://127.0.0.1:8008
AGENT_JOB_QUEUE_STREAM=agent_jobs:queue
AGENT_JOB_WORKER_GROUP=gateway_workers
AGENT_JOB_MAX_CONCURRENCY=200
```

异步任务默认也使用 `POSTGRES_DSN`。`AGENT_JOB_POSTGRES_DSN` 和 `MEILANHU_POSTGRES_DSN` 只作为旧配置兼容或特殊拆库时的覆盖项。

## 本地一键启动

本地测试可以使用：

```bash
bash scripts/start_async_gateway.sh
```

脚本会完成：

- 读取 `.env`
- 如果本机有 Docker，则启动名为 `yili-redis-local` 的本地 Redis 容器
- 在 `127.0.0.1:8008` 启动网关
- 启动 `scripts/run_agent_worker.py`

如果希望一个 worker 进程并行处理多个学校/问题：

```bash
AGENT_JOB_MAX_CONCURRENCY=200 bash scripts/start_async_gateway.sh
```

停止命令：

```bash
bash scripts/stop_async_gateway.sh
```
