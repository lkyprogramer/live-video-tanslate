"""Bounded, reproducible probes for docs/exploration-plan.md (experimental only)."""

import argparse
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import select
import struct
import time
import wave

import numpy as np
import sherpa_onnx

from monitor import mark
from next_asr import read_rows, write_rows
from review import P0, percentile
from subtitle_service.local_pipeline import RATE, VAD_SHA, silero_config

ROOT = P0.parent
HISTORY = P0 / "evidence"


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def progress(run, stage, status, conclusion, artifacts):
    path = run / "progress.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[stage] = dict(status=status, conclusion=conclusion, artifacts=artifacts,
                       updated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    dump(path, data)


def freeze(run):
    if (run/"run-settings.json").exists():
        raise FileExistsError("Use a fresh evidence directory; historical settings are immutable")
    out = run / "E0"
    out.mkdir(exist_ok=True, parents=True)
    original = HISTORY / "n0-next"
    recent = HISTORY / "n2-mt-greedy"
    frozen = json.loads((original / "frozen.json").read_text())
    inputs = {str(P0 / "fixtures" / row["file"]): row["sha256"] for row in frozen["inputs"]}
    inputs.update({str(original / "reazon.jsonl"): "49da6a970940d2bf298eed6a2afa2ea1cbc65077dadbfa79d000a9940be3adb4",
                   str(original / "final-references/units.jsonl"): "735af958d2c8e48673f79dbf6d1f3354192ab1d603d04722d5d01e4c4c365408",
                   str(ROOT / ".model-cache/reazonspeech-assets/silero_vad.onnx"): VAD_SHA})
    for path, expected in inputs.items():
        assert digest(Path(path)) == expected, path
    tracked = subprocess.check_output(["git", "ls-files", "-m", "-o", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    settings = dict(plan="docs/exploration-plan.md", plan_sha256=digest(ROOT / "docs/exploration-plan.md"),
                    commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    source_sha256={p: digest(ROOT/p) for p in tracked if (ROOT/p).is_file()},
                    inputs_sha256=inputs, runtime={p: importlib.metadata.version(p) for p in
                        ("sherpa-onnx", "mlx", "mlx-lm", "whisperlivekit")},
                    human_listening="deferred_by_user", blind_holdout="test/ sealed",
                    argv=sys.argv, frozen_config=frozen)
    dump(run / "run-settings.json", settings)
    dump(run / "frozen.json", frozen)
    verdict = json.loads((recent / "blind-verdict.json").read_text())
    blind = json.loads((recent / "review-blind.json").read_text())
    key = json.loads((recent / "review-key.json").read_text())
    latest = json.loads((recent / "mt-semantic-result.json").read_text())
    labels = {r["id"]: {k:v["zh"] for k,v in r["answers"].items()} for r in blind["slices"]}
    results = {}
    for seed in (11,29):
        base = read_rows(original / f"mt-reazon-m0-{seed}.jsonl")
        hit = read_rows(HISTORY / "n2-mt/1p8b" / f"mt-reazon-m2-hit-{seed}.jsonl")
        unchanged, changed, no_new_errors = [], [], True
        for a,b in zip(base,hit,strict=True):
            assert all(a[k]==b[k] for k in ("clip","segment_id","input_ja"))
            rid = f"{a['clip']}/{a['segment_id']}"
            (unchanged if a["text_zh"]==b["text_zh"] else changed).append(rid)
        usable = set(verdict["always_usable"])
        for unit, rule in verdict["conditional"].items():
            rid = rule["slice"]
            row = next(r for r in hit if f"{r['clip']}/{r['segment_id']}"==rid)
            assert row["text_zh"] == next(r for r in base if f"{r['clip']}/{r['segment_id']}"==rid)["text_zh"]
            if key[rid][f"m0-{seed}"] in rule["usable_labels"]:
                usable.add(unit)
        # Existing text-reviewed restorations; both differences are written into the audit below.
        usable.update(("csj-reproduction/1", "csj-task/30"))
        for a,b in zip(base,hit,strict=True):
            if a["text_zh"]!=b["text_zh"]:
                changed_row = dict(id=f"{a['clip']}/{a['segment_id']}", ja=a["input_ja"],
                                   baseline=a["text_zh"], candidate=b["text_zh"], review="ai_reviewed")
                with (out / "m2-hit-differences.jsonl").open("a") as file:
                    file.write(json.dumps(changed_row,ensure_ascii=False)+"\n")
        baseline = set(latest["outcomes"][f"m0-{seed}"]["usable"])
        assert len(unchanged)==58 and len(changed)==4
        results[str(seed)] = dict(baseline=len(baseline), m2_hit=len(usable),
            historical_baseline=20, historical_m2_hit=22, usable=sorted(usable),
            restored=sorted(usable-baseline), lost=sorted(baseline-usable),
            net=len(usable)-len(baseline), unchanged=unchanged, changed=changed,
            review="ai_reviewed", new_critical_errors=[], formal_gate=False)
    dump(out / "baseline-score.json", results)
    dump(out / "pending-listen.json", json.loads((recent / "pending-listen.json").read_text()))
    units = read_rows(original / "final-references/units.jsonl")
    dump(out / "reference-manifest.json", dict(denominator=34, units=units,
        adjudication_sha256=digest(recent / "blind-verdict.json"), review="ai_reviewed",
        reference_status="historical", timing_sensitivity={"wmf-04/153-155":[119.6,126.04]},
        human_verified=False, small_gain_diagnostic_allowed=True))
    # Recompute stored time scores directly from persisted arrival/end pairings.
    old_timing = json.loads((recent / "timing-rescore.json").read_text())
    recalculated = {}
    for name,data in old_timing.items():
        recalculated[name] = {}
        for title,field in (("historical","old_end_s"),("sensitivity","provisional_end_s")):
            delays=[r["received_s"]-r[field] for r in data["rows"] if r["received_s"] is not None]
            matched=[max(0,d) for d in delays if d<=15]
            metrics=dict(units=len(data["rows"]), **{f"within_{n}":sum(d<=n for d in delays) for n in (2,3,5)},
                         within_15=len(matched), matched_p95_s=percentile(matched,.95))
            assert metrics==data[title], (name,title)
            recalculated[name][title]=metrics
    dump(out / "timing-rescore.json", recalculated)
    with wave.open(str(out/"wmf-04-153-155.wav"),"wb") as target:
        target.setparams((1,2,RATE,0,"NONE","not compressed"))
        target.writeframes((P0/"fixtures/wmf-04.pcm").read_bytes()[round(110*RATE)*2:round(128*RATE)*2])
    dump(out / "new-development-status.json", dict(status="pending", max_units=20,
        eligible_local_material="CSJ interview and non-main WMF clips; previously used for regression, not blind",
        independent_blind_material_available=False, test_accessed=False))
    progress(run,"E0","passed","自动冻结和复算完成；m2-hit为22/20，各净增2，仅可作小收益诊断。15项待复听。",["E0/reference-manifest.json","E0/baseline-score.json","E0/pending-listen.json","E0/timing-rescore.json"])
    print(json.dumps(results,ensure_ascii=False),flush=True)


class WindowVAD:
    """Feed complete model windows while retaining actual packet arrival clocks."""
    def __init__(self, config, windowed, pad_tail=False):
        self.vad=sherpa_onnx.VoiceActivityDetector(config,buffer_size_in_seconds=120)
        self.windowed=windowed
        self.pad_tail=pad_tail
        self.pending=np.zeros(0,np.float32)
        self.received=self.processed=self.padded=0
        self.finished=False

    def feed(self, pcm=None):
        assert pcm is None or not self.finished
        out=[]
        def drain():
            while not self.vad.empty():
                segment=self.vad.front
                a=segment.start
                b=min(self.received,a+len(segment.samples))
                if b>a:
                    out.append((a,b,np.asarray(segment.samples[:b-a],np.float32)))
                self.vad.pop()
        if pcm is not None:
            self.received+=len(pcm)
            if self.windowed:
                self.pending=np.concatenate((self.pending,pcm))
                while len(self.pending)>=512:
                    self.vad.accept_waveform(self.pending[:512])
                    self.pending=self.pending[512:]
                    self.processed+=512
                    drain()
            else:
                self.vad.accept_waveform(pcm)
                self.processed=(self.received//512)*512
                drain()
        elif not self.finished:
            if self.pad_tail and len(self.pending):
                tail=len(self.pending)
                self.vad.accept_waveform(np.pad(self.pending,(0,512-tail)))
                self.processed+=tail
                self.padded+=512-tail
                self.pending=np.zeros(0,np.float32)
                drain()
            self.vad.flush()
            self.finished=True
            drain()
        return out

    def accounting(self):
        return dict(received=self.received,processed=self.processed,residual=self.received-self.processed,padded=self.padded)


def vad_probe(run):
    out=run/"E1"; out.mkdir(exist_ok=True)
    progress(run,"E1","running","比较3200整包和512窗口，保持原生EOF。",[])
    dump(out/"hypothesis.json",dict(hypothesis="Packet-level OR aggregation changes speech endpoints; flush omits residual samples",
        control="3200 sample accept_waveform", variable="512-sample FIFO, then independent EOF padding",
        unchanged="Silero v4, 600ms, true arrival clock, same PCM", budget_s=7200))
    config=silero_config(ROOT/".model-cache/reazonspeech-assets/silero_vad.onnx",600)
    original=HISTORY/"n0-next"
    manifest=json.loads((run/"frozen.json").read_text())
    previous=read_rows(original/"slices.jsonl")
    summary={}
    for mode in ("packet","window","window-tail"):
        cuts=[]; accounts=[]
        mark(mode,"start")
        for item in manifest["inputs"]:
            path=P0/"fixtures"/item["file"]
            audio=np.frombuffer(path.read_bytes(),dtype="<i2").astype(np.float32)/32768
            vad=WindowVAD(config,mode!="packet",mode=="window-tail")
            for offset in [*range(0,len(audio),3200),None]:
                chunks=vad.feed(audio[offset:offset+3200] if offset is not None else None)
                for a,b,samples in chunks:
                    assert np.array_equal(audio[a:b],samples)
                    cuts.append(dict(clip=item["id"],segment_id=1+sum(r["clip"]==item["id"] for r in cuts),
                        start_sample=a,end_sample=b,released_at_sample=vad.received,pcm_path=str(path),
                        flush=offset is None,pcm_sha256=sha256(samples.astype("<f4").tobytes()).hexdigest()))
            assert vad.feed(None)==[]
            accounts.append(dict(clip=item["id"],**vad.accounting()))
        mark(mode,"end")
        write_rows(out/f"{mode}-slices.jsonl",cuts)
        fields=("clip","segment_id","start_sample","end_sample","released_at_sample","flush")
        if mode=="packet":
            assert len(cuts)==len(previous)==88
            assert all(tuple(a[k] for k in fields)==tuple(b[k] for k in fields) for a,b in zip(cuts,previous,strict=True))
        summary[mode]=dict(cuts=len(cuts),accounting=accounts,
            audio_s=sum(r["end_sample"]-r["start_sample"] for r in cuts)/RATE)
    # Packet shape invariance applies only to content/window sequence, not wall-clock availability.
    checks=[]
    audio=np.frombuffer((P0/"fixtures/csj-task.pcm").read_bytes(),dtype="<i2").astype(np.float32)/32768
    for tail in (511,512,513):
        # 25600 is divisible by both the network packet and the model window.
        data=audio[:25600+tail]
        signatures=[]
        for pattern in ([3200],[512],[1,731,4096,53,160]):
            vad=WindowVAD(config,True,True); got=[]; offset=0; i=0
            while offset<len(data):
                n=pattern[i%len(pattern)]; got+=vad.feed(data[offset:offset+n]); offset+=n; i+=1
            got+=vad.feed(None)
            assert vad.feed(None)==[]
            assert vad.accounting()["received"]==vad.accounting()["processed"]==len(data)
            signatures.append([(a,b,sha256(s.astype("<f4").tobytes()).hexdigest()) for a,b,s in got])
            checks.append(dict(base=25600,tail=tail,pattern=pattern,**vad.accounting()))
        assert signatures[0]==signatures[1]==signatures[2]
    dump(out/"tail-invariance.json",checks)
    dump(out/"vad-granularity.json",summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def decode_cuts(run, stage, name):
    from subtitle_service.local_pipeline import MODEL_FILES
    out=run/stage
    frozen=json.loads((run/"frozen.json").read_text())
    snapshot=ROOT/".model-cache/hub/models--reazon-research--reazonspeech-k2-v2/snapshots"/frozen["reazon_revision"]
    model=sherpa_onnx.OfflineRecognizer.from_transducer(
        **{k:str(snapshot/v) for k,v in zip(("encoder","decoder","joiner","tokens"),MODEL_FILES)},
        num_threads=2, sample_rate=RATE,provider="cpu")
    def transcribe(samples):
        stream=model.create_stream(); stream.accept_waveform(RATE,samples); model.decode_stream(stream)
        return stream.result.text.strip()
    transcribe(np.zeros(RATE,np.float32))
    old=read_rows(HISTORY/"n0-next/reazon.jsonl")
    stable={(r["clip"],r["start_sample"],r["end_sample"],r["text_ja"]):i for i,r in enumerate(old)}
    rows=read_rows(out/f"{name}-slices.jsonl")
    mark(f"{name}-asr","start")
    def results():
        for row in rows:
            samples=np.frombuffer(Path(row["pcm_path"]).read_bytes(),dtype="<i2")[row["start_sample"]:row["end_sample"]].astype(np.float32)/32768
            start=time.perf_counter(); ja=transcribe(samples); elapsed=time.perf_counter()-start
            key=(row["clip"],row["start_sample"],row["end_sample"],ja)
            index=stable.get(key,int(sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:7],16)+100000)
            yield row|dict(text_ja=ja,decode_s=elapsed,mt_seed_index=index)
    write_rows(out/f"{name}.jsonl",results())
    mark(f"{name}-asr","end")
    print(name,"decoded",len(rows),flush=True)


def translate_cuts(run, stage, name):
    from next_mt import translate
    out=run/stage/name; out.mkdir()
    (out/"frozen.json").write_bytes((run/"frozen.json").read_bytes())
    (out/"reazon.jsonl").write_bytes((run/stage/f"{name}.jsonl").read_bytes())
    (out/"units.jsonl").write_bytes((HISTORY/"n0-next/final-references/units.jsonl").read_bytes())
    translate(out,ROOT/".model-cache",["reazon"],["m0"])


def whisperkit_probe(run):
    out=run/"E2"
    progress(run,"E2","running","原构建、诊断构建和windowClipTime=0逐切片对照。",[])
    dump(out/"hypothesis.json",dict(hypothesis="Subsecond cuts do not enter decoder; longer empties need raw token evidence",
        variable="windowClipTime:1 to 0", preserved="WhisperKit1.1.1, CoreML small, Japanese, no fallback",
        no_speech_prob="fixed library constant 0; gate unchanged", budget_s=7200))
    model=ROOT/".model-cache/hub/models--argmaxinc--whisperkit-coreml/snapshots/0f63a7800b00dd0226abd051b906c246e1907482/openai_whisper-small"
    tokenizer=ROOT/".model-cache/hub/models--openai--whisper-small/snapshots/973afd24965f72e36ca33b3055d56a652f456b4d"
    source=json.loads((HISTORY/"n2-whisperkit/whisperkit-asr.json").read_text())
    rows=[dict(r,clip=clip,pcm_path=str(P0/"fixtures"/f"{clip}.pcm")) for clip,rr in source.items() for r in rr]
    assert len(rows)==43 and sum(not r["text_ja"] for r in rows)==19
    silence=dict(clip="silence",segment_id=1,start_sample=0,end_sample=RATE*10,
                 pcm_path=str(P0/"fixtures/silence.pcm"),text_ja="")
    fresh=ROOT/"p0/whisperkit/.build/out/Products/Release/WhisperKitProbe"
    dump(out/"binary-sha256.json",{str(p):digest(p) for p in (out/"WhisperKitProbe-original",fresh)})
    for mode in ("original","audit","window0"):
        binary=out/"WhisperKitProbe-original" if mode=="original" else fresh
        env=os.environ|dict(P0_WHISPERKIT_AUDIT="0" if mode=="original" else "1",P0_WHISPERKIT_WINDOW_CLIP="0" if mode=="window0" else "1")
        with (out/f"{mode}-stderr.log").open("w") as stderr:
            worker=subprocess.Popen([str(binary),str(model),str(tokenizer)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,env=env)
            def receive(timeout):
                if not select.select([worker.stdout],[],[],timeout)[0]:
                    raise TimeoutError("WhisperKit response")
                line=worker.stdout.readline()
                if not line: raise RuntimeError(f"worker exited {worker.poll()}")
                return json.loads(line)
            try:
                assert receive(180)==dict(ready=True)
                mark(mode,"start")
                def infer():
                    for row in [*rows,silence]:
                        samples=np.frombuffer(Path(row["pcm_path"]).read_bytes(),dtype="<i2")[row["start_sample"]:row["end_sample"]].astype("<f4")/32768
                        started=time.perf_counter()
                        worker.stdin.write(struct.pack("<I",len(samples))+samples.tobytes()); worker.stdin.flush()
                        result=receive(30)
                        yield row|dict(previous_text=row["text_ja"],text_ja=result["text"],decode_s=time.perf_counter()-started,audit=result.get("audit"))
                write_rows(out/f"{mode}.jsonl",infer())
                mark(mode,"end")
            finally:
                worker.stdin.close()
                try: worker.wait(5)
                except subprocess.TimeoutExpired: worker.kill(); worker.wait()
                worker.stdout.close()
        print(mode,"complete",flush=True)
    control=read_rows(out/"original.jsonl"); audit=read_rows(out/"audit.jsonl"); candidate=read_rows(out/"window0.jsonl")
    parity=[dict(clip=a["clip"],segment_id=a["segment_id"],old=a["text_ja"],audit=b["text_ja"]) for a,b in zip(control,audit,strict=True) if a["text_ja"]!=b["text_ja"]]
    assert not parity, parity
    findings=[]
    for a,b in zip(audit,candidate,strict=True):
        if a["previous_text"] or a["clip"]=="silence": continue
        windows=a["audit"]["windows"]
        reason=("window_clip_prevents_decoder" if not windows else
                "decoder_emits_no_word_tokens" if not any(w["word_text"].strip() for w in windows) else
                "seek_or_segment_postprocessing_drops_words")
        findings.append(dict(clip=a["clip"],segment_id=a["segment_id"],samples=a["end_sample"]-a["start_sample"],
            reason=reason,control=a["text_ja"],window0=b["text_ja"],control_audit=a["audit"],candidate_audit=b["audit"]))
    dump(out/"empty-result-audit.json",dict(original_empty=19,audited=len(findings),instrumentation_parity=True,
        reproduced_historical=sum(a["text_ja"]==a["previous_text"] for a in control[:43]),
        candidate_empty=sum(not a["text_ja"] for a in candidate[:43]),silence=candidate[-1]["text_ja"],items=findings))
    print("audit",len(findings),"remaining empty",sum(not a["text_ja"] for a in candidate[:43]),flush=True)


def vad_models(run, output_tag="final", only=None, max_frames=400):
    import importlib.util
    import onnxruntime as ort
    import kaldiio
    import kaldi_native_fbank as knf
    out=run/"E3"/output_tag; out.mkdir(exist_ok=True)
    progress(run,"E3","running","固定FIFO与真实PCM到达时钟，运行v6.2及FireRed Stream。",[])
    cache=ROOT/".model-cache/exploration-sources"
    dump(out/"hypothesis.json",dict(variable="VAD model",control="Silero v4 FIFO512 padded EOF from E1",
        promotion_control="original packet pipeline20/18",pause_ms=600,budget_s=7200,
        FireRed=dict(runtime="official packed-cache ONNX, CPU1",threshold=.5,smooth_frames=5,min_speech_frames=8,
                     max_speech_frames=max_frames,min_silence_frames=60,lookahead_samples=240),
        model_sha256={p.name:digest(p) for p in [cache/"silero-v6.2.onnx",cache/"firered-stream.onnx",cache/"firered-cmvn.ark"]}))
    spec=importlib.util.spec_from_file_location("firered_post",cache/"stream_vad_postprocessor-core.txt")
    # The downloaded reference is plain Python with a .txt evidence suffix.
    from importlib.machinery import SourceFileLoader
    spec=importlib.util.spec_from_loader("firered_post",SourceFileLoader("firered_post",str(cache/"stream_vad_postprocessor-core.txt")))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    options=knf.FbankOptions()
    options.frame_opts.samp_freq=16000
    options.frame_opts.frame_length_ms=25
    options.frame_opts.frame_shift_ms=10
    options.frame_opts.snip_edges=True
    options.frame_opts.dither=0
    options.mel_opts.num_bins=80
    options.mel_opts.debug_mel=False
    stats=kaldiio.load_mat(str(cache/"firered-cmvn.ark"))
    count=stats[0,-1]; means=stats[0,:-1]/count
    invstd=1/np.sqrt(np.maximum(stats[1,:-1]/count-means**2,1e-20))
    ort_options=ort.SessionOptions();ort_options.intra_op_num_threads=1;ort_options.inter_op_num_threads=1
    model=ort.InferenceSession(str(cache/"firered-stream.onnx"),sess_options=ort_options,providers=["CPUExecutionProvider"])
    summary={}
    for name in ([only] if only else ("silero-v6","firered")):
        cuts=[]; probabilities=[]; feature_parity=[]
        mark(name,"start")
        for item in json.loads((run/"frozen.json").read_text())["inputs"]:
            path=P0/"fixtures"/item["file"]
            raw=np.frombuffer(path.read_bytes(),dtype="<i2")
            audio=raw.astype(np.float32)/32768
            if name=="silero-v6":
                vad=WindowVAD(silero_config(cache/"silero-v6.2.onnx",600),True,True)
                spans=[]
                for offset in [*range(0,len(audio),3200),None]:
                    spans.extend((a,b,vad.received,offset is None) for a,b,_ in vad.feed(audio[offset:offset+3200] if offset is not None else None))
            else:
                fbank=knf.OnlineFbank(options);whole=knf.OnlineFbank(options)
                eof_pad=240+(-len(raw))%160
                whole.accept_waveform(RATE,np.pad(raw.astype(np.float32),(0,eof_pad)).tolist())
                state=np.zeros((8,1,128,19),np.float32)
                post=module.StreamVadPostprocessor(5,.5,5,8,max_frames,60)
                index=0;spans=[];features=[];start=None
                for offset in [*range(0,len(raw),3200),None]:
                    received=min(offset+3200,len(raw)) if offset is not None else len(raw)
                    chunk=raw[offset:received].astype(np.float32) if offset is not None else np.zeros(eof_pad,np.float32)
                    fbank.accept_waveform(RATE,chunk.tolist())
                    if fbank.num_frames_ready==index: continue
                    feat=np.vstack([fbank.get_frame(i) for i in range(index,fbank.num_frames_ready)])
                    features.append(feat)
                    probs,state=model.run(None,dict(feat=((feat-means)*invstd).astype(np.float32)[None],caches_in=state))
                    for j,prob in enumerate(probs.reshape(-1)):
                        result=post.process_one_frame(float(prob))
                        probabilities.append(dict(clip=item["id"],frame=index+j,prob=float(prob),received_sample=received))
                        if result.is_speech_start: start=max(0,(result.speech_start_frame-1)*160)
                        if result.is_speech_end and start is not None:
                            end=min(len(raw),(result.speech_end_frame-1)*160)
                            spans.append((start,end,received,False));start=None
                    index=fbank.num_frames_ready
                if start is not None: spans.append((start,len(raw),len(raw),True))
                merged=np.vstack(features)
                expected=np.vstack([whole.get_frame(i) for i in range(whole.num_frames_ready)])
                assert merged.shape==expected.shape and np.allclose(merged,expected,atol=1e-6)
                feature_parity.append(dict(clip=item["id"],whole_frames=len(expected),stream_frames=len(merged),max_abs=float(np.abs(merged-expected).max()),eof_padding=eof_pad))
            for a,b,received,flush in spans:
                cuts.append(dict(clip=item["id"],segment_id=1+sum(r["clip"]==item["id"] for r in cuts),
                    start_sample=a,end_sample=b,released_at_sample=received,flush=flush,pcm_path=str(path),pcm_sha256=digest_pcm(audio[a:b])))
        mark(name,"end")
        write_rows(out/f"{name}-slices.jsonl",cuts)
        if probabilities:write_rows(out/f"{name}-probabilities.jsonl",probabilities)
        summary[name]=dict(cuts=len(cuts),audio_s=sum(r["end_sample"]-r["start_sample"] for r in cuts)/RATE,
            silence_cuts=sum(r["clip"]=="silence" for r in cuts),feature_parity=feature_parity)
    dump(out/"vad-model-comparison.json",summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def digest_pcm(audio):
    return sha256(audio.astype("<f4").tobytes()).hexdigest()


def hotwords_probe(run):
    from subtitle_service.local_pipeline import MODEL_FILES
    out=run/"E4";out.mkdir(exist_ok=True)
    progress(run,"E4","running","冻结来自视频主题页面的词表，比较greedy/beam空/热词1.5和3。",[])
    metadata=ROOT/".model-cache/exploration-sources/hotwords-source.html"
    terms=["運動憲章","憲章","ウィキメディア"]
    assert all(term in metadata.read_text() for term in terms)
    frozen=json.loads((run/"frozen.json").read_text())
    snapshot=ROOT/".model-cache/hub/models--reazon-research--reazonspeech-k2-v2/snapshots"/frozen["reazon_revision"]
    tokens={line.rsplit(None,1)[0] for line in (snapshot/"tokens.txt").read_text().splitlines()}
    assert all(c in tokens for term in terms for c in term),"Unencodable hotword"
    dump(out/"hotwords-frozen.json",dict(terms=terms,source="https://meta.wikimedia.org/wiki/Movement_Charter/ja",
        source_sha256=digest(metadata),source_role="video theme metadata; no reference-derived names",
        apply_clips=["wmf-04","first-europe","first-slides"],negative_control="same terms explicitly applied to first-photo/first-strangers",
        token_encoding="cjkchar",token_coverage=True,thresholds=[1.5,3.0],budget_s=7200))
    rows=read_rows(HISTORY/"n0-next/slices.jsonl")
    old=read_rows(HISTORY/"n0-next/reazon.jsonl")
    stable={(r["clip"],r["start_sample"],r["end_sample"],r["text_ja"]):i for i,r in enumerate(old)}
    for name,method,score in (("greedy","greedy_search",0),("beam-empty","modified_beam_search",1.5),("hot1p5","modified_beam_search",1.5),("hot3","modified_beam_search",3.0)):
        model=sherpa_onnx.OfflineRecognizer.from_transducer(
            **{k:str(snapshot/v) for k,v in zip(("encoder","decoder","joiner","tokens"),MODEL_FILES)},
            num_threads=2,sample_rate=RATE,provider="cpu",decoding_method=method,max_active_paths=4,
            hotwords_score=score,modeling_unit="cjkchar")
        def transcribe(audio,words=""):
            stream=model.create_stream(hotwords=words)
            stream.accept_waveform(RATE,audio);model.decode_stream(stream)
            return stream.result.text.strip()
        transcribe(np.zeros(RATE,np.float32))
        mark(name,"start")
        def infer():
            for row in rows:
                words="/".join(terms) if name.startswith("hot") and row["clip"] in ("wmf-04","first-europe","first-slides") else ""
                audio=np.frombuffer(Path(row["pcm_path"]).read_bytes(),dtype="<i2")[row["start_sample"]:row["end_sample"]].astype(np.float32)/32768
                started=time.perf_counter();ja=transcribe(audio,words);elapsed=time.perf_counter()-started
                key=(row["clip"],row["start_sample"],row["end_sample"],ja)
                index=stable.get(key,int(sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:7],16)+100000)
                yield row|dict(text_ja=ja,decode_s=elapsed,mt_seed_index=index,hotwords=words)
        write_rows(out/f"{name}.jsonl",infer())
        negative=[]
        if name.startswith("hot"):
            for row in rows:
                if row["clip"] not in ("first-photo","first-strangers"):continue
                audio=np.frombuffer(Path(row["pcm_path"]).read_bytes(),dtype="<i2")[row["start_sample"]:row["end_sample"]].astype(np.float32)/32768
                negative.append(row|dict(text_ja=transcribe(audio,"/".join(terms))))
            write_rows(out/f"{name}-negative.jsonl",negative)
        mark(name,"end")
        print(name,"decoded",flush=True)
    greedy=read_rows(out/"greedy.jsonl");empty=read_rows(out/"beam-empty.jsonl")
    assert all(a["text_ja"]==b["text_ja"] for a,b in zip(greedy,old,strict=True))
    result={}
    for name in ("hot1p5","hot3"):
        got=read_rows(out/f"{name}.jsonl")
        result[name]=dict(changed_vs_empty=[dict(clip=a["clip"],segment_id=a["segment_id"],empty=a["text_ja"],hot=b["text_ja"]) for a,b in zip(empty,got,strict=True) if a["text_ja"]!=b["text_ja"]])
    dump(out/"hotword-comparison.json",result)


def freeze_development(run):
    from next_asr import csj_transcript, japanese_srt
    from review import references
    out=run/"E0"
    suite={r["id"]:r for r in json.loads((P0/"fixtures/suite.json").read_text())}
    transcript=csj_transcript(P0/"fixtures/source/csj-interview_aps.txt")
    groups=[(13,14,"请解释副语言信息、共振峰和调音运动"),(19,22,"语言指词语，语言信息可简单理解为词语含义"),
            (24,33,"名词、助词、动词、助动词等教科书内容属于语言信息"),(35,42,"para的词源含义是在旁边、相邻")]
    units=[]
    for first,last,zh in groups:
        ja="。".join(transcript[i][2] for i in range(first,last+1) if i in transcript and transcript[i][2])
        units.append(dict(clip="csj-interview_aps",group=f"{first}-{last}",source_ja=ja,reference_zh=zh,
            reference_status="source_provisional",human_verified=False,review="ai_reviewed",route="source-text MT diagnostic; audio alignment unverified"))
    for clip in ("wmf-01","wmf-05"):
        ja=japanese_srt(P0/"fixtures/source/wmf-ja.srt",suite[clip]["source_offset"])
        candidates=[r for r in references(suite) if r[0]==clip][:8]
        for _,group,start,end,zh in candidates:
            source=" ".join(r[2] for r in ja.values() if r[0]<end and r[1]>start)
            units.append(dict(clip=clip,group=group,source_ja=source,reference_zh=zh,
                start_sample=round(start*RATE),end_sample=round(end*RATE),reference_status="source_provisional",
                human_verified=False,route="source-text MT diagnostic; subtitle alignment provisional"))
    assert len(units)==20
    write_rows(out/"development-units.jsonl",units)
    dump(out/"new-development-status.json",dict(status="frozen",units=20,test_accessed=False,blind=False,
        sources=["CSJ published interview transcript","WMF other sections"],
        limitation="Existing audio previously used in broad regressions. Independent from 34-unit tuning, not unseen-source generalization. Source-text MT diagnostic only; actual-ASR validation remains separate.",
        glossary_natural_hits=[dict(clip=r["clip"],group=r["group"]) for r in units if any(t in r["source_ja"] for t in ("パラ言語情報","非言語情報","運動憲章","片仮名","カタカナ","オフサイド"))]))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run",type=Path)
    parser.add_argument("stage",choices=("E0","E1","E2","E3","E4"))
    parser.add_argument("--step",choices=("probe","decode","translate","freeze-dev"),default="probe")
    parser.add_argument("--name")
    parser.add_argument("--variant",choices=("final","official-default"),default="final")
    args=parser.parse_args()
    if args.step=="probe":
        if args.stage=="E3" and args.name=="firered-default":
            vad_models(args.run,output_tag="official-default",only="firered",max_frames=2000)
        else:
            {"E0":freeze,"E1":vad_probe,"E2":whisperkit_probe,"E3":vad_models,"E4":hotwords_probe}[args.stage](args.run)
    elif args.step=="freeze-dev":
        if args.stage!="E0":parser.error("freeze-dev belongs to E0")
        freeze_development(args.run)
    else:
        if not args.name:parser.error("--name is required")
        stage=f"E3/{args.variant}" if args.stage=="E3" else args.stage
        {"decode":decode_cuts,"translate":translate_cuts}[args.step](args.run,stage,args.name)
