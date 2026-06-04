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
- `check_file_size.py`：pre-commit 文件大小检查，控制新增/增长文件保持模块化。
- `hooks/`：pre-commit 分类 hook，分为 Safety、Quality、Architecture、Review 四类。

## 5. 数据流

- 运维命令调用脚本，脚本再调用 `gateway_core` 的稳定入口。

## 6. 不负责什么（Boundary）

- 不承载核心业务逻辑。
- 不写死真实密钥。

## 7. 修改这里时的注意事项

- 脚本应保持幂等；危险操作必须显式参数化。
- 文件大小治理采用增量策略：新文件超过 400 行会阻止提交；已有超大文件继续增长会阻止提交；已有超大文件未增长只提醒拆分。
- Hook 分类职责：
  - Safety Hook：阻止真实 `.env`、私钥、疑似密钥/令牌进入提交。
  - Quality Hook：运行 `git diff --check` 和 staged Python 语法编译。
  - Architecture Hook：执行文件大小治理，并阻止新增绕过 `GatewayToolRegistry` 的 `StructuredTool.from_function`。
  - Review Hook：对核心代码缺少测试/文档同步给出提醒，不默认阻止提交。
