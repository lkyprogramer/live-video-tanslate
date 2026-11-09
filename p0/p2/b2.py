"""Two B2 fixed-cut ASR candidates sharing the existing frozen MT/scoring path."""
from hashlib import sha256
from importlib.metadata import version
import importlib.util
import json
from pathlib import Path
import shutil
import time

from . import b1,b1_scoring
from .freeze import ROOT,PLAN,digest,dump,rows

IDS=("ASR-SENSEVOICE-I8","ASR-WHISPER-JA760")
PREVIOUS=ROOT/"p0/evidence/p2-b1-20261108-202438"


def runtime():
    return b1.runtime() | {p:version(p) for p in ("faster-whisper","ctranslate2","packaging")}


def verify(run):
    return b1.verify(run,runtime_reader=runtime)


def freeze(run,registry):
    import numpy as np
    if (run/"run-manifest.json").exists():
        return verify(run)
    (run/"locks").mkdir(exist_ok=True);(run/"inputs").mkdir(exist_ok=True)
    prior=json.loads((PREVIOUS/"decision.json").read_text())
    if any(c["Q"] or c["R_offline_eligible"] for c in prior["candidates"].values()):
        raise ValueError("B1 handoff differs from expected no-candidate outcome")
    for step in json.loads((PREVIOUS/"state.json").read_text())["steps"].values():
        if step["status"]!="passed":
            raise ValueError("Incomplete B1 handoff")
        for name,expected in step["outputs"].items():
            if digest(PREVIOUS/name)!=expected:
                raise ValueError("B1 raw receipt changed")
    dump(run/"locks/previous-phase.json",dict(run=str(PREVIOUS),decision_sha256=digest(PREVIOUS/"decision.json"),
        review_sha256=digest(PREVIOUS/"reviews/final-review.json"),preserved_historical_source_snapshot=True))
    paths=json.loads((run/"sources/model-paths.json").read_text());models=[]
    for key,name in (("sensevoice-i8","sensevoice"),("whisper-ja760-ct2","whisper-ja760")):
        snapshot=Path(paths[key]);files=sorted(p for p in snapshot.iterdir() if p.is_file())
        item=dict(id=key,source="release" if name=="sensevoice" else "hf",local_path=str(snapshot),
            revision=None if name=="sensevoice" else snapshot.name,
            repository="k2-fsa/sherpa-onnx:2024-07-17-int8" if name=="sensevoice" else "efwkjn/faster-whisper-ja-760M",
            files=[dict(path=str(p),sha256=digest(p)) for p in files])
        if name=="sensevoice":
            release=json.loads((run/"sources/sensevoice/release.json").read_text());item.update(release)
            archive=ROOT/".model-cache/p2-b2-release"/release["url"].rsplit("/",1)[-1]
            archive_hash=digest(archive)
            if archive_hash!=release["archive_sha256"] or archive_hash!=release["expected_release_digest"]:
                raise ValueError("SenseVoice official archive content lock mismatch")
            item["files"].append(dict(path=str(archive),sha256=archive_hash))
        models.append(item)
    # Frozen synthetic instrumental control: not evidence about natural music/BGM.
    rate=16000;t=np.arange(8*rate)/rate;melody=np.zeros_like(t)
    for i,freq in enumerate((261.63,329.63,392,523.25)*4):
        start=i*rate//2;end=(i+1)*rate//2;x=t[start:end]-i/2
        melody[start:end]=.16*np.sin(np.pi*x/.5)**2*sum(np.sin(2*np.pi*freq*h*x)/h for h in (1,2,3))
    music=run/"inputs/synthetic-instrumental.pcm";music.write_bytes((np.clip(melody,-1,1)*32767).astype("<i2").tobytes())
    source=rows(b1.PARENT/"baseline/reazon.jsonl")
    selected=[next(r for r in source if r["clip"]==clip and r["segment_id"]==sid) for clip,sid in (("csj-reproduction",1),("csj-task",7),("wmf-04",43))]
    dump(run/"inputs/probes.json",dict(speech=selected,controls=[dict(id="silence",path=str(ROOT/"p0/fixtures/silence.pcm"),sha256=digest(ROOT/"p0/fixtures/silence.pcm")),dict(id="synthetic-instrumental",path=str(music),sha256=digest(music))],
        natural_music_reference="NOT_AVAILABLE; synthetic control is not a natural-music benchmark"))
    import sherpa_onnx
    from subtitle_service.local_pipeline import silero_config,vad_segments
    from .runner import pcm_packets
    vad_path=next(Path(m["local_path"]) for m in json.loads((b1.PARENT/"locks/models.json").read_text())["models"] if m["id"]=="silero-v4-b0")
    guards={}
    for item in json.loads((run/"inputs/probes.json").read_text())["controls"]:
        detector=sherpa_onnx.VoiceActivityDetector(silero_config(vad_path,600),buffer_size_in_seconds=120)
        cuts=[]
        for _,frame in pcm_packets(Path(item["path"]).read_bytes()):
            cuts.extend((start,len(samples)) for start,samples in vad_segments(detector,frame))
        cuts.extend((start,len(samples)) for start,samples in vad_segments(detector,None))
        guards[item["id"]]=dict(released_cuts=cuts,source_sha256=item["sha256"])
    dump(run/"inputs/control-vad.json",dict(controls=guards,config="unchanged Silero v4 pause600ms",not_natural_music_evidence=True))
    extra=[]
    for package in ("sherpa_onnx","faster_whisper","ctranslate2","whisperlivekit"):
        base=Path(importlib.util.find_spec(package).origin).parent
        extra += [p for p in base.rglob("*") if p.is_file() and p.suffix in (".py",".so",".dylib")]
    extra += [p for p in (run/"sources").rglob("*") if p.is_file()]
    hypotheses=dict(ids=IDS,modes=["fixed-cuts","fixed-text"],seeds=[11,29],routes=["Q","R"],
        Q="net>=3 each round; new critical=0; same-round protected losses=0",
        R="no quality/protected/critical regression; primary full_chain_hot_process_tree_peak_footprint >=20% later B5, not offline proof",
        unchanged="original88 spans/VAD and Hy-MT2 M0; paired current Reazon88 exact and M0 two62 exact; changed JA range+text SHA stable seed; no reference correction",
        sensevoice=dict(language="ja",provider="cpu",threads=2,decoding_method="greedy_search",use_itn=True,metadata_to_mt=False),
        whisper=dict(language="ja",task="transcribe",device="cpu",compute_type="int8",cpu_threads=2,num_workers=1,beam_size=5,temperature=0,
            condition_on_previous_text=False,vad_filter=False,without_timestamps=True,max_new_tokens=256,
            native_cap="CT2v4.8.5 min((prompt_len+256)//2,257),usually130; rawlen>=cap unusable",
            suppress_tokens=[-1],suppress_blank=True,no_speech_threshold=.6,log_prob_threshold=-1.0,compression_ratio_threshold=2.4),
        budget=dict(per_candidate_s=10800,request_s=30,tree_stop_gib=12),holdout="sealed",human_verified=False)
    return b1.freeze(run,registry,phase="B2",model_additions=models,runtime_lock=dict(engine=runtime()),hypotheses=hypotheses,extra_sources=extra)


