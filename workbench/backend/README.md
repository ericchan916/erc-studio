# 原认证网关与执行模块

入口 server.py；成员绑定、会话与执行状态由 core/domain 管理。只监听配置回环和允许 Host/Origin；个人成员、pepper、Profile、目录、模型配置均来自源码外 0600 JSON。

源代码迁移不代表成员已注册、供应商已授权、Skill/数据科学家已安装。先读 ../../docs/modules/home.md、production.md、publishing.md、data.md 与本目录 AGENTS。真实动作须本人明确资源与审批；源码查看不调用本服务。

`python3 -B workbench/backend/server.py --help` 不创建业务 state 或调用供应商。启动时使用自己的实际 PRIVATE_CONFIG，拒绝 YOUR_ 槽。个人数据库/会话/素材工程不提交 Git。未知保留现场；回滚不删除任何个人状态。
