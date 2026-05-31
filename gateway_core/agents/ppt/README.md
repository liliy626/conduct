# gateway_core.agents.ppt

## 1. 目录职责

- PPT 多模态输出 Worker，将校园分析状态转换为可下载的汇报演示资产。

## 2. 输入

- `UniversalAgentState`、`RuntimeContext`、PPT URL 工厂或 mock URL。

## 3. 输出

- `ppt_artifact` 的 `MultimodalOutputContract`，由基类统一转换为 `evidence_completed` 事件。

## 4. 核心文件

- `ppt_generation_skill.py`：PPT 资产生成 Skill。

## 5. 数据流

- state/ctx -> PPT URL 与预览大纲 -> SHA-256 proof -> typed multimodal contract -> SSE 输出矩阵。

## 6. 不负责什么（Boundary）

- 不拼装 OpenAI SSE。
- 不直接手写 `evidence_completed` 裸字典。
- 不修改大图拓扑。

## 7. 修改这里时的注意事项

- 交工必须经过 `BaseMultimodalAgentSkill` 和 `MultimodalOutputContract`。
- 新增大对象生成逻辑时，需要在生成函数边界用 `try/finally` 缩短引用生命周期。
