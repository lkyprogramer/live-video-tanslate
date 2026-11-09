"""Versioned postprocessing of B0 raw samples; never reruns or edits inference.

The sampler's original report is retained. Fixed-text CPU is per request rather
than per video second. This analysis source has its own hash, separate from the
already frozen measurement implementation.
"""
import argparse
import csv
import json
from pathlib import Path

from .freeze import digest, dump, rows


def cpu_peak(points, tick_to_ns):
    values = []
    for before, after in zip(points, points[1:]):
        elapsed = after["t"]-before["t"]
        ticks = sum(current[1]-before["processes"][pid][1] for pid,current in after["processes"].items()
                    if pid in before["processes"] and current[0] == before["processes"][pid][0])
        values.append(ticks*tick_to_ns/1e9/elapsed*100)
    return max(values, default=None)


def export(run):
    results = {}
    for path in sorted((run / "comparisons").glob("*/comparison.json")):
        report = json.loads(path.read_text())
        original = path.with_name("comparison-original.json")
        if not original.exists():
            original.write_bytes(path.read_bytes())
        raw = rows(path.with_name("samples.jsonl"))
        meta = json.loads(path.with_name("resources.json").read_text())
        tick = meta["mach_timebase"][0]/meta["mach_timebase"][1]
        report["whole_run"]["process_cpu_peak_pct"] = cpu_peak(raw[16:], tick)
        opened, occurrences, intervals = {}, {}, {}
        for event in rows(path.with_name("phases.jsonl")):
            key = event["clip"]
            if event["phase"] == "start":
                opened[key] = event["t"]
            elif key in opened:
                occurrences[key] = occurrences.get(key,0)+1
                intervals[f"{key}#{occurrences[key]}"] = (opened.pop(key),event["t"])
        for key,(start,end) in intervals.items():
            if key in report["intervals"]:
                report["intervals"][key]["process_cpu_peak_pct"] = cpu_peak([p for p in raw if start<=p["t"]<=end],tick)
        if "fixed-text" in meta["command"]:
            requests = {str(seed):sum(r["event"]=="mt_end" for r in rows(run / f"baseline/mt-events-seed{seed}.jsonl"))
                        for seed in (11,29)}
            report["actual_audio_s"] = None
            report["normalization"] = dict(unit="translation_request", requests=sum(requests.values()), requests_by_seed=requests)
            report["whole_run"]["cpu_seconds_per_audio_second"] = None
            report["whole_run"]["cpu_seconds_per_request"] = report["whole_run"]["cpu_seconds_sampled"]/sum(requests.values())
            for key,value in report["intervals"].items():
                if key.startswith("mt-seed"):
                    seed = key.removeprefix("mt-seed").split("#")[0]
                    value["cpu_seconds_per_request"] = value["cpu_seconds_sampled"]/requests[seed]
        else:
            report["normalization"] = dict(unit="source_audio_second", seconds=report["actual_audio_s"])
        report["analysis_provenance"] = dict(source_sha256=digest(Path(__file__)), raw_samples_sha256=digest(path.with_name("samples.jsonl")),
            original_report_sha256=digest(original), measurement_not_rerun=True)
        dump(path, report)
        results[path.parent.name] = report
    dump(run / "resource-export.json", dict(reports=results, analysis_source_sha256=digest(Path(__file__)),
         units="CPU100%=one core; RSS/footprint/whole-host GPU shared memory overlap; do not add them"))
    fields = ["run", "interval", "scope", "samples", "process_cpu_avg_pct", "process_cpu_p95_pct", "process_cpu_peak_pct",
              "process_footprint_peak_mib", "footprint_median_mib", "rss_sum_peak_mib", "host_gpu_avg_pct", "host_gpu_p95_pct",
              "host_gpu_mem_peak_mib", "cpu_seconds_per_audio_second", "cpu_seconds_per_request"]
    with (run / "resource-comparison.csv").open("w") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for name,report in results.items():
            for interval,value in {"whole_run":report["whole_run"],**report["intervals"]}.items():
                writer.writerow(dict(run=name,interval=interval,**value))
    return dict(reports=len(results), analysis_source_sha256=digest(Path(__file__)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run",type=Path)
    print(json.dumps(export(parser.parse_args().run.resolve())))
