"""One registered B4 Gemma protocol ablation; no production selection."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import b1,b2,b3
from .b4_protocol import ZH_PROMPT,reserve_slot
from .freeze import ROOT,PLAN,digest,dump,rows,validate_lock

ID='AST-G4-ZH'
CONTROL='AST-G4-OFFICIAL'
PARENT=ROOT/'p0/evidence/p2-b3-20261109-102811-audio-pad-fix'


def archived(run):
    manifest=json.loads((run/'run-manifest.json').read_text())
    for name,expected in manifest['source_sha256'].items():
        if digest(run/'source-snapshot'/Path(name).relative_to(ROOT))!=expected:raise ValueError('historical source snapshot drift')
    for name,expected in manifest['artifact_sha256'].items():
        if digest(run/name)!=expected:raise ValueError('historical frozen artifact drift')
    state=json.loads((run/'state.json').read_text())
    if state['fingerprint']!=manifest['fingerprint']:raise ValueError('historical fingerprint drift')
    for step in state['steps'].values():
        if step['status']=='passed':
            for name,expected in step['outputs'].items():
                if digest(run/name)!=expected:raise ValueError('historical completed receipt drift')
    return manifest


def verify(run,*,role='engine'):
    return b1.verify(run,runtime_reader=lambda:b3.runtime(role),runtime_key=role)


def freeze(run,registry):
    if (run/'run-manifest.json').exists():return verify(run)
    parent=archived(PARENT)
    status=json.loads((PARENT/'AST-G4-OFFICIAL/quality/probe.json').read_text())
    if status['status']!='complete' or status['rows_per_seed']!=88:raise ValueError('completed official parent required')
    data=json.loads(registry.read_text());slot=reserve_slot(data,ID)
    if slot!='B4-1':raise ValueError('this registered packet reserves B4-1 only')
    # Reservation is global and persisted before any model output; retries reuse the same ID.
    dump(registry,data)
    (run/'locks').mkdir(exist_ok=True);(run/'inputs').mkdir(exist_ok=True)
    shutil.copyfile(PARENT/'inputs/probes.json',run/'inputs/probes.json')
    for control in json.loads((run/'inputs/probes.json').read_text())['controls']:
        if digest(control['path'])!=control['sha256']:raise ValueError('control PCM drift')
    dump(run/'locks/parent.json',dict(run=str(PARENT),manifest_sha256=digest(PARENT/'run-manifest.json'),
        decision_sha256=digest(PARENT/'decision.json'),official_hits=[6,8],completed_output_receipts_verified=True,
        historical_current_source_drift_expected=True,parent_fingerprint=parent['fingerprint']))
    model=next(m for m in json.loads((PARENT/'locks/models.json').read_text())['models'] if m['id']=='gemma4')
    validate_lock(dict(models=[model]))
    protocols=json.loads((run/'locks/protocols.json').read_text())
    if protocols[ID]['instruction']!=ZH_PROMPT:raise ValueError('ZH prompt differs from fixed plan')
    if protocols[CONTROL]['instruction']!=json.loads((PARENT/'sources/official-prompt.json').read_text())['prompt']:raise ValueError('official prompt drift')
    expected=json.loads((PARENT/'AST-G4-OFFICIAL/quality/prompt.json').read_text())['sha256']
    if protocols[CONTROL]['rendered_sha256']!=expected:raise ValueError('official rendered template drift')
    runtime={'engine':b3.runtime()}
    runtime['gemma']=json.loads(subprocess.check_output([str(b3.PYTHONS['gemma']),'-c','import json;from p0.p2.b3 import runtime;print(json.dumps(runtime("gemma")))'],cwd=ROOT,text=True))
    if runtime['gemma']!=parent['runtime']['gemma'] or runtime['engine']!=parent['runtime']['engine']:raise ValueError('B3 runtime drift')
    extra=[]
    for name,commit in b3.COMMITS.items():
        source=ROOT/'.model-cache/p2-b3-src'/name
        if subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()!=commit:raise ValueError('runtime commit drift')
        if subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True).strip():raise ValueError('dirty pinned runtime')
        extra.extend(source.rglob('*.py'))
    extra.extend(p for p in (run/'sources').rglob('*') if p.is_file())
    extra.append(ROOT/'docs/p2-b4-gemma-zh-protocol-2026-11-09.md')
    hypotheses=dict(id=ID,control=CONTROL,slot=slot,routes=['Q'],single_variable='visible output protocol and its necessary parser',
        protocols=protocols,parent_quality_not_passed=True,trigger='completed official + predeclared protocol-cost hypothesis, authorized B4-1',
        seeds=[11,29],seed_binding='seed+original mt_seed_index; G0 same11 for all changed audio',
        model='238767527555cb75a05732a84dff5d6ba0dd6809',temperature=1.,top_p=.95,top_k=64,max_tokens=256,thinking=False,num_audios=1,
        unchanged=['model/runtime/quantization/published weights','original88cuts','640invalid right padding','freshKV every request','no history/glossary/cache changes'],
        controls='Reazon88 exact, M0two62 exact, same-run official176 raw/tokens/finish/filter/PCM/features exact B3',
        cost_signal='both seeds: full88 generation tokens AND generation wall sum >=20% reduction vs paired official, protocol-valid coverage not reduced',
        first_chinese='official only after line-start Chinese: space; ZH-only body; raw event Han is not semantically correct/stable/DOM; invalid/missing retained',
        Q='each seed >=23/21 vs original20/18, zero new critical, zero same-round protected losses; never compare Q against6/8',
        safety='nonempty valid nonspeech caption is safety_fail, meta response not automatically speech hallucination; offline diagnostic allowed but no fullG0/engineering promotion',
        parser='stop,nonempty,Han,noKana,noASCII markup/fences/known language labels; no label stripping; input_ja empty in ZH',
        budget=dict(setup_s=3600,quality_analysis_s=3600,total_s=7200,request_s=30,tree_stop_gib=12),
        no_rescue='no further prompt/parser/dtype/caching variants; new attempts preserve failures and same slot',
        human_verified=False,pending_listen=15,holdout='sealed')
    return b1.freeze(run,registry,phase='B4',model_additions=[model],runtime_lock=runtime,hypotheses=hypotheses,extra_sources=extra)


def official_parity(run,out):
    fields=('source_id','start_sample','end_sample','pcm_sha256','raw_output','text_zh','filtered_zh','input_ja','finish_reason','generation_tokens','token_ids','prompt_tokens')
    count=0
    for seed in (11,29):
        old=rows(PARENT/CONTROL/'quality'/f'mt-seed{seed}.jsonl');new=rows(out/f'mt-seed{seed}.jsonl')
        if len(new)!=88 or len(old)!=88:raise ValueError('official176 incomplete')
        for a,b in zip(old,new):
            if any(a[k]!=b[k] for k in fields):raise ValueError('official output parity drift: '+b['source_id'])
            if a['audio_audit']!=b['audio_audit']:raise ValueError('official feature/path parity drift')
            count+=1
    result=dict(exact_rows=count,raw_tokens_filter_audio_exact=True,adjudication_reused=[6,8],new_semantic_trials=False)
    dump(out/'parity.json',result);return result


def probe(run,experiment,mode):
    if experiment=='B0-CURRENT':
        if mode not in ('fixed-cuts','fixed-text'):raise NotImplementedError('offline control only')
        out=run/experiment;out.mkdir(exist_ok=True)
        return b1.execute(run,experiment+'-'+mode,lambda:b2.asr_probe(run,experiment,out) if mode=='fixed-cuts' else b1.text_probe(run,experiment,out),out,verify_run=verify)
    if experiment not in (ID,CONTROL):raise NotImplementedError('outside allocated B4 protocol experiment')
    state=json.loads((run/'state.json').read_text())['steps'];control=state.get('B0-CURRENT-fixed-text',{})
    if state.get('B0-CURRENT-fixed-cuts',{}).get('status')!='passed' or not all(control.get('result',{}).get('parity',{}).get(str(s),False) for s in (11,29)):
        raise ValueError('exact paired Reazon/M0 required')
    if mode not in ('capability','fixed-cuts'):raise NotImplementedError('B4 Gemma offline only')
    if experiment==CONTROL and mode!='fixed-cuts':raise NotImplementedError('official capability inherited; reproduce full176')
    if experiment==ID:
        if state.get(CONTROL+'-fixed-cuts',{}).get('result',{}).get('parity',{}).get('exact_rows')!=176:raise ValueError('paired official176 exact prerequisite')
        if mode=='fixed-cuts' and state.get(ID+'-capability',{}).get('result',{}).get('status') not in ('ready_for_quality','ready_for_offline_diagnostic_with_safety_fail'):
            raise ValueError('completed speech/structure G0 prerequisite')
    out=run/experiment/('g0' if mode=='capability' else 'quality');out.mkdir(parents=True,exist_ok=True)
    def operation():
        command=[str(b3.PYTHONS['gemma']),'-m','p0.p2.b4_worker','--run-dir',str(run),'--experiment',experiment]+(['--capability'] if mode=='capability' else [])
        code=subprocess.run(command,cwd=ROOT).returncode
        if code:return json.loads((out/'failure.json').read_text()) if (out/'failure.json').exists() else dict(status='blocked_runtime',returncode=code)
        result=json.loads((out/('capability.json' if mode=='capability' else 'probe.json')).read_text())
        if experiment==CONTROL:result['parity']=official_parity(run,out)
        return result
    return b1.execute(run,experiment+'-'+mode,operation,out,verify_run=verify)
