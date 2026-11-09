"""Post-inference B1 integrity/resource export, with separate analysis provenance."""
import argparse
import csv
import json
from pathlib import Path
import subprocess

from p0.review import percentile
from .adapters.punctuation import remove_insertions
from .b1 import IDS, PLAMO_PYTHON, ROOT, verify
from .freeze import digest, dump, rows


def export(run):
    manifest=verify(run)
    isolated=json.loads(subprocess.check_output([str(PLAMO_PYTHON),"-c",
        "import json; from p0.p2.b1 import runtime; print(json.dumps(runtime()))"],cwd=ROOT,text=True))
    assert isolated==manifest["runtime"]["plamo"]
    original=rows(run / "baseline/reazon.jsonl")
    padded=rows(run / "PAD-900/reazon.jsonl")
    span_fields=("clip","segment_id","source_id","start_sample","end_sample","released_at_sample","flush")
    assert len(original)==len(padded)==88
    assert all(tuple(a[k] for k in span_fields)==tuple(b[k] for k in span_fields) for a,b in zip(original,padded))
    transforms=rows(run / "PUNCT-JA/punctuation.jsonl")
    assert len(transforms)==62
    assert all(remove_insertions(r["input_ja"],r["punctuation"]["insertions"])==r["text_ja"] for r in transforms)
    statistics={}
    for name in ("B0-CURRENT",*IDS):
        rounds={}
        for seed in (11,29):
            data=rows(run / name / f"mt-seed{seed}.jsonl")
            assert len(data)==62 and len({r["source_id"] for r in data})==62
            plamo=name.startswith("MT-PLAMO")
            if plamo:
                assert all(r["finish_reason"]=="stop" and r["terminal_token"] in (1,16) for r in data)
            rounds[str(seed)]=dict(requests=62,filtered=[r["source_id"] for r in data if r["filtered_zh"] is None],
                all_output_generation_s=dict(total=sum(r["mt_s"] for r in data),median=percentile([r["mt_s"] for r in data],.5),
                    p95=percentile([r["mt_s"] for r in data],.95),maximum=max(r["mt_s"] for r in data)),
                termination="all stop at official EOS" if plamo else "not exposed by unchanged Hy wrapper")
        a=rows(run / name / "mt-seed11.jsonl");b=rows(run / name / "mt-seed29.jsonl")
        identical=sum(x["text_zh"]==y["text_zh"] for x,y in zip(a,b))
        if name.startswith("MT-PLAMO"):
            assert identical==62
        statistics[name]=dict(rounds=rounds,identical_across_rounds=identical,latency_scope="offline generation; not sentence-end/first-Chinese latency")
    resource_rows=[]
    for path in sorted((run / "comparisons").glob("*/comparison.json")):
        report=json.loads(path.read_text())
        meta=json.loads(path.with_name("resources.json").read_text())
        assert meta["exit_code"]==0
        opened,occurrences,durations={},{},{}
        for event in rows(path.with_name("phases.jsonl")):
            key=event["clip"]
            if event["phase"]=="start":
                opened[key]=event["t"]
            else:
                occurrences[key]=occurrences.get(key,0)+1
                durations[f"{key}#{occurrences[key]}"]=event["t"]-opened.pop(key)
        assert not opened
        idle_gpu_mem=report["baseline"].get("host_gpu_mem_median_mib")
        for phase,metrics in {"whole_run":report["whole_run"],**report["intervals"]}.items():
            requests=62 if phase.startswith("mt-seed") else report.get("normalization",{}).get("requests") if phase=="whole_run" else None
            item=metrics | dict(run=path.parent.name,phase=phase,wall_s=durations.get(phase),scope=report["scope"],
                idle_host_gpu_mem_median_mib=idle_gpu_mem,host_gpu_mem_peak_minus_idle_mib=metrics["host_gpu_mem_peak_mib"]-idle_gpu_mem,
                idle_host_gpu_avg_pct=report["baseline"]["host_gpu_avg_pct"],
                host_gpu_avg_minus_idle_pct=metrics["host_gpu_avg_pct"]-report["baseline"]["host_gpu_avg_pct"],
                cpu_seconds_per_request=metrics["cpu_seconds_sampled"]/requests if requests else None,
                requests=requests,analysis_source_sha256=digest(Path(__file__)))
            resource_rows.append(item)
    dump(run / "resource-export.json",dict(rows=resource_rows,source_sha256=digest(Path(__file__)),
        units="CPU100%=one core; process footprint/RSS/MLX/whole-host GPU shared memory overlap and must not be added; host idle delta is descriptive, not model-attributed VRAM"))
    fields=["run","phase","wall_s","samples","process_cpu_avg_pct","process_cpu_p95_pct","process_cpu_peak_pct",
        "process_footprint_peak_mib","footprint_median_mib","rss_sum_peak_mib","host_gpu_avg_pct","host_gpu_p95_pct","host_gpu_mem_peak_mib",
        "idle_host_gpu_mem_median_mib","host_gpu_mem_peak_minus_idle_mib","cpu_seconds_sampled","requests","cpu_seconds_per_request","scope"]
    fields[fields.index("host_gpu_mem_peak_mib"):fields.index("host_gpu_mem_peak_mib")]=["idle_host_gpu_avg_pct","host_gpu_avg_minus_idle_pct"]
    with (run / "resource-comparison.csv").open("w") as target:
        writer=csv.DictWriter(target,fieldnames=fields,extrasaction="ignore");writer.writeheader();writer.writerows(resource_rows)
    dump(run / "timing-offline.json",statistics)
    validation=dict(status="B1_INFERENCE_AND_INTEGRITY_PASS_QUALITY_SEPARATE",fingerprint=manifest["fingerprint"],
        checks=dict(parent_receipts_adopted=True,frozen_sources_inputs_models_verified=True,engine_and_isolated_runtime_verified=True,
            all_steps_passed=all(s["status"]=="passed" for s in json.loads((run / "state.json").read_text())["steps"].values()),
            paired_control_exact=json.loads((run / "B0-CURRENT/probe.json").read_text())["parity"],
            original88_spans_preserved=True,actual62_punctuation_reversible=True,all_two_round62_outputs_present=True,
            plamo_greedy_repeat62=True,plamo_no_length_termination=True,all_six_resource_runs_exit0=True),
        postanalysis_source_sha256=digest(Path(__file__)),inference_not_rerun=True,human_verified=False,holdout="sealed",
        realtime="NOT_RUN: B1 offline screening",full_chain_resources="NOT_RUN: not R20% proof")
    dump(run / "validation.json",validation)
    return validation


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("run",type=Path)
    print(json.dumps(export(parser.parse_args().run.resolve()),ensure_ascii=False))
