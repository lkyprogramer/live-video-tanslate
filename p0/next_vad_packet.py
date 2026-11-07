"""Compare Silero v4/v6.2 with the original packet calls and native EOF."""

import argparse
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np

from exploration import ROOT, HISTORY, WindowVAD, decode_cuts, digest, dump
from monitor import mark
from next_asr import read_rows, write_rows
from subtitle_service.app import chinese
from subtitle_service.local_pipeline import RATE, MODEL_FILES, VAD_SHA, silero_config


def prepare(run):
    run.mkdir(parents=True,exist_ok=False)
    frozen=json.loads((HISTORY/"n0-next/frozen.json").read_text())
    inputs={str(ROOT/"p0/fixtures"/r["file"]):r["sha256"] for r in frozen["inputs"]}
    for name in ("slices.jsonl","reazon.jsonl","final-references/units.jsonl"):
        path=HISTORY/"n0-next"/name;inputs[str(path)]=digest(path)
    for path,expected in inputs.items():assert digest(Path(path))==expected,path
    models=dict(v4=ROOT/".model-cache/reazonspeech-assets/silero_vad.onnx",
                v6=ROOT/".model-cache/exploration-sources/silero-v6.2.onnx")
    assert digest(models["v4"])==VAD_SHA
    prior=json.loads((HISTORY/"exploration-20261107-0312/E3/final/hypothesis.json").read_text())
    assert digest(models["v6"])==prior["model_sha256"]["silero-v6.2.onnx"]
    settings=dict(hypothesis="v6.2 may behave differently with original packet aggregation than with FIFO",
        variable="VAD checkpoint only",arms=["v4","v6"],order=["v4","v6"],budget_s=7200,
        sample_rate=RATE,packet_samples=3200,eof="native flush; no padding",window_size=512,
        vad=dict(threshold=.5,min_speech_s=.15,min_silence_s=.6,max_speech_s=4),
        asr_threads=2,seeds=[11,29],gate=dict(min_net_each_seed=3,no_new_critical_errors=True),
        commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        runtime={p:version(p) for p in ("sherpa-onnx","numpy","mlx","mlx-lm","whisperlivekit","transformers")},
        input_sha256=inputs,vad_models={arm:dict(path=str(path),sha256=digest(path)) for arm,path in models.items()},
        v6_revision="be95df9152c0d7618fa1edfeb296fc3dae32376f",human_listening="deferred",holdout="test/ sealed",
        timing="offline; released_at_sample/rate is logical arrival, not measured 1x latency")
    for component,repo,revision,files in (
        ("asr","reazon-research--reazonspeech-k2-v2",frozen["reazon_revision"],MODEL_FILES),
        ("mt","mlx-community--Hy-MT2-1.8B-4bit",frozen["hy_revision"],["config.json","model.safetensors","tokenizer.json","tokenizer_config.json"])):
        base=ROOT/".model-cache/hub"/f"models--{repo}"/"snapshots"/revision
        settings[component]=dict(repo=repo,revision=revision,sha256={f:digest(base/f) for f in files})
    snapshot=run/"source-snapshot"
    source_files=[Path(__file__).resolve(),ROOT/"p0/exploration.py",ROOT/"p0/next_mt.py",ROOT/"p0/next_asr.py",ROOT/"p0/monitor.py",ROOT/"engine/src/subtitle_service/local_pipeline.py"]
    settings["source_sha256"]={str(p.relative_to(ROOT)):digest(p) for p in source_files}
    for path in source_files:
        target=snapshot/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    dump(run/"settings.json",settings);dump(run/"frozen.json",frozen)
    (run/"vad").mkdir()
    dump(run/"progress.json",dict(status="prepared",gate="offline screening before E6"))
    print("Frozen nine PCM clips, model hashes, native EOF, paired seeds and source snapshot.",flush=True)


