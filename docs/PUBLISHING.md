# 蚁小二真实发布

> v0.4 分层说明：本文件描述保留的兼容 CLI / 控制面。完整原工作台见 [模块地图](MODULE_MAP.md) 与 [原生接入](WORKBENCH_SETUP.md)；源码只读查看不等于业务已接通。
## 不假装适配，不跳过确认

发布路径为用户自己安装的 `yxer` CLI。账号授权、平台规则和通道由蚁小二负责，底座不复制 Cookie、重建平台登录或直连隐藏 API。首版每次任务一个账号，支持 `imageText` / `video`，固定 `cloud` / `local` 通道；不自动切通道。

**准备发布会真实上传素材。** 本人确认冻结预览后才正式投递；未确认、未知、过期或冲突的任务不会自动重发。

## 准备自己的环境

```bash
yxer doctor --json
yxer accounts list --all --json
yxer prepare 'YOUR_PLATFORM' imageText --json
yxer schema get 'YOUR_PLATFORM' imageText --json
```

账号 ID 必须来自自己的真实账号列表，准备和提交都重新检查是否可操作。平台名用 CLI 实际接受的名称，例如 CLI 使用“哔哩哔哩”而非自行简写。具体平台 / 类型能力以本机 CLI 精确契约为准，不以静态目录缺项或旧教程推断。

动态资源按目标账号查询，使用查询返回的完整对象，不能凭名称拼假 ID：

```bash
yxer query music 'YOUR_ACCOUNT_ID' --query 'YOUR_MUSIC_QUERY' --json
yxer query categories 'YOUR_ACCOUNT_ID' --type video --json
```

需要 BGM 的图文必须选择真实返回的音乐；查询空或必填资源缺失就停在准备阶段。需要分区 / 标签的视频按 schema 填写。既不截断正文，也不以默认占位绕过必填项。

## 形成任务

Agent 先交付文稿，飞书草稿经本人写计划批准 / apply 后再准备发布；这次写入批准不等于正式发布批准。

平台字段放在私有 JSON 文件。普通平台字段使用 schema 规定的简单类型。动态选择先运行底座 `choices`，从返回快照的 options 选择稳定 id，并在 fields 同时填写 `music_choice_id` / `music_choice_receipt` 或 `category_choice_id` / `category_choice_receipt`；music_query 仍须与原查询一致。numeric `*_choice` 兼容但必须带对应 receipt，索引从原快照解析，二次查询不按新顺序换对象。不在文件中放密钥。

```json
{
  "music_query": "YOUR_MUSIC_QUERY",
  "music_choice_id": "YOUR_OPTION_ID",
  "music_choice_receipt": "YOUR_RECEIPT_ID"
}
```

先用 `python3 -m contentbase --config "$CONFIG" choices "$ACCOUNT_ID" --kind imageText --field music --query "$MUSIC_QUERY"` 获取 receipt / options。只添加当前平台 / 类型需要的字段，实际 schema 和查询结果优先。首版动态查询只支持 music / category；collection / location 等 opaque 动态对象尚未适配，不伪造对象或 ID。视频封面由本人提供，上传图片 / 视频 / 封面后保存完整回执（URL、尺寸、时长等），不只保留 URL 或自填尺寸。

图文示例：

```bash
python3 -m contentbase --config "$CONFIG" publish-prepare "$RECORD_ID" \
  --account "$ACCOUNT_ID" --platform "$PLATFORM" --kind imageText \
  --channel cloud --media "$IMAGE_FILE" --fields "$FIELDS_FILE" \
  --key 'publish:article:1'
```

多张图片可重复 `--media`。Web 只接受本人上传的文件内容和媒体 ID，每个素材上限 20 MiB，不接受宿主文件路径；较大视频使用 CLI。视频示例：

```bash
python3 -m contentbase --config "$CONFIG" publish-prepare "$RECORD_ID" \
  --account "$ACCOUNT_ID" --platform "$PLATFORM" --kind video \
  --channel local --media "$VIDEO_FILE" --cover "$COVER_FILE" \
  --fields "$FIELDS_FILE" --key 'publish:video:1'
```

准备流程：记录 / 身份 / 账号 → prepare + schema / 字段预检 → 动态资源 → 本机复制冻结实际字节与有序 manifest → 仅上传冻结副本 `--auto-meta` → validate → publish `--dry-run`。validate、dry-run 和后续提交使用同一 payload / 通道，无自动回退。

## 实际素材审核与本人正式投递

从准备成功输出 `data` 取 `id` 为 `JOB_ID`、`confirmation_digest` 为 `DIGEST`；同层 `digest` 是输入摘要，不是批准摘要。冻结 manifest 另有 `media_review_digest`，绑定实际字节、顺序、角色、尺寸及 MIME；发布要求声明本人审核的是这份准确 manifest。

### Web：先加载素材，再由本人勾选

在 ready 卡点击“加载冻结素材审核”。工作台经 Bearer + 当前本人指纹读取本任务原冻结字节，核对长度、MIME、SHA-256 后，以临时 Blob 显示全部图片，或视频与独立封面；原始顺序和完整文案保持。只看到 JSON、文件名、hash，或只加载视频第一帧，不等于本人已经完整观看。

