# 本机 RUM 适配器

源自用户自己的官网项目 server/modules（非第三方下载），仅在本工作台修复和打包；原官网文件与私有配置保持原样。来源项目 package.json 声明 private，未引入外部源码许可或新 npm 依赖。

原始 SHA-256：

- tencentRumClient.mjs fb18176f037df2f5708b5373105fa756578e99c0e3436f2493f88274c7d7c5ec
- tencentRumAnalyticsProvider.mjs 13e9603280c778453455f9b9042939ac355a6997181d9c912cc01042e7692a2e

TC3 签名复用原 tencentClsClient.mjs 的签名函数及其校验辅助（原文件前219行）；本工作台只调用 RUM，不实例化其他采集客户端。密钥仅由主进程读取既有本机配置，不随代码或构建包分发。
