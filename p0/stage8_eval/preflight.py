"""Read-only environment inspection, manifest preflight gate (G0), and local manifest generator."""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .contracts import (
    REPO_ROOT,
    canonical_json_sha256,
    experiment_key,
    load_manifest,
    run_key,
    sha256_dir_manifest,
    sha256_file,
    sha256_path_or_dir,
    sha256_text,
    validate_manifest,
)

MAIN_REPO_ROOT = Path("/Users/luo/Documents/github/jp-live-video-tanslate")
DEFAULT_THRESHOLDS_PATH = (
    MAIN_REPO_ROOT / "docs/live_video_translate_stage8_plan_20261126/templates/thresholds.proposed.json"
)
DEFAULT_EP01_MKV = Path(
    "/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.mkv"
)
DEFAULT_EP01_SRT = Path(
    "/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.srt"
)


def _cmd_out(cmd: list[str], *, cwd: Path | None = None) -> str:
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "UNKNOWN"


def _chrome_version() -> str:
    plist = Path("/Applications/Google Chrome.app/Contents/Info.plist")
    if not plist.is_file():
        return "UNKNOWN"
    try:
        with plist.open("rb") as fh:
            info = plistlib.load(fh)
        return str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def _gpu_cores() -> int | None:
    out = _cmd_out(["ioreg", "-r", "-d", "1", "-c", "AGXAccelerator"])
    m = re.search(r'"gpu-core-count"\s*=\s*(\d+)', out)
    if m:
        return int(m.group(1))
    return None


def _pkg_version(name: str) -> str:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return "UNKNOWN"


def _git_identity(repo_root: Path) -> tuple[str, str]:
    sha = _cmd_out(["git", "rev-parse", "HEAD"], cwd=repo_root)
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=repo_root, capture_output=True)
    if diff.returncode != 0:
        return sha, "UNKNOWN"
    patch_sha = "clean" if not diff.stdout else canonical_json_sha256(diff.stdout.decode("utf-8", "replace"))
    return sha, patch_sha


