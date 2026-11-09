"""Sample Mac resources around a replay command and summarize its marked phases."""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import plistlib
import signal
import statistics
import subprocess
import time

if __package__:
    from .review import percentile
else:
    from review import percentile


INTERVAL_S = 0.25
BASELINE_SAMPLES = 16


class Rusage(C.Structure):
    # macOS SDK sys/resource.h, struct rusage_info_v0.
    _fields_ = [("uuid", C.c_uint8 * 16)] + [
        (field, C.c_uint64) for field in (
            "user", "system", "pkg_wakeups", "interrupt_wakeups", "pageins",
            "wired", "resident", "footprint", "start", "exit",
        )
    ]


def mark(clip, phase):
    if path := os.environ.get("P0_RESOURCE_PHASES"):
        with open(path, "a") as log:
            log.write(json.dumps({"t": time.monotonic(), "clip": clip, "phase": phase}) + "\n")


def sample(root_pid, libproc, mach, host):
    tree = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,comm="], text=True)
    children = {}
    xpc_pids = []
    translation_pids = []
    for line in tree.splitlines():
        raw_pid, raw_parent, command = line.split(maxsplit=2)
        pid, parent = int(raw_pid), int(raw_parent)
        children.setdefault(parent, []).append(pid)
        if command.startswith("/System/Library/Frameworks/Speech.framework/") and command.endswith(
            "/localspeechrecognition.xpc/Contents/MacOS/localspeechrecognition"
        ):
            xpc_pids.append(pid)
        if command == "/System/Library/Frameworks/Translation.framework/translationd":
            translation_pids.append(pid)

    def usage_for(pids):
        processes = {}
        for pid in pids:
            usage = Rusage()
            if libproc.proc_pid_rusage(pid, 0, C.byref(usage)) == 0:
                processes[str(pid)] = [usage.start, usage.user + usage.system, usage.footprint]
        return processes

    engine_pids = []
    stack = [root_pid] if root_pid is not None else []
    while stack:
        pid = stack.pop()
        engine_pids.append(pid)
        stack.extend(children.get(pid, []))
    ticks = (C.c_uint32 * 4)()
    count = C.c_uint32(4)
    result = mach.host_statistics(host, 3, C.byref(ticks), C.byref(count))
    if result:
        raise OSError(f"host_statistics failed: {result}")
    sampled_at = time.monotonic()
    processes = usage_for(engine_pids)
    xpc = usage_for(xpc_pids)
    try:
        gpu = plistlib.loads(subprocess.check_output(
            ["/usr/sbin/ioreg", "-a", "-r", "-d", "1", "-c", "AGXAccelerator"],
            timeout=0.5, stderr=subprocess.DEVNULL,
        ))
        counters = next((row["PerformanceStatistics"] for row in gpu if "PerformanceStatistics" in row), {})
    except (subprocess.SubprocessError, plistlib.InvalidFileException):
        counters = {}
    return {
        "t": sampled_at, "processes": processes,
        "apple_speech_xpc": xpc, "host_cpu_ticks": list(ticks),
        "apple_translation_daemon": usage_for(translation_pids),
        "gpu_pct": counters.get("Device Utilization %"),
        "gpu_in_use_bytes": counters.get("In use system memory"),
    }


