# 内容助手团队对话后端 V1 设计

> 状态：原V1设计中的共享文本网关、持久会话、官方CLI设备授权扫码与备用飞书私聊一次性码已实现并部署；业务工具和OS执行沙箱尚未实现或上线。2026-10-01用户确认共享一个团队后端，每人独立身份与会话。当前实现见[backend/README.md](backend/README.md)。已创建独立技术状态库与网关监听，未修改原Bridge、原CLI凭据或App运行包。

> 实施边界：Mac App首页现已通过可信原生IPC与本服务联动，真人Native认证和重启保持分别验收。以下保留完整目标方案，实际可运行入口和验收以backend/README.md及Library验证记录为准。

## 1. 已确定的产品行为

- 各成员Mac App是客户端；同一成员在不同Mac登录可恢复本人的历史。
- 一个成员可新建多段持久对话，按主题/一期视频分开。新对话不自动混入其他对话或群聊。
- 每次提问创建独立job；对话和结果记录持久，执行工作区临时。成员认证、对话归属和工具授权分别校验。
- 内容助手复用同一人格、工作流知识和受限工具适配器，不共享个人聊天上下文或管理员权限。
- 团队任务、视频阶段、脚本及复盘仍以飞书原记录为权威；聊天、执行、鉴权状态属于技术存储，不新增业务登记账本。
- 首页显示处理状态及最终答案/产物，不展示内部推理或文本增量。三维首页已接入纯文本网关，未认证时发送禁用。

## 2. 现有实现与关键缺口

以下实现事实以本工作树源码为依据，不等同于当前共享App运行包。本轮终态核对发现运行包6个src/web文件与本工作树有差异，来源未核验；本轮没有修改这些运行文件，也不重建或覆盖当前App。

| 当前证据 | 设计处理 |
|---|---|
| Bridge按chat_id映射Cherry session，私聊/群聊隔离 | App新建channel=app的独立映射，不自动继承飞书聊天 |
| CherryClient通过本机API调用Agent，现有Agent可能具备执行工具 | 新入口先使用chat_only受限执行配置；单独验证工具限制，提示词不当安全隔离 |
| Cherry API只监听回环；客户端现有工具调用经lark-cli | 新共享网关同机访问Cherry，客户端不直连或获取Cherry Key |
| CLI auth list可列登录用户；auth login为Device Flow；公开global/login help未见显式按请求选人参数 | 多成员凭据路由是P0能力探针，不推断“列出用户=可安全选人” |
| 生产Profile已绑定当前授权身份 | 保留现状；新App不默认继承它，未核验成员绑定时工具保持待授权 |
| 内置飞书Base有独立浏览器会话 | 该Cookie不作为App认证凭据，不读取或复制桌面/CLI Cookie与Token |

最初设计阶段仅查看CLI help；后续已通过官方Device Flow独立认证目录、纯文本Cherry代理、回环网关及私有HTTPS实施。浏览器扫码结果须本人完成后验收；Provider PKCE和OS沙箱不在本版声明范围。

## 3. 最小部署：一个网关，不搭完整ERP

```text
各成员Mac App
  └─ Electron主进程：受限IPC、登录挑战、保管本系统会话凭据
       └─ 私有网络HTTPS团队网关
            ├─ AuthAdapter：验证飞书身份、组织/成员白名单、会话撤销
            ├─ ConversationStore：持久消息、个人上下文、会话归属
            ├─ JobQueue：幂等、状态、限并发、恢复与取消
            └─ RuntimeAdapter / ToolBroker
                 ├─ 受限Agent（共享人格，独立session）
                 ├─ 按成员授权的CLI/采集适配器
                 └─ 临时执行工作区
```

- 第一版采用单网关进程、单受限worker、SQLite技术状态库的轻量结构；这一结构已在独立技术目录实施，不修改飞书业务表。
- SQLite在网关主机本地磁盘使用WAL和事务，不放共享网络盘。正式实施时复核并发和备份需要。
- Cherry保留回环监听，不把其端口直接开放给团队。网关入口采用私有网络和有效HTTPS；不假定局域网内人人可信。
- App渲染层不直接请求高权限服务。主进程根据可信本地页面IPC发起请求，OAuth/CLI/Cherry凭据不进渲染层。对话内容按原文传递。
- 后端Mac休眠或断线时App显示明确离线状态。已受理任务保留原job_id，未收到受理回执的不冒充已接单；恢复后用同一幂等键查原任务，不换键补发。
- 第一版不增加Redis、向量库、消息总线、微服务或全功能容器平台；如果需要任意代码执行，再单独选择并验收真正的进程隔离。

