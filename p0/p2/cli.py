"""P2 B0 reproduction and bounded B1 offline screening commands."""
import argparse
import json
from pathlib import Path
import sys
import time
import subprocess

from .freeze import PLAN, digest, dump, freeze, frozen_fingerprint


def execute(run, key, operation, paths):
    manifest = frozen_fingerprint(run)
    state_path = run / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(
        fingerprint=manifest["fingerprint"], steps={})
    if state["fingerprint"] != manifest["fingerprint"]:
        raise ValueError("run fingerprint changed")
    previous = state["steps"].get(key)
    if previous:
        if previous["status"] != "passed":
            raise ValueError("interrupted/failed attempt retained; create a fresh run before retry")
        for path, sha in previous["outputs"].items():
            if digest(run / path) != sha:
                raise ValueError("resume output hash mismatch")
        return previous["result"] | {"resumed_verified": True}
    state["steps"][key] = dict(status="running", started_ns=time.monotonic_ns())
    dump(state_path, state)
    try:
        result = operation()
    except Exception as exc:
        state["steps"][key].update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        dump(state_path, state)
        raise
    state["steps"][key].update(status="passed", finished_ns=time.monotonic_ns(), result=result,
        outputs={str(p.relative_to(run)): digest(p) for p in paths()})
    dump(state_path, state)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--registry", type=Path, default=PLAN / "config/experiment-registry.json")
    commands = parser.add_subparsers(dest="command", required=True)
    freezing = commands.add_parser("freeze")
    freezing.add_argument("--phase", choices=("B0", "B1", "B2", "B3", "B4"), default="B0")
    for name in ("preflight", "probe", "score", "replay", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--experiment", required=True)
        if name == "probe":
            command.add_argument("--mode", required=True, choices=("fixed-cuts", "fixed-text", "causal-stream"))
        if name == "replay":
            command.add_argument("--repeat", required=True, type=int, choices=(1, 2))
        if name == "accept":
            command.add_argument("--stage", required=True, choices=("engineering", "holdout"))
    commands.add_parser("report")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    try:
        phase = getattr(args, "phase", None)
        existing = run / "run-manifest.json"
        if phase=="B4" or (existing.exists() and json.loads(existing.read_text()).get("phase")=="B4"):
            from . import b4,b4_analysis
            if args.command=="freeze":
                result=dict(status="frozen",phase="B4",fingerprint=b4.freeze(run,args.registry.resolve())["fingerprint"])
            elif args.command=="preflight":
                result=dict(status="hash_runtime_pass",fingerprint=b4.verify(run)["fingerprint"]) if args.experiment=="B0-CURRENT" else b4.probe(run,args.experiment,"capability")
            elif args.command=="probe":
                result=b4.probe(run,args.experiment,args.mode)
            elif args.command in ("score","report"):
                result=b4_analysis.score(run) if (run/"reviews/adjudication.json").exists() else b4_analysis.packet(run)
            else:raise NotImplementedError("B4 protocol screen only; no candidate replay/accept")
            print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
            return 0
        if phase=="B3" or (existing.exists() and json.loads(existing.read_text()).get("phase")=="B3"):
            from . import b3
            if args.command=="freeze":
                manifest=b3.freeze(run,args.registry.resolve())
                result=dict(status="frozen",phase="B3",fingerprint=manifest["fingerprint"])
            elif args.command=="preflight":
                result=dict(status="hash_runtime_pass",fingerprint=b3.verify(run)["fingerprint"]) if args.experiment=="B0-CURRENT" else b3.probe(run,args.experiment,"capability")
            elif args.command=="probe":
                result=b3.probe(run,args.experiment,args.mode)
            elif args.command in ("score","report"):
                from . import b3_postprocess
                result=b3_postprocess.score(run) if (run/"reviews/adjudication.json").exists() else b3_postprocess.packet(run)
            else:
                raise NotImplementedError("B3 replay/accept are later phases")
            print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
            return 0
        if phase=="B2" or (existing.exists() and json.loads(existing.read_text()).get("phase")=="B2"):
            from . import b2
            if args.command=="freeze":
                manifest=b2.freeze(run,args.registry.resolve())
                result=dict(status="frozen",phase="B2",fingerprint=manifest["fingerprint"])
            elif args.command=="preflight":
                manifest=b2.verify(run)
                if args.experiment not in b2.IDS+("B0-CURRENT",):
                    raise NotImplementedError("experiment not in B2")
                result=dict(status="capability_hash_runtime_pass",experiment_id=args.experiment,fingerprint=manifest["fingerprint"])
            elif args.command=="probe":
                result=b2.probe(run,args.experiment,args.mode)
            elif args.command in ("score","report"):
                result=b2.score_or_packet(run)
            else:
                raise NotImplementedError("B2 is offline screening; candidate replay/accept/holdout are later phases")
            print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
            return 0
        if phase == "B1" or (existing.exists() and json.loads(existing.read_text()).get("phase") == "B1"):
            from . import b1
            if args.command == "freeze":
                manifest=b1.freeze(run,args.registry.resolve())
                result=dict(status="frozen",phase="B1",fingerprint=manifest["fingerprint"])
            elif args.command == "preflight":
                manifest=b1.verify(run)
                if args.experiment not in b1.IDS+("B0-CURRENT",):
                    raise NotImplementedError("experiment not in B1")
                result=dict(status="capability_pass",experiment_id=args.experiment,fingerprint=manifest["fingerprint"])
            elif args.command == "probe":
                if args.experiment.startswith("MT-PLAMO"):
                    b1.verify(run)
                    proc=subprocess.run([str(b1.PLAMO_PYTHON),"-m","p0.p2.b1","--run-dir",str(run),
                        "--experiment",args.experiment,"--mode",args.mode],cwd=b1.ROOT)
                    return proc.returncode
                result=b1.probe(run,args.experiment,args.mode)
            elif args.command in ("score","report"):
                from .b1_scoring import packet,score
                result=score(run) if (run / "reviews/adjudication.json").exists() else packet(run)
            else:
                raise NotImplementedError("B1 offline screening: scoring requires explicit reviewed verdicts; realtime/holdout remain later phases")
            print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
            return 0
        if getattr(args, "experiment", "B0") != "B0":
            raise NotImplementedError("B0 implements only the current baseline; candidate adapters belong to B1+")
        if args.command == "freeze":
            result = freeze(run, args.registry.resolve())
            result = dict(status="frozen", fingerprint=result["fingerprint"], units=34)
        elif args.command == "preflight":
            manifest = frozen_fingerprint(run)
            result = dict(status="capability_pass", experiment_id="B0", fingerprint=manifest["fingerprint"])
            dump(run / "baseline/preflight.json", result)
        elif args.command == "probe":
            from .runner import run_probe
            result = execute(run, args.mode, lambda: run_probe("B0", args.mode, {"run_dir": str(run)}),
                             lambda: sorted((run / "baseline").glob("asr*")) + [run / "baseline/reazon.jsonl"]
                             if args.mode == "fixed-cuts" else sorted((run / "baseline").glob("mt-*")))
        elif args.command == "score":
            from .scoring import score_baseline
            result = execute(run, "score", lambda: score_baseline(run),
                             lambda: sorted((run / "baseline").glob("score*.json")))
        elif args.command == "replay":
            from .realtime import replay_baseline
            result = execute(run, f"replay-{args.repeat}", lambda: replay_baseline(run, args.repeat),
                             lambda: sorted((run / f"replay-{args.repeat}").glob("*")))
        elif args.command == "accept":
            if args.stage != "engineering":
                raise NotImplementedError("holdout remains sealed in B0")
            from .report import acceptance
            result = acceptance(run)
        else:
            from .report import report
            result = report(run)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except NotImplementedError as exc:
        print(json.dumps(dict(status="unsupported", error=str(exc), release_eligible=False)))
        return 3
    except TimeoutError as exc:
        print(json.dumps(dict(status="timeout", error=str(exc), release_eligible=False)))
        return 4
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="invalid_evidence", error=str(exc), release_eligible=False)))
        return 2


if __name__ == "__main__":
    sys.exit(main())
