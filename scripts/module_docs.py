#!/usr/bin/env python3
"""Read the selected public module contract. No network, business state or tools."""
import argparse
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module",nargs="?")
    parser.add_argument("--list",action="store_true")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    registry=json.loads((root/"workbench/module-registry.json").read_text())
    if args.list:
        print(json.dumps(registry,ensure_ascii=False,indent=2));return 0
    matches=[entry for entry in registry["modules"] if entry["id"]==args.module]
    if len(matches)!=1:
        print(json.dumps({"ok":False,"error":"unknown_module","hint":"--list"}));return 2
    spec=(root/matches[0]["spec"]).resolve()
    if not spec.is_relative_to(root/"docs") or not spec.is_file():
        print(json.dumps({"ok":False,"error":"invalid_module_document"}));return 2
    print(spec.read_text());return 0

if __name__=="__main__":raise SystemExit(main())