def seed_index(row,ja):
    if ja==row["text_ja"]:
        return row["mt_seed_index"]
    key=(row["clip"],row["start_sample"],row["end_sample"],ja)
    return int(sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:7],16)+100000


def asr_probe(run,experiment,out):
    import numpy as np
    from p0.monitor import mark
    from .events import EventLog
    from .adapters.sensevoice import SenseVoice
    from .adapters.whisper_ja import WhisperJapanese
    paths=b1.model_paths(run)
    mark("asr-load","start")
    if experiment=="B0-CURRENT":
        from types import SimpleNamespace
        from subtitle_service.config import load_settings
        from subtitle_service.local_pipeline import ReazonRecognizer
        recognizer=ReazonRecognizer(paths["reazon-b0"],paths["silero-v4-b0"],load_settings(run/"locks/profiles.toml").profiles["reazon-hy"].asr)
        model=SimpleNamespace(settings=dict(provider="cpu",threads=2,decoding_method="greedy_search",padding=False),
            decode=lambda samples:(recognizer.decode(samples),dict(usable=True,finish_reason="offline_decode_returned")))
    else:
        model=SenseVoice(paths["sensevoice-i8"]) if experiment==IDS[0] else WhisperJapanese(paths["whisper-ja760-ct2"])
    dump(out/"asr-settings.json",model.settings);mark("asr-load","end")
    probes=json.loads((run/"inputs/probes.json").read_text());smoke=[]
    mark("synthetic-and-speech-smoke","start")
    for item in probes["speech"]+probes["controls"]:
        control="path" in item;path=item["path"] if control else item["pcm_path"]
        samples=np.fromfile(path,dtype="<i2")
        if not control:samples=samples[item["start_sample"]:item["end_sample"]]
        started=time.monotonic_ns();text,meta=b1.bounded_translate(lambda:model.decode(samples.astype(np.float32)/32768))
        smoke.append(dict(id=item.get("id",item.get("source_id")),text_ja=text,metadata=meta,decode_s=(time.monotonic_ns()-started)/1e9))
        b1.write_rows(out/f"smoke-row-{len(smoke)}.jsonl",[smoke[-1]])
    dump(out/"smoke.json",dict(rows=smoke,quality_sample=False,natural_music_claim=False));mark("synthetic-and-speech-smoke","end")
    log=EventLog(out/"asr-events.jsonl",run.name,experiment_id=experiment);output=[]
    mark("asr-88","start")
    try:
        for row in rows(run/"baseline/reazon.jsonl"):
            pcm=np.fromfile(row["pcm_path"],dtype="<i2")[row["start_sample"]:row["end_sample"]]
            span={k:row[k] for k in ("source_id","segment_id","start_sample","end_sample")}
            log.record("asr_start",session_id=row["clip"],pcm_sha256=sha256(pcm.tobytes()).hexdigest(),**span)
            started=time.monotonic_ns();ja,metadata=b1.bounded_translate(lambda:model.decode(pcm.astype(np.float32)/32768))
            elapsed=(time.monotonic_ns()-started)/1e9;body=ja if metadata["usable"] else ""
            log.record("asr_end",session_id=row["clip"],text_ja=body,decode_s=elapsed,metadata=metadata,**span)
            item=row|dict(text_ja=body,raw_text_ja=ja,metadata=metadata,decode_s=elapsed,mt_seed_index=seed_index(row,body))
            output.append(item);b1.write_rows(out/f"asr-row-{len(output):02d}.jsonl",[item])
            print(json.dumps(dict(stage=experiment,row=len(output),decode_s=round(elapsed,3))),flush=True)
        b1.write_rows(out/"reazon.jsonl",output)
    finally:
        log.close();mark("asr-88","end")
    result=dict(rows=88,empty=sum(not r["text_ja"] for r in output),decode_total_s=sum(r["decode_s"] for r in output),
        changed=sum(a["text_ja"]!=b["text_ja"] for a,b in zip(output,rows(run/"baseline/reazon.jsonl"))),timing="offline fixed cuts")
    dump(out/"asr-summary.json",result)
    if experiment=="B0-CURRENT" and result["changed"]:
        raise ValueError("Paired current Reazon88 drift; no candidate may proceed")
    return result


