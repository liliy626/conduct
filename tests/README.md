# tests

## 1. 目录职责

- 回归测试和架构护栏，锁死 Universal Hub、Plan Cache、多模态、DDL 索引等关键边界。

## 2. 输入

- pytest、mock state/context、源码 introspection。

## 3. 输出

- 测试通过/失败结果和架构违规信号。

## 4. 核心文件

- `test_universal_hub_graph.py`：图行为。
- `test_image_generation_skill.py`：生图/adapter。
- `test_chat_pipeline_plan_cache.py`：A 轨租户隔离。
- `test_ddl_retriever_indexing.py`：DDL 索引护栏。

## 5. 数据流

- 测试构造假上下文或检查源码，验证关键路径和禁止项。

## 6. 不负责什么（Boundary）

- 不放生产 fixture 大文件。
- 不联网，除非测试显式 skip/fuse。

## 7. 修改这里时的注意事项

- 新增架构约束先写红灯测试。
- Live 测试必须默认跳过。
