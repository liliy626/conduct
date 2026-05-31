# gateway_core.prompts

## 1. 目录职责

- 集中存放提示词母版、规则矩阵、格式约束和领域口径。

## 2. 输入

- 业务 payload、用户问题、角色/领域提示、lineage/artifact payload。

## 3. 输出

- 系统提示词、格式 guard、多模态 prompt、输出渲染片段。

## 4. 核心文件

- `prompt_domains.py`：领域规则和多模态矩阵。
- `prompt_registry.py`：最终回答提示词组装。
- `data_format_guard.py`：数据格式约束。
- `list_query_signals.py`：名单类问题信号。

## 5. 数据流

- Agent/adapter/skill 调用 prompts 里的纯函数或配置，得到文本规则和渲染结果。

## 6. 不负责什么（Boundary）

- 不调用模型。
- 不访问数据库。
- 不写运行时状态。

## 7. 修改这里时的注意事项

- 新增关键词/母版优先归仓这里。
- 不要把 HTTP 或数据库逻辑放入 prompts。
