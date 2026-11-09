"""Freeze local B0 assets. No downloads, holdout access or inference."""
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/live-video-translate-p2-2026-11-07"
HISTORY = ROOT / "p0/evidence/n0-next"


def digest(path):
    with Path(path).open("rb") as source:
        return sha256(source.read()).hexdigest() if Path(path).stat().st_size < 2**20 else _stream_digest(source)


def _stream_digest(source):
    result = sha256()
    while block := source.read(2**20):
        result.update(block)
    return result.hexdigest()


def dump(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".writing")
    with temporary.open("x") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")
    temporary.replace(path)


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def validate_lock(lock):
    for model in lock["models"]:
        if model["source"] == "release":
            if not re.fullmatch(r"[0-9a-f]{64}", model.get("archive_sha256") or ""):
                raise ValueError(f"unresolved release content: {model['id']}")
        elif model["source"] != "local" and not re.fullmatch(r"[0-9a-f]{40}", model.get("revision") or ""):
            raise ValueError(f"unresolved revision: {model['id']}")
        if not model["files"]:
            raise ValueError(f"unresolved files: {model['id']}")
        for file in model["files"]:
            if not re.fullmatch(r"[0-9a-f]{64}", file.get("sha256") or ""):
                raise ValueError(f"unresolved content hash: {model['id']}")
            if digest(file["path"]) != file["sha256"]:
                raise ValueError(f"content changed: {model['id']}")


def frozen_fingerprint(run):
    manifest = json.loads((run / "run-manifest.json").read_text())
    for field in ("source_sha256", "input_sha256"):
        for name, expected in manifest[field].items():
            if digest(name) != expected:
                raise ValueError(f"frozen input/source drift: {name}")
    for name, expected in manifest["artifact_sha256"].items():
        if digest(run / name) != expected:
            raise ValueError(f"frozen artifact drift: {name}")
    validate_lock(json.loads((run / "locks/models.json").read_text()))
    runtime = json.loads((run / "locks/runtime.json").read_text())
    if any(version(name) != expected for name, expected in runtime["packages"].items()):
        raise ValueError("runtime package drift")
    state_path = run / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        for step in state["steps"].values():
            if step["status"] == "passed":
                for name, expected in step["outputs"].items():
                    if digest(run / name) != expected:
                        raise ValueError(f"completed output drift: {name}")
    return manifest


