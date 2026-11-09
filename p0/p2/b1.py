"""Bounded B1 fixed-cut/text screening. No realtime or production integration."""
import argparse
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import signal

from .freeze import ROOT, PLAN, digest, dump, rows, validate_lock

IDS = ("PAD-900", "PUNCT-JA", "MT-PLAMO08-H0", "MT-PLAMO2-H0")
PARENT = ROOT / "p0/evidence/p2-b0-20261108-103637-browser-fix-settings"
PLAMO_PYTHON = ROOT / ".model-cache/p2-b1-venv/bin/python"


def runtime():
    packages=("numpy", "mlx", "mlx-lm", "transformers", "huggingface-hub", "tokenizers")
    packages += ("numba",) if Path(sys.executable).parent == PLAMO_PYTHON.parent else ("sherpa-onnx", "onnxruntime", "whisperlivekit")
    return {key: version(key) for key in packages}


def freeze(run, registry, *, phase="B1", model_additions=None, runtime_lock=None, hypotheses=None, extra_sources=()):
    if (run / "run-manifest.json").exists():
        return verify(run)
    accepted = json.loads((PARENT / "acceptance.json").read_text())
    if not accepted["ready_for_B1"]:
        raise ValueError("B0 not accepted")
    # Verify saved outputs against their execution receipts, independently of today's source checkout.
    state = json.loads((PARENT / "state.json").read_text())
    for step in state["steps"].values():
        if step["status"] == "passed":
            for name, expected in step["outputs"].items():
                if digest(PARENT / name) != expected:
                    raise ValueError("B0 output receipt drift: " + name)
    for folder in ("locks", "inputs", "baseline", "comparisons", "reviews"):
        (run / folder).mkdir(parents=True, exist_ok=True)
    parent_manifest = json.loads((PARENT / "run-manifest.json").read_text())
    for name, expected in parent_manifest["artifact_sha256"].items():
        if digest(PARENT / name) != expected:
            raise ValueError("B0 frozen artifact drift: " + name)
    for name in ("reazon.jsonl", "units.jsonl", "mt-seed11.jsonl", "mt-seed29.jsonl", "verdict.json", "pending-listen.json", "unit-map.json"):
        shutil.copyfile(PARENT / "inputs" / name, run / "inputs" / name)
    # Reazon rows carry original index/source IDs in the reproduced B0, unlike historical input rows.
    shutil.copyfile(PARENT / "baseline/reazon.jsonl", run / "baseline/reazon.jsonl")
    for name in ("asset-manifest.json", "reference-manifest.json", "baseline-score.json"):
        shutil.copyfile(PARENT / name, run / name)
    assets = json.loads((run / "asset-manifest.json").read_text())["assets"]
    for asset in assets:
        if digest(asset["path"]) != asset["sha256"]:
            raise ValueError("PCM changed")
    models = json.loads((PARENT / "locks/models.json").read_text())["models"]
    paths = json.loads((run / "sources/model-paths.json").read_text()) if model_additions is None else {}
    for key, path in paths.items():
        snapshot = Path(path)
        info = json.loads((run / "sources" / key / "repository-files.json").read_text())
        if info["revision"] != snapshot.name:
            raise ValueError("snapshot revision mismatch")
        models.append(dict(id=key, source="hf", repository=info["repo"], revision=snapshot.name,
            local_path=path, files=[dict(path=str(p), sha256=digest(p)) for p in sorted(snapshot.iterdir()) if p.is_file()]))
    models.extend(model_additions or [])
    validate_lock(dict(models=models))
    dump(run / "locks/models.json", dict(models=models))
    shutil.copyfile(PARENT / "locks/profiles.toml", run / "locks/profiles.toml")
    shutil.copyfile(registry, run / "locks/registry.json")
    if runtime_lock is None:
        isolated = subprocess.check_output([str(PLAMO_PYTHON), "-c", "from p0.p2.b1 import runtime; import json; print(json.dumps(runtime()))"], cwd=ROOT, text=True)
        runtime_lock=dict(engine=runtime(), plamo=json.loads(isolated))
    dump(run / "locks/runtime.json", runtime_lock)
    dump(run / "locks/hypotheses.json", hypotheses or dict(ids=IDS, modes=["fixed-cuts", "fixed-text"], seeds=[11,29],
        routes=["Q", "R"], Q="each round net>=3/34; no new critical errors; no protected losses",
        R="offline eligibility only: no score/critical/protected regression; full-chain hot footprint primary >=20% is later B5",
        PAD=dict(head_ms=900, tail_ms=900, vad="unchanged", changed_seed="sha256((clip,start,end,ja))[:7]+100000"),
        PUNCT=dict(max_chars=510, past_overlap=16, threads=1, provider="CPU", same_original_seed_index=True),
        PLAMO=dict(temperature=0, max_tokens=256, source_lang="ja", target_lang="zh-hans", history=False, glossary=False, shared_cache=False),
        budget=dict(deployment_diagnosis_s=7200, quality_s=3600, request_s=30), human_verified=False, holdout="sealed"))
    sources = list((ROOT / "p0/p2").rglob("*.py")) + [ROOT / "p0/monitor.py", ROOT / "engine/config/profiles.toml"]
    sources += list((ROOT / "engine/src/subtitle_service").glob("*.py"))
    sources += list(extra_sources)
    source_sha = {str(p):digest(p) for p in sorted(sources)}
    artifact = list((run / "locks").glob("*")) + list((run / "inputs").glob("*")) + [run / "baseline/reazon.jsonl",run / "asset-manifest.json",run / "reference-manifest.json",run / "baseline-score.json"]
    artifact_sha = {str(p.relative_to(run)):digest(p) for p in artifact}
    manifest = dict(phase=phase, run_dir=str(run), parent=str(PARENT), parent_manifest_sha256=digest(PARENT / "run-manifest.json"),
                    source_sha256=source_sha, artifact_sha256=artifact_sha, assets=assets, runtime=json.loads((run / "locks/runtime.json").read_text()))
    manifest["fingerprint"] = sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    (run / "source-snapshot").mkdir()
    for name in source_sha:
        target = run / "source-snapshot" / Path(name).relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(name,target)
    dump(run / "run-manifest.json", manifest)
    return manifest


