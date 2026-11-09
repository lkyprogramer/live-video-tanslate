"""Reuse p0.monitor; retain RSS and system pressure alongside its native counters."""
import argparse
import ctypes as C
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

from p0 import monitor
from p0.review import percentile
from .freeze import dump, rows
from .export_resources import cpu_peak


def summaries(output, audio_s, scope, requests=None):
    original = json.loads((output / "resources.json").read_text())
    raw = rows(output / "samples.jsonl")
    phases, opened, occurrences = {}, {}, {}
    for row in rows(output / "phases.jsonl"):
        name = row["clip"]
        if row["phase"] == "start":
            opened[name] = row["t"]
        elif name in opened:
            occurrences[name] = occurrences.get(name, 0) + 1
            phases[f"{name}#{occurrences[name]}"] = (opened.pop(name), row["t"])
    tick = original["mach_timebase"][0]/original["mach_timebase"][1]

    def summarize(points):
        result = monitor.summarize(points, tick)
        cpu_s = sum(sum(max(0, cur[1]-prev["processes"][pid][1])
                        for pid, cur in now["processes"].items()
                        if pid in prev["processes"] and prev["processes"][pid][0] == cur[0])
                    for prev, now in zip(points, points[1:])) * tick / 1e9
        rss = [sum(r.get("rss_bytes", {}).values())/2**20 for r in points]
        result.update(cpu_seconds_sampled=cpu_s, rss_sum_peak_mib=max(rss, default=None),
                      process_cpu_peak_pct=cpu_peak(points, tick),
                      footprint_median_mib=percentile([sum(p[2] for p in r["processes"].values())/2**20 for r in points], .5),
                      ane="N/A", dedicated_vram="N/A: unified memory", scope=scope)
        return result

    summary = dict(scope=scope, sample_interval_s=.25, actual_audio_s=audio_s,
                   units="CPU100%=one core; footprint and RSS are separate; host GPU shared memory overlaps them",
                   baseline=summarize(raw[:monitor.BASELINE_SAMPLES]),
                   whole_run=summarize(raw[monitor.BASELINE_SAMPLES:]),
                   intervals={name: summarize([r for r in raw if start <= r["t"] <= end])
                              for name, (start, end) in phases.items()}, unclosed_phases=opened)
    if requests is not None:
        summary["normalization"] = dict(unit="translation_request", requests=requests)
        summary["whole_run"]["cpu_seconds_per_request"] = summary["whole_run"]["cpu_seconds_sampled"]/requests
    else:
        summary["whole_run"]["cpu_seconds_per_audio_second"] = summary["whole_run"]["cpu_seconds_sampled"]/audio_s
    dump(output / "comparison.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    units = parser.add_mutually_exclusive_group(required=True)
    units.add_argument("--audio-seconds", type=float)
    units.add_argument("--requests", type=int)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if (args.requests is not None and args.requests <= 0) or (args.audio_seconds is not None and args.audio_seconds <= 0):
        parser.error("normalization denominator must be positive")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a monitored command is required")
    original_sample = monitor.sample
    last_system = -10.0
    system = {}
    started = time.monotonic()

    def sample(root_pid, libproc, mach, host):
        nonlocal last_system, system
        point = original_sample(root_pid, libproc, mach, host)
        if point["t"]-started > args.timeout_seconds:
            raise RuntimeError("resource stop: monitored command exceeded its time budget")
        rss = {}
        for pid in point["processes"]:
            usage = monitor.Rusage()
            if libproc.proc_pid_rusage(int(pid), 0, C.byref(usage)) == 0:
                rss[pid] = usage.resident
        if point["t"]-last_system >= 4:
            system = {}
            for key, cmd in (("memory_pressure", ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]),
                             ("swap", ["sysctl", "-n", "vm.swapusage"]), ("vm_stat", ["vm_stat"])):
                proc = subprocess.run(cmd, capture_output=True, text=True)
                system[key] = proc.stdout.strip() if proc.returncode == 0 else None
            last_system = point["t"]
        point.update(rss_bytes=rss, system_memory=system)
        if sum(p[2] for p in point["processes"].values()) >= 12 * 2**30:
            raise RuntimeError("resource stop: process tree footprint >=12GiB")
        return point

    monitor.sample = sample
    mach = C.CDLL(None)
    code = 4
    try:
        code = monitor.collect(args.output, command, mach)
    finally:
        monitor.sample = original_sample
        if (args.output / "samples.jsonl").exists():
            monitor.write_report(args.output, command, code, mach)
            summaries(args.output, args.audio_seconds, args.scope, args.requests)
    sys.exit(code)


if __name__ == "__main__":
    main()
