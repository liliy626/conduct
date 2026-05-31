# gateway_core.agents.universal_hub

## 1. 目录职责

- 通用两节点中台图，负责 supervisor 差集调度和 skill runner 执行。

## 2. 输入

- `UniversalAgentState`、Skill 注册表、运行时 ctx。

## 3. 输出

- 更新后的 State、SkillEvent、自定义流事件和 evidence/artifact refs。

## 4. 核心文件

- `graph_builder.py`：图构建与节点。
- `registry.py`：Skill 注册。
- `supervisor_core.py`：确定性 required_outputs 决策。
- `state.py`：图状态结构。

## 5. 数据流

- Supervisor 计算缺口 -> Runner 执行专家 -> 更新 completed/evidence/artifact -> 回到 Supervisor 或完成。

## 6. 不负责什么（Boundary）

- 不写 HTTP 路由。
- 不硬编码某个专家私有名称。
- 不存放非序列化运行时对象。

## 7. 修改这里时的注意事项

- 临时槽位清理通过配置集合和集合差集完成。
- 修改 State 字段时必须确认可序列化。
