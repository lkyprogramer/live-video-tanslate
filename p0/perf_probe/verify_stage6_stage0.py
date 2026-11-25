"""Stage 6 Stage 0 verification: idle ANE check, native timing breakdown, GPU+greedy ablation, and 1x WS A/B."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_mt"))

from dataset import CACHE_ROOT, D1_PCM, SILENCE_PCM, VAD_PATH, load_pcm_float32, percentile  # noqa: E402
from metrics_utils import compute_cer  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import AsrConfig, load_settings, load_token  # noqa: E402
from subtitle_service.local_pipeline import RATE, NeMoAneBridge, SpeechCppNeMoRecognizer, _SmartSegmenter  # noqa: E402
from subtitle_service.replay import replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, find_server_pid, launched_server  # noqa: E402
from verify_stage2 import FRAME_64MS_BYTES, active_seek_64ms, analyze_stage2_latencies  # noqa: E402
from verify_stage4 import analyze_stage4_extra as analyze_draft_onset  # noqa: E402
from verify_stage5a2_live import _patch_profile, _wired_mb  # noqa: E402

OUT_DIR = REPO_ROOT / "p0/evidence/stage6-stage0-20261125"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage6-stage0-2026-11-25"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"
JP_SRT_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.srt")
GGUF_PATH = CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
DYLIB_PATH = CACHE_ROOT / "speech-cpp/libspeech.dylib"
ANE_DIR = CACHE_ROOT / "nemo-ane"

_SRT_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_META_PREFIX_RE = re.compile(r"^\([^\)]+\)|>>|[⟨⟩]")


def load_d1_ref_text(t0_s: float = 275.0, t1_s: float = 320.0) -> str:
    raw = JP_SRT_PATH.read_bytes().decode("utf-8-sig")
    parts: list[str] = []
    for block in re.split(r"\r?\n\r?\n+", raw.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        m = _SRT_TS_RE.search(lines[1])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        st = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        en = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        if en >= t0_s and st <= t1_s:
            txt = " ".join(lines[2:])
            txt = txt.replace("→", "")
            parts.append(txt)
    return "".join(parts)


def build_eval_slices(suite: dict) -> list[dict]:
    import sherpa_onnx
    from subtitle_service.local_pipeline import silero_config

    sources = [("d1-ep01-45s", D1_PCM)] + [
        (cid, SUITE_PATH.parent / suite[cid]["file"])
        for cid in ("csj-reproduction", "csj-task", "wmf-04")
    ]
    vad_cfg = silero_config(VAD_PATH, 350)
    slices: list[dict] = []
    for clip_id, pcm_path in sources:
        vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=120)
        seg = _SmartSegmenter(vad, max_segment_s=4.5, overlap_ms=350)
        raw = pcm_path.read_bytes()
        chunks = []
        for off in range(0, len(raw), FRAME_64MS_BYTES):
            chunks.extend(seg.push(raw[off : off + FRAME_64MS_BYTES]))
        chunks.extend(seg.push(None))
        for idx, ch in enumerate(chunks):
            slices.append({
                "clip": clip_id,
                "idx": idx,
                "start_sample": ch.start_sample,
                "dedup_before_sample": ch.dedup_before_sample,
                "samples": ch.samples,
                "duration_s": round(len(ch.samples) / float(RATE), 3),
            })
    return slices


def run_offline_ablation(slices: list[dict], d1_ref_text: str) -> dict:
    silence_audio = load_pcm_float32(SILENCE_PCM)
    arms = {}
    for label, dev, dec in [
        ("gpu_beam", "gpu", "beam"),
        ("gpu_greedy", "gpu", "greedy"),
        ("ane_greedy", "ane", "greedy"),
    ]:
        rec = SpeechCppNeMoRecognizer(
            DYLIB_PATH,
            GGUF_PATH,
            VAD_PATH,
            AsrConfig(
                type="speech_cpp_nemo",
                model_file=GGUF_PATH.name,
                threads=6,
                decoding=dec,
                device=dev,
                pad_ms=350,
            ),
        )
        try:
            durs_ms: list[float] = []
            d1_texts: list[str] = []
            timings_rows: list[dict] = []
            fallback_count = 0
            for sl in slices:
                t0 = time.perf_counter()
                txt = rec.decode(
                    sl["samples"],
                    start_sample=sl["start_sample"],
                    dedup_before_sample=sl["dedup_before_sample"],
                )
                durs_ms.append((time.perf_counter() - t0) * 1000.0)
                if sl["clip"] == "d1-ep01-45s":
                    d1_texts.append(txt)
                meta = rec.last_decode_meta
                if meta.get("fallback_reason"):
                    fallback_count += 1
                if meta.get("timings"):
                    timings_rows.append(meta["timings"])
            sil_txt = rec.decode(silence_audio)
            d1_cer = compute_cer("".join(d1_texts), d1_ref_text, strip_speakers=True)
            res = {
                "slices": len(slices),
                "asr_p50_ms": round(percentile(durs_ms, 0.50), 2),
                "asr_p95_ms": round(percentile(durs_ms, 0.95), 2),
                "d1_cer": d1_cer,
                "silence_text": sil_txt,
                "fallback_count": fallback_count,
            }
            if timings_rows:
                res["ane_timings_summary"] = {
                    k: {
                        "p50_ms": round(percentile([r[k] for r in timings_rows], 0.50), 2),
                        "p95_ms": round(percentile([r[k] for r in timings_rows], 0.95), 2),
                    }
                    for k in ("frontend_ms", "predict_ms", "output_copy_ms", "decode_ms", "total_ms")
                }
            arms[label] = res
        finally:
            rec.close()
    return arms


def run_idle_keepalive_check() -> dict:
    bridge = NeMoAneBridge(ANE_DIR, GGUF_PATH)
    try:
        c0 = bridge.eval_count()
        time.sleep(3.5)
        c1 = bridge.eval_count()
        return {
            "eval_count_after_init": c0,
            "eval_count_after_3_5s_idle": c1,
            "idle_eval_delta": c1 - c0,
            "passed": (c1 - c0) == 0,
        }
    finally:
        bridge.close()


def _analyze_trace_extras(model_jsonl: Path, session_id: str) -> dict:
    rows = [
        json.loads(ln)
        for ln in model_jsonl.read_text(encoding="utf-8").splitlines()
        if ln.strip() and json.loads(ln).get("session_id") == session_id
    ]
    backends: dict[str, int] = {}
    fallbacks = 0
    timings: list[dict] = []
    for row in rows:
        if row.get("stage") != "asr_end":
            continue
        b = row.get("asr_backend", "unknown")
        backends[b] = backends.get(b, 0) + 1
        if row.get("fallback_reason"):
            fallbacks += 1
        if row.get("ane_timings"):
            timings.append(row["ane_timings"])
    out: dict = {"asr_backends": backends, "fallback_count": fallbacks}
    if timings:
        out["ane_timings_summary"] = {
            k: {
                "p50_ms": round(percentile([t[k] for t in timings], 0.50), 2),
                "p95_ms": round(percentile([t[k] for t in timings], 0.95), 2),
            }
            for k in ("frontend_ms", "predict_ms", "output_copy_ms", "decode_ms", "total_ms")
        }
    return out


def run_live_arm(label: str, base_toml: str, suite: dict, d1_audio: bytes, *, device: str, decoding: str) -> dict:
    run_dir = OUT_DIR / label
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = run_dir / "profiles.toml"
    cfg_file.write_text(_patch_profile(base_toml, device=device, decoding=decoding), encoding="utf-8")
    config = load_settings(cfg_file)

    w0 = _wired_mb()
    with launched_server(cfg_file, config, run_dir):
        pid = find_server_pid(config.server.port)
        assert pid is not None
        token = load_token(token_path(), create=False)
        res_map = {}
        with ResourceSampler(pid) as sampler:
            for cid in ("csj-reproduction", "csj-task", "silence"):
                audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
                tp = run_dir / f"{cid}.jsonl"
                with tp.open("w", buffering=1) as trace:
                    r = asyncio.run(replay(config, "m1-pro-nemo", token, cid, audio, trace, frame_bytes=FRAME_64MS_BYTES))
                sid = json.loads(tp.read_text().splitlines()[0])["event"]["session_id"]
                lat = analyze_stage2_latencies(tp, run_dir / "model.jsonl", sid)
                onset = analyze_draft_onset(tp, run_dir / "model.jsonl", sid) if cid != "silence" else {}
                extra = _analyze_trace_extras(run_dir / "model.jsonl", sid)
                res_map[cid] = r | lat | onset | extra

            d1_tp = run_dir / "d1.jsonl"
            with d1_tp.open("w", buffering=1) as trace:
                d1_r = asyncio.run(replay(config, "m1-pro-nemo", token, "d1-ep01-45s", d1_audio, trace, frame_bytes=FRAME_64MS_BYTES))
            d1_sid = json.loads(d1_tp.read_text().splitlines()[0])["event"]["session_id"]
            res_map["d1-ep01-45s"] = (
                d1_r
                | analyze_stage2_latencies(d1_tp, run_dir / "model.jsonl", d1_sid)
                | analyze_draft_onset(d1_tp, run_dir / "model.jsonl", d1_sid)
                | _analyze_trace_extras(run_dir / "model.jsonl", d1_sid)
            )

            csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
            seek_r = asyncio.run(active_seek_64ms(config, "m1-pro-nemo", token, csj_audio, run_dir))

        w1 = _wired_mb()
        return {
            "results": res_map,
            "seek_50ms": seek_r,
            "resources": {
                "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
                "wired_delta_mb": round(w1 - w0, 1),
            },
        }


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d1_audio = D1_PCM.read_bytes()
    d1_ref_text = load_d1_ref_text()
    slices = build_eval_slices(suite)

    print(f"=== Part A: Offline 3-arm ablation ({len(slices)} slices) ===")
    offline = run_offline_ablation(slices, d1_ref_text)

    print("=== Part B: Live ANE 3.5s idle keepalive check ===")
    idle = run_idle_keepalive_check()
    assert idle["passed"], f"Idle ANE check failed: {idle}"

    base_toml = CFG_PATH.read_text(encoding="utf-8").replace(
        'cache_root = "../../.model-cache"',
        f'cache_root = "{CACHE_ROOT}"',
    )
    live = {}
    for label, dev, dec in [("gpu_greedy", "gpu", "greedy"), ("ane_greedy", "ane", "greedy")]:
        print(f"=== Part C: 1x Live WS Arm: {label} (device={dev}, decoding={dec}) ===")
        live[label] = run_live_arm(label, base_toml, suite, d1_audio, device=dev, decoding=dec)

    summary = {"offline_ablation": offline, "idle_check": idle, "live_ab": live}
    out_file = ARCHIVE_DIR / "stage0_verification.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(OUT_DIR, ignore_errors=True)

    compact = {
        "idle_check": idle,
        "offline_ablation": offline,
        "live_summary": {
            k: {
                "resources": v["resources"],
                "d1": {
                    "captions": v["results"]["d1-ep01-45s"]["captions"],
                    "warnings": v["results"]["d1-ep01-45s"]["warnings"],
                    "asr_p50_s": v["results"]["d1-ep01-45s"]["asr_p50_s"],
                    "first_delta_e2e_p50_s": v["results"]["d1-ep01-45s"]["first_delta_e2e_p50_s"],
                    "final_e2e_p50_s": v["results"]["d1-ep01-45s"]["final_e2e_p50_s"],
                    "final_e2e_p95_s": v["results"]["d1-ep01-45s"]["final_e2e_p95_s"],
                    "asr_backends": v["results"]["d1-ep01-45s"]["asr_backends"],
                    "ane_timings_summary": v["results"]["d1-ep01-45s"].get("ane_timings_summary"),
                },
                "csj_repro": {
                    "captions": v["results"]["csj-reproduction"]["captions"],
                    "asr_p50_s": v["results"]["csj-reproduction"]["asr_p50_s"],
                    "final_e2e_p50_s": v["results"]["csj-reproduction"]["final_e2e_p50_s"],
                },
            }
            for k, v in live.items()
        },
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    print(f"Saved {out_file}")


if __name__ == "__main__":
    main()
