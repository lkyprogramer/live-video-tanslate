"""Post-inference B2 integrity/resource views; separate from frozen inference source."""
import argparse
import csv
import json
from pathlib import Path

from p0.review import percentile
from . import b2
from .freeze import digest,dump,rows


def export(run):
    manifest=b2.verify(run);steps=json.loads((run/"state.json").read_text())["steps"]
    original=rows(run/"baseline/reazon.jsonl");fields=("source_id","clip","segment_id","start_sample","end_sample","released_at_sample","flush")
    integrity={};timing={};native={};memory={}
    for name in ("B0-CURRENT",*b2.IDS):
        complete=steps.get(name+"-fixed-cuts",{}).get("status")=="passed"
        if not complete:
            integrity[name]=dict(asr_status=steps.get(name+"-fixed-cuts",{}).get("status","NOT_RUN"),denominator=88)
            continue
        asr=rows(run/name/"reazon.jsonl")
        assert len(asr)==88
        assert all(tuple(a[k] for k in fields)==tuple(b[k] for k in fields) for a,b in zip(original,asr))
        assert all(b2.seed_index(a,b["text_ja"])==b["mt_seed_index"] for a,b in zip(original,asr))
        integrity[name]=dict(asr_rows=88,source_spans_unchanged=True,stable_seed_mapping=True,
            empty_source_ids=[r["source_id"] for r in asr if not r["text_ja"]],
            unusable_source_ids=[r["source_id"] for r in asr if not r["metadata"]["usable"]])
        times=[r["decode_s"] for r in asr]
        timing[name]=dict(asr=dict(count=88,total_s=sum(times),p50_s=percentile(times,.5),p95_s=percentile(times,.95),max_s=max(times),
            scope="offline fixed-cut full ASR call; no VAD wait, queue or1x submission"),mt={})
        if name=="ASR-WHISPER-JA760":
            calls=[c for r in asr for c in r["metadata"]["native_calls"]]
            native[name]=dict(native_calls=len(calls),native_caps=sorted({c["native_decode_cap"] for c in calls}),
                max_output_tokens=max(c["generated_tokens"] for c in calls),truncated_calls=sum(c["length_boundary_unconfirmed"] for c in calls),
                no_speech_filtered_calls=sum(c["no_speech_filtered"] for c in calls),termination="selected hypothesis inferred from fixed CT2 source, not runtime stop_reason",
                settings=json.loads((run/name/"asr-settings.json").read_text()))
        elif name=="ASR-SENSEVOICE-I8":
            native[name]=dict(unknown_tag_rows=[r["source_id"] for r in asr if r["metadata"]["unknown_tags"]],
                metadata_to_mt=False,configured_use_itn=True,provider="explicit CPU in native sherpa config; low-level session query not exposed")
        if steps.get(name+"-fixed-text",{}).get("status")!="passed":
            integrity[name]["mt_status"]=steps.get(name+"-fixed-text",{}).get("status","NOT_RUN");continue
        memory[name]={}
        for seed in (11,29):
            output=rows(run/name/f"mt-seed{seed}.jsonl");assert len(output)==62
            assert len({r["source_id"] for r in output})==62
            times=[r["mt_s"] for r in output]
            timing[name]["mt"][str(seed)]=dict(count=62,total_s=sum(times),p50_s=percentile(times,.5),p95_s=percentile(times,.95),max_s=max(times),
                filtered_source_ids=[r["source_id"] for r in output if r["filtered_zh"] is None],
                scope="offline full MT call incl template/tokenization/prefill/decode; seed/filter outside; not1x or first-Chinese")
            events=[e for e in rows(run/name/f"events-seed{seed}.jsonl") if e["event"]=="mt_end"]
            memory[name][str(seed)]=dict(mlx_active_peak_mib=max(e["mlx_active_bytes"] for e in events)/2**20,
                mlx_allocator_peak_mib=max(e["mlx_peak_bytes"] for e in events)/2**20)
        integrity[name]["mt_rows_per_seed"]=62
    exported=[]
    for path in sorted((run/"comparisons").glob("*/comparison.json")):
        report=json.loads(path.read_text());meta=json.loads(path.with_name("resources.json").read_text());opened={};durations={};occurrences={}
        for e in rows(path.with_name("phases.jsonl")):
            key=e["clip"]
            if e["phase"]=="start":opened[key]=e["t"]
            else:
                occurrences[key]=occurrences.get(key,0)+1;durations[f"{key}#{occurrences[key]}"]=e["t"]-opened.pop(key)
        for phase,value in {"whole_run":report["whole_run"],**report["intervals"]}.items():
            count=62 if phase.startswith("mt-seed") else 16 if phase.startswith("short-controls") else report.get("normalization",{}).get("requests") if phase=="whole_run" else None
            idle=report["baseline"];gpu_mem=value.get("host_gpu_mem_peak_mib");idle_mem=idle.get("host_gpu_mem_median_mib")
            exported.append(value|dict(run=path.parent.name,phase=phase,wall_s=durations.get(phase),exit_code=meta["exit_code"],
                requests=count,cpu_seconds_per_workload_request=value["cpu_seconds_sampled"]/count if count else None,
                idle_host_gpu_avg_pct=idle.get("host_gpu_avg_pct"),idle_host_gpu_mem_median_mib=idle_mem,
                host_gpu_mem_peak_minus_idle_mib=gpu_mem-idle_mem if gpu_mem is not None and idle_mem is not None else None,
                unclosed_phases=list(opened)))
    dump(run/"resource-export.json",dict(rows=exported,analysis_source_sha256=digest(Path(__file__)),
        units="CPU100%=one core; RSS/footprint/MLX/all-host GPU shared memory overlap, never add; idle delta not dedicated model VRAM"))
    columns=["run","phase","wall_s","exit_code","samples","process_cpu_avg_pct","process_cpu_p95_pct","process_cpu_peak_pct",
        "process_footprint_peak_mib","footprint_median_mib","rss_sum_peak_mib","host_gpu_avg_pct","host_gpu_p95_pct","host_gpu_mem_peak_mib",
        "idle_host_gpu_avg_pct","idle_host_gpu_mem_median_mib","host_gpu_mem_peak_minus_idle_mib","cpu_seconds_sampled","requests","cpu_seconds_per_workload_request","scope"]
    with (run/"resource-comparison.csv").open("w") as file:
        writer=csv.DictWriter(file,fieldnames=columns,extrasaction="ignore");writer.writeheader();writer.writerows(exported)
    dump(run/"timing-offline.json",timing);dump(run/"native-decode-summary.json",native);dump(run/"mlx-memory-summary.json",memory)
    validation=dict(status="INFERENCE_INTEGRITY_CHECKED_QUALITY_SEPARATE",fingerprint=manifest["fingerprint"],
        experiments=integrity,resource_groups=len({r["run"] for r in exported}),resource_rows=len(exported),
        baseline_current_mt_exact=json.loads((run/"B0-CURRENT/probe.json").read_text())["parity"],
        human_verified=False,pending_listen=15,holdout="sealed",realtime="NOT_RUN in B2 offline screening",
        analysis_source_sha256=digest(Path(__file__)),inference_rerun=False)
    dump(run/"validation.json",validation)
    return validation


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("run",type=Path)
    print(json.dumps(export(parser.parse_args().run.resolve()),ensure_ascii=False))
