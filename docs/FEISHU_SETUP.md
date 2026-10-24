# 接入你自己的飞书

> v0.4 分层说明：本文件描述保留的兼容 CLI / 控制面。完整原工作台见 [模块地图](MODULE_MAP.md) 与 [原生接入](WORKBENCH_SETUP.md)；源码只读查看不等于业务已接通。
## 绑定原则

飞书表是业务权威；底座只读你显式指定的 Base / table，不做全局搜索，不复制别人的授权，不另建平行业务账本。所有自动飞书操作均经 `lark-cli`，业务读写使用 `user` 身份，每条命令显式指定你自己的 Profile。

先按 `lark-cli` 自己的说明安装并完成本人授权。已有 Profile 就使用现有 Profile，不因接入底座重建授权。本人可执行只读管理命令 `lark-cli profile list` 找到自己的 Profile ID，再把它填入 YOUR_PROFILE 槽；该命令不使用 --json，不执行 profile use / 登录 / 切换。后续业务命令全部显式指定该 Profile。底座配置存绑定对象，不存 OAuth Token / App Secret。

## 准备表结构

可绑定现有内容表并映射字段；缺少必要字段时，由本人在自己的飞书原生界面建立，底座不会自动改表结构。首版只处理现存记录，测试记录亦由本人建立。

| 配置键 | 示例字段名 | 建议结构 / 用途 |
|---|---|---|
| `title` | 标题 | 文本，内容标题 |
| `body` | 正文 | 多行文本，UTF-8 正文 |
| `stage` | 阶段 | 单选：待审核、已审核、已投递、已发布、已复盘 |
| `published_url` | 作品链接 | 文本，本人核验后的完整 HTTPS 作品链接 |
| `metrics` | 复盘指标 | 多行文本，明确输入的指标 JSON；缺失不补零 |
| `review` | 复盘结论 | 多行文本，事实与判断分开 |

字段名可以不同，在 `feishu.fields` 映射；阶段文字在 `feishu.stages` 映射；未配 review / published / reviewed 时，分别默认待审核 / 已发布 / 已复盘。目标单选若缺对应文字，写计划会停止；由本人映射到实际既有选项，不自动加选项或回填旧记录。技术审核状态不等于程序已经写入飞书已审核 / 已投递。每个键映射到独立字段。其他已有业务字段保留；底座不重命名原表、不批量回填阶段。

## 第一次使用空表：本人建立一条明确记录

底座只处理现存记录，不会自动建表 / 加字段 / 造选题。先由本人在自己的飞书原生界面建立上述六项字段（或映射到既有字段），再新增一条允许演练的记录：

- **标题**：本人要处理的选题，例如“第一篇内容演练”。
- **素材原话**：本人粘贴一段真实素材。新表可另建“素材原话”文本列保留原始输入；没有该列时先填入映射的正文列，再由外部 Agent 提交草稿。不要用无来源占位冒充素材。
- **阶段**：选择本人映射的“待审核”；公开链接、指标与复盘可先留空，但相应字段仍需存在。

完成后保存，取得该现存记录 ID，再 doctor / sync。表返回零条是实测空表，不是导入成功或生成成功；本人新增后重新读取即可。底座没有内置模型，Agent 根据原素材产出文稿后才生成受审写计划。

## 找到准确对象并确认读取

从你自己的 Base 链接获取 token，明确目标表 ID。已知道 table ID 就跳过其他表，不查 Wiki / 云盘。以下只读命令使用你的值：

```bash
PROFILE='YOUR_PROFILE'
BASE_TOKEN='YOUR_BASE_TOKEN'
TABLE_ID='YOUR_TABLE_ID'
lark-cli --profile "$PROFILE" auth status --verify --json
lark-cli --profile "$PROFILE" base +base-get --base-token "$BASE_TOKEN" --as user --format json
# 仅在尚不知道 table ID 时：
lark-cli --profile "$PROFILE" base +table-list --base-token "$BASE_TOKEN" --limit 100 --offset 0 --as user --format json
# 数据 total 超过本页数量时继续 --offset 100、200…；不要将首 50 张当完整目录。
# 已有准确 TABLE_ID 时，直接读取该表，不枚举无关表：
lark-cli --profile "$PROFILE" base +table-get --base-token "$BASE_TOKEN" --table-id "$TABLE_ID" --as user --format json
lark-cli --profile "$PROFILE" base +field-list \
  --base-token "$BASE_TOKEN" --table-id "$TABLE_ID" --as user --format json
lark-cli --profile "$PROFILE" base +record-list \
  --base-token "$BASE_TOKEN" --table-id "$TABLE_ID" --limit 10 --as user --format json
```

