# gateway_core.schema_context

## 1. 目录职责

- Schema/DDL/历史 SQL 经验检索与缓存，为 SQL Agent 提供结构上下文。

## 2. 输入

- 用户问题、schema_name、embedding 函数、PostgreSQL 元数据和历史 SQL 表。

## 3. 输出

- DDL 上下文、历史经验、metadata 候选、缓存命中。

## 4. 核心文件

- `ddl_retriever.py`：DDL 检索。
- `ddl_embedding.py`：DDL embedding。
- `query_experience.py`：经验召回逻辑。
- `query_experience_store.py`：经验持久化。
- `ttl_cache.py`：TTL 缓存。

## 5. 数据流

- SQL Agent 请求 schema context，先查缓存/向量/元数据，再返回 DDL 文本和候选表。

## 6. 不负责什么（Boundary）

- 不执行业务 SQL 查询。
- 不生成最终回答。

## 7. 修改这里时的注意事项

- 索引结构优先 dict/hash，避免静态缓存线性扫描。
- 同步缓存改异步要先迁移调用链。
