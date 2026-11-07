"""Screen two pinned checkpoints without changing the service environment."""

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import time

import numpy as np

from monitor import mark

ROOT=Path(__file__).resolve().parent.parent
HISTORY=ROOT/"p0/evidence/n0-next"


def read(path):return [json.loads(line) for line in path.read_text().splitlines()]


def main(run,name):
    out=run/"E5"/name;out.mkdir()
    base=read(HISTORY/"reazon.jsonl")
    stable={(r["clip"],r["start_sample"],r["end_sample"],r["text_ja"]):i for i,r in enumerate(base)}
    revision={"granite":"1dd23f46f6d4e6eae2531982ba2e33220086dd97","ja-en":"12b44671ff48b9aed622551d64e02fea9a2cf870"}[name]
    repo={"granite":"ibm-granite--granite-speech-5.0-470m-turboctc","ja-en":"csukuangfj--reazonspeech-k2-v2-ja-en"}[name]
    snapshot=ROOT/".model-cache/hub"/f"models--{repo}"/"snapshots"/revision
    settings=dict(checkpoint=repo,revision=revision,precision="original checkpoint BF16/F32" if name=="granite" else "INT8 encoder/joiner + original FP32 decoder",
        input_sha256=sha256((HISTORY/"slices.jsonl").read_bytes()).hexdigest(),
        model_sha256={p.name:sha256(p.read_bytes()).hexdigest() for p in snapshot.iterdir() if p.is_file()},
        hypothesis="new multilingual checkpoint on exactly the original 88 cuts", budget_s=10800)
    mark(name+"-load","start")
    start=time.perf_counter()
    if name=="granite":
        import mlx.core as mx
        from mlx_audio.stt import load
        from mlx.utils import tree_flatten
        from collections import Counter
        settings["runtime"]={p:version(p) for p in ("mlx-audio","mlx","transformers","numpy")}
        model=load(snapshot)
        settings["loaded_parameter_dtypes"]=dict(Counter(str(v.dtype) for _,v in tree_flatten(model.parameters())))
        def transcribe(audio):return model.generate(audio).text.strip()
        settings["metadata_caveat"]="mlx-audio hardcodes result.language=en; assess actual Japanese tokens/text, not this metadata"
    else:
        import sherpa_onnx
        settings["runtime"]={p:version(p) for p in ("sherpa-onnx","numpy")}
        files=["encoder-epoch-35-avg-1.int8.onnx","decoder-epoch-35-avg-1.onnx","joiner-epoch-35-avg-1.int8.onnx","tokens.txt"]
        model=sherpa_onnx.OfflineRecognizer.from_transducer(
            **{k:str(snapshot/v) for k,v in zip(("encoder","decoder","joiner","tokens"),files)},num_threads=2,sample_rate=16000,provider="cpu")
        def transcribe(audio):
            stream=model.create_stream();stream.accept_waveform(16000,audio);model.decode_stream(stream)
            return stream.result.text.strip()
    settings["load_s"]=time.perf_counter()-start
    mark(name+"-load","end")
    mark(name+"-smoke","start")
    settings["silence_text"]=transcribe(np.zeros(160000,np.float32))
    audio=np.frombuffer((ROOT/"p0/fixtures/first-photo.pcm").read_bytes(),dtype="<i2").astype(np.float32)/32768
    settings["japanese_smoke"]=transcribe(audio)
    mark(name+"-smoke","end")
    (out/"settings.json").write_text(json.dumps(settings,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(settings|{"model_sha256":"see settings.json"},ensure_ascii=False),flush=True)
    mark(name,"start")
    with (out/"reazon.jsonl").open("x",buffering=1) as file:
        for row in read(HISTORY/"slices.jsonl"):
            audio=np.frombuffer(Path(row["pcm_path"]).read_bytes(),dtype="<i2")[row["start_sample"]:row["end_sample"]].astype(np.float32)/32768
            start=time.perf_counter();ja=transcribe(audio);elapsed=time.perf_counter()-start
            key=(row["clip"],row["start_sample"],row["end_sample"],ja)
            index=stable.get(key,int(sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:7],16)+100000)
            file.write(json.dumps(row|dict(text_ja=ja,decode_s=elapsed,mt_seed_index=index),ensure_ascii=False)+"\n")
    mark(name,"end")
    (out/"frozen.json").write_bytes((run/"frozen.json").read_bytes())
    (out/"units.jsonl").write_bytes((HISTORY/"final-references/units.jsonl").read_bytes())
    print(name,"decoded88",flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("run",type=Path);p.add_argument("name",choices=("granite","ja-en"))
    a=p.parse_args();main(a.run,a.name)
