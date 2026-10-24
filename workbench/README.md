# 原工作台源码

保留原 Native / web / backend 模块，去除私人绑定和资产。先读 ../docs/MODULE_MAP.md 与所改目录的 AGENTS。

- `npm --prefix workbench start`：真实 Native 入口，自己的配置 / 网关未准备好时显示未配置。
- `npm --prefix workbench run inspect`：本地只读源码查看，无本人身份、数据或供应商调用。
- `python3 -m contentbase workbench`：无需安装 Electron 的相同源码查看入口。
- `python3 workbench/backend/server.py --config PRIVATE_CONFIG`：自己配置的网关，非自动安装原生产服务。

所有个人资源与技术 state 在源码外。源码查看、离线测试、本人授权、真实业务分层；详见 ../docs/WORKBENCH_SETUP.md 与 WORKBENCH_MIGRATION.md。
