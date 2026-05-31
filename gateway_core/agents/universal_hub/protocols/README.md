# gateway_core.agents.universal_hub.protocols

## 1. 目录职责

- Universal Hub 的结果与证据协议模型。

## 2. 输入

- 专家执行结果、证据 payload、artifact refs。

## 3. 输出

- 结构化 result/evidence 协议对象。

## 4. 核心文件

- `result.py`：结果协议。
- `evidence.py`：证据协议。

## 5. 数据流

- Skill 产出的原始数据被规范化为 Hub 可理解的结果/证据结构。

## 6. 不负责什么（Boundary）

- 不执行专家。
- 不访问数据库或模型。

## 7. 修改这里时的注意事项

- 协议字段变化会影响 adapter、shadow test 和图节点。
