"""CLI protocol for external Agents and explicit human approval."""
import argparse
import json
import sys
from pathlib import Path
from .config import load_config, private_write, state_home
from .errors import BaseError


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def read_json(path):
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def human_confirm(operation, media_review=None):
    if not sys.stdin.isatty():
        raise BaseError("human_tty_confirmation_required")
    print("冻结预览（不会自动修改正文；发布会投递真实作品）：", file=sys.stderr)
    preview = operation["data"].get("payload") or operation["data"].get("plan")
    print(json.dumps(preview, ensure_ascii=False, indent=2), file=sys.stderr)
    if media_review:
        print("先在本机浏览器打开审核包 index.html，按顺序完整查看图片 / 视频和独立封面。导出不是审核通过。", file=sys.stderr)
        print(json.dumps(media_review, ensure_ascii=False, indent=2), file=sys.stderr)
    print("确认这份精确预览后输入 CONFIRM：", file=sys.stderr)
    if input().strip() != "CONFIRM":
        raise BaseError("human_confirmation_cancelled")


def parser():
    p = argparse.ArgumentParser(description="内容型一人公司底座：飞书记事实，蚁小二执行发布。")
    p.add_argument("--config", help="用户自己的私有配置文件")
    p.add_argument("--state", help="私有技术状态目录，须位于源码外")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-config", help="创建源码外的占位配置，不登录或复制凭据")
    sub.add_parser("doctor", help="真实身份/表结构/蚁小二环境只读检查")
    sub.add_parser("sync", help="实时读取绑定飞书表，不建立第二业务账本")
    sub.add_parser("accounts", help="读取真实蚁小二账号")
    choices = sub.add_parser("choices", help="实时列出音乐或分区，选择索引供预检使用")
    choices.add_argument("account")
    choices.add_argument("--kind", choices=("imageText", "video"), required=True)
    choices.add_argument("--field", choices=("music", "category"), required=True)
    choices.add_argument("--query", default="")
    for command in ("draft", "published", "review"):
        child = sub.add_parser(command, help="生成飞书写入计划，尚未正式写入")
        child.add_argument("record_id")
        child.add_argument("--key", required=True)
        if command == "draft":
            child.add_argument("--title", required=True)
            child.add_argument("--expected-source", help="编辑所依据的完整飞书原记录摘要，源改变时停止")
        if command in ("draft", "review"):
            child.add_argument("--body-file", required=True)
        if command == "published":
            child.add_argument("--url", required=True)
        if command == "review":
            child.add_argument("--metrics-file", required=True)
    for command in ("approve", "apply", "verify-write", "publish-submit", "publish-verify"):
        child = sub.add_parser(command)
        child.add_argument("operation_id")
        if command in ("approve", "publish-submit"):
            child.add_argument("--digest", required=True)
    pub = sub.add_parser("publish-prepare", help="真实上传所选素材并validate/dry-run，不正式发布")
    pub.add_argument("record_id")
    pub.add_argument("--account", required=True)
    pub.add_argument("--platform", required=True)
    pub.add_argument("--kind", choices=("imageText", "video"), required=True)
    pub.add_argument("--channel", choices=("cloud", "local"), required=True)
    pub.add_argument("--media", action="append", required=True)
    pub.add_argument("--cover")
    pub.add_argument("--fields", help="平台字段JSON：普通字段与动态选择索引")
    pub.add_argument("--key", required=True)
    packet = sub.add_parser("publish-review", help="导出私有离线素材审核包，不正式发布")
    packet.add_argument("operation_id")
    packet.add_argument("--destination", required=True, help="源码外的新目录，不覆盖已有文件")
    inspection = sub.add_parser("inspect", help="技术操作和事件；输出可能含本人的私有业务材料")
    inspection.add_argument("operation_id", nargs="?")
    server = sub.add_parser("serve", help="只监听127.0.0.1的本人工作台")
    server.add_argument("--port", type=int, default=8787)
    viewer = sub.add_parser("workbench", help="原工作台源码只读查看；无身份、无业务数据、不执行外部动作")
    viewer.add_argument("--port", type=int, default=8787)
    return p


