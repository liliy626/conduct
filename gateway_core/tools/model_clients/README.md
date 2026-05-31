# gateway_core.tools.model_clients

## 1. 目录职责

- 模型客户端相关扩展命名空间，目前主要作为保留/聚合入口。

## 2. 输入

- 运行时模型配置或客户端工厂。

## 3. 输出

- 可复用模型客户端封装。

## 4. 核心文件

- `__init__.py`：包入口。

## 5. 数据流

- 需要时由 tools/runtime 导入客户端扩展。

## 6. 不负责什么（Boundary）

- 不直接读取 HTTP 请求。
- 不放业务 prompt。

## 7. 修改这里时的注意事项

- 新增客户端前先确认 `runtime/gateway_config.py` 是否已有统一工厂。
