# Agent 模块地图

本地图是入口，不是业务事实快照。先定位模块，再读全文规范、代码/调用方和测试；动态数据最后查询本人原对象。机器可读索引为 [`workbench/module-registry.json`](../workbench/module-registry.json)。

```bash
python3 -B scripts/module_docs.py --list
python3 -B scripts/module_docs.py content
python3 -B scripts/module_docs.py route:daily-task-checkin
```

## 状态解释

- `ported_original_source`：原工作台实现源码已迁移；配置和真人外部业务另行验收。
- `contract_only`：原工作流规则已迁移，未宣称该新机器有运行服务。
- 源码查看只展示原工作台布局和未接入状态，禁止用替身成功或假账号当本人完成。

| ID | 模块 | 文档 | 当前边界 |
|---|---|---|---|
| `home` | 主页、本人对话与认证 | [规范](modules/home.md) | 原实现源码已迁移；真实接入待验收 |
| `tasks` | 任务、飞书原生页面与脚本资料 | [规范](modules/tasks.md) | 原实现源码已迁移；真实接入待验收 |
| `content` | 选题、项目、草稿和版本推进 | [规范](modules/content.md) | 原实现源码已迁移；真实接入待验收 |
| `production` | 制作、口播、原片和成片回执 | [规范](modules/production.md) | 原实现源码已迁移；真实接入待验收 |
| `accounts` | 账号、平台后台与双授权 | [规范](modules/accounts.md) | 原实现源码已迁移；真实接入待验收 |
| `publishing` | 发布、上传、审核与平台终态 | [规范](modules/publishing.md) | 原实现源码已迁移；真实接入待验收 |
| `data` | 账号指标、作品关联与运营复盘 | [规范](modules/data.md) | 原实现源码已迁移；真实接入待验收 |
| `website` | 官网、页面与 RUM 统计 | [规范](modules/website.md) | 原实现源码已迁移；真实接入待验收 |
| `radar` | 选题雷达、原文与仿写稿 | [规范](modules/radar.md) | 原实现源码已迁移；真实接入待验收 |
| `activity-ops` | 活动运营、任务与复盘 | [规范](modules/activity-ops.md) | 原实现源码已迁移；真实接入待验收 |
| `workflow-monitor` | 工作流状态、积压与技术回执 | [规范](modules/workflow-monitor.md) | 原实现源码已迁移；真实接入待验收 |
| `notifications` | 通知中心、CardKit 和送达去重 | [规范](modules/notifications.md) | 原实现源码已迁移；真实接入待验收 |
| `route:daily-task-checkin` | 日报登记 | [规范](workflows/daily-task-checkin.md) | 规范已迁移；后台运行安装待迁移 |
| `route:task-deadline-followup` | 任务截止日期追问 | [规范](workflows/task-deadline-followup.md) | 规范已迁移；后台运行安装待迁移 |
| `route:commercial-brief-evaluation` | 商单判断 | [规范](workflows/commercial-brief-evaluation.md) | 规范已迁移；后台运行安装待迁移 |
| `route:commercial-brief-receivable` | 接单与回款 | [规范](workflows/commercial-brief-receivable.md) | 规范已迁移；后台运行安装待迁移 |
| `route:activity-attendance-review` | 活动参加与复盘 | [规范](workflows/activity-attendance-review.md) | 规范已迁移；后台运行安装待迁移 |
| `route:note-topic-linker` | 笔记选题加工 | [规范](workflows/note-topic-linker.md) | 规范已迁移；后台运行安装待迁移 |
| `route:feishu-lark-cli` | 飞书文档读写 | [规范](workflows/feishu-lark-cli.md) | 规范已迁移；后台运行安装待迁移 |
| `route:feishu-input-workflow` | 附件与链接输入 | [规范](workflows/feishu-input-workflow.md) | 规范已迁移；后台运行安装待迁移 |
| `route:background-workflows` | 后台任务 | [规范](workflows/background-workflows.md) | 规范已迁移；后台运行安装待迁移 |
| `route:data-scientist-api` | 数据科学家 CLI | [规范](workflows/data-scientist-api.md) | 规范已迁移；后台运行安装待迁移 |
| `route:wechat-official-reply` | 微信公众号自动回复 | [规范](workflows/wechat-official-reply.md) | 规范已迁移；后台运行安装待迁移 |
| `route:studio-content-workbench` | 内容工作台（App） | [规范](workflows/studio-content-workbench.md) | 规范已迁移；后台运行安装待迁移 |
| `route:isolated-member-chat` | 隔离成员飞书助手 | [规范](workflows/isolated-member-chat.md) | 规范已迁移；后台运行安装待迁移 |
| `route:studio-conversation-gateway` | ERC Studio对话后端 | [规范](workflows/studio-conversation-gateway.md) | 规范已迁移；后台运行安装待迁移 |
| `route:publisher-to-x-draft` | 公众号素材转X审核稿 | [规范](workflows/publisher-to-x-draft.md) | 规范已迁移；后台运行安装待迁移 |
| `route:confirmed-note-to-x-draft` | 已确认瞬间转X待审稿 | [规范](workflows/confirmed-note-to-x-draft.md) | 规范已迁移；后台运行安装待迁移 |
| `route:workbench-image-generation` | 工作台Codex配图 | [规范](workflows/workbench-image-generation.md) | 规范已迁移；后台运行安装待迁移 |
| `route:workbench-review-notifications` | 工作台图文审核通知 | [规范](workflows/workbench-review-notifications.md) | 规范已迁移；后台运行安装待迁移 |
| `route:feishu-verbatim-publisher` | 飞书作品原文发布稿 | [规范](workflows/feishu-verbatim-publisher.md) | 规范已迁移；后台运行安装待迁移 |
| `route:bar-acceptance` | 活动运营活动验收 | [规范](workflows/bar-acceptance.md) | 规范已迁移；后台运行安装待迁移 |
| `route:content-review` | 内容运营复盘Agent | [规范](workflows/content-review.md) | 规范已迁移；后台运行安装待迁移 |
| `route:blogger-radar` | 博主选题雷达 | [规范](workflows/blogger-radar.md) | 规范已迁移；后台运行安装待迁移 |

规范更新时同时更新机器索引、模块书、受影响入口及测试；不能只在会话中补一段说明。
