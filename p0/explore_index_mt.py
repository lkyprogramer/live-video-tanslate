"""Run the pinned Index Q8 model through an isolated local llama.cpp server."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from monitor import mark

ROOT=Path(__file__).resolve().parent.parent
HISTORY=ROOT/"p0/evidence/n0-next"


def read(path):return [json.loads(line) for line in path.read_text().splitlines()]


def main(run):
    out=run/"E5/index";out.mkdir()
    source=ROOT/".model-cache/exploration-llama"
    model=ROOT/".model-cache/hub/models--IndexTeam--Index-Translate-2B-GGUF/snapshots/3a0eca6ce931d88d00614d427ab3ed8ff0743902/Index-Translate-2B.Q8_0.gguf"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1",0));port=reserve.getsockname()[1]
    command=[str(source/"build/bin/llama-server"),"-m",str(model),"--host","127.0.0.1","--port",str(port),
        "-c","4096","-np","1","-ngl","99","-t","2","--jinja","--chat-template-kwargs",'{"enable_thinking":false}',
        "--reasoning-budget","0","--alias","Index-Translate-2B-Q8"]
    template="请将以下文本翻译为简体中文，直接输出翻译结果，不要进行任何解释。\n\n{source}"
    settings=dict(model_revision="3a0eca6ce931d88d00614d427ab3ed8ff0743902",model_sha256=sha256(model.read_bytes()).hexdigest(),
        llama_revision=subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD"],text=True).strip(),
        binary_sha256=sha256((source/"build/bin/llama-server").read_bytes()).hexdigest(),command=command,
        template=template,temperature=0,top_p=1,max_tokens=256,cache_prompt=False,stop="native template EOS",
        source_status="source_provisional for C-route, historical fixed ASR for main gate",budget_s=10800)
    (out/"settings.json").write_text(json.dumps(settings,ensure_ascii=False,indent=2)+"\n")
    def get(path,payload=None):
        request=Request(f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type":"application/json"})
        with urlopen(request,timeout=60) as response:return json.load(response)
    with (out/"server.log").open("w") as log:
        mark("index-load","start")
        worker=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if worker.poll() is not None:raise RuntimeError(f"llama-server exited {worker.returncode}")
                try:
                    if get("/health").get("status")=="ok":break
                except URLError:time.sleep(.25)
            else:raise TimeoutError("llama startup")
            mark("index-load","end")
            def translate(ja,seed):
                start=time.perf_counter()
                result=get("/v1/chat/completions",dict(model="Index-Translate-2B-Q8",messages=[dict(role="user",content=template.format(source=ja))],
                    temperature=0,top_p=1,seed=seed,max_tokens=256,stream=False,cache_prompt=False,
                    chat_template_kwargs=dict(enable_thinking=False)))
                answer=result["choices"][0]
                return dict(text_zh=answer["message"].get("content") or "",reasoning=answer["message"].get("reasoning_content"),
                    mt_s=time.perf_counter()-start,finish_reason=answer["finish_reason"],usage=result.get("usage"),model=result.get("model"))
            mark("index-warmup","start")
            (out/"warmup.json").write_text(json.dumps(translate("これは日本語の音声認識の試験です。",11),ensure_ascii=False,indent=2)+"\n")
            mark("index-warmup","end")
            actual=[(i,r) for i,r in enumerate(read(HISTORY/"reazon.jsonl")) if r["clip"] in ("csj-task","csj-reproduction","wmf-04")]
            assert len(actual)==62
            for title,rows in [("actual-11",actual),("actual-29",actual),("source-11",list(enumerate(read(HISTORY/"final-references/units.jsonl"))))]:
                seed=29 if title=="actual-29" else 11
                mark(title,"start")
                with (out/f"{title}.jsonl").open("x",buffering=1) as file:
                    for index,row in rows:
                        ja=row.get("text_ja",row.get("source_ja",""))
                        result=translate(ja,seed+index)
                        file.write(json.dumps(row|dict(input_ja=ja,**result),ensure_ascii=False)+"\n")
                mark(title,"end")
                print(title,"complete",flush=True)
        finally:
            worker.terminate()
            try:worker.wait(10)
            except subprocess.TimeoutExpired:worker.kill();worker.wait()


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("run",type=Path)
    main(p.parse_args().run)
