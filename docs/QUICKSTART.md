# 快速上手

> v0.4 分层说明：本文件描述保留的兼容 CLI / 控制面。完整原工作台见 [模块地图](MODULE_MAP.md) 与 [原生接入](WORKBENCH_SETUP.md)；源码只读查看不等于业务已接通。
目标是先读到**你的飞书记录**，再完成一条受审闭环；不复制其他人的运行环境。启动时间以供应商 CLI、本人授权 / 套餐与表权限已经到位为前提；首次注册、审批或供应商配置时间不包含在本机启动步骤内。

## 1. 你需要准备什么

| 资源 | 到位标准 |
|---|---|
| Python 3.11+ | `python3 --version` 满足要求 |
| lark-cli | 已自行安装；存在你自己的已授权 Profile；user 身份有目标表的读取 / 更新权限 |
| 飞书 Base / 内容表 | 你知道准确 Base token、table ID、字段映射；至少有一条本人建立的测试记录，填明确标题 / 素材原话 / 阶段 |
| yxer | 已自行安装和配置；`doctor` 能核验通道；目标账号状态可操作 |
| Agent | 可调用 CLI 或 HTTP，能交付 UTF-8 文稿 / 指标文件；无需特定模型 |
| 发布素材与本人 | 本人认可的稿件、图文图片或视频 / 封面；正式投递时本人实际确认 |

安装 / 登录按各供应商自己的说明完成；底座不替你升级 CLI、切账号或登录。服务费用、CLI 权限及平台限制由使用者自己确认。

## 2. 配置与只读启动

以下命令从源码根目录执行。`YOUR_` 是待填写槽，不代表已授权或已成功。

```bash
export CONFIG="$HOME/.config/content-company-base/config.json"
python3 -m contentbase --config "$CONFIG" init-config
```

编辑生成的配置，参考 [飞书接入](FEISHU_SETUP.md)。空表时先由本人在飞书建立一条标题 / 素材原话 / 阶段明确的记录；底座不会自动造记录。不要写入密码、Token 或 API Key；Profile / Base token / table ID 是绑定信息，仍应留在私有配置而非 Git。

```bash
python3 -m contentbase --config "$CONFIG" doctor
python3 -m contentbase --config "$CONFIG" sync
python3 -m contentbase --config "$CONFIG" serve --port 8787
```

兼容控制面打开 `http://127.0.0.1:8787/control`。访问 Token 保存在有效 state 目录的 `web-access.json`（0600），默认是 `~/.local/state/content-company-base/web-access.json`；由本人在本机读取文件的 `token` 并输入网页，终端启动输出不会打印它。不要把 Token 放在 URL、截图或问题报告中。首次看到表格只表示读取成功，不表示写权限和发布权限全部通过。

