# 自己的原生工作台接入

## 资源与到位标准

| 资源 | 本人准备 | 到位标准 |
|---|---|---|
| Python / Node / Electron | Python 3.11+ / Node 22.12+，manifest 的依赖 | help / 语法与离线测试通过 |
| 飞书 | 本人 app/Profile、user 授权、明确 Base/表/目录/角色 | 实时身份、字段、表权限、原对象已核验 |
| 蚁小二 | 真实 yxer、套餐、本人平台账号 | doctor / schema / accounts 只读通过；费用由本人确认 |
| 本人网关 | 源码外 0600 配置、随机 pepper、成员和允许来源 | 服务端真实认证，无共享管理员 Token |
| 模型 | 本人兼容文本端点 / model / 环境 Key 引用 | 用本人输入获得真实最终文本；不更换供应商掩盖失败 |
| 制作 / 数据 / 官网 / 活动 | 可选外部 CLI/Skill、素材、RUM、本人原对象 | 必要字段和回执逐项核验；缺资源标未接入 |

## 配置路径

`CONTENTBASE_WORKBENCH_STATE` 是原生工作台自己的私有目录，默认为系统 appData 下 content-company-workbench。兼容 CLI state 不与其共享；真实账号记录、缓存、浏览器空间、认证会话和回执保持源码外。

```bash
export CONTENTBASE_WORKBENCH_STATE="$HOME/.local/share/content-company-workbench"
node workbench/scripts/init-config.cjs --state "$CONTENTBASE_WORKBENCH_STATE"
```

生成 task-space/content-space/setup 槽位，不覆盖已有文件，不登录或建表。本人填写准确 URL/table/允许人员；不要使用源码中的槽位或测试 ID 当真实对象。另建 `conversation-space.json`（0600）：`{"version":1,"url":"http://127.0.0.1:8844"}`，只在自己的网关已准备好后添加。

## Native CLI 与网站绑定

```bash
export CONTENTBASE_LARK_PROFILE='YOUR_PROFILE'
export CONTENTBASE_LARK_CLI='lark-cli'
export CONTENTBASE_YXER_CLI='yxer'
export CONTENTBASE_WEBSITE_URL='https://YOUR_WEBSITE_HOST/'
export CONTENTBASE_PYTHON='python3'
npm --prefix workbench start
```

替换 YOUR_ 项；Profile 对应本人的已授权 CLI，不靠当前默认 Profile。缺官网/后端/表时保留未配置，源码不会连接旧开发系统。Native 页面没有 Token 输入框；它按原流程认证自己的网关。

## 网关

复制 `workbench/config.example.json` 到源码外 0600 文件并填写自己的字段。state/工作台目录、notification-center 路径必须是本人真实绝对路径；成员 UUID/tenant/open_id/chat 均由本人授权核验；pepper 是本人新生成的随机值。槽位文件应在 load_config 处拒绝，不拿它启动假身份。

```bash
python3 -B workbench/backend/server.py --config "$HOME/.config/content-company-base/gateway.json"
```

保持回环/显式来源白名单。可选择自己受控的私有网关，但原生 endpoint 约束仍需逐项验证。扫码身份、原生会话、个人历史、重启保持分别验收，未配置时不启动第二个飞书长连接或后台调度。

## 模型与可选依赖

`CONTENTBASE_MODEL_CONFIG` 指源码外 0600 JSON：`{"url":"https://YOUR_MODEL_HOST/v1/chat/completions","model":"YOUR_MODEL","apiKeyEnv":"YOUR_MODEL_KEY_ENV"}`。Key 由本人环境/供应商保管，JSON 只存环境变量名；网关 `model_config` 使用同类文件。缺失返回未配置，不导入旧 Bridge 设置。

制作使用自己明确配置的 Codex/完整 Skill/ffmpeg/素材；历史 renderer 的音乐、模型、试验缓存不随代码提供。数据科学家、RUM 和后台工作流是可选资源，不预设已安装/登录。环境键可从相应源文件读取；资源不足、额度不足、格式错误要如实报告。

## 不要做的事

不复制开发者 Cookie/Token/目录，不扫描整个飞书，不自动修历史公式或复制原业务记录，不为测试发真实作品。空白结构也要审核人员选项/关联/公式。未知执行保留原 key/state/旧配置与回执，先只读核验。

### 制作资源的显式配置

允许制作的账号由网关 `production_accounts` 指定，空数组默认不执行。`local_codex.skill_files` 分别填写本人 `video-use` / `ffmpeg` Skill 的绝对目录，或通过 CONTENTBASE_VIDEO_USE_SKILL / CONTENTBASE_FFMPEG_SKILL 提供。缺失目录不解析为当前仓库；不复制整库、旧私人缓存或其他人的 Skill。额外 runtime 只读根通过本人 CONTENTBASE_RUNTIME_READ 显式列出，不由 Agent 临时放宽。
