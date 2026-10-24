# 测试分层

## 离线门禁

```bash
python3 -B -m unittest discover -s tests -v
python3 -B scripts/check_workbench.py
python3 -B scripts/privacy_audit.py --root .
```

检查原工作台源码语法、模块索引/链接、许可证摘要、无个人模型与错误自动凭据读取。Node 为完整语法门禁必需，不用跳过项冒充全测。

## 原模块回归

原测试源码一并迁移，用于定位各模块正向与拒绝/隔离/未知分支。测试包含历史安装器/私人 GLB/旧渲染器覆盖，这些不是新公开版的默认运行前提；需要对新配置与移除个人资产后的契约调整。实际运行结果和不适用项须如实记录，不宣称移入 tests 等于全部已通过。

```bash
CONTENTBASE_LARK_PROFILE=cli_exampleWorkspace node --test workbench/tests/*.test.cjs
PYTHONPATH=workbench/backend:workbench/shared/bridge:workbench/shared/bridge/tests python3 -B -m unittest discover -s workbench/backend/tests -v
```

以上只能在无供应商凭据、合成输入的隔离环境执行；测试脚本若包含默认真实执行入口先审查再运行。不要在日常个人 HOME 里运行未核对的原部署/登录/发布脚本。

## UI 与真实资源

源码查看：原导航、布局、未登录标识、无业务记录、禁用写入、点击无供应商调用、资源全部本地加载；截图留源码外。

Native / 网关：本人扫码/权限/历史重启/跨成员拒绝/准确源记录/真实模型/写表回读/素材上传/人工审片/正式投递/原任务查询/平台公开/表内登记逐层记录。没有必要输入或本人确认的项目继续待验收。

## 发布与回滚

精确白名单→私人标识扫描→干净源目录/包→冷启动→远端逐文件读回→同份源码重验；diff、字面命令/输入/退出码/输出与回滚放私有位置。回滚源码不抹掉 state 和外部业务结果。