def verify(run, *, runtime_reader=None, runtime_key=None):
    manifest = json.loads((run / "run-manifest.json").read_text())
    for name, expected in manifest["source_sha256"].items():
        if digest(name) != expected:
            raise ValueError("B1 source drift: " + name)
    for name, expected in manifest["artifact_sha256"].items():
        if digest(run / name) != expected:
            raise ValueError("B1 artifact drift: " + name)
    for item in manifest["assets"]:
        if digest(item["path"]) != item["sha256"]:
            raise ValueError("B1 PCM drift")
    validate_lock(json.loads((run / "locks/models.json").read_text()))
    key = runtime_key or ("plamo" if Path(sys.executable).parent == PLAMO_PYTHON.parent else "engine")
    if (runtime_reader or runtime)() != manifest["runtime"][key]:
        raise ValueError("B1 runtime drift")
    state_path = run / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["fingerprint"] != manifest["fingerprint"]:
            raise ValueError("B1 fingerprint drift")
        for step in state["steps"].values():
            if step["status"] == "passed":
                for name, expected in step["outputs"].items():
                    if digest(run / name) != expected:
                        raise ValueError("B1 completed output drift: " + name)
    return manifest


def execute(run, key, operation, output, *, verify_run=None):
    manifest = (verify_run or verify)(run)
    state_path = run / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(fingerprint=manifest["fingerprint"],steps={})
    if key in state["steps"]:
        step = state["steps"][key]
        if step["status"] != "passed":
            raise ValueError("failed/interrupted attempt retained; use fresh attempt")
        return step["result"] | dict(resumed_verified=True)
    state["steps"][key] = dict(status="running",started_ns=time.monotonic_ns())
    dump(state_path,state)
    try:
        result = operation()
    except Exception as exc:
        state["steps"][key].update(status="failed",error=str(exc));dump(state_path,state)
        dump(output / "failure.json",dict(status="failed",error=type(exc).__name__+": "+str(exc),
            denominator_unchanged=34,quality_eligible=False,partial_outputs_retained=True,
            missing_rows="All absent rows are NOT_RUN, never counted as correct",next_action="fresh attempt after bounded diagnosis"))
        raise
    state["steps"][key].update(status="passed",result=result,finished_ns=time.monotonic_ns(),
        outputs={str(p.relative_to(run)):digest(p) for p in sorted(output.rglob("*")) if p.is_file()})
    dump(state_path,state)
    return result


def model_paths(run):
    return {m["id"]:Path(m["local_path"]) for m in json.loads((run / "locks/models.json").read_text())["models"]}


def write_rows(path, values):
    with path.open("x") as target:
        for row in values:
            target.write(json.dumps(row, ensure_ascii=False,allow_nan=False)+"\n")


def bounded_translate(operation):
    def expired(signum, frame):
        raise TimeoutError("MT request exceeded preregistered 30s budget")
    previous=signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,30)
    try:
        return operation()
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)