def probe(run,experiment,mode):
    if experiment not in (*IDS,"B0-CURRENT") or mode not in ("fixed-cuts","fixed-text"):
        raise NotImplementedError("B2 implements only two fixed-cut/text ASR routes and current MT control")
    if experiment=="B0-CURRENT" and mode=="fixed-text":
        control=json.loads((run/"state.json").read_text())["steps"].get("B0-CURRENT-fixed-cuts",{})
        if control.get("status")!="passed":
            raise ValueError("Paired current Reazon ASR prerequisite required")
    if experiment!="B0-CURRENT":
        steps=json.loads((run/"state.json").read_text())["steps"];control=steps["B0-CURRENT-fixed-text"]
        if control["status"]!="passed" or not all(control["result"]["parity"].values()):
            raise ValueError("Exact paired current MT control required")
        if mode=="fixed-text" and steps.get(experiment+"-fixed-cuts",{}).get("status")!="passed":
            raise ValueError("Completed candidate ASR prerequisite required")
    out=run/experiment;out.mkdir(exist_ok=True)
    return b1.execute(run,experiment+"-"+mode,
        lambda:asr_probe(run,experiment,out) if mode=="fixed-cuts" else b1.text_probe(run,experiment,out,
            asr_path=out/"reazon.jsonl",short_controls=True),out,verify_run=verify)


def score_or_packet(run):
    if not (run/"reviews/adjudication.json").exists():
        return b1_scoring.packet(run,mapping={"A":IDS[1],"B":IDS[0]},verify_run=verify,phase="B2")
    return b1_scoring.score(run,experiments=IDS,verify_run=verify,phase="B2")
