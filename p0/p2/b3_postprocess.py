"""B3 all34-unit review linkage and route-specific deterministic gates."""
import argparse
import json
from pathlib import Path
from . import b3
from .freeze import digest,dump,rows


def packet(run,*,verify_run=None,candidate_folders=None,paired_official=None):
    (verify_run or b3.verify)(run)
    original=rows(run/'baseline/reazon.jsonl');required=json.loads((run/'inputs/unit-map.json').read_text())['required_segments']
    refs=rows(run/'inputs/units.jsonl');material=[];completed={}
    for experiment,folder in (candidate_folders or ((b3.IDS[0],'mt'),(b3.IDS[1],'quality'))):
        path=run/experiment/folder
        if all((path/f'mt-seed{s}.jsonl').exists() for s in (11,29)):
            completed[experiment]={str(seed):rows(path/f'mt-seed{seed}.jsonl') for seed in (11,29)}
    control={str(seed):rows(run/'B0-CURRENT'/f'mt-seed{seed}.jsonl') for seed in (11,29)}
    for u in refs:
        key=f"{u['clip']}/{u['group']}";end=round(126.04*16000) if key=='wmf-04/153-155' else u['end_sample']
        related_original=[r for r in original if r['clip']==u['clip'] and (r['segment_id'] in required[key] or r['start_sample']<end and r['end_sample']>u['start_sample'])]
        ranges=[(r['start_sample'],r['end_sample']) for r in related_original]+[(u['start_sample'],end)]
        def related(data,native=False):
            return [dict(source_id=r['source_id'],segment=r['segment_id'],start_sample=r['start_sample'],end_sample=r['end_sample'],
                ja=r['input_ja'],raw_zh=r['text_zh'],valid_zh=r['filtered_zh'],finish_reason=r['finish_reason'])
                for r in data if r['clip']==u['clip'] and (any(r['start_sample']<b and r['end_sample']>a for a,b in ranges) if native else
                r['segment_id'] in required[key] or r['start_sample']<end and r['end_sample']>u['start_sample'])]
        material.append(dict(unit=key,necessary_information=u['reference_zh'],source_ja=u['source_ja'],reference_status=u['reference_status'],
            native_linkage_ranges=ranges,native_alignment='estimated decoder ranges; review boundaries explicitly; no word-level timing claim',
            control={s:related(data) for s,data in control.items()},
            candidates={name:{s:related(data,native=name==b3.IDS[0]) for s,data in rounds.items()} for name,rounds in completed.items()},
            paired_official={s:related(data) for s,data in paired_official.items()} if paired_official else None))
    dump(run/'reviews/anonymous-packet.json',dict(instructions='All34 frozen semantic cores, valid Chinese only. Same frozen B1/B2 rubric, no human listening. No credit from Japanese or invalid/truncated output. Check new critical errors also on baseline-failed units. Native spans are decoder estimates; no reference-based commit. Candidate IDs visible: independent, not fully blinded.',units=material))
    return dict(completed=list(completed),units=34)


def score(run,*,verify_run=None,experiments=None,phase="B3"):
    (verify_run or b3.verify)(run);adjudication=run/'reviews/adjudication.json'
    review=json.loads(adjudication.read_text()) if adjudication.exists() else {}
    baseline=json.loads((run/'baseline-score.json').read_text())['usable'];protected=set(json.loads((run/'reference-manifest.json').read_text())['protected_unit_ids'])
    ids={f"{u['clip']}/{u['group']}" for u in rows(run/'inputs/units.jsonl')};state=json.loads((run/'state.json').read_text())['steps'];candidates={}
    for experiment in (experiments or b3.IDS):
        capability=state.get(experiment+'-capability',{}).get('result',{})
        if experiment not in review:
            candidates[experiment]=dict(status=capability.get('status','NOT_RUN'),error=capability.get('error'),
                denominator=34,quality_status='NOT_RUN',Q=False,R_offline_eligible=False,R_full_chain_pass=False,
                next_stage='stop_at_capability' if capability.get('status')!='ready_for_quality' else 'review_incomplete')
            continue
        prerequisite=experiment+('-fixed-text' if experiment==b3.IDS[0] else '-fixed-cuts')
        if state.get(prerequisite,{}).get('result',{}).get('status')!='complete':raise ValueError('cannot score partial/incomplete candidate')
        rounds={}
        for seed in ('11','29'):
            decisions=review[experiment][seed]
            if len(decisions)!=34 or {r['unit'] for r in decisions}!=ids:raise ValueError('all34 explicit judgments required')
            if any(r['status'] not in ('correct','partial','incorrect','missing','uncertain') for r in decisions):raise ValueError('unknown verdict')
            correct={r['unit'] for r in decisions if r['status']=='correct'};old=set(baseline[seed]);critical=[r for r in decisions if r['new_critical_error']]
            lost=(old&protected)-correct;net=len(correct)-len(old)
            rounds[seed]=dict(hits=len(correct),denominator=34,net=net,correct=sorted(correct),restored=sorted(correct-old),lost=sorted(old-correct),
                protected_lost=sorted(lost),new_critical_errors=critical,uncertain=[r['unit'] for r in decisions if r['status']=='uncertain'],
                Q=net>=3 and not critical and not lost,R_offline_eligible=experiment==b3.IDS[0] and net>=0 and not critical and not lost)
        candidates[experiment]=dict(status='screened',rounds=rounds,Q=all(r['Q'] for r in rounds.values()),
            R_offline_eligible=experiment==b3.IDS[0] and all(r['R_offline_eligible'] for r in rounds.values()),
            R_formal_eligible=False,R_full_chain_pass=False,next_stage='eligible_for_later_provisional_engineering' if all(r['Q'] or r['R_offline_eligible'] for r in rounds.values()) else 'stop_after_'+phase+'_screen')
    report=dict(phase=phase,denominator=34,baseline_hits=[20,18],candidates=candidates,human_verified=False,pending_listen=15,holdout='sealed',
        resources='component offline only; no 1x or full-chain timing proof',R_primary='paired_common_correct_dom_p95',R_formal='ineligible baseline18<20; no human timing',
        provenance=dict(manifest_sha256=digest(run/'run-manifest.json'),analysis_source_sha256=digest(Path(__file__)),
            adjudication_sha256=digest(adjudication) if adjudication.exists() else None))
    dump(run/'decision.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True);parser.add_argument('command',choices=('packet','score'))
    args=parser.parse_args();print(json.dumps((packet if args.command=='packet' else score)(args.run_dir),ensure_ascii=False))
