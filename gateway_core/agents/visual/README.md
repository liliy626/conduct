# gateway_core.agents.visual

## 1. 目录职责

- 多模态输出专家，将 SQL lineage 和上下文合成为图片资产。

## 2. 输入

- 历史消息、SQL lineage、ImageTool/OpenAI image provider、artifact store。

## 3. 输出

- `image_artifact` 事件、Markdown 图片帧、图片 provenance 元数据。

## 4. 核心文件

- `image_generation_skill.py`：生图 Skill。
- `prompt_synthesizer.py`：三轴提示词合成器。

## 5. 数据流

- SQL lineage -> 三轴 prompt -> ImageTool -> artifact URL/hash -> evidence_completed。

## 6. 不负责什么（Boundary）

- 不查数据库。
- 不把图片 URL/Base64 写入对话 messages。

## 7. 修改这里时的注意事项

- 优先选择 row_count > 0 的黄金 lineage。
- 大对象必须在线程/函数边界及时释放。