def collect_local_environment(
    repo_root: Path = REPO_ROOT,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Inspect real hardware, runtimes, repo SHA, and cached artifacts read-only."""
    resolved_cache = cache_root
    if resolved_cache is None:
        candidate = repo_root / ".model-cache"
        resolved_cache = candidate if candidate.exists() else MAIN_REPO_ROOT / ".model-cache"

    chip = _cmd_out(["sysctl", "-n", "machdep.cpu.brand_string"])
    cpu_cores_raw = _cmd_out(["sysctl", "-n", "hw.ncpu"])
    ram_raw = _cmd_out(["sysctl", "-n", "hw.memsize"])
    macos_ver = _cmd_out(["sw_vers", "-productVersion"])
    chrome_ver = _chrome_version()

    lock_file = repo_root / "engine/uv.lock"
    lock_sha = sha256_file(lock_file) if lock_file.is_file() else "UNKNOWN"

    repo_sha, patch_sha = _git_identity(repo_root)
    disk = shutil.disk_usage(repo_root)

    return {
        "hardware": {
            "chip": chip,
            "cpu_cores": int(cpu_cores_raw) if cpu_cores_raw.isdigit() else None,
            "gpu_cores": _gpu_cores(),
            "ram_bytes": int(ram_raw) if ram_raw.isdigit() else None,
            "macos_version": macos_ver,
            "chrome_version": chrome_ver,
        },
        "runtime": {
            "python_version": platform.python_version(),
            "mlx": _pkg_version("mlx"),
            "mlx_lm": _pkg_version("mlx-lm"),
            "mlx_audio": _pkg_version("mlx-audio"),
            "transformers": _pkg_version("transformers"),
            "lock_sha256": lock_sha,
        },
        "repo": {
            "actual_repo_sha": repo_sha,
            "patch_sha256": patch_sha,
        },
        "disk_free_gib": round(disk.free / (1024**3), 2),
        "cache_root": str(resolved_cache),
    }


def preflight(
    m: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
    cache_root: Path | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Read-only G0 validation: never downloads, never alters user config."""
    reasons = validate_manifest(m, require_local_assets=True, schema_path=schema_path)
    env = collect_local_environment(repo_root=repo_root, cache_root=cache_root)
    if (m.get("execution") or {}).get("allow_download") is True:
        reasons.append("preflight must not depend on allow_download=true for local ready assets")

    status = "PASS" if not reasons else "BLOCKED"
    exp_key = experiment_key(m) if isinstance(m, dict) else ""
    r_key = run_key(m) if isinstance(m, dict) else ""
    return {
        "schema_version": "stage8-preflight-v1",
        "status": status,
        "reasons": reasons,
        "experiment_key": exp_key,
        "run_key": r_key,
        "observed_env": env,
    }


def build_local_manifest(
    *,
    run_id: str,
    experiment_id: str,
    arm_id: str,
    baseline_role: str,
    output_dir: Path,
    mode: str = "offline",
    duration_s: float | None = None,
    media_path: Path | None = None,
    pcm_path: Path | None = None,
    semantic_units_path: Path | None = None,
    repo_root: Path = REPO_ROOT,
    cache_root: Path | None = None,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
) -> dict[str, Any]:
    """Construct a valid local manifest from verified repo and .model-cache artifacts."""
    env = collect_local_environment(repo_root=repo_root, cache_root=cache_root)
    c_root = Path(env["cache_root"])

    profile_path = MAIN_REPO_ROOT / "engine/config/profiles.toml"
    if not profile_path.is_file():
        profile_path = repo_root / "engine/config/profiles.example.toml"

    nemo_dylib = c_root / "nemo-ane/libnemo_ane.dylib"
    native_src = repo_root / "engine/native/nemo_ane.mm"
    native_sha = sha256_file(nemo_dylib) if nemo_dylib.is_file() else sha256_file(native_src)
    ext_sha = sha256_dir_manifest(repo_root / "extension/src")
    obs_files = [
        p for p in sorted((repo_root / "p0/stage8_eval").glob("*.py")) if p.is_file()
    ]
    obs_sha = canonical_json_sha256(
        [{"name": p.name, "sha256": sha256_file(p)} for p in obs_files]
    )

    from subtitle_service.local_pipeline import Qwen3Translator

    prompt_sha = sha256_text(Qwen3Translator.SYSTEM_PROMPT)

    # Local Qwen3-4B-4bit pinned snapshot
    qw_rev = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
    qw_snap = c_root / "hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots" / qw_rev
    qw_weights = qw_snap / "model.safetensors"
    qw_tok = qw_snap / "tokenizer.json"
    qw_tok_cfg = qw_snap / "tokenizer_config.json"

    gguf_path = c_root / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
    ane_dir = c_root / "nemo-ane"

    med = media_path or DEFAULT_EP01_MKV
    units_p = semantic_units_path or DEFAULT_EP01_SRT
    pcm_p = pcm_path or med

    # Avoid hashing 1GB MKV repeatedly unless requested by checking size or fast file hash; here read_file chunked takes ~0.7s.
    med_sha = sha256_file(med) if med.is_file() else "UNKNOWN"
    pcm_sha = med_sha if pcm_p == med else (sha256_file(pcm_p) if pcm_p.is_file() else "UNKNOWN")
    units_sha = sha256_file(units_p) if units_p.is_file() else "UNKNOWN"

    models_list: list[dict[str, Any]] = []
    roles: dict[str, str | None] = {
        "asr_primary": None,
        "asr_gpu_fallback": None,
        "asr_challenger": None,
        "mt": None,
    }
    if baseline_role != "subtitles_off":
        models_list = [
            {
                "key": "nemo-baseline",
                "repository": "reazon-research/reazonspeech-nemo-v2",
                "revision": "reazonspeech-nemo-619M-v2-q8_0-ane",
                "backend": "native_coreml_and_speechcpp_metal",
                "precision": "w8a16_ane_q8_gguf",
                "artifacts": [
                    {
                        "path": str(gguf_path),
                        "sha256": sha256_file(gguf_path) if gguf_path.is_file() else "UNKNOWN",
                    },
                    {
                        "path": str(ane_dir),
                        "sha256": sha256_path_or_dir(ane_dir) if ane_dir.exists() else "UNKNOWN",
                    },
                ],
                "tokenizer_sha256": sha256_file(gguf_path) if gguf_path.is_file() else "UNKNOWN",
                "template_sha256": "not_applicable",
                "license_verified": True,
                "trust_remote_code": False,
                "stop_ids": [],
            },
            {
                "key": "qwen4b-q4",
                "repository": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
                "revision": qw_rev,
                "backend": "mlx_lm",
                "precision": "4bit",
                "artifacts": [
                    {
                        "path": str(qw_weights),
                        "sha256": sha256_file(qw_weights) if qw_weights.is_file() else "UNKNOWN",
                    }
                ],
                "tokenizer_sha256": sha256_file(qw_tok) if qw_tok.is_file() else "UNKNOWN",
                "template_sha256": sha256_file(qw_tok_cfg) if qw_tok_cfg.is_file() else "UNKNOWN",
                "license_verified": True,
                "trust_remote_code": False,
                "stop_ids": [151645, 151643],
            },
        ]
        roles = {
            "asr_primary": "nemo-baseline",
            "asr_gpu_fallback": "nemo-baseline",
            "asr_challenger": None,
            "mt": "qwen4b-q4",
        }

    return {
        "schema_version": "stage8-manifest-v1",
        "template_only": False,
        "run_id": run_id,
        "experiment_id": experiment_id,
        "baseline_sha": "7ea13865c8c3673a1463495058a200723b153111",
        "actual_repo_sha": env["repo"]["actual_repo_sha"],
        "patch_sha256": env["repo"]["patch_sha256"],
        "bundle": {
            "bundle_key": f"stage8-{baseline_role}-{env['repo']['actual_repo_sha'][:8]}",
            "native_sha256": native_sha,
            "extension_sha256": ext_sha,
            "observer_sha256": obs_sha,
            "profile_sha256": sha256_file(profile_path),
            "profile_path": str(profile_path),
        },
        "hardware": env["hardware"],
        "runtime": env["runtime"],
        "data": {
            "dataset_id": "brush-up-life",
            "episode_id": "ep01",
            "split": "dev",
            "media_path": str(med),
            "media_sha256": med_sha,
            "audio_track": "0:a:0",
            "pcm_path": str(pcm_p),
            "pcm_sha256": pcm_sha,
            "sample_rate": 16000,
            "channels": 1,
            "resample_downmix_recipe": "ffmpeg -v error -ac 1 -ar 16000 -f s16le",
            "semantic_units_path": str(units_p),
            "semantic_units_sha256": units_sha,
            "reference_human_verified": False,
            "scene_count": None,
            "holdout_sealed": True,
        },
        "models": models_list,
        "roles": roles,
        "arm": {
            "arm_id": arm_id,
            "baseline_role": baseline_role,
            "parameters": {
                "decoding": "hybrid",
                "device": "ane",
                "draft_interval_s": 1.2,
                "vad_pause_ms": 180,
                "max_segment_s": 2.8,
                "overlap_ms": 350,
                "pad_ms": 500,
                "context_turns": 4,
                "sampling": "greedy",
                "max_output_tokens": 256,
            },
            "rendered_prompt_policy_sha256": prompt_sha,
        },
        "thresholds": {
            "path": str(thresholds_path),
            "sha256": sha256_file(thresholds_path),
        },
        "budgets": {
            "final_request_timeout_ms": 2000,
            "preview_expiry_ms": 900,
            "verify_max_extra_wait_ms": 120,
            "reserve_mt_p95_ms": None,
            "reserve_transport_p95_ms": None,
        },
        "execution": {
            "mode": mode,
            "output_dir": str(output_dir),
            "duration_s": duration_s,
            "allow_download": False,
            "allow_remote_audio_upload": False,
            "allow_privileged_telemetry": False,
            "allow_default_switch": False,
            "allow_git_commit": False,
            "allow_git_push": False,
            "paired_order": "ABBA",
            "repetitions": 1,
            "bootstrap_seed": 20261126,
        },
        "review": {
            "blind_model_identity": True,
            "human_verified": False,
            "reviewer_count": 0,
            "status": "BLOCKED",
            "reason_code": "PENDING_HUMAN_REVIEW",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 8 read-only G0 manifest preflight")
    parser.add_argument("--manifest", type=Path, help="Path to manifest JSON to validate")
    parser.add_argument("--out", type=Path, help="Optional output path for preflight JSON")
    parser.add_argument(
        "--generate-manifest",
        type=Path,
        help="Optional output path to generate a local manifest from current repo/cache",
    )
    parser.add_argument("--run-id", default="stage8-batch1-local-001")
    parser.add_argument("--experiment-id", default="stage8-batch1-b-corrected")
    parser.add_argument("--arm-id", default="b-corrected")
    parser.add_argument("--baseline-role", default="corrected", choices=["deployed", "corrected", "candidate", "subtitles_off"])
    parser.add_argument("--mode", default="offline", choices=["offline", "ws_replay", "chrome", "native_probe"])
    parser.add_argument("--duration-s", type=float, default=None)
    parser.add_argument("--output-dir", type=Path, default=MAIN_REPO_ROOT / "p0/evidence/stage8-batch1-20261126/run")
    parser.add_argument("--media-path", type=Path, default=None)
    parser.add_argument("--pcm-path", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.generate_manifest is not None:
        m = build_local_manifest(
            run_id=args.run_id,
            experiment_id=args.experiment_id,
            arm_id=args.arm_id,
            baseline_role=args.baseline_role,
            output_dir=args.output_dir,
            mode=args.mode,
            duration_s=args.duration_s,
            media_path=args.media_path,
            pcm_path=args.pcm_path,
        )
        args.generate_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.generate_manifest.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.manifest is None:
            args.manifest = args.generate_manifest

    if args.manifest is None:
        parser.error("--manifest or --generate-manifest is required")

    manifest = load_manifest(args.manifest)
    report = preflight(manifest)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
