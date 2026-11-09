"""B1 anonymous review material and deterministic gates; no semantic auto-grader."""
import json
from pathlib import Path

from .b1 import IDS, verify
from .freeze import digest, dump, rows


def packet(run, *, mapping=None, verify_run=None, phase="B1"):
    (verify_run or verify)(run)
    mapping=mapping or dict(zip(("A","B","C","D"),("MT-PLAMO2-H0","PAD-900","MT-PLAMO08-H0","PUNCT-JA")))
    refs=rows(run / "inputs/units.jsonl")
    required=json.loads((run / "inputs/unit-map.json").read_text())["required_segments"]
    state=json.loads((run / "state.json").read_text())["steps"]
    completed={name for name in ("B0-CURRENT",*mapping.values()) if state.get(name+"-fixed-text",{}).get("status")=="passed"
        and ((phase!="B2" and name!="PAD-900") or name=="B0-CURRENT" or state.get(name+"-fixed-cuts",{}).get("status")=="passed")}
    if "B0-CURRENT" not in completed:
        raise ValueError("paired control incomplete")
    outputs={name:{str(seed):rows(run / name / f"mt-seed{seed}.jsonl") for seed in (11,29)} for name in completed}
    material=[]
    for unit in refs:
        key=f"{unit['clip']}/{unit['group']}"
        end=round(126.04*16000) if key=="wmf-04/153-155" else unit["end_sample"]
        def related(data):
            return [dict(segment=r["segment_id"],ja=r["input_ja"],raw_zh=r["text_zh"],
                         valid_zh=r["filtered_zh"],finish_reason=r["finish_reason"])
                    for r in data if r["clip"]==unit["clip"] and (r["segment_id"] in required[key] or
                        (r["start_sample"]<end and r["end_sample"]>unit["start_sample"]))]
        material.append(dict(unit=key,necessary_information=unit["reference_zh"],source_ja=unit["source_ja"],
            reference_status=unit["reference_status"],human_verified=False,
            control={s:related(data) for s,data in outputs["B0-CURRENT"].items()},
            candidates={label:{s:related(data) for s,data in outputs[name].items()} for label,name in mapping.items() if name in completed}))
    dump(run / "reviews/anonymous-map.json",mapping)
    dump(run / "reviews/anonymous-packet.json",dict(instructions="Evaluate all34 required semantic cores, valid_zh only. Preserve raw for error attribution. Correct/partial/incorrect/missing/uncertain; no human listening claim. Compare critical negation/question/number/entity errors against each same-seed control, including units already failed by control. Original Japanese ASR errors must not be repaired by the judge.",units=material))
    return dict(units=34,completed_candidates=len(completed)-1,registered_candidates=len(mapping),rounds=2)


def score(run, *, experiments=IDS, verify_run=None, phase="B1"):
    (verify_run or verify)(run)
    verdict_path=run / "reviews/adjudication.json"
    reviewed=json.loads(verdict_path.read_text())
    reference={f"{u['clip']}/{u['group']}" for u in rows(run / "inputs/units.jsonl")}
    old=json.loads((run / "inputs/verdict.json").read_text())["outcomes"]
    baseline={str(seed):set(old[f"m0-{seed}"]["usable"]) for seed in (11,29)}
    protected=set(json.loads((run / "reference-manifest.json").read_text())["protected_unit_ids"])
    state=json.loads((run / "state.json").read_text())["steps"]
    result={}
    for experiment in experiments:
        step=state.get(experiment+"-fixed-text",{})
        asr_failed=(experiment=="PAD-900" or phase=="B2") and state.get(experiment+"-fixed-cuts",{}).get("status")!="passed"
        if step.get("status")!="passed" or asr_failed:
            result[experiment]=dict(status=step.get("status","NOT_RUN"),error=step.get("error"),denominator=34,
                Q=False,R_offline_eligible=False,R_full_chain_pass=False,next_stage="stop_incomplete_execution")
            if asr_failed:
                result[experiment].update(status="incomplete_ASR_dependency")
            continue
        rounds={}
        for seed in ("11","29"):
            decisions=reviewed[experiment][seed]
            if len(decisions)!=34 or {r["unit"] for r in decisions}!=reference:
                raise ValueError("B1 requires all34 explicit unit judgments")
            if any(r["status"] not in ("correct","partial","incorrect","missing","uncertain") for r in decisions):
                raise ValueError("unknown semantic judgment")
            correct={r["unit"] for r in decisions if r["status"]=="correct"}
            critical=[r for r in decisions if r["new_critical_error"]]
            lost=baseline[seed]-correct
            protected_lost=(baseline[seed] & protected)-correct
            net=len(correct)-len(baseline[seed])
            rounds[seed]=dict(hits=len(correct),denominator=34,net=net,correct=sorted(correct),
                restored=sorted(correct-baseline[seed]),lost=sorted(lost),protected_lost=sorted(protected_lost),
                uncertain=[r["unit"] for r in decisions if r["status"]=="uncertain"],
                new_critical_errors=critical,Q=net>=3 and not critical and not protected_lost,
                R_offline_eligible=net>=0 and not critical and not protected_lost)
        result[experiment]=dict(rounds=rounds,Q=all(r["Q"] for r in rounds.values()),
            R_offline_eligible=all(r["R_offline_eligible"] for r in rounds.values()),R_full_chain_pass=False,
            next_stage="eligible_for_later_validation" if all(r["Q"] or r["R_offline_eligible"] for r in rounds.values()) else f"stop_after_{phase}_screen")
    report=dict(phase=phase,denominator=34,baseline_hits=[20,18],candidates=result,human_verified=False,
        references="AI text-reviewed provisional;15 pending listening retained",holdout="sealed",realtime_latency=f"NOT_RUN: {phase} offline only",
        provenance=dict(adjudication_sha256=digest(verdict_path),packet_sha256=digest(run / "reviews/anonymous-packet.json"),
            analysis_source_sha256=digest(Path(__file__)),manifest_sha256=digest(run / "run-manifest.json")))
    dump(run / "decision.json",report)
    return report
