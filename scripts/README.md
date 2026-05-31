# scripts

## 1. 目录职责

- 本地/运维脚本入口，用于启动、初始化、worker 和 readiness 检查。

## 2. 输入

- 环境变量、命令行参数、数据库连接配置。

## 3. 输出

- 启动进程、初始化数据、检查报告或后台 worker。

## 4. 核心文件

- `restart_gateway.sh`：重启网关。
- `init_gateway_api_keys.py`/`seed_gateway_api_keys.py`：API key 初始化。
- `run_agent_worker.py`：后台 worker。
- `check_school_launch_readiness.py`：上线检查。

## 5. 数据流

- 运维命令调用脚本，脚本再调用 `gateway_core` 的稳定入口。

## 6. 不负责什么（Boundary）

- 不承载核心业务逻辑。
- 不写死真实密钥。

## 7. 修改这里时的注意事项

- 脚本应保持幂等；危险操作必须显式参数化。