## 4. 身份与登录：由飞书证明“是谁”

### 4.1 主体与权限

- AuthAdapter从可信登录结果产生principal，内部身份键作用域为tenant_key + provider_app_id + open_id；open_id仅在应用内识别成员。实际字段以选定应用契约为准。
- 成员记录单独存status和authz_version。客户端姓名、user_id、owner_id、profile、角色字段不作为授权来源。
- 新成员不自动加入团队。只有预配置组织/成员白名单允许建立受保护会话；移出/停用后停止新请求。
- 管理员可管理成员和服务健康，但不默认拥有他人私人对话的读取权限。团队共享的是已授权的飞书记录，不是全员聊天。
- 网关会话是本系统凭据，和飞书Token、浏览器Cookie分开。每次受保护请求重新检查成员状态、会话有效性和资源归属。

### 4.2 登录交接流程

1. App主进程生成一次性client_verifier，将其SHA-256挑战值发送POST /v1/auth/login/start；verifier不进入渲染层。
2. 网关建立短期login_id、单次state及预注册回调绑定，返回本网关认证入口authorization_url。App只打开预配置网关/飞书认证域名，不执行任意返回URL。
3. 飞书登录由AuthAdapter完成；实现必须遵守lark-cli和实际供应商契约。服务端验证state、回调、租户、成员白名单，并获取可信成员信息。
4. 浏览器回调只显示认证结果，不在URL、页面、日志中返回Token。回调结果只关联此login_id，不信任客户端自报成员信息。
5. App主进程用login_id + client_verifier调用POST /v1/auth/login/poll。网关验证挑战和一次性交接，才向该主进程发本系统会话凭据。
6. App主进程将本系统凭据放系统安全存储，渲染层只拿到可展示的本人资料和能力状态。退出登录撤销对应会话并清理客户端凭据。

client_challenge是本系统App交接保护，不声称它替代供应商OAuth PKCE。选定OAuth端点须验证CSRF/state、授权码注入保护、预注册精确回调及PKCE能力。采用原生公开客户端流程时须满足PKCE要求；供应商支持未实测前不自动降级上线。身份认证和飞书用户信息读取也须满足项目CLI规则，不在设计阶段假定可直连API绕过。

建议配置初值（待实施前确认）：登录挑战5分钟、会话闲置30分钟、会话绝对有效期12小时；这些仅约束登录，不删除持久对话。外部失效信号可用时立即撤销；信号延迟作为待验收项，不承诺瞬时发现所有权限变动。

设备ID仅作关联，不作认证。需要防范登录地址被转发：首次设备配对由指定成员/管理员确认，之后将设备注册证明与目标成员绑定；OAuth结果与已绑定成员不一致时终止交接。client_challenge本身不证明成员身份，也不替代受信客户端登记。这些保护须在P0测试中确认，匿名挑战不会直接获得会话凭据。

### 4.3 调用权限：两道门

```text
飞书身份验证 → principal
principal + 目标资源 + 已绑定凭据 + 当前授权 → ToolBroker允许执行
```

保持当前生产Profile及原工作流不变。按成员选择凭据必须由正式支持的CLI契约/隔离绑定验证，客户端和模型不选择任意Profile。若适配器未能证明当前操作者，返回authorization_required/adapter_unavailable并保持工具关闭；不回退共享管理员身份，也不通过切换全局默认用户/改全局Token来处理并发请求；每次执行使用已核验且不可被客户端覆盖的credential_ref。登录成功本身不授予全部飞书资源访问权。

### 4.4 App身份与原网页账号分开

同一Mac切换成员时，App关闭旧的远端pane，下一次打开使用该成员已验证principal对应的浏览空间；既有旧空间保留，不复制Cookie，不自动删除或注销它。任务网页当前使用的单一本机分区是既有实现，不声称它已支持多人同机身份切换。后续迁移须本人重新验证，分别标明“Agent成员身份”和“原网页账号”；账号矩阵的团队共享账号也需要明确访问授权。

## 5. 持久数据模型（仅设计，不执行DDL）

