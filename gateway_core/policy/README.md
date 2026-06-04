# gateway_core.policy

## 1. 目录职责

- 官方政策/公开材料的时间解析、检索服务、向量/关键词数据库访问。

## 2. 输入

- 政策问题、时间范围、embedding 函数、PostgreSQL/向量表。

## 3. 输出

- 政策候选、引用来源、相关性分数。

## 4. 核心文件

- `gateway_core.policy.official_policy.time_scope`：政策/通知检索的相对时间解析。
- `gateway_core.policy.official_policy.search_service`：`official_policy_search` 的应用服务层，读取 `POLICY_VECTOR_*` 配置并组织检索参数。
- `gateway_core.policy.official_policy.vector_repository`：只负责访问 `official_policy` schema 的 PostgreSQL/pgvector 数据。

## 5. 数据流

- `PolicyTool` 调用 `official_policy/search_service.py`，service 调用 `official_policy/vector_repository.py`，返回政策证据给 Agent/回答层。

## 6. 不负责什么（Boundary）

- 不查学校私有业务表。
- 不生成最终回答文本。
- 不引用 `api/openai_compat` 响应适配层。

## 7. 修改这里时的注意事项

- 政策检索必须保留 source/citation。
- 调整阈值要配合灰度验证。
