#!/usr/bin/env python3
"""Rebuild/check P3 documentation only; no model imports, downloads or inference."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MASTER = "P3-AI-EXECUTION-PLAN.md"
CONFIGS = ("experiment-registry.json", "model-lock.json",
           "runtime-artifact-lock.json", "evaluation-contract.json")


def files(root):
    return sorted(p for p in root.rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relocate_links(body, source, root):
    def replace(match):
        label, target = match.groups()
        parsed = urlsplit(target)
        if parsed.scheme or target.startswith("#"):
            return match.group(0)
        path = os.path.relpath(source.parent / unquote(parsed.path), root)
        return f"[{label}]({path}" + (f"#{parsed.fragment}" if parsed.fragment else "") + ")"
    return re.sub(r"\[([^\]]*)\]\(([^\s)]+)\)", replace, body)


def render_master(root=ROOT):
    docs = sorted((root / "docs").glob("*.md"))
    parts = [
        "# M1 Pro 日语实时中文字幕：P3 完整 AI 执行方案\n\n"
        "版本：2026-11-10（北京时间）；项目基点77e24c3e490a11721041a40ad4bd203f51f84039。\n\n"
        "自动生成阅读副本：分章、config和prompts是维护真源。修改后运行"
        "scripts/validate_package.py --refresh；不要直接修改本文件。"
        "本包没有新M1模型实测或已实现的P3 runner，入口见[README](README.md)。\n\n"
        "## 文档目录\n\n" +
        "\n".join(f"- [{p.name}](docs/{p.name})" for p in docs)
    ]
    for path in docs:
        name = str(path.relative_to(root))
        body = relocate_links(path.read_text(), path, root).rstrip()
        parts.append(f"---\n\n<!-- BEGIN {name} -->\n\n{body}\n\n<!-- END {name} -->")
    for name in CONFIGS:
        path = root / "config" / name
        parts.append(f"---\n\n## config/{name}\n\n```json\n{path.read_text().rstrip()}\n```")
    for name in ("EXECUTOR.md", "REVIEWER.md", "RESUME.md"):
        path = root / "prompts" / name
        relative = str(path.relative_to(root))
        body = relocate_links(path.read_text(), path, root).rstrip()
        parts.append(f"---\n\n<!-- BEGIN {relative} -->\n\n{body}\n\n<!-- END {relative} -->")
    return "\n\n".join(parts) + "\n"


def validate_sources():
    import jsonschema

    all_files = files(ROOT)
    for path in all_files:
        if path.suffix == ".json":
            json.loads(path.read_text())
        elif path.suffix == ".py":
            ast.parse(path.read_text(), filename=str(path))
    registry = json.loads((ROOT / "config/experiment-registry.json").read_text())
    contract = json.loads((ROOT / "config/evaluation-contract.json").read_text())
    models = json.loads((ROOT / "config/model-lock.json").read_text())
    budget = registry["budgets"]
    assert budget["primary_candidate_limit"] == 3
    assert budget["conditional_configuration_limit"] == 1
    assert budget["total_formal_candidate_configuration_limit"] == 4
    assert budget["e2e_configuration_limit"] == 2
    model_ids = {item["id"] for item in models["entries"]}
    assert len(model_ids) == len(models["entries"])
    choices = registry["primary"] + registry["secondary"]["choices"]
    assert all(set(item["model_ids"]) <= model_ids for item in choices)
    r2t2 = registry["primary"][0]
    assert r2t2["language"] == "Japanese" and r2t2["first_chunk_ms"] == 320
    assert r2t2["commit_subpolicy"] == "S0_UNTIMED_VAD_SNAPSHOT"
    assert r2t2["step_token_budget"]["first"] == 4 and r2t2["eof_max_new_tokens"] == 4
    assert contract["research_R_memory"]["main_metric"] == "tree_physical_footprint_peak_bytes"
    assert contract["resources"]["default_hot_tree_footprint_peak_bytes_max"] == 2684354560
    assert contract["readability"]["continuous_visible_min_ms"] == 600
    assert contract["readability"]["characters_per_second"] == 12
    validators = {}
    for path in (ROOT / "schemas").glob("*.json"):
        schema = json.loads(path.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        validators[path.name] = jsonschema.Draft202012Validator(schema)
    instances = {
        "model-lock.schema.json": ["config/model-lock.json", "examples/synthetic-lock-pending.json"],
        "timing-input.schema.json": ["examples/synthetic-timing.json"],
        "prefix-audit-input.schema.json": [
            "examples/synthetic-prefix-valid.json", "examples/synthetic-prefix-invalid.json"],
        "run-manifest.schema.json": ["templates/run-manifest.json"],
        "decision.schema.json": ["templates/decision.json"],
    }
    instance_count = 0
    for name, paths in instances.items():
        for name_in_package in paths:
            validators[name].validate(json.loads((ROOT / name_in_package).read_text()))
            instance_count += 1
    links = 0
    for path in (p for p in all_files if p.suffix == ".md"):
        body = path.read_text()
        assert sum(line.startswith("```") for line in body.splitlines()) % 2 == 0, path
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", body):
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            assert (path.parent / unquote(parsed.path)).exists(), f"{path}: {target}"
            links += 1
    return dict(schemas=len(validators),
                schema_instances=instance_count, local_links=links)


def manifest_text():
    return "".join(f"{digest(p)}  {p.relative_to(ROOT)}\n"
                   for p in files(ROOT) if p != ROOT / "MANIFEST.sha256")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    master = ROOT / MASTER
    rendered = render_master()
    if args.refresh:
        master.write_text(rendered)
    else:
        assert master.read_text() == rendered, "generated master is stale"
    if args.check:
        counts = validate_sources()
        assert (ROOT / "MANIFEST.sha256").read_text() == manifest_text(), "manifest bytes/file list changed"
        assert json.loads((ROOT / "validation/BUILD-VALIDATION.json").read_text())["status"] == "PASS"
        print(json.dumps(dict(status="PASS", scope="documentation only; no inference", counts=counts)))
        return
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                            cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    count = re.search(r"Ran (\d+) tests?", log)
    assert count and int(count[1]) > 0, "no tests collected"
    commands = [
        ("timing", "examples/synthetic-timing.json", 0),
        ("prefix-audit", "examples/synthetic-prefix-valid.json", 0),
        ("prefix-audit", "examples/synthetic-prefix-invalid.json", 1),
        ("check-lock", "examples/synthetic-lock-pending.json", 2),
        ("check-lock", "config/model-lock.json", 2),
    ]
    examples = []
    for command, name, expected in commands:
        call = subprocess.run([sys.executable, "-B", "scripts/p3_evidence.py", command, name],
                              cwd=ROOT, capture_output=True, text=True)
        assert call.returncode == expected, call.stdout + call.stderr
        value = json.loads(call.stdout)
        assert value["release_eligible"] is False
        examples.append(dict(command=command, path=name, expected_exit=expected,
                             observed_exit=call.returncode))
    report = dict(status="PENDING_DOCUMENT_CHECKS", created_at=datetime.now(timezone.utc).isoformat(),
                  scope="local P3 document/helper validation; no model or product test",
                  python=sys.version.split()[0], tests=dict(run=int(count[1]), passed=int(count[1])),
                  examples=examples, target_models_run=False,
                  production_code_changed=False, release_eligible=False)
    validation = ROOT / "validation"
    (validation / "LOCAL-OFFLINE-TESTS.txt").write_text(log)
    (validation / "BUILD-VALIDATION.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report["counts"] = validate_sources()
    report["status"] = "PASS"
    (validation / "BUILD-VALIDATION.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "MANIFEST.sha256").write_text(manifest_text())
    print(json.dumps(report))


if __name__ == "__main__":
    main()
