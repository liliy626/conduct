# gateway_core.agents.school_sql

## 1. 目录职责

- 学校数据查询专家，负责 DDL 检索、SQL 生成/安全校验、只读查库和证据板。

## 2. 输入

- 用户问题、学校 schema/index、只读 DSN、模型客户端和工具上下文。

## 3. 输出

- SQL 查询结果、`EvidenceBoard`、SQL lineage、Sources 和回答文本。

## 4. 核心文件

- `school_sql_skill.py`：Skill 外壳。
- `agent_stream.py`：ReAct 主循环。
- `sql_tools.py`：DDL/SQL 工具。
- `evidence_board.py`：证据板。
- `canonicalizer.py`：SQL 规范化。

## 5. 数据流

- 问题 -> DDL/历史经验检索 -> SQL 工具 -> EvidenceBoard/lineage -> 总结与流式输出。

## 6. 不负责什么（Boundary）

- 不直接处理 HTTP。
- 不绕过 guardrail 执行 SQL。
- 不把运行时连接对象写入 State。

## 7. 修改这里时的注意事项

- 新增 SQL 工具必须保留只读、安全和 lineage 约束。
- 高频稳定计划优先走 plan cache，不要在这里硬编码入口路由。
