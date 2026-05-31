# gateway_core.infra

## 1. 目录职责

- 基础设施读写适配：数据库连接、API key 读取、鉴权辅助和模型默认配置读取。

## 2. 输入

- 环境变量、`model_config.yaml`、PostgreSQL DSN、Authorization header。

## 3. 输出

- 连接对象、API key record、鉴权结果、配置默认值。

## 4. 核心文件

- `api_keys.py`：API key 表读取和请求上下文记录。
- `db_pool.py`：数据库连接池。
- `postgres_dsn.py`：DSN 读取。
- `china_llm_defaults.py`：embedding 默认值读取。

## 5. 数据流

- runtime 调用 infra 获取连接/鉴权/配置，业务层只消费结果。

## 6. 不负责什么（Boundary）

- 不实现业务路由。
- 不生成回答。
- 不持有垂直专家状态。

## 7. 修改这里时的注意事项

- 配置性文本优先放入 `model_config.yaml`。
- infra 层新增状态前必须确认是否可用 runtime/provider 替代。
