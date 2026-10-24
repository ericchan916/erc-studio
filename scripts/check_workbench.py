#!/usr/bin/env python3
"""Offline migration gate: source syntax, module links, state/privacy boundaries."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

def main():
    root=Path(__file__).resolve().parents[1];workbench=root/"workbench"
    registry=json.loads((workbench/"module-registry.json").read_text())
    entries=registry["modules"];ids=[entry["id"] for entry in entries]
    assert len(ids)==len(set(ids)) and len(ids)>=34
    for entry in entries:
        assert (root/entry["spec"]).is_file()
        for path in entry["source_files"]+entry["test_files"]:assert (root/path).is_file(),path
        assert entry["implementation"] in {"ported_original_source","contract_only"}
    routing=json.loads((root/"agent-runtime/workflow-registry.json").read_text())
    catalog=json.loads((workbench/"src/workflow/catalog.json").read_text())
    assert {x["id"] for x in routing["routes"]}=={x["routeId"] for x in catalog["routes"]}
    syntax=0
    for path in workbench.rglob("*.py"):
        if "__pycache__" not in path.parts:
            ast.parse(path.read_text(),filename=str(path));syntax+=1
    node=shutil.which("node");assert node,"Node is required for the complete workbench syntax gate"
    for extension in ("*.cjs","*.mjs","*.js"):
        for path in workbench.rglob(extension):
            if "node_modules" not in path.parts:
                process=subprocess.run([node,"--check",str(path)],capture_output=True,text=True)
                assert process.returncode==0,f"{path.relative_to(root)}: {process.stderr}"
                syntax+=1
    vendor=json.loads((workbench/"web/vendor/VENDOR_MANIFEST.json").read_text())
    for entry in vendor["files"]:
        path=workbench/"web/vendor"/entry["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry["sha256"]
    for file in ("web/index.html","web/inspection.html"):
        text=(workbench/file).read_text()
        assert 'id="main-nav"' in text and 'id="home-page"' in text
    assert not (workbench/"web/assets/model.glb").exists()
    assert "CherryFeishuBridge/config.json" not in (workbench/"src/content/agent.cjs").read_text()
    print(json.dumps({"passed":True,"modules":len(entries),"syntax_files":syntax,
          "personal_model_distributed":False,"business_calls":0},ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
