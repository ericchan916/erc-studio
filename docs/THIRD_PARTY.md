# 第三方与原源码来源

原工作台项目的本人代码经脱敏/配置外置迁入 workbench，未下载或提交原运行数据、凭据和私人资产。迁移审计的原/新文件 SHA 与变更映射保存在开发者私有记录中，不含于公开仓库。

| 资源 | 本轮用途 | 许可 / 来源 |
|---|---|---|
| Tabler CSS / 图标 | 原工作台控件和图标 | MIT；对应 LICENSE/SOURCE 随 vendor 保存 |
| ECharts | 原数据图表 | Apache-2.0；vendor 许可证保留 |
| markdown-it | Markdown 文字渲染 | MIT；原许可证/摘要保存 |
| Hermes 上下文助手 | 原网关上下文处理 | 保留 workbench/backend/vendor/hermes/LICENSE 与原出处 |
| RUM 适配器 | 原官网统计契约 | 本人源代码；自己的配置/密钥不分发 |

前端资源直接复用原项目已安装依赖，运行不访问 CDN。vendor/VENDOR_MANIFEST.json 记录实际字节摘要；安装版本从原包 metadata 核验，Tabler 同名版本 tag 未在官方仓库找到，因此不声称已做该 tag 逐字节对照；MIT 许可从官方仓库另行读取保留。许可证不代表供应商服务或真实账号授权。
