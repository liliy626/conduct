# gateway_core.agents.policy_only

## 1. 目录职责

- 公开政策助手 Agent，仅面向 `key_type=policy` 场景。

## 2. 输入

- 政策类用户问题、政策检索工具、联网工具和时间工具。

## 3. 输出

- 政策解释、引用来源和 OpenAI 兼容流式片段。

## 4. 核心文件

- `agent_stream.py`：policy-only ReAct 流。
- `policy_skill.py`：政策 Skill 包装。

## 5. 数据流

- 入口层识别 policy key 后进入 policy-only Agent，工具补证后输出。

## 6. 不负责什么（Boundary）

- 绝不查询学校业务数据库。
- 不使用学校 schema 或 EvidenceBoard。

## 7. 修改这里时的注意事项

- 政策 key 与学校 key 的权限边界不能合并。