全部网页素材成功加载后，**本人**完整查看视频 / 图片 / 独立封面、核对文案 / 账号 / 平台 / 通道，再明确勾选。程序从不自动勾选；正式确认还需一次明确确认。读取失败、解码失败、篡改、取消、重绘或身份 / 提供方切换会撤销确认；Blob 被释放，正式投递保持禁用。

每项网页读取上限 20 MiB。超限视频，或浏览器不支持的有效编码，可以点击“导出完整本机审核包”；完整包覆盖**所有**素材，不要求浏览器先逐项载入。本人打开返回的私有 `index_path`，完整观看、核对清单后再明确勾选。导出成功不会自动变成“已审”；发现字节 / manifest 不符时先核验原任务，不通过换审核路径掩盖篡改。

### CLI：查看完整本机审核包

```bash
python3 -m contentbase --config "$CONFIG" inspect "$JOB_ID"
python3 -m contentbase --config "$CONFIG" publish-review "$JOB_ID" \
  --destination "$HOME/content-reviews/$JOB_ID"
# 由本人打开返回的 index_path；按原顺序观看全部 media、完整视频和独立封面。
python3 -m contentbase --config "$CONFIG" publish-submit "$JOB_ID" --digest "$DIGEST"
```

`publish-review` 输出到源码外的新目录，不覆盖既有目录。包中包含 HTML、原冻结媒体、review.json 和 checksum。`publish-submit` 会再次在该任务私有 review 目录准备 / 核验完整包，要求本人打开观看后，在 TTY 输入确切 `CONFIRM`，才带上对应媒体摘要正式投递。

素材审核勾选、TTY 与媒体摘要是声明和一致性门禁，**不是人的视觉能力证明或同用户恶意进程的 OS 隔离**。Agent 不替本人勾选 / 输入确认；开发或自动化授权不等于允许发布真实作品。审核包含本人真实稿件和媒体，只留私有目录，不提交 Git 或问题报告。

确认必须匹配同一账号、平台、稿件、实际素材、资源对象、通道、批准摘要和媒体摘要。稿件 / 记录 / 账号状态变化后重新核验；旧摘要不批准新版本。

### CLI 分步执行与重新核验

准备快照有效期 30 分钟。CLI 的准备与提交可以是不同进程：对仍未过期的 `ready` 任务，提交先核验本人身份、飞书来源和冻结包，再按原查询重取 schema / 账号 / 动态资源，完整原音乐或分区对象必须仍在结果中；随后对原 payload / 通道重做 validate + dry-run。此过程不再次上传、不替换稿件 / 资源、不修改冻结包。

过期、资源变更或未知状态不会自动恢复为可提交，也不会自动换键重跑；先查看原任务并处理具体事实。已经 `submitting` / `unknown` 的任务按下节核验，不走重新正式投递。

## 核验，不重发

```bash
python3 -m contentbase --config "$CONFIG" publish-verify "$JOB_ID"
```

| 已确认的层级 | 代表什么 | 不代表什么 |
|---|---|---|
| upload 成功 | 素材进入蚁小二 | 已发布作品 |
| validate / dry-run 成功 | 本次 payload 与通道通过预检查 | 正式投递或公开 |
| taskSetId / submitted | 蚁小二受理了任务 | 平台发布成功 |
| details 返回作品信息 | 有对应平台回执 / 作品标识 | 本人已验证公开可见 |
| 本人打开作品 | 该作品实际可见 | 飞书登记已完成 |

提交超时、进程中断或缺少可信回执时保持未知。已保存的私有 `publish/OP_ID/receipt.json` 可由 `publish-verify` 恢复 taskSetId 供查询；脱敏原生业务回执亦保留为技术证据，缺可靠任务标识就停止。先查询原 taskSetId、平台回执或核验原任务，不换 `--key` 重发、不假设未发布。重复提交请求只核对已有任务，恢复程序不重放在途发布。

## 将公开事实写回飞书

本人确认作品实际公开并取得准确 URL 后：

```bash
python3 -m contentbase --config "$CONFIG" published "$RECORD_ID" \
  --url "$PUBLIC_URL" --key 'published:article:1'
# 保存 data.id / data.confirmation_digest，再由本人 approve，最后 apply 并回读。
```

`published` 只是生成登记计划，不会替你打开作品或推断公开。回执先留本机技术 state，不能自动把 submitted 写成飞书“已发布”。指标导入与复盘同样另建受审计划。

### 当前资源与性能边界

素材视频上限 512 MiB，网页 Blob 预览单项仍限 20 MiB。HTTP / 本机包经已核验匿名 FD 快照按 1 MiB 读写，不累积完整视频；兼容 bytes helper 输出超 20 MiB 明确要求流或完整包。Range 每次仍核验全素材 SHA，不能按范围读取成本估算延迟。

本轮已用 512 MiB 稀疏 MP4-compatible header 样本加 PNG，验证精确限额、完整 HTTP / Range / 包的字节一致、两槽与第三请求 429；macOS / 隔离 Linux Python 峰值 RSS 分别约 45.9 / 34.7 MiB，均低于 64 MiB。这是禁网与替身供应商的资源验收，不证明样本可播放、真实 CLI 子进程占用、平台兼容、供应商上传 / 发布或长期并发 SLA。

除已有原素材外，磁盘需保留冻结副本 + 完整包 + 最多两个当前范围快照的空间。单个控制面快照槽为 2；多 CLI 进程不共用此限额。匿名快照在读完 / 出错 / 关闭后释放，原素材和原技术任务不删除。
