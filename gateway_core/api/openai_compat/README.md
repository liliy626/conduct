# gateway_core.api.openai_compat

## 1. 目录职责

- 实现 `/v1/chat/completions`、OpenAI 兼容响应、流式协议和入口编排。

## 2. 输入

- OpenAI ChatCompletion payload、Authorization、OpenWebUI metadata。

## 3. 输出

- OpenAI 兼容非流式/流式响应、Sources、reasoning/content chunk。

## 4. 核心文件

- `chat_pipeline.py`：主入口。
- `adapter.py`：Universal Hub/SkillEvent 到 SSE。
- `pipeline_setup_flow.py`：请求准备。
- `runtime_response_builders.py`：响应构造。

## 5. 数据流

- 请求准备 -> 权限/模型解析 -> route 选择 -> Agent/Hub 执行 -> adapter/response builder 输出。

## 6. 不负责什么（Boundary）

- 不写具体业务 SQL。
- 不持有模型密钥；只通过 runtime 配置取用。

## 7. 修改这里时的注意事项

- 协议 chunk 统一走 helper，不要在循环里手写 choices/delta 字典。
- A 轨 tenant key 必须通过 `QueryNormalizer.extract_tenant_id`。
