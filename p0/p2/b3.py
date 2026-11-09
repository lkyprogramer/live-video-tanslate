"""B3 bounded native streaming and full-audio AST screening. No production routing."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from importlib.metadata import version

from . import b1,b2
from .freeze import ROOT,PLAN,digest,dump,rows

IDS=('NATIVE-VOX-960','AST-G4-OFFICIAL')
PREVIOUS=ROOT/'p0/evidence/p2-b2-20261109-093739-tags-fix'
PYTHONS={role:ROOT/f'.model-cache/p2-b3-{name}-env/bin/python' for role,name in (('vox','vox'),('gemma','gemma'))}
COMMITS={'mlx-audio':'d52b8cde2ee28d583f173e1bc0eb424b425c11c4','mlx-vlm':'a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4'}


def runtime(role='engine'):
    if role=='engine':return b1.runtime()
    names=('mlx','mlx-audio','transformers','numpy','huggingface-hub','tokenizers')+(('mlx-vlm',) if role=='gemma' else ())
    return {name:version(name) for name in names}


def verify(run,*,role='engine'):
    return b1.verify(run,runtime_reader=lambda:runtime(role),runtime_key=role)


def freeze(run,registry):
    if (run/'run-manifest.json').exists():return verify(run)
    prior=json.loads((PREVIOUS/'decision.json').read_text())
    if any(c['Q'] or c['R_offline_eligible'] for c in prior['candidates'].values()):raise ValueError('unexpected B2 shortlist')
    for step in json.loads((PREVIOUS/'state.json').read_text())['steps'].values():
        if step['status']!='passed':raise ValueError('incomplete B2')
        for path,expected in step['outputs'].items():
            if digest(PREVIOUS/path)!=expected:raise ValueError('B2 receipt drift')
    (run/'locks').mkdir(exist_ok=True);(run/'inputs').mkdir(exist_ok=True)
    dump(run/'locks/previous-phase.json',dict(run=str(PREVIOUS),decision_sha256=digest(PREVIOUS/'decision.json'),
        review_sha256=digest(PREVIOUS/'reviews/final-review.json')))
    shutil.copyfile(PREVIOUS/'inputs/probes.json',run/'inputs/probes.json')
    for control in json.loads((run/'inputs/probes.json').read_text())['controls']:
        if digest(control['path'])!=control['sha256']:raise ValueError('control PCM drift')
    shutil.copyfile(run/'sources/official-prompt.json',run/'locks/official-prompt.json')
    models=[];extra=[]
    for key,path in json.loads((run/'sources/model-paths.json').read_text()).items():
        info=json.loads((run/'sources'/key/'repository-files.json').read_text())
        if info['revision']!=Path(path).name:raise ValueError('snapshot revision mismatch')
        models.append(dict(id=key,source='hf',repository=info['repo'],revision=info['revision'],local_path=path,
            files=[dict(path=str(p),sha256=digest(p)) for p in sorted(Path(path).iterdir()) if p.is_file()]))
    runtimes={'engine':runtime()}
    for role,python in PYTHONS.items():
        result=subprocess.check_output([str(python),'-c',f'from p0.p2.b3 import runtime; import json; print(json.dumps(runtime("{role}")))'],cwd=ROOT,text=True)
        runtimes[role]=json.loads(result)
    for name,commit in COMMITS.items():
        source=ROOT/'.model-cache/p2-b3-src'/name
        if subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()!=commit:raise ValueError('runtime commit drift')
        if subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True).strip():raise ValueError('dirty pinned runtime')
        extra.extend(source.rglob('*.py'))
    extra.extend(p for p in (run/'sources').rglob('*') if p.is_file())
    hypotheses=dict(ids=IDS,seeds=[11,29],routes={'NATIVE-VOX-960':['Q','R'],'AST-G4-OFFICIAL':['Q']},
        Q='net>=3 in each round vs20/18; zero new critical errors and same-round protected losses',
        R=dict(primary='paired_common_correct_dom_p95',formal='ineligible: seed29 baseline18<20 and no human timing',
            offline='noninferiority only; never claim speed from component peaks'),
        vox=dict(mode='causal-stream offline; original9 continuous PCM,200ms feeds, fixed VAD release watermarks',delay_ms=960,
            steps_per_call=4,steps_per_session=8192,interval='estimated_decoder_interval[k*1280,(k+1)*1280), not acoustic word timing',
            endpoints='released_at_sample AND decoder frontier >= end; coalesce only simultaneously pending unsubmitted endpoints',
            seed='reuse exact span+JA original index, else SHA clip/span/JA[:7]+100000',mt='separate offline text stage, no full-chain resource or online MT queue claim',
            pending_pcm_samples=64000,mt_pending=0,drain_s=8),
        ast=dict(mode='original88 audio cuts twice',prompt_sha256=digest(run/'locks/official-prompt.json'),temperature=1.,top_p=.95,top_k=64,max_tokens=256,
            kv='fresh every cut request, persistent model',seed='11/29 + original cut index; G0 same11 for swapped audio',
            parser='exactly one line-start Chinese: space; nonempty JA/Chinese; no markup; Han and no Kana; stop only',thinking=False,
            g0='3speech/silence/syntheticmusic same prompt+seed; structure+feature+output change; >=2/3 protocol-compliant speech outputs; missing audio rejected at boundary'),
        budget=dict(deployment_s=14400,quality_s=7200,request_s=30,tree_stop_gib=12,max_targeted_ast_repairs=1),
        controls='same B0 Reazon88 + M0 2x62 exact before candidates; B2 raw receipts preserved',holdout='sealed',human_verified=False)
    return b1.freeze(run,registry,phase='B3',model_additions=models,runtime_lock=runtimes,hypotheses=hypotheses,extra_sources=extra)


def native_mt(run,out):
    import mlx.core as mx
    from subtitle_service.local_pipeline import HyTranslator
    from subtitle_service.app import chinese
    from p0.monitor import mark
    source=rows(run/'NATIVE-VOX-960/quality/reazon.jsonl')
    mark('mt-load','start');model=HyTranslator(b1.model_paths(run)['hymt-b0']);session=model.new_session();mark('mt-load','end')
    from dataclasses import asdict
    dump(out/'mt-settings.json',dict(prompt=session._prompt,generation=asdict(session._config)))
    for seed in (11,29):
        translated=[];mark(f'mt-seed{seed}','start')
        for index,row in enumerate(source):
            mx.random.seed(seed+row['mt_seed_index'])
            import time
            started=time.monotonic()
            text=b1.bounded_translate(lambda:model.translate(session,row['text_ja'])) if row['text_ja'] else ''
            item=row|dict(input_ja=row['text_ja'],text_zh=text,filtered_zh=chinese(text),mt_s=time.monotonic()-started,
                finish_reason='unknown: same Hy wrapper',seed=seed,mlx_active_bytes=mx.get_active_memory(),mlx_peak_bytes=mx.get_peak_memory())
            translated.append(item);b1.write_rows(out/f'row-seed{seed}-{index+1:03}.jsonl',[item])
            print(json.dumps(dict(stage='native-mt',seed=seed,row=index+1)),flush=True)
        b1.write_rows(out/f'mt-seed{seed}.jsonl',translated);mark(f'mt-seed{seed}','end')
    session.close();return dict(status='complete',rows_per_seed=len(source),seeds=[11,29],full_chain_resource_pass=False)


def probe(run,experiment,mode):
    if experiment=='B0-CURRENT':
        if mode not in ('fixed-cuts','fixed-text'):raise NotImplementedError('B0 fixed offline control only')
        out=run/experiment;out.mkdir(exist_ok=True)
        return b1.execute(run,experiment+'-'+mode,lambda:b2.asr_probe(run,experiment,out) if mode=='fixed-cuts' else b1.text_probe(run,experiment,out),out,verify_run=verify)
    if experiment not in IDS:raise NotImplementedError('experiment outside B3')
    state=json.loads((run/'state.json').read_text())['steps']
    if state.get('B0-CURRENT-fixed-cuts',{}).get('status')!='passed' or not all(state.get('B0-CURRENT-fixed-text',{}).get('result',{}).get('parity',{}).values()) or 'B0-CURRENT-fixed-text' not in state:
        raise ValueError('exact paired ASR and two-round M0 required')
    capability=mode=='capability';expected='causal-stream' if experiment==IDS[0] else 'fixed-cuts'
    if not capability and mode!=expected and not (experiment==IDS[0] and mode=='fixed-text'):raise NotImplementedError('unsupported B3 mode')
    if not capability and state.get(experiment+'-capability',{}).get('result',{}).get('status')!='ready_for_quality':raise ValueError('G0 must pass before quality')
    out=run/experiment/('g0' if capability else 'mt' if mode=='fixed-text' else 'quality');out.mkdir(parents=True,exist_ok=True)
    def operation():
        if mode=='fixed-text':
            if state.get(experiment+'-causal-stream',{}).get('result',{}).get('status')!='complete':raise ValueError('native ASR incomplete')
            return native_mt(run,out)
        role='vox' if experiment==IDS[0] else 'gemma'
        command=[str(PYTHONS[role]),'-m','p0.p2.b3_worker','--run-dir',str(run),'--experiment',experiment]+(['--capability'] if capability else [])
        code=subprocess.run(command,cwd=ROOT).returncode
        filename='capability.json' if capability else 'asr-summary.json' if role=='vox' else 'probe.json'
        if code:
            failure=out/'failure.json'
            return json.loads(failure.read_text()) if failure.exists() else dict(status='blocked_runtime',returncode=code,quality_eligible=False)
        return json.loads((out/filename).read_text())
    return b1.execute(run,experiment+'-'+mode,operation,out,verify_run=verify)