只读预览可能有分页，不能把这 10 条当全量统计；真实结果只留私有目录。参数错误先看本机对应子命令 `--help`，不要改走另一条接口绕过。授权错误应处理本人缺失权限，不自动换 Profile 或 user / bot 身份。

## 填入配置

用 `init-config` 生成的模板，替换你自己的 `feishu.profile`、`base_token`、`table_id` 和字段名；CLI 不在 PATH 时，可把 `feishu.cli` 配成自己机器的可执行文件绝对路径。

```bash
python3 -m contentbase --config "$CONFIG" doctor
python3 -m contentbase --config "$CONFIG" sync
```

`doctor` 当次核验真实 user 身份。计划和技术任务绑定当次 `open_id` / `tenant_key`；它不是把 user Token 存入底座，也不自动选择新的共享账号。首次核验后此 state 固定本人身份；同一 Profile 后续换人成 workspace_operator_changed，混合旧身份成 workspace_operator_ambiguous。新本人须独立配置 / state 与自己的供应商授权，保留旧数据库；不靠重连借用旧授权。字段改变时原计划需要重新核验，不沿用旧审核。

## 写入验收

草稿、公开链接登记和复盘都先读取字段 / 目标记录、核验身份、执行 CLI dry-run，再冻结计划。本人批准后 `apply` 再核验身份、字段摘要与目标记录快照，实际更新，并使用同一 Profile / user 回读。只完成 dry-run 不算写入成功；CLI 返回不完整或中断时可能已写入，先核验原记录，不能换键重跑。

“读取成功”“有写权限”“字段回读一致”“作品公开”是不同事实。首个真实写入请选本人允许演练的记录，不把开源开发或本地测试视为向任意业务记录写入的授权。

并发边界：快照 / 字段摘要核验是本地乐观门禁，lark-cli 不提供远端原子 CAS。核验与更新之间仍可能被另一个写入者改动，首版请串行处理同一记录；不能把门禁当成多用户数据库事务。

unknown 写入可执行 `python3 -m contentbase --config "$CONFIG" verify-write "$PLAN_ID"`：只读检查本人 / schema / 当前目标字段。匹配后本地记录 applied，表示当前目标达到预期；不证明原请求回执，也不再执行写入。若字段不匹配则保留未知并调查。

## 完整来源 API 起点：供 CLI 产稿调用者

当外部 Agent 需要按飞书原文编辑，完整来源摘要来自本机 API，不是 record-list 预览 hash。先在另一终端用同一配置 / state 启动 serve，再由受信任的本机调用者读取访问文件和 API；供应商凭据仍留在 lark-cli，不读或复制它们：

```bash
python3 -m contentbase --config "$CONFIG" --state "$STATE" serve --port 8787
```

1. 本人读取有效 state 的 web-access.json，令牌只用于本机 Bearer，不放 URL / 聊天 / 日志。
2. GET `/api/operation-page?limit=1&offset=0` 取得 identity_fingerprint。
3. GET `/api/records/YOUR_RECORD_ID`，同时带 Bearer 和 X-Contentbase-Identity；返回完整 record / source_fingerprint。资料仅存私有目录，供已有 Agent 产稿。
4. CLI draft 时可传 `--expected-source "$SOURCE_HASH"`；这不授权批准、写表或发布。若完全不用 HTTP，CLI 支持本人已确认的完整素材稿件并形成新写计划，但不声称已把 record-list 预览绑定成完整编辑来源。

同一 OS 用户的不同 state 不等于供应商身份隔离。配置中只有 yxer 可执行文件路径，运行器沿用本机供应商环境；换人前本人必须独立确认自己的 yxer 授权 / 套餐 / 账号，不只换 STATE 或借用旧登录。CLI 可观察的 provider_binding 仍不证明当前供应商本人或稳定平台 UID。

配置或映射变化前保留旧完整 config 私有快照；原 unknown 任务回到原 config / state 用 inspect / verify-write / publish-verify 只读核验，不用新 scope 的空列表断定未执行。详见排错手册。