def main(argv=None):
    if sys.version_info < (3, 11):
        emit({"ok": False, "error": {"code": "python_version_unsupported", "required": ">=3.11", "unknown": False}})
        raise SystemExit(2)
    args = parser().parse_args(argv)
    try:
        if args.command == "workbench":
            if not (Path(__file__).resolve().parents[1] / "workbench/web/inspection.html").is_file():
                raise BaseError("workbench_source_checkout_required")
            from .server import make_server
            server, _ = make_server(None, args.port)
            emit({"ok": True, "data": {"url": "http://127.0.0.1:%d" % server.server_port,
                  "mode": "source_inspection", "authenticated": False, "business_enabled": False}})
            try: server.serve_forever()
            except KeyboardInterrupt: pass
            finally: server.server_close()
            return
        if args.command == "init-config":
            path = Path(args.config or state_home() / "config.json").expanduser()
            if path.exists():
                raise BaseError("configuration_already_exists")
            fields = {"title": "标题", "body": "正文", "stage": "阶段", "published_url": "作品链接", "metrics": "复盘指标", "review": "复盘结论"}
            example = {"feishu": {"cli": "lark-cli", "profile": "YOUR_PROFILE", "base_token": "YOUR_BASE_TOKEN", "table_id": "YOUR_TABLE_ID", "fields": fields,
                                   "stages": {"review": "待审核", "approved": "已审核", "submitted": "已投递", "published": "已发布", "reviewed": "已复盘"}}, "yxer": {"cli": "yxer"}}
            if path.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
                raise BaseError("configuration_must_be_outside_source")
            private_write(path, example)
            emit({"ok": True, "data": {"config": str(path), "configured": False}})
            return
        from .service import Service
        service = Service(load_config(args.config), args.state)
        command = args.command
        if command in ("doctor", "sync"):
            result = getattr(service, command)()
        elif command == "accounts":
            result = service.account_snapshot()
        elif command == "choices":
            result = service.query_choices(args.account, args.kind, args.field, args.query)
        elif command == "draft":
            result = service.draft(args.record_id, args.title, Path(args.body_file).read_text(encoding="utf-8"), args.key, args.expected_source)
        elif command == "published":
            result = service.published(args.record_id, args.url, args.key)
        elif command == "review":
            result = service.review(args.record_id, read_json(args.metrics_file), Path(args.body_file).read_text(encoding="utf-8"), args.key)
        elif command == "approve":
            op = service.inspect(args.operation_id)
            if args.digest != op["data"].get("confirmation"):
                raise BaseError("confirmation_changed")
            human_confirm(op)
            result = service.approve(args.operation_id, args.digest, True)
        elif command == "apply":
            result = service.apply(args.operation_id)
        elif command == "verify-write":
            result = service.verify_write(args.operation_id)
        elif command == "publish-prepare":
            media = [(path, "image" if args.kind == "imageText" else "video") for path in args.media]
            if args.cover:
                media.append((args.cover, "cover"))
            result = service.publish_prepare(args.record_id, args.account, args.platform, args.kind, args.channel,
                                             media, read_json(args.fields) if args.fields else {}, args.key)
        elif command == "publish-submit":
            op = service.inspect(args.operation_id)
            if args.digest != op["data"].get("confirmation"):
                raise BaseError("confirmation_changed")
            review = service.export_publish_review(args.operation_id)
            human_confirm(op, review)
            result = service.publish_submit(args.operation_id, args.digest, True, review["media_review_digest"])
        elif command == "publish-review":
            result = service.export_publish_review(args.operation_id, args.destination)
        elif command == "publish-verify":
            result = service.publish_verify(args.operation_id)
        elif command == "inspect":
            result = service.inspect(args.operation_id)
        elif command == "serve":
            from .server import serve
            serve(service, args.port)
            return
        emit({"ok": True, "data": result})
    except BaseError as error:
        from .server import error_value
        emit({"ok": False, "error": error_value(error)})
        raise SystemExit(2)
    except (OSError, ValueError, KeyError):
        emit({"ok": False, "error": {"code": "invalid_local_input", "unknown": False}})
        raise SystemExit(2)