| 记录 | 必要字段与约束 |
|---|---|
| identity | principal_id(UUID)、tenant_key、provider_app_id、open_id；组合唯一 |
| membership | principal_id、status、roles、authz_version；停用后保护请求中止 |
| login_session | session_id、credential_hash、principal_id、device_id、issued/expires/revoked_at；不存明文会话凭据 |
| credential_binding | principal_id、adapter_kind、credential_ref、verified_actor、verified_at、status；真实密钥放受限安全存储 |
| conversation | conversation_id、tenant_key、owner_id、channel=app、title、version、runtime_session_ref、created/updated/archived_at |
| message | message_id、conversation_id、tenant_key、owner_id、client_message_id、role、content、created_at；按conversation分页，不跨owner |
| execution | job_id、conversation_id、message_id、tenant_key、owner_id、idempotency_key、request_fingerprint、state、attempt_id、result_ref、created/finished_at |
| event | job_id、seq、type、payload、created_at；只保存状态、终态或引用，不保存内部推理 |
| active_context | conversation_id、workflow_id、object_ref、stage、pending_action_ref；不由“继续”二字回猜对象 |
| audit | principal_id、job_id、action、resource_ref、outcome、timestamp；脱敏、不含Token或完整聊天正文 |

技术ID随机不可猜测，归属检查仍是必需；每次查询按tenant + owner + object_id限定。跨用户/跨租户对象返回同样的404，避免泄露是否存在。复合外键/唯一约束约束message/job与conversation的归属，事务同时写消息和job，避免写入半条状态。

每个App conversation单独映射Runtime session；同一会话串行执行，避免上下文乱序。首版默认每人1个活动job、全局1个活动job，完成跨成员并发隔离与Runtime能力探针后再提高并发，值可配置。所有成员共享模型能力而不是共享一个模型会话。

历史在网关持久保存，不每次把全部历史塞给模型：取该会话最近消息、自己的摘要、当前对象与授权资源引用；摘要也是个人上下文。长记忆经本人确认，共享知识经团队明确批准。授权变化时重新建立受限上下文，避免继续依赖过期资源快照。

第一版仅文本。附件后续复用既有feishu-input-workflow的大小/类型/路径和非可信内容检查；不开放任意本机路径字段。历史保留/删除策略实施前确认，不默默自动清空聊天。临时工作区建议终态后24小时清理；结果未知、待审批或待核验的工作区先保留，业务记录不随目录清理删除。

## 6. API契约

机器可读定义：[conversation-api.openapi.json](conversation-api.openapi.json)。机器契约已同步文本网关实现；OAuth回调仍为保留入口，明确返回未接入。

| 路径 | 用途 | 边界 |
|---|---|---|
| POST /v1/auth/login/start | 建立绑定客户端挑战的登录流程 | 未登录可调用，限流、固定回调、短期有效 |
| GET /v1/auth/feishu/callback | 供应商授权回调 | 必须匹配一次性state；不向浏览器返回会话Token |
| POST /v1/auth/login/poll | App完成认证交接 | login_id和verifier匹配，成功只交接一次 |
| GET /v1/me | 本人资料/可用能力 | 需要有效本系统会话；能力来自服务器 |
| POST /v1/auth/logout | 撤销本机网关会话 | 身份核验、幂等；不是删除对话 |
| GET/POST /v1/conversations | 列表/新建对话 | owner来自principal，客户端不传owner/profile |
| GET /v1/conversations/{conversation_id}/messages | 本人历史分页 | tenant/owner过滤，cursor只用于本人的该会话 |
| POST /v1/conversations/{conversation_id}/messages | 接受文本并创建job | 202只表示受理；Idempotency-Key必须有 |
| GET /v1/jobs/{job_id} | 查询原任务 | 当前身份和job归属核验 |
| GET /v1/jobs/{job_id}/events | 状态与最终答案事件 | 同样鉴权，凭据不放URL，断开后查询原job |
| POST /v1/jobs/{job_id}/cancel | 请求取消 | 202是请求受理，不把在途写入当已撤销 |

客户端正文不包含身份、权限、工具名称、CLI参数或任意模型/文件路径。未知字段视为参数错误。正文长度、UTF-8、分页上限和速率在服务边界校验。本人以外的conversation/job既不返回数据，也不触发执行。

同一principal、conversation、Idempotency-Key和相同输入返回原job；同键不同输入返回409。请求受理以事务持久化为准；客户端断线不丢任务，重试不自动新建第二个业务执行。失败结果字段只描述已知事实，缓存结果带来源/时间。

事件类型仅status、final、result_ref、error；不发送thought/delta。SSE断线以Last-Event-ID恢复该job的后续事件；身份失效时中止读取。日志记录request_id、job_id和脱敏码，不打印凭据或完整OAuth查询串。

## 7. 执行状态、取消与恢复

