# gateway_core.school

## 1. 目录职责

- 学校 schema 索引和 school trace 数据结构。

## 2. 输入

- school_id、schema_name、PostgreSQL metadata、trace 事件。

## 3. 输出

- `SchoolSchemaIndex`、数据集/字段索引、trace 汇总。

## 4. 核心文件

- `schema_index.py`：索引模型。
- `schema_indexer.py`：从数据库构建索引。
- `trace.py`：school trace 记录和读取。

## 5. 数据流

- runtime 根据 API key 定位学校，构建/缓存 schema index，Agent 执行过程写入 trace。

## 6. 不负责什么（Boundary）

- 不负责 HTTP 路由。
- 不执行模型调用。

## 7. 修改这里时的注意事项

- 索引缓存 key 必须包含 school/schema。
- trace 输出要避免泄露敏感 SQL，遵循 include_sql 开关。