def vad(run,arm):
    settings=json.loads((run/"settings.json").read_text());frozen=json.loads((run/"frozen.json").read_text())
    path=Path(settings["vad_models"][arm]["path"])
    assert digest(path)==settings["vad_models"][arm]["sha256"]
    config=silero_config(path,600);cuts=[];accounts=[]
    mark(arm+"-vad","start")
    for item in frozen["inputs"]:
        pcm=ROOT/"p0/fixtures"/item["file"]
        audio=np.frombuffer(pcm.read_bytes(),dtype="<i2").astype(np.float32)/32768
        detector=WindowVAD(config,windowed=False,pad_tail=False)
        segment_id=0
        for offset in [*range(0,len(audio),3200),None]:
            output=detector.feed(audio[offset:offset+3200] if offset is not None else None)
            for start,end,samples in output:
                segment_id+=1
                assert np.array_equal(samples,audio[start:end])
                cuts.append(dict(clip=item["id"],segment_id=segment_id,start_sample=start,end_sample=end,
                    released_at_sample=detector.received,released_logical_s=detector.received/RATE,
                    recorded_monotonic=time.monotonic(),flush=offset is None,pcm_path=str(pcm),
                    pcm_sha256=sha256(samples.astype("<f4").tobytes()).hexdigest()))
        assert detector.feed(None)==[]
        accounts.append(dict(clip=item["id"],**detector.accounting()))
    mark(arm+"-vad","end")
    write_rows(run/"vad"/f"{arm}-slices.jsonl",cuts)
    if arm=="v4":
        old=read_rows(HISTORY/"n0-next/slices.jsonl")
        fields=("clip","segment_id","start_sample","end_sample","released_at_sample","flush")
        assert len(old)==len(cuts)==88
        assert all(tuple(a[k] for k in fields)==tuple(b[k] for k in fields) for a,b in zip(cuts,old,strict=True))
    report=dict(cuts=len(cuts),speech_audio_s=sum(r["end_sample"]-r["start_sample"] for r in cuts)/RATE,
        input_audio_s=sum(r["received"] for r in accounts)/RATE,silence_cuts=sum(r["clip"]=="silence" for r in cuts),accounting=accounts)
    dump(run/"vad"/f"{arm}-summary.json",report)
    print(arm,json.dumps(report,ensure_ascii=False),flush=True)


def mt(run,arm):
    import next_mt
    from subtitle_service.local_pipeline import HyTranslator
    # Save the real WLK template and generation config from the same initialization as inference.
    # next_mt owns model loading below; wrap new_session without altering its behavior.
    original=HyTranslator.new_session
    def new_session(self):
        session=original(self)
        dump(run/"vad"/arm/"mt-settings.json",dict(prompt=session._prompt,generation=asdict(session._config)))
        return session
    HyTranslator.new_session=new_session
    original_mark=next_mt.mark
    next_mt.mark=lambda job,phase:mark(f"{arm}/{job}",phase)
    try:
        from exploration import translate_cuts
        translate_cuts(run,"vad",arm)
    finally:
        HyTranslator.new_session=original;next_mt.mark=original_mark
    if arm=="v4":
        for seed in (11,29):
            rows=read_rows(run/"vad"/arm/f"mt-reazon-m0-{seed}.jsonl")
            old=read_rows(HISTORY/"n0-next"/f"mt-reazon-m0-{seed}.jsonl")
            assert len(rows)==len(old)==62
            assert all(a["input_ja"]==b["input_ja"] and a["text_zh"]==b["text_zh"] for a,b in zip(rows,old,strict=True))
        dump(run/"baseline-parity.json",dict(text_matches={"11":62,"29":62},hits={"11":20,"29":18}))


def review(run):
    units=read_rows(HISTORY/"n0-next/final-references/units.jsonl")
    # Anonymize labels; reviewers still disclose AI text review and possible baseline recognition.
    labels={"A":"v6","B":"v4"};dump(run/"review-key.json",labels)
    runs={(label,seed):read_rows(run/"vad"/arm/f"mt-reazon-m0-{seed}.jsonl") for label,arm in labels.items() for seed in (11,29)}
    reviewed=[];lines=[]
    for unit in units:
        key=f"{unit['clip']}/{unit['group']}"
        end=round(126.04*RATE) if key=="wmf-04/153-155" else unit["end_sample"]
        candidates={}
        lines.append(f"\n{key}: {unit['reference_zh']}\nSOURCE {unit['source_ja']}")
        for (label,seed),rows in runs.items():
            related=[r for r in rows if r["clip"]==unit["clip"] and r["start_sample"]<end and r["end_sample"]>unit["start_sample"]]
            outputs=[dict(id=f"{r['clip']}/{r['segment_id']}",start_sample=r["start_sample"],end_sample=r["end_sample"],
                input_ja=r["input_ja"],text_zh=r["text_zh"],passes_gateway=chinese(r["text_zh"]) is not None) for r in related]
            candidates[f"{label}-{seed}"]=outputs
            lines.append(f"{label}-{seed}: "+" | ".join(f"{r['id']}: {r['text_zh']}"+(' [FILTERED]' if not r['passes_gateway'] else '') for r in outputs))
        reviewed.append(dict(unit=key,necessary_information=unit["reference_zh"],reference=unit,candidates=candidates))
    dump(run/"review.json",reviewed);(run/"review.txt").write_text("\n".join(lines)+"\n")
    print("Prepared 34 per-unit comparisons; no scores assigned automatically.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("run",type=Path)
    parser.add_argument("step",choices=("prepare","vad","asr","mt","review"));parser.add_argument("--arm",choices=("v4","v6"))
    args=parser.parse_args()
    if args.step in ("vad","asr","mt") and not args.arm:parser.error("--arm is required")
    if args.step=="prepare":prepare(args.run)
    elif args.step=="vad":vad(args.run,args.arm)
    elif args.step=="asr":decode_cuts(args.run,"vad",args.arm)
    elif args.step=="mt":mt(args.run,args.arm)
    else:review(args.run)
