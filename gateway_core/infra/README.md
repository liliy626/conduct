# gateway_core.infra

基础设施层，提供网关运行所需的通用能力，不包含业务领域逻辑。

- `db_pool.py`：数据库连接池与连接创建。
- `gateway_auth.py`：网关鉴权能力，供 runtime 统一调用。
- `api_keys.py`：`platform.gateway_api_keys` 读取、缓存与当前请求 key 记录。
