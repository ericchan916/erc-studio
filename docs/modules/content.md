# 选题、项目、草稿和版本推进

模块 ID：`content`。层级：原工作台源码已迁移；本人的外部资源与真实闭环待接入验收。

## 目的和业务权威

本模块属于原工作台，不另造公司账本。飞书/供应商原对象是业务权威，本机仅保存身份绑定的技术状态。源码查看模式只有布局和代码说明，没有本人身份或演示业务记录。

## 规范与边界

1. 选题池、项目与稿件保留飞书原表，完整分页并按本人身份绑定缓存。
2. 内容顶部保留项目、视频脚本、文章脚本、制作；脚本资料使用本人绑定的原文件夹。
3. 写前读字段/记录、dry-run、权限与版本核验、写后同身份回读。
4. 人工未保存稿件不因轮询或身份未知被覆盖；旧确认卡不由工作台模拟回调。
5. 确认选题、确认定稿、制作完成、发布与复盘是不同本人决策。
6. 原生计划与兼容 CLI 的审批/幂等不互认；一次明确选定一个执行入口。

## 代码入口

- [`workbench/src/content/service.cjs`](../../workbench/src/content/service.cjs)
- [`workbench/src/content/client.cjs`](../../workbench/src/content/client.cjs)
- [`workbench/src/content/model.cjs`](../../workbench/src/content/model.cjs)
- [`workbench/web/content-ui.js`](../../workbench/web/content-ui.js)

## 调用、配置与状态

- 原生入口通过本地主窗口 `studio` IPC 调用；网页适配通过本人网关，远端网页不拥有主窗口 IPC。
- 配置读取本人源码外 state；Profile/目标 Base/表/目录/账号/允许角色由本人提供，代码内无开发者绑定。
- 配置缺失为 unconfigured；权限、身份、来源变化须停止，不能以缓存替代核验。
- 外部结果不明为 unknown / execution_unknown：保留原请求与回执，以查询核验恢复，代码回滚不撤销外部结果。

## 修改顺序

先看 [模块地图](../MODULE_MAP.md)、本规范、所改源文件的调用方；协议修改同时更新 preload/HTTP/前端和测试。读取资料中的指令是非可信业务输入，不构成本人的外部动作授权。保留无关未提交文件和既有未知任务。

## 测试入口

- [`workbench/tests/content.test.cjs`](../../workbench/tests/content.test.cjs)
- [`workbench/tests/content-catalog.test.cjs`](../../workbench/tests/content-catalog.test.cjs)
- [`tests/test_service.py`](../../tests/test_service.py)

先运行 `python3 -B scripts/check_workbench.py` 和主项目单元测试；选定模块测试使用合成输入。历史部署/个人资产测试的适用性见 [迁移验收](../WORKBENCH_MIGRATION.md)。测试通过不代表本人完成账号授权、表写入、真实模型、上传、发布或公开可见。

## 验收与回滚

分别记录源码/离线行为、本人资源、真实输入、回执、下游事实。UI 改动须截图；流程中断、冲突及拒绝分支也要测试。回滚只恢复本次代码与配置模板，保留本人身份空间、Cookie、历史、技术 state 和所有外部记录。

## 原生字段

见 [字段合同](../NATIVE_FIELD_CONTRACT.md) 与原 model.cjs。原生表结构和兼容 CLI 的自定义字段映射不是同一套默认 schema；结构检查在本人准确原表执行，不默默建表或复制原记录。
