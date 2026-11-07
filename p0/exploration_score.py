"""Persist text-reviewed unit verdicts with actual gateway filtering and source IDs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from exploration import HISTORY, dump, progress, read_rows
from subtitle_service.app import chinese


def main(run):
    reference=read_rows(HISTORY/"n0-next/final-references/units.jsonl")
    old=json.loads((HISTORY/"n2-mt-greedy/mt-semantic-result.json").read_text())
    common=set(json.loads((HISTORY/"n2-mt-greedy/blind-verdict.json").read_text())["always_usable"])
    # Explicit semantic decisions made against the persisted translations and frozen necessary information.
    configurations={
        "firered-default":dict(paths=[f"E3/official-default/firered/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"csj-task/5":"adds unsupported attachment location", "wmf-04/153-155":"focus becomes jurisdiction over three orange objects",
                  "wmf-04/158-160":"decision becomes disciple-city production", "wmf-04/169":"drafting schedule absent"},
            extra={11:[],29:["csj-reproduction/2-3"]},uncertain=["wmf-04/133-135","wmf-04/153-155"]),
        "silero-v6":dict(paths=[f"E3/final/silero-v6/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"csj-task/5":"adds unsupported email/attachment location", "csj-task/9":"unknown-person clause absent", "wmf-04/153-155":"committee focus relation uncertain",
                  "wmf-04/156-157":"explicit discussion/decision relation uncertain", "wmf-04/158-160":"decision mechanism becomes finger-guided mechanism"},
            extra={11:[],29:["wmf-04/131-132"]},uncertain=["wmf-04/153-155","wmf-04/156-157"]),
        "firered":dict(paths=[f"E3/final/firered/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"csj-reproduction/5-7":"contrast with language information absent", "csj-task/5":"adds unsupported attachment location", "csj-task/9":"unknown people becomes people not knowing a matter",
                  "csj-task/20":"researcher and appearance details lost", "wmf-04/136":"preamble absent",
                  "wmf-04/153-155":"committee focus relation uncertain", "wmf-04/156-157":"explicit discussion/decision relation uncertain",
                  "wmf-04/158-160":"decision becomes number-of-hands creation", "wmf-04/166-167":"adding appendix absent",
                  "wmf-04/169":"drafting-process schedule not explicit", "wmf-04/170-172":"recruitment and appointment absent",
                  "wmf-04/173":"holding meetings absent"},extra={11:["wmf-04/131-132"],29:["wmf-04/131-132"]},
            uncertain=["wmf-04/153-155","wmf-04/156-157","wmf-04/169"]),
        "window":dict(paths=[f"E1/window/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"csj-task/9":"unknown-person statement absent", "wmf-04/156-157":"discussion and decision lost",
                  "wmf-04/158-160":"decision mechanism mistranslated as finger-guided mechanism"},
            extra={11:[],29:["wmf-04/131-132"]},uncertain=[]),
        "beam-hotwords":dict(paths=[f"E4/beam-empty/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"wmf-04/169":"drafting becomes prosecution", "wmf-04/158-160":"decision mechanism becomes data measurement"},
            extra={11:["wmf-04/131-132","wmf-04/161-165"],29:[]},uncertain=[]),
        "granite":dict(paths=[f"E5/granite/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"csj-task/20":"short haircut and researcher identity incomplete", "wmf-04/169":"process schedule becomes family schedule",
                  "wmf-04/173":"meeting becomes event like day"},extra={11:["wmf-04/131-132"],29:["wmf-04/131-132"]},
            uncertain=["wmf-04/148-152","wmf-04/161-165"]),
        "ja-en":dict(paths=[f"E5/ja-en/mt-reazon-m0-{s}.jsonl" for s in (11,29)],
            lost={"csj-reproduction/5-7":"control attributed to linguistic information rather than speaker", "csj-task/20":"haircut appearance becomes haircut action",
                  "wmf-04/158-160":"decision mechanism becomes a disciple-like image", "wmf-04/173":"meeting absent",
                  "wmf-04/179":"slides becomes slider", "wmf-04/156-157":"explicit decision relation missing in seed11"},
            extra={11:["csj-task/6","wmf-04/131-132"],29:["csj-task/6","wmf-04/156-157"]},
            uncertain=["wmf-04/156-157","wmf-04/176-178"]),
        "index":dict(paths=[f"E5/index/actual-{s}.jsonl" for s in (11,29)],
            lost={"csj-reproduction/5-7":"contrast reversed; language information described as continuous", "csj-task/9":"unknown-person negation reversed",
                  "csj-task/12-15":"necessary caption contains kana and is filtered", "csj-task/20":"necessary caption contains kana and is filtered",
                  "wmf-04/158-160":"decision becomes antialiasing decision", "wmf-04/166-167":"adding appendix becomes sorting appendix contents"},
            extra={11:["wmf-04/131-132"],29:["wmf-04/131-132"]},uncertain=[]),
    }
    all_scores={}
    for name,config in configurations.items():
        all_scores[name]={}
        for seed,relative in zip((11,29),config["paths"]):
            path=run/relative;rows=read_rows(path)
            hits=(common-set(config["lost"]))|set(config["extra"][seed])
            decisions=[]
            for unit in reference:
                key=f"{unit['clip']}/{unit['group']}"
                end=round(126.04*16000) if key=="wmf-04/153-155" else unit["end_sample"]
                related=[r for r in rows if r["clip"]==unit["clip"] and r["start_sample"]<end and r["end_sample"]>unit["start_sample"]]
                text=[dict(id=f"{r['clip']}/{r['segment_id']}",input_ja=r["input_ja"],output=r["text_zh"],passes_gateway=chinese(r["text_zh"]) is not None) for r in related]
                status="usable" if key in hits else "uncertain" if key in config["uncertain"] else "missing" if not related else "incorrect_or_incomplete"
                decisions.append(dict(unit=key,necessary_information=unit["reference_zh"],reference_status="historical",
                    review="ai_reviewed",human_verified=False,status=status,
                    reason=config["lost"].get(key,"semantic core present" if key in hits else "frozen necessary information not fully recovered"),
                    outputs=text,clock_status="offline: wall-clock deadline coverage not measured"))
            baseline=set(old["outcomes"][f"m0-{seed}"]["usable"])
            result=dict(hits=len(hits),upper_sensitivity=len(hits|set(config["uncertain"])),denominator=34,
                uncertain_units=config["uncertain"],restored=sorted(hits-baseline),lost=sorted(baseline-hits),
                net=len(hits)-len(baseline),qualified=False,rows=decisions,translation_sha256=sha256(path.read_bytes()).hexdigest())
            all_scores[name][str(seed)]=result
    dump(run/"adjudication.json",all_scores)
    # New models have already failed clear quality gates. Unresolved source/audio labels do not affect this decision.
    critical={
        "granite":[dict(unit="wmf-04/169",type="entity_relation",detail="process schedule becomes family schedule"),
                   dict(unit="wmf-04/174-175",type="number",detail="new phrase 34月"),
                   dict(unit="wmf-04/161-165",type="action_relation",detail="makes writing explicit into distinguishing opposition")],
        "ja-en":[dict(unit="wmf-04/161-165",type="action_relation",detail="explicitly writing down becomes decided by public culture"),
                 dict(unit="wmf-04/158-160",type="entity_relation",detail="adds disciple-like image")],
        "index":[dict(unit="csj-task/9",type="negation",detail="知らない人 becomes 知道的人"),
                 dict(unit="wmf-04/148-152",type="negation",detail="not becoming managers becomes becoming managers"),
                 dict(unit="wmf-04/161-165",type="modality",detail="question about speaking rights becomes private assertion of rights")],
    }
    dump(run/"E5/asr-screen.json",dict(denominator=34,review="ai_reviewed",human_verified=False,candidates={
        n:dict(scores={s:{k:v for k,v in item.items() if k!="rows"} for s,item in all_scores[n].items()},new_critical_errors=critical[n],
               raw_silence=json.loads((run/f"E5/{n}/settings.json").read_text())["silence_text"],qualified=False) for n in ("granite","ja-en")},
        replacement="unused: both formal checkpoints deployed and completed quality screening"))
    index11=read_rows(run/"E5/index/actual-11.jsonl");index29=read_rows(run/"E5/index/actual-29.jsonl")
    assert len(index11)==len(index29)==62 and all(a["text_zh"]==b["text_zh"] for a,b in zip(index11,index29))
    dump(run/"E5/mt-screen.json",dict(model="Index-Translate-2B.Q8_0.gguf",review="ai_reviewed",human_verified=False,
        scores={s:{k:v for k,v in item.items() if k!="rows"} for s,item in all_scores["index"].items()},
        new_critical_errors=critical["index"],seed_text_exact_matches=62,
        filtered=[f"{r['clip']}/{r['segment_id']}" for r in index11 if chinese(r["text_zh"]) is None],
        stopped_by_limit=[r for r in index11 if r["finish_reason"]!="stop"],
        source_route="34 complete published-source Japanese inputs; source_provisional; not promotion evidence",
        source_critical=["csj-task/9 negation reversed even with source input","csj-task/20 and /30 retain kana","wmf-04/176-178 temporal relation corrupted"],
        qualified=False,concurrent_ASR="skipped: offline quality gate failed"))
    development={}
    for seed in (11,29):
        p=run/"E5/development-mt"
        a=read_rows(p/f"mt-oracle-m0-{seed}.jsonl");b=read_rows(p/f"mt-oracle-m2-hit-{seed}.jsonl")
        assert len(a)==len(b)==20
        development[str(seed)]=dict(source_text_units=20,natural_hit_units=2,
            nonhit_text_exact=18,baseline_usable=14,candidate_usable=14,
            baseline_filtered=[f"{r['clip']}/{r['group']}" for r in a if chinese(r["text_zh"]) is None],
            candidate_filtered=[f"{r['clip']}/{r['group']}" for r in b if chinese(r["text_zh"]) is None],
            restored=[],lost=[],new_filtering="csj-interview_aps/13-14: quoted kana leaks into output")
    dump(run/"E5/development-mt/comparison.json",dict(review="ai_reviewed",human_verified=False,route="source-text diagnostic only",
        prior_material_exposure="historical regression audio; outside original 34-unit tuning; not blind/new-source generalization",
        outcomes=development,promoted=False))
    dump(run/"E5/selective-rescue.json",dict(status="skipped",reason="No passing complementary configuration or independently validated runtime selection rule",
        allowed_triggers=dict(primary_empty_asr=0,tail_padding_changed_text=0),
        observed_source="E1 original88 cuts and tail comparison",reference_oracle="not deployed as selector",
        limitation="Does not exclude future runtime selectors; no answer-dependent selection was fitted"))
    progress(run,"E5","passed","两个ASR、Index及小收益诊断全部完成；均未达净增3且无新增关键错误的晋级条件。",["E5/asr-screen.json","E5/mt-screen.json","E5/development-mt/comparison.json","adjudication.json","resource-comparison.json"])
    progress(run,"E6","skipped","正式候选池为空，未运行1×原速组合；离线耗时不充当句末延迟。",["E5/asr-screen.json","E5/mt-screen.json"])
    progress(run,"E7a","skipped","E6没有过线配置，本轮不运行Chrome和60分钟候选验收。",["progress.json"])
    progress(run,"E7b","skipped","尚无过线配置；人工参考按用户要求暂缓，test/保持封存。",["E0/pending-listen.json"])
    print({name:{s:v["hits"] for s,v in values.items()} for name,values in all_scores.items()})


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("run",type=Path)
    main(parser.parse_args().run)