def pad_probe(run, out):
    import numpy as np
    from p0.monitor import mark
    from subtitle_service.config import load_settings
    from subtitle_service.local_pipeline import ReazonRecognizer
    from .adapters.reazon import pad_for_decode

    paths = model_paths(run)
    mark("asr-load","start")
    model = ReazonRecognizer(paths["reazon-b0"], paths["silero-v4-b0"], load_settings(run / "locks/profiles.toml").profiles["reazon-hy"].asr)
    mark("asr-load","end")
    output = []
    mark("asr-88","start")
    for row in rows(run / "baseline/reazon.jsonl"):
        samples = np.fromfile(row["pcm_path"],dtype="<i2")[row["start_sample"]:row["end_sample"]].astype(np.float32)/32768
        started=time.monotonic_ns()
        ja=bounded_translate(lambda:model.decode(pad_for_decode(samples,16000,900,900)))
        seed_index = row["mt_seed_index"] if ja == row["text_ja"] else int(sha256(json.dumps((row["clip"],row["start_sample"],row["end_sample"],ja),ensure_ascii=False).encode()).hexdigest()[:7],16)+100000
        item=row | dict(text_ja=ja,decode_s=(time.monotonic_ns()-started)/1e9,mt_seed_index=seed_index,head_padding_samples=14400,tail_padding_samples=14400)
        output.append(item)
        write_rows(out / f"asr-row-{len(output):02d}.jsonl",[item])
        print(json.dumps(dict(stage="PAD-900",row=len(output),changed=ja!=row["text_ja"])),flush=True)
    mark("asr-88","end")
    write_rows(out / "reazon.jsonl",output)
    mark("silence-direct-decode","start")
    silent=np.zeros(160000,dtype=np.float32)
    raw_silence=bounded_translate(lambda:model.decode(silent))
    padded_silence=bounded_translate(lambda:model.decode(pad_for_decode(silent,16000,900,900)))
    dump(out / "silence.json",dict(control_raw=raw_silence,padded_raw=padded_silence,
        upstream_vad="unchanged: B0 silence has zero released cuts",diagnostic="direct decoder, outside88 cuts"))
    mark("silence-direct-decode","end")
    dump(out / "asr-summary.json",dict(rows=88, added_decode_audio_s=158.4, source_spans_unchanged=True,
        changed=sum(a["text_ja"]!=b["text_ja"] for a,b in zip(output,rows(run / "baseline/reazon.jsonl")))))
    return dict(rows=88)


