#!/usr/bin/env python3
"""Validate this documentation package; never downloads or runs models.

--refresh regenerates the reading copy, test report and SHA256 manifest.
--check verifies sources, schemas, generated copy and manifest without writes.
Requires the project's existing jsonschema development dependency.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "P2-AI-EXECUTION-PLAN.md"
MANIFEST = ROOT / "MANIFEST.sha256"


def package_files():
    return sorted(p for p in ROOT.rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_master():
    parts = ["# P2探索与验证方案（自动生成阅读副本）\n\n"
             "> 执行入口为README.md；分章与config是维护真源。不要直接修改本文件。\n"
             "> 用scripts/validate_package.py --refresh重新生成；已测范围与条件路线状态见README及阶段报告。\n"]
    def rewrite_links(body, source):
        def rewrite_link(match):
            label, target = match.groups()
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                return match.group(0)
            local = os.path.relpath(source.parent / unquote(parsed.path), ROOT)
            return f"[{label}]({local}" + (f"#{parsed.fragment}" if parsed.fragment else "") + ")"
        return re.sub(r"\[([^\]]*)\]\(([^\s)]+)\)", rewrite_link, body)

    for path in sorted((ROOT / "docs").glob("*.md")):
        body = rewrite_links(path.read_text(), path)
        parts.append(f"\n<!-- source: {path.relative_to(ROOT)} -->\n\n" + body.rstrip() + "\n")
    for folder in ("config", "schemas", "prompts"):
        for path in sorted((ROOT / folder).glob("*")):
            if not path.is_file():
                continue
            label = str(path.relative_to(ROOT))
            content = path.read_text().rstrip()
            if path.suffix == ".md":
                content = rewrite_links(content, path)
            parts.append(f"\n## 附录：{label}\n\n" + (
                f"```json\n{content}\n```\n" if path.suffix == ".json" else content + "\n"))
    return "\n".join(parts)


def validate_sources():
    for path in package_files():
        if path.suffix == ".json":
            json.loads(path.read_text())
        elif path.suffix == ".py":
            ast.parse(path.read_text(), filename=str(path))
    registry = json.loads((ROOT / "config/experiment-registry.json").read_text())
    locks = json.loads((ROOT / "config/model-lock.json").read_text())
    experiments = registry["experiments"]
    ids = [row["id"] for row in experiments]
    assert len(ids) == len(set(ids)), "duplicate experiment IDs"
    for key in ("models", "runtimes"):
        keys = [row["id"] for row in locks[key]]
        assert len(keys) == len(set(keys)), f"duplicate {key} IDs"
    graph = {row["id"]: row["depends_on_experiments"] for row in experiments}
    visiting, done = set(), set()

    def visit(node):
        assert node in graph, f"unknown experiment dependency: {node}"
        assert node not in visiting, f"dependency cycle: {node}"
        if node in done:
            return
        visiting.add(node)
        for parent in graph[node]:
            visit(parent)
        visiting.remove(node)
        done.add(node)

    for row in experiments:
        visit(row["id"])
        for key, lock_key in (("model_keys", "models"), ("runtime_keys", "runtimes")):
            assert set(row[key]) <= {x["id"] for x in locks[lock_key]}, row["id"]
        if row["parent_binding"] is not None:
            assert row["parent_binding"] in registry["bindings"], row["id"]
        assert (ROOT / row["gate"]).is_file(), row["id"]
        assert (ROOT / row["recipe"]).is_file(), row["id"]
    assert sum(x["selection"] == "primary" for x in experiments) == 8
    assert registry["policy"]["b4_formal_slots_max"] == 3
    schemas = {}
    for path in (ROOT / "schemas").glob("*.json"):
        schema = json.loads(path.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        schemas[path.stem] = jsonschema.Draft202012Validator(schema)
    for stem, schema_name in (("score", "score-report.schema"), ("timing", "timing-report.schema")):
        schemas[schema_name].validate(json.loads((ROOT / f"examples/synthetic-{stem}.json").read_text()))
    for line in (ROOT / "examples/synthetic-events.jsonl").read_text().splitlines():
        schemas["event.schema"].validate(json.loads(line))
    links = 0
    for path in (p for p in package_files() if p.suffix == ".md"):
        text = path.read_text()
        assert sum(line.startswith("```") for line in text.splitlines()) % 2 == 0, path
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", text):
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            assert (path.parent / unquote(parsed.path)).exists(), f"{path}: {target}"
            links += 1
    return {"experiments": len(experiments), "primary_experiments": 8,
            "models": len(locks["models"]), "runtimes": len(locks["runtimes"]),
            "schemas": len(schemas), "local_links": links}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render_master()
    if args.refresh:
        MASTER.write_text(expected)
    else:
        assert MASTER.read_text() == expected, "generated master is stale"
    counts = validate_sources()
    if args.check:
        recorded = {}
        for line in MANIFEST.read_text().splitlines():
            sha, name = line.split("  ", 1)
            assert name not in recorded, f"duplicate manifest path: {name}"
            recorded[name] = sha
        actual = {str(p.relative_to(ROOT)): digest(p) for p in package_files() if p != MANIFEST}
        assert actual == recorded, "manifest hash/file list mismatch; refresh after final edits"
        print(json.dumps({"status": "passed", "counts": counts,
                          "scope": "package/schema/hash checks; no model or release validation"}))
        return
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-q"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    log = result.stdout + result.stderr
    (ROOT / "BUILD-TESTS.log").write_text(log)
    assert result.returncode == 0, log
    matched = re.search(r"Ran (\d+) tests?", log)
    assert matched, "unittest count missing"
    examples = {}
    for command, filename in (("gate", "synthetic-score.json"), ("timing", "synthetic-timing.json"),
                              ("events", "synthetic-events.jsonl")):
        call = subprocess.run([sys.executable, "-B", "scripts/evidence_tools.py", command,
                               f"examples/{filename}"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert call.returncode == 0, call.stdout + call.stderr
        examples[command] = json.loads(call.stdout)
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "scope": "local documentation/helper validation; no target-model benchmarks",
              "environment": {"system": platform.system(), "machine": platform.machine(),
                              "python": platform.python_version()},
              "unit_tests": {"run": int(matched[1]), "passed": int(matched[1]),
                             "exit_code": 0, "log": "BUILD-TESTS.log"},
              "counts": counts, "checks": {"json_parse": True, "python_syntax": True,
              "registry_references": True, "dependency_dag": True, "local_links": True,
              "markdown_fences": True, "schema_metaschemas_and_examples": True,
              "generated_master": True, "target_models_run": False,
              "production_code_modified": False, "zip_crc": "not_run: no ZIP created"},
              "examples": examples}
    (ROOT / "BUILD-VALIDATION.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    MANIFEST.write_text("".join(f"{digest(p)}  {p.relative_to(ROOT)}\n"
                               for p in package_files() if p != MANIFEST))
    print(json.dumps({"status": "passed", "unit_tests": report["unit_tests"], "counts": counts}))


if __name__ == "__main__":
    main()
