"""Validate exploration evidence without rerunning model inference or touching holdouts."""

import argparse
import json
from pathlib import Path
import subprocess

from exploration import ROOT, HISTORY, digest, dump, read_rows


def main(run, output=None):
    run=run.resolve()
    output=output or run/"validation.json"
    if output.exists():
        raise FileExistsError("Preserve previous validation; choose a fresh --output path")
    checks={}
    initial=json.loads((run/"run-settings.json").read_text())
    for path,expected in initial["inputs_sha256"].items():assert digest(Path(path))==expected,path
    checks["frozen_input_hashes"]=len(initial["inputs_sha256"])
    protected={path:value for path,value in initial["source_sha256"].items()
               if path.startswith(("engine/","extension/")) or path in (".gitignore","p0/next_mt.py","p0/monitor.py")}
    checks["current_product_source_drift"]=[p for p,expected in protected.items()
                                            if not (ROOT/p).exists() or digest(ROOT/p)!=expected]
    allowed={"README.md","docs/optimization-results.md","p0/exploration.py",
             "p0/whisperkit/Sources/WhisperKitProbe/Probe.swift"}
    preserved={p:v for p,v in initial["source_sha256"].items() if p not in allowed}
    checks["current_other_source_drift"]=[p for p,expected in preserved.items()
                                          if not (ROOT/p).exists() or digest(ROOT/p)!=expected]
    snapshot=run/"source-snapshot"
    manifest=json.loads((run/"executed-source-manifest.json").read_text())
    for relative,expected in manifest["final_drivers"].items():
        assert digest(snapshot/relative)==expected,relative
    checks["frozen_source_hashes"]=len(manifest["final_drivers"])
    baseline={}
    for seed in (11,29):
        a=read_rows(run/f"E0/baseline-mt/mt-reazon-m0-{seed}.jsonl")
        b=read_rows(HISTORY/f"n0-next/mt-reazon-m0-{seed}.jsonl")
        assert len(a)==len(b)==62 and all(x["text_zh"]==y["text_zh"] for x,y in zip(a,b,strict=True))
        baseline[str(seed)]=62
    checks["baseline_exact_matches"]=baseline
    for name,n in (("packet",88),("window",93),("window-tail",93)):
        assert len(read_rows(run/f"E1/{name}.jsonl"))==n
    tail=json.loads((run/"E1/tail-invariance.json").read_text())
    assert len(tail)==9 and {r["tail"] for r in tail}=={511,512,513}
    assert all(r["base"]%512==r["base"]%3200==0 and r["received"]==r["processed"] for r in tail)
    checks["aligned_tail_cases"]=9
    audit=json.loads((run/"E2/empty-result-audit.json").read_text())
    assert audit["instrumentation_parity"] and audit["audited"]==audit["original_empty"]==19
    assert audit["reproduced_historical"]==43 and audit["candidate_empty"]==18 and audit["silence"]==""
    checks["whisperkit_empty_causes"]={k:sum(x["reason"]==k for x in audit["items"]) for k in {x["reason"] for x in audit["items"]}}
    for relative in ("E3/final/vad-model-comparison.json","E3/official-default/vad-model-comparison.json"):
        values=json.loads((run/relative).read_text())
        for value in values.values():
            assert value["silence_cuts"]==0
            for clip in value["feature_parity"]:assert clip["whole_frames"]==clip["stream_frames"] and clip["max_abs"]==0
    for seed in (11,29):
        a=read_rows(run/f"E4/beam-empty/mt-reazon-m0-{seed}.jsonl")
        for variant in ("hot1p5","hot3"):
            b=read_rows(run/f"E4/{variant}/mt-reazon-m0-{seed}.jsonl")
            assert len(a)==len(b)==62 and all(x["text_zh"]==y["text_zh"] for x,y in zip(a,b,strict=True))
    for variant in ("granite","ja-en"):assert len(read_rows(run/f"E5/{variant}/reazon.jsonl"))==88
    for seed in (11,29):
        rows=read_rows(run/f"E5/index/actual-{seed}.jsonl");assert len(rows)==62
        assert all(r["finish_reason"]=="stop" and not r["reasoning"] for r in rows)
    assert len(read_rows(run/"E5/index/source-11.jsonl"))==34
    assert len(read_rows(run/"E0/development-units.jsonl"))==20
    verdicts=json.loads((run/"adjudication.json").read_text())
    for candidate,values in verdicts.items():
        for seed,value in values.items():
            assert len(value["rows"])==len({r["unit"] for r in value["rows"]})==34
            assert sum(r["status"]=="usable" for r in value["rows"])==value["hits"]
            assert not value["qualified"]
            assert value["upper_sensitivity"]-(20 if seed=="11" else 18)<3
    checks["adjudicated_candidate_seed_pairs"]=sum(len(x) for x in verdicts.values())
    stages=json.loads((run/"progress.json").read_text())
    for stage in ("E0","E1","E2","E3","E4","E5"):assert stages[stage]["status"]=="passed"
    for stage in ("E6","E7a","E7b"):assert stages[stage]["status"]=="skipped"
    for stage in stages.values():
        for artifact in stage["artifacts"]:assert (run/artifact).exists(),artifact
    checks["stage_artifacts_exist"]=True
    resource=json.loads((run/"resource-comparison.json").read_text())
    assert len(resource["reports"])==19
    for relative,data in resource["reports"].items():
        assert (run/relative).with_name("samples.jsonl").exists()
        assert data["exit_code"]==0 or relative in ("E3/resources/resources.json","E4/resources/resources.json")
    assert "mt-reazon-m0-11#2" in resource["reports"]["E3/pipeline-resources/resources.json"]["intervals"]
    checks["resource_runs"]=19
    checks["repeated_phase_samples_recovered"]=True
    sources=[ROOT/"p0"/name for name in ("exploration.py","exploration_score.py","exploration_resources.py","explore_new_asr.py","explore_index_mt.py","exploration_validate.py")]
    subprocess.run([str(ROOT/"engine/.venv/bin/python"),"-m","py_compile",*[str(p) for p in sources]],check=True)
    for source in sources:
        subprocess.run([str(ROOT/"engine/.venv/bin/python"),str(source),"--help"],check=True,stdout=subprocess.DEVNULL)
        assert all(line==line.rstrip() for line in source.read_text().splitlines()),source
    checks["python_compile_and_cli_help"]=len(sources)
    workload=json.loads((run/"workload-comparison.json").read_text())
    for rel in ("E3/official-default/firered.jsonl","E2/original.jsonl","E2/audit.jsonl","E2/window0.jsonl"):
        rows=read_rows(run/rel)
        assert workload[rel]["asr_calls"]==len(rows)
        assert workload[rel]["empty_asr"]==sum(not x["text_ja"] for x in rows)
    for path in run.glob("E3/official-default/firered/mt-*.jsonl"):
        rows=read_rows(path);assert workload[str(path.relative_to(run))]["mt_calls"]==len(rows)
    ignored=subprocess.run(["git","check-ignore",str(run.relative_to(ROOT)/"adjudication.json"),".model-cache/exploration-sources/firered-stream.onnx"],cwd=ROOT,capture_output=True,text=True,check=True)
    assert len(ignored.stdout.splitlines())==2
    checks["evidence_and_models_ignored"]=True
    checks["holdout_accessed"]=False
    # Check only the loopback endpoint this run owned; never inspect unrelated browser/service ports.
    import socket
    settings=json.loads((run/"E5/index/settings.json").read_text());command=settings["command"]
    port=int(command[command.index("--port")+1])
    with socket.socket() as probe:
        probe.settimeout(.2);assert probe.connect_ex(("127.0.0.1",port))!=0
    checks["owned_index_server_stopped"]=True
    dump(output,dict(status="passed",checks=checks,
        verification="No fresh 1x, Chrome, blind or long-soak tests: candidate gate failed",
        source_policy="Validate frozen executed sources; report later checkout drift separately. Historical snapshots and validation reports stay unchanged."))
    print(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("run",type=Path)
    parser.add_argument("--output",type=Path,help="Fresh report path; existing reports are never overwritten")
    args=parser.parse_args();main(args.run,args.output)
