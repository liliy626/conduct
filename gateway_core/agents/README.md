# gateway_core.agents

## 1. 目录职责

- Agent 与 Skill 的领域层集合，定义可流式执行的专家能力。
- 承接运行时上下文，产出 `SkillEvent`、证据、文本和多模态资产。

## 2. 输入

- `UniversalAgentState`、`RuntimeContext`、用户消息和工具上下文。

## 3. 输出

- Agent 流式事件、最终回答片段、Evidence/Artifact 引用。

## 4. 核心文件

- `base_skill.py`：Skill 基类与运行时上下文约定。
- `school_sql/`：学校 SQL 专家。
- `ppt/`：PPT 多模态输出专家。
- `universal_hub/`：两节点中台图。
- `visual/`：多模态生图专家。

## 5. 数据流

- 入口层选择 Agent/Skill 后，Agent 调用工具或子专家并向适配器持续产出事件。

## 6. 不负责什么（Boundary）

- 不处理 HTTP 协议细节。
- 不读取 `.env` 密钥；运行时对象必须经 ctx 注入。

## 7. 修改这里时的注意事项

- 新增专家必须声明 `provided_outputs`，并通过注册表接入。
- 不要在通用图层硬编码具体专家名称或私有槽位。
