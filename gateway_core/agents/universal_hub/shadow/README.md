# gateway_core.agents.universal_hub.shadow

## 1. 目录职责

- Universal Hub 的离线影子回放、审计、比较和 Live 沙箱。

## 2. 输入

- 历史 trace/jsonl、Legacy 快照、沙箱运行时依赖。

## 3. 输出

- 影子审计 JSONL、对账指标、live audit 记录。

## 4. 核心文件

- `trace_loader.py`：trace 加载。
- `replayer.py`/`live_replayer.py`：回放器。
- `runtime_provider.py`：只读运行时注入。
- `comparator.py`：流式对账。

## 5. 数据流

- trace -> 快照 -> sandbox state/ctx -> Skill/Graph 回放 -> comparator/auditor 写账。

## 6. 不负责什么（Boundary）

- 不接生产 HTTP。
- 默认不联网；Live 必须由环境开关显式开启。

## 7. 修改这里时的注意事项

- 严禁在 State 放入 model/db 句柄。
- Live 测试必须保留 skip/fuse。