```text
queued → running → succeeded
             ├→ failed（已明确执行失败且副作用已核验）
             ├→ waiting_authorization（等待本人授权）
             ├→ waiting_confirmation（等待原生人工确认）
             ├→ execution_unknown（副作用待核验，不自动重放）
             └→ cancel_requested → cancelled / execution_unknown / succeeded
queued → cancelled
```

- 数据库job状态和真实执行证据共同决定终态；进程结束、HTTP200、模型回答都不单独证明飞书写入完成。
- cancel_requested不删除原任务、结果或记录。已完成的写入不会因为取消聊天等待而自动回滚。
- 崩溃/休眠恢复后：未派发queued可继续调度；running只有能核验原执行时才恢复，结果不确定转execution_unknown。
- 同一会话恢复前一个未知执行不能通过重新提问自动绕开；提供“核验原任务”路径。
- 授权恢复/人工确认恢复必须匹配job与原输入版本，原生回调原子消费一次，不凭模型说“已确认”继续。
- 第一版chat_only不产生写动作。后续飞书写入、发布、记账继续用既有人工确认边界、唯一CardKit回调所有者与统一通知中心；不另启event bus，也不用URL按钮冒充原生确认。

## 8. 临时工作区与真正的隔离

工作区路径由服务器生成：work/<principal_id>/<job_id>/。校验路径落在受控根目录、拒绝符号链接逃逸和客户端路径，禁止跨成员临时文件读取。凭据只由ToolBroker短期使用，不放工作区/模型上下文。

独立目录、独立session和提示词只属于逻辑隔离，不是OS沙箱。第一版工具使用固定适配器及允许的参数，不开放任意shell。若当前Cherry Agent未能技术上限制工具，App不转接其管理员执行环境；改接经验证的chat_only受限执行器，不改原生产Agent的能力。

未来开放代码执行前，必须验证进程用户/文件系统/网络限制、超时与资源预算、任务间清理及凭据隔离。不得把个人资料目录、所有Cookie、生产源码或全局Token挂给普通成员的执行器。

该模型保护成员之间的访问；有主机管理员权限者仍可能访问服务器未加密存储。本版不声称端到端加密。需限制运维权限并规划加密备份与恢复演练。

## 9. 实施顺序与验收门禁

| 阶段 | 交付 | 放行证据 |
|---|---|---|
| P0 | CLI身份/凭据选择、OAuth回调、Runtime工具限制能力探针 | 两名真实成员的身份分开；跨成员和跨租户访问失败；现有生产Profile/Agent不变 |
| P1 | 受保护网关、持久会话、文本chat_only | 两名成员各自多段历史；跨Mac恢复；失效会话中止；没有管理员工具权限 |
| P2 | 只读授权工具、结果引用、job恢复 | 对应成员凭据正确；缺权限明确待授权；数据有来源与时间 |
| P3 | 关联一期视频、审批后写动作 | 飞书原生确认、幂等、写后回读；不重放未知副作用 |
| P4 | 附件、长期记忆、真实GLB和语音等 | 单独按需要增加，不影响前述隔离与身份 |

必须覆盖：A拿B的conversation/job ID，另一租户同ID，客户端伪造owner/profile、登录地址转发/挑战替换、设备成员绑定不一致、同机切换后遗留旧网页账号，Token过期/撤销，白名单停用，重复提交、同键改正文、断网重连、休眠未知结果、取消在途写入、路径/符号链接逃逸、外部页调用IPC、主机重启后的会话与job恢复。

契约检查只验证文档与OpenAPI自洽。后续实现已增加真实模型HTTP隔离测试及重启持久化测试，但测试身份不冒充真人飞书登录，也不等于OS执行沙箱验收。第一步是P0能力探针，不是先把管理员Agent直接接到多人输入框。

## 10. 外部参考与未验证项

- [飞书官方登录流程](https://open.feishu.cn/solutions/detail/qrcode?lang=zh-CN)：授权回调、可信用户信息、本系统服务端会话。
- [飞书用户标识定义](https://open.feishu.cn/document/ukTMukTMukTM/uMzM1YjLzMTN24yMzUjN/hire-v1/job_requirement/list_by_id)：open_id应用作用域，不按昵称隔离。
- [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)：CSRF/state、公开客户端PKCE、精确回调和凭据传递原则；不据此推断所选飞书端点已满足所有能力。

待确认/实测：后端主机与私有HTTPS域名、Feishu应用及回调配置、认证适配器的CLI契约、PKCE支持、成员级CLI授权路由、chat_only执行器的技术限制、历史保留策略、临时目录和审计保留期。本轮不代做这些权限或部署操作。