def summarize(points, tick_to_ns):
    proc_cpu, xpc_cpu, translation_cpu, host_cpu = [], [], [], []
    for prev, now in zip(points, points[1:]):
        seconds = now["t"] - prev["t"]
        for key, counters in (("processes", proc_cpu), ("apple_speech_xpc", xpc_cpu),
                              ("apple_translation_daemon", translation_cpu)):
            if key in now and key in prev:
                counters.append(sum(
                    curr[1] - prev[key][pid][1]
                    for pid, curr in now[key].items()
                    if pid in prev[key] and prev[key][pid][0] == curr[0]
                ) * tick_to_ns / 1e9 / seconds * 100)
        ticks = [(b - a) % (2**32) for a, b in zip(prev["host_cpu_ticks"], now["host_cpu_ticks"])]
        if total := sum(ticks):
            host_cpu.append(100 * (total - ticks[2]) / total)
    gpu = [row["gpu_pct"] for row in points if row["gpu_pct"] is not None]
    gpu_mem = [row["gpu_in_use_bytes"] / 2**20 for row in points if row["gpu_in_use_bytes"] is not None]
    return {
        "samples": len(points),
        "gpu_samples": len(gpu),
        "gpu_mem_samples": len(gpu_mem),
        "process_cpu_avg_pct": round(statistics.mean(proc_cpu), 2) if proc_cpu else None,
        "process_cpu_p95_pct": percentile(proc_cpu, .95),
        "process_footprint_peak_mib": round(max((
            sum(p[2] for p in row["processes"].values()) / 2**20 for row in points
        ), default=0), 2),
        "apple_speech_xpc_cpu_avg_pct": round(statistics.mean(xpc_cpu), 2) if xpc_cpu else None,
        "apple_speech_xpc_footprint_peak_mib": round(max((
            sum(p[2] for p in row["apple_speech_xpc"].values()) / 2**20
            for row in points if "apple_speech_xpc" in row
        ), default=0), 2) if xpc_cpu else None,
        "apple_translation_daemon_cpu_avg_pct": round(statistics.mean(translation_cpu), 2) if translation_cpu else None,
        "apple_translation_daemon_footprint_peak_mib": round(max((
            sum(p[2] for p in row["apple_translation_daemon"].values()) / 2**20
            for row in points if "apple_translation_daemon" in row
        ), default=0), 2) if translation_cpu else None,
        "host_cpu_avg_pct": round(statistics.mean(host_cpu), 2) if host_cpu else None,
        "host_gpu_avg_pct": round(statistics.mean(gpu), 2) if gpu else None,
        "host_gpu_p95_pct": percentile(gpu, .95),
        "host_gpu_peak_pct": max(gpu, default=None),
        "host_gpu_mem_median_mib": percentile(gpu_mem, .50),
        "host_gpu_mem_peak_mib": percentile(gpu_mem, 1),
    }


def collect(output, command, mach):
    output.mkdir(parents=True, exist_ok=False)
    phases_file = output / "phases.jsonl"
    phases_file.touch()
    libproc = C.CDLL("/usr/lib/libproc.dylib")
    host = mach.mach_host_self()
    baseline_start = time.monotonic()
    with (output / "samples.jsonl").open("w", buffering=1) as trace:
        def take(pid):
            trace.write(json.dumps(sample(pid, libproc, mach, host)) + "\n")

        for index in range(BASELINE_SAMPLES):
            time.sleep(max(0, baseline_start + index * INTERVAL_S - time.monotonic()))
            take(None)
        deadline = time.monotonic()
        env = os.environ | {"P0_RESOURCE_PHASES": str(phases_file.resolve())}
        child = subprocess.Popen(command, env=env, start_new_session=True)
        try:
            while child.poll() is None:
                take(child.pid)
                deadline = max(deadline + INTERVAL_S, time.monotonic())
                time.sleep(max(0, deadline - time.monotonic()))
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
    return child.returncode


def write_report(output, command, code, mach):
    samples = [json.loads(line) for line in (output / "samples.jsonl").read_text().splitlines()]
    # proc_pid_rusage CPU totals use Mach absolute-time ticks, not nanoseconds.
    timebase = (C.c_uint32 * 2)()
    if mach.mach_timebase_info(C.byref(timebase)):
        raise OSError("mach_timebase_info failed")
    tick_to_ns = timebase[0] / timebase[1]
    phases = {}
    for line in (output / "phases.jsonl").read_text().splitlines():
        phase = json.loads(line)
        phases.setdefault(phase["clip"], {})[phase["phase"]] = phase["t"]
    result = {
        "command": command, "sample_interval_s": INTERVAL_S, "exit_code": code,
        "mach_timebase": list(timebase),
        "baseline": summarize(samples[:BASELINE_SAMPLES], tick_to_ns),
        "whole_run": summarize(samples[BASELINE_SAMPLES:], tick_to_ns),
        "clips": {
            clip: summarize([row for row in samples if span["start"] <= row["t"] <= span["end"]], tick_to_ns)
            for clip, span in phases.items() if span.keys() == {"start", "end"}
        },
    }
    (output / "resources.json").write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    mach = C.CDLL(None)
    if args.command:
        code = collect(args.output, args.command, mach)
        write_report(args.output, args.command, code, mach)
        raise SystemExit(code)
    previous = json.loads((args.output / "resources.json").read_text())
    write_report(args.output, previous["command"], previous["exit_code"], mach)


if __name__ == "__main__":
    main()