def freeze(run, registry):
    from subtitle_service.config import load_settings
    from subtitle_service.local_pipeline import MODEL_FILES, VAD_SHA
    if (run / "run-manifest.json").exists():
        return frozen_fingerprint(run)
    run.mkdir(parents=True, exist_ok=True)
    for folder in ("locks", "inputs", "baseline", "reviews", "comparisons"):
        (run / folder).mkdir(exist_ok=True)
    original = json.loads((HISTORY / "frozen.json").read_text())
    inputs = {}
    assets = []
    for item in original["inputs"]:
        path = ROOT / "p0/fixtures" / item["file"]
        actual = digest(path)
        if actual != item["sha256"] or path.stat().st_size % 2:
            raise ValueError(f"PCM asset mismatch: {item['id']}")
        inputs[str(path)] = actual
        assets.append(item | dict(path=str(path), samples=path.stat().st_size // 2))
    if len(assets) != 9:
        raise ValueError("expected nine frozen PCM assets")
    copies = {"slices.jsonl": HISTORY / "slices.jsonl", "reazon.jsonl": HISTORY / "reazon.jsonl",
              "units.jsonl": HISTORY / "final-references/units.jsonl",
              "verdict.json": ROOT / "p0/evidence/n2-mt-greedy/mt-semantic-result.json",
              "pending-listen.json": ROOT / "p0/evidence/n2-mt-greedy/pending-listen.json",
              "historical-timing.json": ROOT / "p0/evidence/n2-mt-greedy/timing-rescore.json"}
    copies.update({f"mt-seed{seed}.jsonl": HISTORY / f"mt-reazon-m0-{seed}.jsonl" for seed in (11, 29)})
    for name, path in copies.items():
        inputs[str(path)] = digest(path)
        shutil.copyfile(path, run / "inputs" / name)
    for path in sorted((run / "diagnostic-dist").glob("*")):
        if path.is_file():
            inputs[str(path)] = digest(path)
    video = run / "inputs/browser-csj-task.mp4"
    if not video.is_file() or not (run / "diagnostic-dist/manifest.json").is_file():
        raise ValueError("prepare the local video and diagnostic build before freezing")
    inputs[str(video)] = digest(video)
    cuts = rows(run / "inputs/slices.jsonl")
    units = rows(run / "inputs/units.jsonl")
    if len(cuts) != 88 or len(units) != 34 or any(len(rows(run / f"inputs/mt-seed{s}.jsonl")) != 62 for s in (11, 29)):
        raise ValueError("frozen development denominators changed")
    profile_path = ROOT / "engine/config/profiles.toml"
    settings = load_settings(profile_path)
    profile = settings.profiles["reazon-hy"]
    if (profile.asr.threads, profile.asr.vad_pause_ms) != (2, 600):
        raise ValueError("B0 ASR profile differs from frozen control")
    cache = Path(settings.cache_root)
    if (profile.asr.model_revision, profile.mt.model_revision) != (original["reazon_revision"], original["hy_revision"]):
        raise ValueError("B0 profile revision drift")
    models = []
    for key, repository, revision, names in (
            ("reazon-b0", "reazon-research/reazonspeech-k2-v2", profile.asr.model_revision, MODEL_FILES),
            ("hymt-b0", "mlx-community/Hy-MT2-1.8B-4bit", profile.mt.model_revision, None)):
        snapshot = cache / "hub" / ("models--" + repository.replace("/", "--")) / "snapshots" / revision
        files = [snapshot / name for name in names] if names else sorted(p for p in snapshot.iterdir() if p.is_file())
        models.append(dict(id=key, source="hf", repository=repository, revision=revision,
                           local_path=str(snapshot), files=[dict(path=str(p), sha256=digest(p)) for p in files]))
    vad = cache / "reazonspeech-assets/silero_vad.onnx"
    if digest(vad) != VAD_SHA:
        raise ValueError("Silero v4 content drift")
    models.append(dict(id="silero-v4-b0", source="local", revision=None, local_path=str(vad),
                       files=[dict(path=str(vad), sha256=VAD_SHA)]))
    dump(run / "locks/models.json", dict(models=models))
    packages = {name: version(name) for name in (
        "sherpa-onnx", "numpy", "mlx", "mlx-lm", "whisperlivekit", "transformers", "jsonschema")}
    dump(run / "locks/runtime.json", dict(python=platform.python_version(), packages=packages))
    shutil.copyfile(registry, run / "locks/registry.json")
    shutil.copyfile(profile_path, run / "locks/profiles.toml")
    dump(run / "locks/B0-hypothesis.json", dict(experiment_id="B0", role="control_reproduction_not_candidate",
         variable="none: current Reazon CPU2 + Silero v4 native packet VAD + Hy-MT2 M0",
         seeds=[11,29], expected_offline_hits=[20,18], reference_status="ai_reviewed",
         mode_order=["fixed-cuts","fixed-text","realtime-1x seed11","realtime-1x seed29", "browser play/asr/mt/full"],
         browser_input="csj-task H264/AAC, 640x360@15fps; captured PCM is a separate input condition",
         timing_mapping="unit-map.json frozen before outputs; retain original and 126.04s sensitivity",
         stop=dict(process_tree_footprint_gib=12, command_budget_s=3600),
         human_listening="not_started", holdout="sealed", production_default="unchanged"))
    dump(run / "asset-manifest.json", dict(assets=assets, continuous_pcm=9, fixed_cuts=88,
                                          mt_inputs_per_seed=62, input_audio_s=sum(a["samples"] for a in assets) / 16000))
    verdict = json.loads((run / "inputs/verdict.json").read_text())
    usable = {str(s): verdict["outcomes"][f"m0-{s}"]["usable"] for s in (11, 29)}
    if [len(usable[str(s)]) for s in (11, 29)] != [20, 18]:
        raise ValueError("historical B0 text adjudication differs from 20/18")
    dump(run / "baseline-score.json", dict(denominator=34, usable=usable, counts={s: len(v) for s, v in usable.items()},
                                           reference_status="ai_reviewed", inference_reproduced=False))
    shutil.copyfile(run / "inputs/pending-listen.json", run / "pending-listen.json")
    dump(run / "reference-manifest.json", dict(units=units, denominator=34, reference_status="ai_reviewed",
         pending_listen=15, human_verified=False, timing_sensitivity={"wmf-04/153-155": [119.60, 126.04]},
         protected_unit_ids=sorted(set(usable["11"]) | set(usable["29"])),
         protected_facts="Existing necessary facts in units.jsonl; not automatic semantic judgments"))
    source_paths = [*sorted((ROOT / "p0/p2").rglob("*.py")), *sorted((ROOT / "engine/src").rglob("*.py")),
                    *sorted((ROOT / "extension/src").glob("*.ts")), ROOT / "engine/uv.lock", ROOT / "engine/pyproject.toml",
                    ROOT / "p0/monitor.py", ROOT / "p0/next_asr.py", ROOT / "p0/next_mt.py", ROOT / "p0/review.py",
                    ROOT / "p0/exploration.py", *sorted(PLAN.rglob("*.md")),
                    *sorted((PLAN / "config").glob("*.json")), *sorted((PLAN / "schemas").glob("*.json")),
                    *sorted((PLAN / "scripts").glob("*.py")), ROOT / "extension/build.mjs",
                    *sorted((ROOT / "p0/p2").glob("*.mjs"))]
    source_hashes = {str(p): digest(p) for p in source_paths}
    artifacts = {str(p.relative_to(run)): digest(p) for folder in ("inputs", "locks") for p in (run / folder).glob("*")}
    artifact_names = ("asset-manifest.json", "baseline-score.json", "pending-listen.json", "reference-manifest.json")
    artifacts.update({name: digest(run / name) for name in artifact_names})
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff = subprocess.check_output(["git", "diff", "HEAD", "--", ".", ":(exclude)test"], cwd=ROOT)
    hardware = dict(chip=subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip(),
                    memory_bytes=int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)),
                    gpu_cores=next((row.get("gpu-core-count") for row in plistlib.loads(subprocess.check_output(
                        ["/usr/sbin/ioreg", "-a", "-r", "-d", "1", "-c", "AGXAccelerator"])) if row.get("gpu-core-count")), None),
                    os_version=platform.mac_ver()[0], power_mode="recorded_in_hardware.txt",
                    background_load="recorded_per_resource_run")
    fingerprint_data = dict(source=source_hashes, inputs=inputs, artifacts=artifacts, packages=packages, hardware=hardware)
    fingerprint = sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    manifest = dict(schema_version=1, run_id=run.name, created_at=datetime.now(timezone.utc).isoformat(),
                    evidence_kind="measured", repository=dict(url="https://github.com/lkyprogramer/live-video-tanslate",
                    head=head, worktree_diff_sha256=sha256(diff).hexdigest()), hardware=hardware,
                    locks={k + "_sha256": digest(run / "locks" / v) for k, v in
                           (("models", "models.json"), ("runtime", "runtime.json"), ("registry", "registry.json"))},
                    datasets=[dict(id="D0", manifest_sha256=digest(run / "asset-manifest.json"), unit_count=34)],
                    protocol=dict(sample_rate=16000, channels=1, encoding="s16le", packet_samples=3200,
                                  clock="normalized_host_monotonic_ns", reference_version=digest(run / "reference-manifest.json")),
                    fingerprint=fingerprint, source_sha256=source_hashes, input_sha256=inputs, artifact_sha256=artifacts,
                    holdout_accessed=False, human_listening="deferred", production_default_changed=False)
    import jsonschema
    jsonschema.validate(manifest, json.loads((PLAN / "schemas/run-manifest.schema.json").read_text()))
    dump(run / "run-manifest.json", manifest)
    return manifest
