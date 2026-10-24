# 排错：保留原请求，按错误类别核验

> v0.4 分层说明：本文件描述保留的兼容 CLI / 控制面。完整原工作台见 [模块地图](MODULE_MAP.md) 与 [原生接入](WORKBENCH_SETUP.md)；源码只读查看不等于业务已接通。
先保留 `error.code`、`unknown`、原 operation ID / key 和步骤。`unknown=true` 表示可能已执行，不表示“没有执行”；不要删 state、换 key 重发或切提供方绕过。问题报告只贴脱敏错误类别与版本，不贴配置、Token、完整 auth / 账号 JSON、原稿或媒体。

## 环境、身份和权限

| code | 本人下一步 |
|---|---|
| `configuration_required` / `feishu_binding_required` | 用 init-config 生成源码外模板，填写自己准确 Profile / Base / table；YOUR_ 槽不代表已接入 |
| `python_version_unsupported` | 使用本机已有 Python 3.11+；不要为排错自动升级系统 Python。维护状态见 [Python 官方维护表](https://devguide.python.org/versions/) |
| `cli_not_installed` / `invalid_cli_path` | 按 lark 官方源码或 yxer 供应商指引安装；核对 PATH 或私有配置中的可执行文件路径，不装未知镜像 |
| `credentials_belong_in_vendor_cli` | 从项目配置移除密码 / Key / Token，继续由原供应商 CLI 保管凭据 |
| `authentication_required` | 本机 Web Token 未通过；本人读取有效 state 的 web-access.json，重新连接；不提交该文件 |
| `workspace_operator_changed` / `workspace_operator_ambiguous` | 当前 state 固定原本人或旧数据混有多身份；清旧浏览器缓存，保留原数据库，新本人准备独立配置 / state 及自己的供应商授权，不通过重连解除绑定 |
| `client_identity_required` / `client_identity_changed` | 先 GET operation-page 获取当次指纹，POST 带 X-Contentbase-Identity；真实本人变化要重连，旧稿和缓存已清理 |
| `client_provider_required` / `client_provider_changed` | 先读取 accounts 获取当次 provider_fingerprint，发布 / choices 带 X-Contentbase-Provider；变化后重读账号与选项 |
| `cli_auth` | 在原供应商 Profile 核验本人授权；缺失范围交本人处理，不自动登录、换 Profile 或复制凭据 |
| `cli_permission` | 核对准确目标和本人读取 / 更新权限；missing_scopes 是经过筛选的权限提示，不是要取得所有权限的指令 |
| `operation_not_found` / `publish_provider_changed` | 核对原任务的本人配置 / 提供方；不从另一个空间展示或执行原私有任务 |

供应商 `provider_binding` 只指纹化 CLI 可观察的 API / 本机目标 / 账号上下文。它**不证明蚁小二当前本人身份或平台稳定账号 UID**；查看返回的 limitations，缺失的验证继续保持缺失。同目标任务使用目标绑定，无关账号新增不应永久使原任务失去查询；Web 可能仍需重读 accounts 更新当次全局指纹。

### 变更配置前：保留原完整快照

技术任务按当时完整配置划分 scope；改字段、阶段或 CLI 路径等配置后，新 scope 的空列表**不证明旧请求未执行**。变更前把旧完整 config 私有保存为 0600，快照同时放源码和 state 目录之外；不要清旧 state 或换原 key。

```bash
CONFIG="$HOME/.config/content-company-base/config.json"
STATE="${CONTENTBASE_STATE_DIR:-$HOME/.local/state/content-company-base}"
BACKUP_DIR="$HOME/.config/content-company-base-config-backups" # 必须在源码和 STATE 之外
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
OLD_CONFIG="$(mktemp "$BACKUP_DIR/config-XXXXXXXX.json")" # 新建唯一的 0600 文件
cp "$CONFIG" "$OLD_CONFIG" && cmp -s "$CONFIG" "$OLD_CONFIG" || exit 1
chmod 600 "$OLD_CONFIG"
# 再由本人编辑 CONFIG；不要覆盖 OLD_CONFIG。
```

已有 unknown / 遗留在途任务时，使用原 state、原 operation ID、原 key，以及原配置快照只读核验；全局参数放在子命令前：

```bash
python3 -m contentbase --config "$OLD_CONFIG" --state "$STATE" inspect "$OP_ID"
python3 -m contentbase --config "$OLD_CONFIG" --state "$STATE" verify-write "$OP_ID" # 非发布写入
python3 -m contentbase --config "$OLD_CONFIG" --state "$STATE" publish-verify "$JOB_ID" # 原发布任务
```

只使用对应任务类型的核验命令，不重复 apply / submit。回到原 config 不等于撤销外部写入或投递；本人身份已经变化时仍停止，不能靠旧 config 借用他人授权。

## 飞书字段与并发

| code | 本人下一步 |
|---|---|
| `feishu_table_missing` / `feishu_field_mapping_missing` / `field_mapping_collision` | 核对指定表和六项映射；每项对应独立实际字段，不全局搜索或自动加字段 |
| `invalid_stage_mapping` / `feishu_write_value_invalid` | 核对 stages 的非空文字和现有单选项；未配 review / published / reviewed 时分别默认待审核 / 已发布 / 已复盘，表内缺默认选项会停止，不自动创建 |
| `feishu_write_field_type_unsupported` | 首版写入只支持已适配类型；按实际表映射，不为通过测试改无关业务字段 |
| `feishu_schema_changed` / `feishu_record_changed` / `feishu_source_changed` | 原快照已变化；保留稿件，核对新字段 / 来源并建立明确新版本，旧摘要不批准新版本 |
| `idempotency_conflict` | 同 key 输入已变；核对原意图和原任务，不用新 key 绕过未知结果 |
| `resource_operation_in_progress` | 同一记录仍在执行；等待并刷新原任务，不启动并发副本 |
| `prior_write_requires_verification` | 有未知写入阻挡后续动作；先对原任务 verify-write |

同一 state 有本机跨进程锁，其他客户端不受它保护。飞书 CLI 没有远端原子 CAS，核验与写入间仍存在竞态；不要宣称本机门禁等同远端数据库事务。

完整来源变化时，先重新读取选中原记录；稿件保持原样，核对下方最新原文后，本人点击“核对并以最新源重新建立计划”。GUI 的 expected_source 是完整 raw row 摘要，不是列表预览或旧稿缓存的 hash；不做静默 rebase 或自动换 key。

## 平台字段、动态快照和素材

| code | 本人下一步 |
|---|---|
| `cli_validation` | 查看对应子命令 --help，修正原参数 / 文件；不猜另一条接口 |
| `cli_unsupported` / `yxer_type_unsupported` / `yxer_dynamic_field_unsupported` | 按精确 schema / prepare 契约确认；首版动态查询只支持 music / category，collection / location 等 opaque 对象尚未适配，不拼假 ID |
| `vendor_choice_receipt_required` / `vendor_choice_receipt_missing` | 先 choices 查询，字段同时填稳定 choice_id 和 choice_receipt；裸 numeric choice 被停止 |
| `vendor_choice_receipt_stale_or_mismatched` | 快照超过 30 分钟或本人 / 提供方 / 账号 / 类型 / query 不一致；重新查询和本人选对象 |
| `vendor_choice_changed_refresh_required` / `yxer_dynamic_selection_changed` | 原完整对象不再存在；停止使用旧选择，重新查询；不按新列表索引换对象 |
| `yxer_required_field_missing` / `yxer_field_value_invalid` | 按当前 schema 补齐必填项、类型、标签项等；不截断正文或塞默认占位 |
| `yxer_music_required` / `yxer_bilibili_fields_required` | 抖音图文选真实配乐；哔哩哔哩视频选真实分区并填写标签 |
| `video_and_cover_required` / `web_media_limit_20mb` / `unsupported_media` | 视频要一个视频和独立封面；图文只用图片；Web 单文件 20 MiB，较大视频走 CLI |
| `publish_preflight_expired` / `confirmation_changed` | 发布快照过期或摘要不符；先查看原任务，不沿用旧批准或自动换 key |

平台字段和媒体角色在供应商上传前预检；Web 的 JSON / 数量 / 格式基础检查也在本机媒体登记前执行。预检通过依然不代表已经发布。

## 素材审核与完整包

- `explicit_frozen_media_review_required`：缺少本人对准确 manifest 的审核摘要；先加载实际冻结素材或导出完整包，由本人完整查看后明确确认，Agent 不代勾选。
- `legacy_publish_media_requires_new_review`：旧任务缺少本机冻结媒体清单；保留原任务与回执，不把旧 metadata 当已审素材，不自动重发。
- `frozen_media_manifest_changed` / `frozen_media_integrity_failed` / `frozen_media_*`：冻结文件或清单不符；停止投递并保留证据，不改 hash 迎合现有文件。
- 网页 `frozen_media_hash_mismatch` / `frozen_media_size_mismatch` / `frozen_media_type_mismatch`：字节与清单不符，Blob / 本人确认已撤销；只看 metadata 不恢复审核。
- 网页 `frozen_media_preview_failed`：有效字节在当前浏览器未成功显示；不算已审。可导出覆盖全部素材的完整包，在本机观看后明确声明；超限视频也走此路径。
- `review_destination_exists` / `review_destination_invalid`：CLI 选源码外的新私有目录，不覆盖已有包；HTTP 只用固定本机路径。
- `review_package_verification_failed` / `review_media_changed`：导出包字节或清单校验失败；保留包并核验原任务，不当成已经观看。

包含完整真实稿件 / 媒体，不提交 Git 或问题报告。SHA / checksum 证明字节一致，不证明有人看完或理解了画面；程序不自动勾选。

## 流式快照与本机资源

- 媒体响应和完整包按 1 MiB 分块处理，完整素材 SHA 校验后从匿名 FD 快照交付；范围外篡改也会阻断。兼容 bytes helper 只允许最多 20 MiB 输出，超限用流或完整审核包，不重新上传。
- `media_stream_busy`：同一控制面已有两个快照；GET 返回 429，稍后重试读取即可，不换发布 key。CLI 多进程不共享这个进程内限额，也不是 OS 全局资源隔离。
- `media_snapshot_unavailable`：保留原任务，检查可用磁盘与私有目录权限后重试读取；磁盘失败不代表外部发布未执行。
- 快照会占本机磁盘，不再整份占 Python 内存。预算除原文件外，需容纳冻结副本、完整审核包，以及最多两个当前读取范围的临时快照；失败 / 关闭自动释放匿名 FD。
- 流响应发出头部后若中断，只断开连接，不在媒体后追加 JSON。客户端应把短读、断开或 hash 不符视为未完成读取，撤销素材确认。

## 未知结果和供应商故障

- **未知飞书写入**：`verify-write OP_ID`，或 Web“只读核验当前目标字段”。unknown 或无活跃资源锁的遗留 applying 可核验；只检查本人 / schema / 当前目标字段；一致后本地标 applied，**不证明原请求回执或写入者**，没有重写。
- **未知发布**：`publish-verify OP_ID` 查询原任务，可处理无活跃资源锁的遗留 submitting，不重发。私有 receipt.json 可恢复已保存 taskSetId；`task_receipt_missing_no_automatic_replay` 表示没有可靠任务标识，保留 unresolved-receipt / 技术证据，由本人核对供应商原任务，不重发。
- `same_source_already_submitted` / `prior_publish_requires_verification`：查看原受理 / 未知任务，不能换 key 绕过。taskSetId 只受理，公开可见仍需本人打开作品。
- `cli_rate_limit`：按安全 `retry_after` 信息等待；写入可能已经到达，先核验原请求。底座不会自动重发业务。
- `cli_provider_unavailable` / `cli_transport_failed`：区分读取与写入。只读可在故障恢复后再查；写入、`cli_execution_unknown`、`write_result_unknown`、`publish_result_unknown` 等先核验。
- `cli_invalid_response` / `cli_failed`：保留类别和退出状态，不回显 CLI 原始响应或凭据；未知按上面处理，不把空响应当成功。

升级前停止旧服务；代际标识迁移保留数据，旧无标识的在途任务保守归 unknown；腐坏数据库返回结构化错误时保留原文件，不删除或自动重建。

## 指标与浏览器恢复

`metric_provenance_required` 要求非空 platform / work_id / observed_at 和 values 对象；`metric_timestamp_with_timezone_required` 要求带时区 ISO 时间；`metric_values_must_be_finite_or_missing` 要求每个指标为有限数值或 null，不能是布尔、字符串、NaN / Infinity。不知道的指标不补零。

浏览器当前标签页按本人指纹保留未提交稿件和原请求 journal。刷新先连接 / 读来源再恢复；关闭标签页、清理存储、Token / 身份变化不保证保留，重要稿件应先保存文件。身份切换清缓存是隐私边界，不是数据迁移。长期操作可切页看队列，提交按钮暂时锁定；不要开第二份任务绕过。
