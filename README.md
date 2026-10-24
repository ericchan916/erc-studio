# ERC Studio

EricChan 的本地内容工作台。把选题、写作、制作、账号管理、发布审核和数据复盘放在同一个工作空间里，通过飞书和蚁小二衔接实际业务。

## 从这里开始

想先看页面，可以直接运行下面的 Python 预览命令，不需要安装桌面端。准备实际使用时，再按接入文档配置自己的账号、数据表和服务。

| 入口 | 用途 | 身份与外部动作 |
|---|---|---|
| `python3 -m contentbase workbench` | 查看工作台页面和模块入口 | 本地只读预览，未连接业务账号与服务 |
| `npm --prefix workbench start` | 桌面工作台、本人登录、隔离官方页面与业务模块 | 配置自己的网关、CLI 与账号后逐层验收 |
| `python3 -m contentbase ... serve` | 已配置的内容控制面；根页为原工作台只读查看 | `/control` 是旧 CLI 兼容界面，令牌在源码外 state |
| `python3 workbench/backend/server.py --config PRIVATE_CONFIG` | 原工作台认证网关及网页适配 | 本人资源准备完成后显式启动；源码不会自动安装服务 |

页面采用 ERC 标志与黑白品牌样式。品牌显示独立于业务账号头像；账号、密钥、业务资料和运行历史保存在源码外。[接入步骤](docs/WORKBENCH_SETUP.md)与[模块地图](docs/MODULE_MAP.md)分别说明如何运行、如何找到对应模块。

## 先看工作台

Python 3.11+ 即可运行只读查看，不需要飞书/蚁小二授权：

```bash
git clone https://github.com/ericchan916/erc-studio.git
cd erc-studio
python3 -B -m contentbase workbench --port 8787
```

打开 `http://127.0.0.1:8787`。预览使用本地资源，展示工作台布局、模块与未接入状态。发布和数据等功能需要完成自己的服务配置。

## 真实原生工作台

Node 22.12+，从自己的源码安装原项目依赖：

```bash
npm --prefix workbench install
export CONTENTBASE_WORKBENCH_STATE="$HOME/.local/share/content-company-workbench"
node workbench/scripts/init-config.cjs --state "$CONTENTBASE_WORKBENCH_STATE"
# 按 docs/WORKBENCH_SETUP.md 填本人 Profile / 表 / 目录 / 网关，不填供应商密钥。
export CONTENTBASE_LARK_PROFILE='YOUR_PROFILE'
npm --prefix workbench start
```

`init-config` 只生成私有槽位，不登录、复制授权或建业务记录；已有文件保持原样。真实运行须替换 YOUR_ 项并启动自己配置的网关。缺资源时显示未配置/待授权，页面加载不表示已登录。个人模型文件可以在源码外配置；源码不附原人物 GLB。

## 给接手的 Agent

**不要遍历整个公司资料或把全部规范塞进 Prompt。**按层读：

1. [AGENTS.md](AGENTS.md)：全局边界。
2. [模块地图](docs/MODULE_MAP.md)：快速定位源代码、规范、状态。
3. 所在目录的 AGENTS 与对应模块全文。
4. 代码入口、调用方、协议及相关测试。
5. 需要动态事实时，再查本人明确绑定的飞书原对象。

```bash
python3 -B scripts/module_docs.py --list
python3 -B scripts/module_docs.py content
python3 -B scripts/module_docs.py publishing
python3 -B scripts/module_docs.py route:daily-task-checkin
```

机器索引：[`workbench/module-registry.json`](workbench/module-registry.json)，含 12 个原工作台模块、22 个原工作流合同；逐项区分 `ported_original_source` 与 `contract_only`。规范存在不表示后台工作流已经安装；规则与接口变化必须同步索引、模块书、调用方及测试。

## 文档地图

| 主题 | 文件 |
|---|---|
| 原工作台结构与迁移边界 | [迁移验收](docs/WORKBENCH_MIGRATION.md) |
| 本人资源与启动步骤 | [原生工作台接入](docs/WORKBENCH_SETUP.md) |
| 所有模块 / 后台工作流规范 | [模块地图](docs/MODULE_MAP.md) |
| Agent 快速读取业务 MD / 规则 | [知识导航](docs/KNOWLEDGE_NAVIGATION.md) |
| 任务、原表及飞书 CLI | [飞书接入](docs/FEISHU_SETUP.md)、[任务模块](docs/modules/tasks.md) |
| 确认、冻结、回执与发布 | [发布流程](docs/PUBLISHING.md)、[发布模块](docs/modules/publishing.md) |
| CLI / HTTP 兼容接口 | [Agent 协议](docs/AGENT_PROTOCOL.md) |
| 审查与贡献 | [PR Agent 交接](docs/PR_AGENT_HANDOFF.md)、[测试分层](docs/TESTING.md) |
| 隐私与源码发布 | [公开边界](docs/PRIVACY.md)、[第三方许可](docs/THIRD_PARTY.md) |

## 原有 CLI 闭环继续保留

飞书是业务权威；Agent 产稿，程序计划，人审批；蚁小二实际执行。真实业务按层验收：本人身份→原表读取→受审写计划→写后回读→素材上传与预检→本人确认投递→原任务查询→本人打开作品→飞书登记与复盘。

```bash
python3 -B -m unittest discover -s tests -v
python3 -B scripts/check_workbench.py
python3 -B scripts/privacy_audit.py --root .
```

详情见 [旧 CLI 快速上手](docs/QUICKSTART.md)。一次选定一个发布入口；原生工作台和兼容控制面的批准、队列、幂等键不互认。同作品不要同时经两种队列投递。

MIT 覆盖本项目代码；供应商服务/平台账号、套餐、授权及使用规则由使用者与供应商决定。第三方前端资源原许可随代码保留。
