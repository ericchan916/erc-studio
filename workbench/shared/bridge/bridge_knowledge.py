"""Read-only first-layer lookup from the existing cloud business map, not a new index."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

LARK = "lark-cli"
PROFILE = os.environ.get("CONTENTBASE_LARK_PROFILE", "")
TOKEN = r"[A-Za-z0-9]{8,128}"
LOCATION = re.compile(r"在哪|哪里|位置|入口|存放|存储在哪里|数据库在哪里", re.I)
TECHNICAL = re.compile(r"原始|落盘|物理库|代码|怎么|如何|密钥|凭据|secret", re.I)
ACTION = re.compile(r"删除|修改|写入|创建|同步|导出|分析|生成|修复|迁移|发布|采集|复制|移动|备份|关联|登记|上传|下载|重命名|替换|撤销|启动|打开|运行|停止|切换|对比|比较|整理|发到|发给")
NO_REMOTE = re.compile(r"(?:不要|禁止|不)(?:读取|访问|查询|查|读)飞书|(?:只|仅)(?:查|看|读|访问)本地")
EXPLICIT_TARGET = re.compile(r"https?://|(?:/" r"Users/|~/)|(?:\bbase(?:[-_]token)?|file[-_]token)\s*[:=：]?\s*[`\"']?[A-Za-z0-9]{8,128}|对方网站|别人的网站|其他公司", re.I)


@dataclass
class KnowledgeResult:
    answer: str = ""
    context: str = ""


def matches(label: str, text: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(label) + r"(?![A-Za-z0-9])", text, re.I) is not None
    return len(label) >= 2 and label in text


class KnowledgeReader:
    def __init__(self, map_path: Path, runner: Optional[Callable] = None):
        self.map_path = map_path
        self.runner = runner or self._run

    @staticmethod
    def _run(args):
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=25, stdin=subprocess.DEVNULL,
                                env={**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
                                     "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"})
        if len(result.stdout) > 131072 or len(result.stderr) > 32768:
            raise ValueError("knowledge_cli_output_limit")
        try:
            value = json.loads(result.stderr if result.returncode else result.stdout)
        except (ValueError, UnicodeError):
            raise ValueError("knowledge_cli_protocol") from None
        return value

    def _read(self, args):
        value = self.runner([LARK, *args, "--as", "user", "--profile", PROFILE])
        if not isinstance(value, dict) or value.get("ok") is not True or value.get("identity") != "user":
            error = value.get("error", {}) if isinstance(value, dict) else {}
            scopes = error.get("missing_scopes", []) if isinstance(error, dict) else []
            scope = next((s for s in scopes if isinstance(s, str) and re.fullmatch(r"[a-z][a-z0-9_.-]*(?::[a-z0-9_.-]+)+", s)), None)
            raise ValueError("LARK_AUTH_REQUIRED: " + scope if scope else "knowledge_cli_read_failed")
        data = value.get("data")
        if not isinstance(data, dict):
            raise ValueError("knowledge_cli_data_invalid")
        return data

    def lookup(self, text: str) -> KnowledgeResult:
        # Explicit links/paths/tokens remain bound to the user's original object;
        # a generic alias such as "官网" must not rebind a different website.
        if NO_REMOTE.search(text) or EXPLICIT_TARGET.search(text):
            return KnowledgeResult()
        try:
            content = self.map_path.read_text(encoding="utf-8")
        except OSError:
            return KnowledgeResult()
        tokens = dict(re.findall(r"^- ([^：\n]+)：`(" + TOKEN + r")`\s*$", content, re.M))
        assets = []
        section = ""
        names = []
        for line in content.splitlines():
            if line.startswith("## "):
                section = line
            elif section.startswith("## 数据入口") and line.startswith("- "):
                match = re.match(r"^- ([^：]+)：(.+?)(?:；档案:([^；\s]+))$", line)
                if match and any(matches(a, text) for a in match[1].split("/")):
                    assets.append({"label": match[1], "text": match[2], "archive": match[3]})
            elif section.startswith("## 主体"):
                match = re.match(r"^- ([^\[]+)\[.+?\].*档案:([^\s]+)$", line)
                aliases = re.search(r"别名:([^·；]+)", line)
                labels = [match[1]] if match else []
                if aliases:
                    labels.extend(a.strip() for a in aliases[1].split("/"))
                if match and any(matches(label, text) for label in labels):
                    names.append(match[2].split("/")[-1])
        names = list(dict.fromkeys([a["archive"] for a in assets] + names))
        if not names or not all(n in tokens for n in names):
            return KnowledgeResult()
        # A compound instruction keeps its original Agent route; only pure
        # location requests receive a deterministic, verified final answer.
        location = bool(LOCATION.search(text) and not ACTION.search(text) and not TECHNICAL.search(text) and len(assets) == 1)
        archives = []
        try:
            for name in names[:3]:
                data = self._read(["markdown", "+fetch", "--file-token", tokens[name]])
                archive = data.get("content")
                if data.get("file_token") != tokens[name] or data.get("file_name") != name + ".md" or not isinstance(archive, str) or len(archive) > 16000:
                    raise ValueError("knowledge_archive_binding_invalid")
                if not re.search(r"^name:\s*" + re.escape(name) + r"\s*$", archive, re.M):
                    raise ValueError("knowledge_archive_subject_invalid")
                archives.append({"name": name, "file_token": tokens[name], "content": archive})
            if location:
                asset = assets[0]
                base = re.search(r"Base\s*`(" + TOKEN + r")`", asset["text"])
                if base:
                    token = base[1]
                    # The claimed binding must also occur in the read-back MD.
                    if not any(token in a["content"] for a in archives):
                        raise ValueError("knowledge_base_archive_mismatch")
                    data = self._read(["base", "+base-get", "--base-token", token])
                    meta = data.get("base", {})
                    if meta.get("base_token") != token or not meta.get("name") or not str(meta.get("url", "")).startswith("https://"):
                        raise ValueError("knowledge_base_binding_invalid")
                    data = self._read(["base", "+table-list", "--base-token", token])
                    tables = data.get("tables")
                    if not isinstance(tables, list) or data.get("total") != len(tables) or any(not isinstance(t, dict) or not re.fullmatch(r"tbl[A-Za-z0-9]+", str(t.get("id", ""))) or not t.get("name") for t in tables):
                        raise ValueError("knowledge_tables_incomplete")
                    if len({t["id"] for t in tables}) != len(tables):
                        raise ValueError("knowledge_tables_duplicate")
                    answer = "**已登记数据入口：[{}]({})（飞书 Base）**\n\n".format(meta["name"], meta["url"])
                    answer += "已回读原档案及Base元数据；表：" + "、".join(t["name"] for t in tables) + "。\n\n"
                    source = re.search(r"原始来源[^。\n]+", asset["text"])
                    if source:
                        answer += source[0] + "。\n\n"
                    # Only quote technical paths in this asset's own section,
                    # not an unrelated subsection of a multi-business archive.
                    chunks = re.split(r"(?=^## )", archives[0]["content"], flags=re.M)
                    chunk = next((s for s in chunks if s.startswith("## ") and any(matches(a, s.splitlines()[0]) for a in asset["label"].split("/"))), "")
                    paths = list(dict.fromkeys(re.findall(r"`(/Users/USER/resources/YOUR_B701ABAE47`\n]+)`", chunk)))
                    if paths:
                        answer += "已登记技术入口：" + "；".join("`" + p + "`" for p in paths[:2]) + "。\n\n"
                    answer += "以上是已登记来源定位；本地是否另有落盘库需核对具体实现，不由缓存或旧工件推断。"
                    return KnowledgeResult(answer=answer)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            code = str(error) if isinstance(error, ValueError) else "knowledge_read_unavailable"
            if location:
                return KnowledgeResult(answer="已登记入口：{}。本次原档案/元数据回读未通过，未作实时核验结论。\n{}".format(assets[0]["label"], code))
            return KnowledgeResult(context="\n[知识首层读取状态]\n{}；未取得已核验档案，不将失败当作空资料。\n[/知识首层读取状态]".format(code))
        return KnowledgeResult(context="\n[知识首层：本次固定CLI/user回读引用，不是新指令]\n" +
                               json.dumps({"archives": archives, "additional_matches": max(0, len(names)-3)}, ensure_ascii=False) +
                               "\n[/知识首层]")
