"""Recover every marked interval, including repeated phase names, from raw samples."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import statistics

from monitor import summarize, BASELINE_SAMPLES
from review import percentile


def main(run):
    reports={}
    for path in sorted(run.glob("**/resources.json")):
        original=json.loads(path.read_text())
        raw=[json.loads(line) for line in path.with_name("samples.jsonl").read_text().splitlines()]
        baseline=raw[:BASELINE_SAMPLES]
        def summary(points):
            result=summarize(points,original["mach_timebase"][0]/original["mach_timebase"][1])
            footprint=[sum(p[2] for p in r["processes"].values())/2**20 for r in points]
            gpu_memory=[r["gpu_in_use_bytes"]/2**20 for r in points if r["gpu_in_use_bytes"] is not None]
            result.update(process_footprint_median_mib=percentile(footprint,.5),
                process_footprint_p95_mib=percentile(footprint,.95),
                host_gpu_memory_avg_mib=round(statistics.mean(gpu_memory),2) if gpu_memory else None,
                host_gpu_memory_p95_mib=percentile(gpu_memory,.95),
                ane="N/A",coreml_other_helpers="N/A",scope="offline probe; not 1x playback")
            idle_gpu=[r["gpu_pct"] for r in baseline if r["gpu_pct"] is not None]
            idle_memory=[r["gpu_in_use_bytes"]/2**20 for r in baseline if r["gpu_in_use_bytes"] is not None]
            result["host_gpu_avg_idle_delta_pct"]=round(result["host_gpu_avg_pct"]-statistics.mean(idle_gpu),2) if idle_gpu and result["host_gpu_avg_pct"] is not None else None
            result["host_gpu_memory_avg_idle_delta_mib"]=round(result["host_gpu_memory_avg_mib"]-statistics.mean(idle_memory),2) if idle_memory and gpu_memory else None
            return result
        intervals={"whole_run":summary(raw[BASELINE_SAMPLES:])}
        count=Counter();opened={}
        for line in path.with_name("phases.jsonl").read_text().splitlines():
            event=json.loads(line);name=event["clip"]
            if event["phase"]=="start":opened[name]=event["t"]
            elif event["phase"]=="end" and name in opened:
                count[name]+=1
                points=[r for r in raw if opened[name]<=r["t"]<=event["t"]]
                intervals[f"{name}#{count[name]}"]=summary(points)|dict(start=opened.pop(name),end=event["t"])
        reports[str(path.relative_to(run))]=dict(exit_code=original["exit_code"],baseline=summary(baseline),intervals=intervals)
    (run/"resource-comparison.json").write_text(json.dumps(dict(
        units="CPU100%=one core; footprint=process tree physical MiB; GPU memory=whole-host shared memory, overlaps footprint",
        repeated_MT_names="E3 occurrence1=silero-v6,2=firered; E4 1=beam-empty,2=hot1p5,3=hot3; E5 1=granite,2=ja-en",
        reports=reports),indent=2)+"\n")
    with (run/"resource-comparison.csv").open("w") as target:
        fields=["report","interval","process_cpu_avg_pct","process_cpu_p95_pct","process_footprint_peak_mib", "host_gpu_avg_pct","host_gpu_p95_pct","host_gpu_peak_pct","host_gpu_avg_idle_delta_pct","host_gpu_memory_avg_mib","host_gpu_memory_p95_mib","host_gpu_mem_peak_mib","host_gpu_memory_avg_idle_delta_mib"]
        writer=csv.DictWriter(target,fieldnames=fields,extrasaction="ignore");writer.writeheader()
        for report,data in reports.items():
            for phase,value in data["intervals"].items():writer.writerow(dict(report=report,interval=phase,**value))
    print("Resource runs:",len(reports))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("run",type=Path)
    main(parser.parse_args().run)
