# Phoenix Local Runtime

## 1. 用途

本目录只负责启动本地 Phoenix，用来和已经接入的 Langfuse 对比同一条
`SchoolTrace` 的呈现方式、检索方式和调试体验。

## 2. 启动

```bash
docker compose -f ops/phoenix/docker-compose.yml up -d
```

## 3. Gateway 配置

如果 Gateway 作为宿主机 Python 进程运行，推荐使用宿主机 Phoenix：

```bash
PHOENIX_ENABLED=1
PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:16006/v1/traces
PHOENIX_PROJECT_NAME=conduct-gateway
```

如果 Gateway 作为 Docker 容器运行，并且和 Phoenix 容器都接入 `conduct_yili_net`，
则 Gateway 内部应使用服务名访问 Phoenix：

```bash
PHOENIX_ENABLED=1
PHOENIX_COLLECTOR_ENDPOINT=http://conduct-phoenix:6006/v1/traces
PHOENIX_PROJECT_NAME=conduct-gateway
```

当前宿主机 Phoenix 的 UI 地址是：

```text
http://127.0.0.1:16006
```

## 4. 边界

- Phoenix 是观测 UI 和 Trace 存储，不参与 Gateway 路由、规划、SQL、回答生成。
- Gateway 仍以 `SchoolTrace` 作为唯一运行时 Trace 契约。
- Langfuse 和 Phoenix 都从 `finish_trace()` 接收同一份 `SchoolTrace`，便于横向比较。
