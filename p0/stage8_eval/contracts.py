"""Stage 8 manifest loading, hashing, schema/semantic validation, and cache keys."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from subtitle_service.quality_types import (  # noqa: F401
    ASRHypothesis,
    AudioSpan,
    RequestKey,
    Stage8Event,
    TokenSpan,
    TranslationRequest,
    TranslationResult,
    Turn,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = (
    REPO_ROOT / "docs/live_video_translate_stage8_plan_20261126/schemas/contracts.schema.json"
)
MAIN_REPO_SCHEMA_PATH = Path(
    "/Users/luo/Documents/github/jp-live-video-tanslate/"
    "docs/live_video_translate_stage8_plan_20261126/schemas/contracts.schema.json"
)
FORBIDDEN_REVISIONS = frozenset({"", "main", "master", "HEAD"})


def resolve_schema_path(schema_path: Path | None = None) -> Path | None:
    if schema_path is not None and schema_path.is_file():
        return schema_path
    if DEFAULT_SCHEMA_PATH.is_file():
        return DEFAULT_SCHEMA_PATH
    if MAIN_REPO_SCHEMA_PATH.is_file():
        return MAIN_REPO_SCHEMA_PATH
    return None


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir_manifest(path: Path) -> str:
    """Compute a deterministic digest of all files inside an artifact directory."""
    root = Path(path)
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            entries.append({"path": rel, "size": p.stat().st_size, "sha256": sha256_file(p)})
    return sha256_bytes(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def sha256_path_or_dir(path: Path) -> str:
    p = Path(path)
    if p.is_dir():
        return sha256_dir_manifest(p)
    return sha256_file(p)


def canonical_json_sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(payload.encode("utf-8"))


def _find_placeholders(obj: Any, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            hits.extend(_find_placeholders(v, sub))
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            hits.extend(_find_placeholders(v, f"{prefix}[{idx}]"))
    elif isinstance(obj, str) and "REPLACE" in obj:
        hits.append(f"placeholder at {prefix}: {obj!r}")
    return hits


def validate_manifest(
    m: dict[str, Any],
    *,
    require_local_assets: bool = True,
    schema_path: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(m, dict):
        return ["manifest root must be a JSON object"]

    resolved_schema = resolve_schema_path(schema_path)
    if resolved_schema is not None:
        root_schema = json.loads(resolved_schema.read_text(encoding="utf-8"))
        manifest_schema = {
            "$schema": root_schema.get("$schema"),
            "$defs": root_schema.get("$defs", {}),
            "$ref": "#/$defs/Manifest",
        }
        validator = Draft202012Validator(manifest_schema)
        for err in sorted(validator.iter_errors(m), key=lambda e: list(e.absolute_path)):
            loc = ".".join(str(x) for x in err.absolute_path) or "<root>"
            errors.append(f"schema:{loc}: {err.message}")

    if m.get("template_only") is True:
        errors.append("template_only must be false for runnable manifests")

    errors.extend(_find_placeholders(m))

    data = m.get("data") or {}
    if data.get("sample_rate") != 16000:
        errors.append(f"data.sample_rate must be 16000, got {data.get('sample_rate')!r}")
    if data.get("channels") != 1:
        errors.append(f"data.channels must be 1, got {data.get('channels')!r}")

    arm = m.get("arm") or {}
    baseline_role = arm.get("baseline_role")
    models = m.get("models") or []
    roles = m.get("roles") or {}
    model_by_key = {mod.get("key"): mod for mod in models if isinstance(mod, dict)}

    for idx, mod in enumerate(models):
        rev = str(mod.get("revision", ""))
        if rev in FORBIDDEN_REVISIONS:
            errors.append(f"models[{idx}].revision {rev!r} is not a pinned revision")
        if not mod.get("artifacts"):
            errors.append(f"models[{idx}].artifacts must be non-empty")

    if baseline_role == "subtitles_off":
        if models:
            errors.append("subtitles_off baseline requires empty models array")
        for role_name in ("asr_primary", "asr_gpu_fallback", "asr_challenger", "mt"):
            if roles.get(role_name) is not None:
                errors.append(f"subtitles_off requires roles.{role_name} to be null")
    else:
        for role_name in ("asr_primary", "asr_gpu_fallback", "asr_challenger", "mt"):
            ref_key = roles.get(role_name)
            if ref_key is not None and ref_key not in model_by_key:
                errors.append(f"roles.{role_name}={ref_key!r} missing from models list")

    if require_local_assets:
        bundle = m.get("bundle") or {}
        profile_path = Path(str(bundle.get("profile_path", "")))
        if not profile_path.is_file():
            errors.append(f"missing bundle.profile_path: {profile_path}")
        elif bundle.get("profile_sha256") and sha256_file(profile_path) != bundle["profile_sha256"]:
            errors.append("bundle.profile_sha256 mismatch with profile_path contents")

        thresh = m.get("thresholds") or {}
        thresh_path = Path(str(thresh.get("path", "")))
        if not thresh_path.is_file():
            errors.append(f"missing thresholds.path: {thresh_path}")
        elif thresh.get("sha256") and sha256_file(thresh_path) != thresh["sha256"]:
            errors.append("thresholds.sha256 mismatch with thresholds file")

        pcm_path = Path(str(data.get("pcm_path", "")))
        media_path = Path(str(data.get("media_path", "")))
        if not pcm_path.is_file() and not media_path.is_file():
            errors.append(f"missing both data.pcm_path ({pcm_path}) and data.media_path ({media_path})")
        if pcm_path.is_file() and data.get("pcm_sha256") and sha256_file(pcm_path) != data["pcm_sha256"]:
            errors.append("data.pcm_sha256 mismatch with pcm_path contents")

        for idx, mod in enumerate(models):
            for a_idx, art in enumerate(mod.get("artifacts") or []):
                apath = Path(str(art.get("path", "")))
                if not apath.exists():
                    errors.append(f"missing models[{idx}].artifacts[{a_idx}]: {apath}")

    return errors


def experiment_key(m: dict[str, Any]) -> str:
    """Content-addressed identity excluding run_id, output_dir, and wall-clock time."""
    bundle = m.get("bundle") or {}
    data = m.get("data") or {}
    arm = m.get("arm") or {}
    models_norm = [
        {
            "key": mod.get("key"),
            "repository": mod.get("repository"),
            "revision": mod.get("revision"),
            "backend": mod.get("backend"),
            "precision": mod.get("precision"),
            "artifact_sha256s": [a.get("sha256") for a in (mod.get("artifacts") or [])],
            "tokenizer_sha256": mod.get("tokenizer_sha256"),
            "template_sha256": mod.get("template_sha256"),
            "stop_ids": mod.get("stop_ids"),
        }
        for mod in (m.get("models") or [])
    ]
    payload = {
        "schema_version": m.get("schema_version"),
        "baseline_sha": m.get("baseline_sha"),
        "actual_repo_sha": m.get("actual_repo_sha"),
        "patch_sha256": m.get("patch_sha256"),
        "bundle": {
            "bundle_key": bundle.get("bundle_key"),
            "native_sha256": bundle.get("native_sha256"),
            "extension_sha256": bundle.get("extension_sha256"),
            "observer_sha256": bundle.get("observer_sha256"),
            "profile_sha256": bundle.get("profile_sha256"),
        },
        "runtime": m.get("runtime"),
        "data": {
            "dataset_id": data.get("dataset_id"),
            "episode_id": data.get("episode_id"),
            "split": data.get("split"),
            "media_sha256": data.get("media_sha256"),
            "audio_track": data.get("audio_track"),
            "pcm_sha256": data.get("pcm_sha256"),
            "sample_rate": data.get("sample_rate"),
            "channels": data.get("channels"),
            "resample_downmix_recipe": data.get("resample_downmix_recipe"),
            "semantic_units_sha256": data.get("semantic_units_sha256"),
        },
        "models": models_norm,
        "roles": m.get("roles"),
        "arm": {
            "arm_id": arm.get("arm_id"),
            "baseline_role": arm.get("baseline_role"),
            "parameters": arm.get("parameters"),
            "rendered_prompt_policy_sha256": arm.get("rendered_prompt_policy_sha256"),
        },
        "thresholds_sha256": (m.get("thresholds") or {}).get("sha256"),
        "budgets": m.get("budgets"),
    }
    return canonical_json_sha256(payload)


def run_key(m: dict[str, Any]) -> str:
    """Full manifest digest including run_id and output_dir to separate paired ABBA runs."""
    return canonical_json_sha256(m)
