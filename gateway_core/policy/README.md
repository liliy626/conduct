# gateway_core.policy

## 1. 目录职责

- 官方政策/公开材料的向量与关键词检索。

## 2. 输入

- 政策问题、embedding 函数、PostgreSQL/向量表。

## 3. 输出

- 政策候选、引用来源、相关性分数。

## 4. 核心文件

- `vector_store.py`：政策向量/关键词检索。

## 5. 数据流

- Policy tool 或 school Agent 调用检索函数，返回政策证据给回答层。

## 6. 不负责什么（Boundary）

- 不查学校私有业务表。
- 不生成最终回答文本。

## 7. 修改这里时的注意事项

- 政策检索必须保留 source/citation。
- 调整阈值要配合灰度验证。