变更配置前先私有保存旧完整 config（0600、源码及 state 外），保留原 state / operation ID / key。新配置 scope 空列表不表示未执行；按 [原配置快照只读恢复](TROUBLESHOOTING.md#变更配置前保留原完整快照) 核验。

默认 state：`~/.local/state/content-company-base`。也可用全局参数 `--state PATH`，或在启动前设置 `CONTENTBASE_STATE_DIR` 为另一私有目录；不要把它设在源码仓库，也不要无故删除，它保存幂等和未知任务。

### 浏览器中的稿件与恢复

一个 state 首次核验固定本人；换人要独立配置 / state 及自己的供应商授权，保留旧数据库。连接核验当次本人空间，列表选中记录时另取完整原文 / 摘要，未提交稿件按本人指纹 / 记录保留在当前标签页的 sessionStorage。切换记录时有未提交稿件可取消切换；源变化保留稿件并显示最新原文，须本人点“核对并以最新源重新建立计划”才重新绑定来源；刷新后先连接并重新读取，才恢复该空间的稿件。关闭标签页、清理存储、Token 或实际身份变化不保证保留，请先导出重要文稿；浏览器缓存不替代飞书。

同一输入的丢响应重试保留原 key 和已上传媒体 ID，并先核验原请求。输入改变建立新意图，未知任务仍先核验。技术队列优先未知 / 活跃项，可加载后续页面；长操作期间可切页查看，其他业务提交暂时锁定。

## 3. 第一次写草稿

选一条本人允许演练的记录，把 UTF-8 正文放在私有目录。以下变量需要替换为你自己的值：

```bash
RECORD_ID='YOUR_RECORD_ID'
BODY_FILE="$HOME/content-inputs/draft.md"
python3 -m contentbase --config "$CONFIG" draft "$RECORD_ID" \
  --title '第一次内容演练' --body-file "$BODY_FILE" --key 'draft:first:1'
```

这一步返回写计划，不自动写飞书。从成功输出 `data` 中取 `id` 作为 `PLAN_ID`、`confirmation_digest` 作为 `DIGEST`（不是同层 `digest` 输入摘要），先检查完整目标、字段和正文：

```bash
python3 -m contentbase --config "$CONFIG" inspect "$PLAN_ID"
python3 -m contentbase --config "$CONFIG" approve "$PLAN_ID" --digest "$DIGEST"
# 本人在 TTY 冻结预览中输入确切 CONFIRM。
python3 -m contentbase --config "$CONFIG" apply "$PLAN_ID"
```

需要按表中原文编辑或取得可靠 source_fingerprint 时，先在另一终端启动 `python3 -m contentbase --config "$CONFIG" --state "$STATE" serve --port 8787`，按 [完整来源 API 起点](FEISHU_SETUP.md#完整来源-api-起点供-cli-产稿调用者) 读取；当前没有 source 子命令，不把列表摘要当完整源。若通过完整记录 API 得到编辑来源摘要，可在 draft 加 `--expected-source "$SOURCE_HASH"`；GUI 始终传此摘要，不把列表预览 / 旧缓存当来源。

`apply` 包含真实外部写入和回读。本人批准的是这份写计划，草稿阶段仍为“待审核”。若原记录被修改、身份变化或结果未知，按实际错误核验，别换键重复写。

## 4. 第一次发布与复盘

先读 [发布流程](PUBLISHING.md)，准备必要资源后再执行 `publish-prepare`。它会真实上传素材，validate / dry-run 不是正式发作品；正式提交前，点击加载实际冻结素材，或导出完整本机审核包；本人完整查看视频 / 图片 / 独立封面后明确勾选，再确认精确版本。浏览器每个素材最多 20 MiB，较大视频从 CLI 提供本机路径，不经 Web 媒体接口。配乐 / 分区先查询并点击具体对象，界面将 receipt 与稳定对象 ID 写入平台 fields，不能只抄一次查询的裸索引；CLI 同样需要快照 receipt。

```text
publish-prepare → 实际冻结素材 / 完整本机审核包 → 本人观看和确认 → publish-submit → publish-verify
→ 本人打开公开作品 → published 写计划 → approve / apply
→ review 写计划 → approve / apply
```

如只想完成本机学习和只读验证，停在 `sync`；如想验证写表，使用自己的演练记录。正式发布是否完成以本人打开作品为准，不以 taskSetId 为准。

## 5. 停止、排错与反馈

升级前先 `Ctrl+C` 停止旧前台服务（后台服务也先停），再换源码；无代际标识的旧在途任务保守归 unknown，只读核验。前台服务用 `Ctrl+C` 停止；不删除 state、不重放未知任务。执行 `inspect` 按当次本人身份 / 配置查询已有计划 / 任务（会真实只读核验身份），`doctor` 核验当前环境。unknown 非发布写计划可用 `verify-write PLAN_ID` 只读检查当前目标字段；匹配仅说明当前目标达到预期，不证明原请求回执。

提交问题时只附：系统 / Python / CLI 版本、脱敏错误类别、步骤和是否已真实执行。不要提交完整配置、auth 响应、账号列表、原稿、媒体、Token 或技术数据库。机器测试、本人授权、真实平台结果分别记录。