def text_probe(run, experiment, out, *, asr_path=None, short_controls=False):
    import mlx.core as mx
    from p0.monitor import mark
    from .events import EventLog

    # In the isolated PLaMo environment use the same pure Chinese filter source without importing FastAPI.
    import ast
    source=ast.parse((ROOT / "engine/src/subtitle_service/app.py").read_text())
    node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="chinese")
    import re
    import unicodedata
    constants=[n for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ("HAN","KANA") for t in n.targets)]
    namespace={"re":re,"unicodedata":unicodedata}
    exec(compile(ast.Module(body=constants+[node],type_ignores=[]),"frozen-chinese-filter","exec"),namespace)
    chinese=namespace["chinese"]
    paths=model_paths(run)
    all_asr=rows(asr_path or run / ("PAD-900/reazon.jsonl" if experiment=="PAD-900" else "baseline/reazon.jsonl"))
    clips={u["clip"] for u in rows(run / "inputs/units.jsonl")}
    data=[r for r in all_asr if r["clip"] in clips]
    assert len(data)==62
    if experiment=="PUNCT-JA":
        from .adapters.punctuation import Punctuation
        mark("punct-load","start");punct=Punctuation(paths["punct-ja"]);mark("punct-load","end")
        transformed=[];mark("punct-62","start")
        for row in data:
            text,proof=punct.restore_punctuation(row["text_ja"])
            transformed.append(row | dict(input_ja=text,punctuation=proof))
        mark("punct-62","end");write_rows(out / "punctuation.jsonl",transformed)
        data=transformed
    mark("mt-load","start")
    is_plamo=experiment.startswith("MT-PLAMO")
    if is_plamo:
        from .adapters.plamo import Plamo
        model=Plamo(paths["plamo08-mlx" if experiment=="MT-PLAMO08-H0" else "plamo2-mlx"])
        mark("mt-load","end");mark("synthetic-smoke","start")
        # Synthetic, unscored H0 isolation/control tests. No tuning from these outputs.
        smoke=[]
        a="私は明日行きません。";b="会議は3時からですか？"
        for text in (a,b,a,"値段は250円です。","はい。"):
            zh,meta=bounded_translate(lambda:model.translate(text));smoke.append(dict(ja=text,zh=zh,**meta))
        if smoke[0]["zh"]!=smoke[2]["zh"]:
            raise ValueError("A/B/A request isolation failed")
        dump(out / "smoke.json",dict(rows=smoke,repeat_identical=True,bos=1,eos=[1,16],h0=True))
        mark("synthetic-smoke","end")
    else:
        from subtitle_service.local_pipeline import HyTranslator
        model=HyTranslator(paths["hymt-b0"]);session=model.new_session()
        dump(out / "mt-settings.json",dict(prompt=session._prompt,generation=asdict(session._config)))
        mark("mt-load","end")
    parity={}
    for seed in (11,29):
        log=EventLog(out / f"events-seed{seed}.jsonl",run.name,experiment_id=experiment,repeat_id=f"seed{seed}")
        translated=[];mark(f"mt-seed{seed}","start")
        try:
            for row in data:
                ja=row.get("input_ja",row["text_ja"])
                mx.random.seed(seed+row["mt_seed_index"])
                span={k:row[k] for k in ("source_id","segment_id","start_sample","end_sample")}
                log.record("mt_start",session_id=row["clip"],**span)
                started=time.monotonic_ns()
                if not ja:
                    zh,meta="",dict(finish_reason="empty_source")
                elif is_plamo:
                    zh,meta=bounded_translate(lambda:model.translate(ja))
                else:
                    zh,meta=bounded_translate(lambda:model.translate(session,ja)),dict(finish_reason="unknown: Hy wrapper does not expose stop token")
                elapsed=(time.monotonic_ns()-started)/1e9
                if elapsed>30:
                    raise TimeoutError("MT request exceeded preregistered 30s budget")
                filtered=None if meta["finish_reason"] == "length" else chinese(zh)
                log.record("mt_end",session_id=row["clip"],raw_zh=zh,filtered_zh=filtered,generation_s=elapsed,
                    mlx_active_bytes=mx.get_active_memory(),mlx_peak_bytes=mx.get_peak_memory(),**span,**meta)
                translated.append(row | dict(input_ja=ja,text_zh=zh,filtered_zh=filtered,mt_s=elapsed,**meta))
                write_rows(out / f"row-seed{seed}-{len(translated):02d}.jsonl",[translated[-1]])
                print(json.dumps(dict(stage=experiment,seed=seed,row=len(translated),mt_s=round(elapsed,3))),flush=True)
            write_rows(out / f"mt-seed{seed}.jsonl",translated)
            if experiment=="B0-CURRENT":
                expected=rows(run / f"inputs/mt-seed{seed}.jsonl")
                parity[str(seed)]=all((a["input_ja"],a["text_zh"])==(b["input_ja"],b["text_zh"]) for a,b in zip(translated,expected))
        finally:
            log.close();mark(f"mt-seed{seed}","end")
    if short_controls or experiment in ("PAD-900","B0-CURRENT"):
        diagnostics=[];mark("short-controls","start")
        for seed in (11,29):
            for row in all_asr:
                if not row["clip"].startswith("first-"):
                    continue
                mx.random.seed(seed+row["mt_seed_index"])
                zh=bounded_translate(lambda:model.translate(session,row["text_ja"])) if row["text_ja"] else ""
                diagnostics.append(row | dict(seed=seed,text_zh=zh,filtered_zh=chinese(zh)))
        write_rows(out / "short-controls.jsonl",diagnostics);mark("short-controls","end")
    if not is_plamo:
        session.close()
    if parity and not all(parity.values()):
        raise ValueError("paired current M0 parity drift")
    dump(out / "probe.json",dict(experiment_id=experiment,rows_per_seed=62,seeds=[11,29],parity=parity,
        timing="offline fixed-text only",full_chain_resource_pass=False,human_verified=False))
    return dict(rows_per_seed=62,parity=parity)


def probe(run, experiment, mode):
    if experiment not in (*IDS,"B0-CURRENT") or mode not in ("fixed-cuts","fixed-text"):
        raise NotImplementedError("B1 only implements four offline candidates and paired current control")
    if experiment!="B0-CURRENT":
        steps=json.loads((run / "state.json").read_text())["steps"]
        control=steps["B0-CURRENT-fixed-text"]
        if control["status"]!="passed" or not all(control["result"]["parity"].values()):
            raise ValueError("current paired control required")
        if experiment=="PAD-900" and mode=="fixed-text" and steps.get("PAD-900-fixed-cuts",{}).get("status")!="passed":
            raise ValueError("completed PAD ASR and silence probe required before MT")
    if mode=="fixed-cuts" and experiment!="PAD-900":
        raise NotImplementedError("only PAD-900 changes ASR")
    out=run / experiment
    out.mkdir(exist_ok=True)
    return execute(run,experiment+"-"+mode,lambda:pad_probe(run,out) if mode=="fixed-cuts" else text_probe(run,experiment,out),out)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,required=True)
    parser.add_argument("--experiment",required=True)
    parser.add_argument("--mode",required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(probe(args.run_dir.resolve(),args.experiment,args.mode)))
    except TimeoutError as exc:
        print(json.dumps(dict(status="timeout",error=str(exc),quality_eligible=False)))
        sys.exit(4)
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps(dict(status="invalid_evidence_or_runtime",error=str(exc),quality_eligible=False)))
        sys.exit(2)
