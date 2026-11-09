"""Post-inference B3 multi-attempt resource/integrity export, no model inference."""
import argparse
import csv
import json
from pathlib import Path
from . import b3
from .freeze import ROOT,digest,dump,rows,validate_lock
from p0.review import percentile


def export(ast_run,vox_run,out,confirmation_run=None):
    out.mkdir(parents=True,exist_ok=True)
    original=Path(json.loads((ast_run/'locks/attempt-parent.json').read_text())['parent'])
    attempts=[original,ast_run,vox_run]+([confirmation_run] if confirmation_run else []);resource=[];verification={};pressure={}
    for run in attempts:
        manifest=json.loads((run/'run-manifest.json').read_text())
        for name,expected in manifest['source_sha256'].items():
            assert digest(run/'source-snapshot'/Path(name).relative_to(ROOT))==expected
        for name,expected in manifest['artifact_sha256'].items():assert digest(run/name)==expected
        for asset in manifest['assets']:assert digest(asset['path'])==asset['sha256']
        validate_lock(json.loads((run/'locks/models.json').read_text()))
        state=json.loads((run/'state.json').read_text());assert state['fingerprint']==manifest['fingerprint']
        for step in state['steps'].values():
            if step['status']=='passed':
                for name,expected in step['outputs'].items():assert digest(run/name)==expected
        verification[run.name]=dict(fingerprint=manifest['fingerprint'],completed_receipts_verified=True,source_verification='archived source-snapshot and receipts; export analysis has its own current hash',
            pending_or_failed_steps={k:v['status'] for k,v in state['steps'].items() if v['status']!='passed'},
            non_success_results={k:dict(receipt_status=v['status'],outcome_status=v.get('result',{}).get('status'),error=v.get('result',{}).get('error')) for k,v in state['steps'].items() if str(v.get('result',{}).get('status','')).startswith(('blocked','failed'))},
            baseline_mt_parity=json.loads((run/'B0-CURRENT/probe.json').read_text())['parity'])
        for path in sorted((run/'resources').glob('*/comparison.json')):
            data=json.loads(path.read_text());meta=json.loads(path.with_name('resources.json').read_text())
            opened={};durations={};count={}
            for e in rows(path.with_name('phases.jsonl')):
                if e['phase']=='start':opened[e['clip']]=e['t']
                elif e['clip'] in opened:
                    count[e['clip']]=count.get(e['clip'],0)+1
                    durations[f"{e['clip']}#{count[e['clip']]}"]=e['t']-opened.pop(e['clip'])
            idle=data['baseline'];baseline_mem=idle.get('host_gpu_mem_median_mib')
            for phase,value in {'whole_run':data['whole_run'],**data['intervals']}.items():
                peak=value.get('host_gpu_mem_peak_mib')
                value=dict(value)
                failed_group=meta['exit_code']!=0 or (run==original and path.parent.name=='AST-G4-g0') or (run==ast_run and path.parent.name=='Vox-g0')
                if failed_group:
                    value.pop('cpu_seconds_per_request',None);value.pop('cpu_seconds_per_audio_second',None)
                normalization=data.get('normalization')
                if path.parent.name=='Vox-mt':
                    value['cpu_seconds_per_source_submission']=value.pop('cpu_seconds_per_request',None)
                    normalization=dict(unit='source_submission',source_submissions=176,nonempty_model_calls=152,empty_source_noops=24)
                resource.append(value|dict(attempt=run.name,group=path.parent.name,phase=phase,wall_s=durations.get(phase),exit_code=meta['exit_code'],
                    idle_host_gpu_avg_pct=idle.get('host_gpu_avg_pct'),idle_host_gpu_mem_median_mib=baseline_mem,
                    host_gpu_mem_peak_minus_idle_mib=peak-baseline_mem if peak is not None and baseline_mem is not None else None,
                    unclosed_phases=list(opened),requested_normalization=normalization,
                    normalization_warning='failed/interrupted group denominators are planned workloads, not completed requests'))
            points=rows(path.with_name('samples.jsonl'));snapshots=[p['system_memory'] for p in points if p.get('system_memory')]
            def vmcounter(key,snapshot):
                import re
                match=re.search(rf'^{key}:\s*([0-9]+)',snapshot.get('vm_stat',''),re.MULTILINE)
                return int(match.group(1)) if match else None
            first,last=(snapshots[0],snapshots[-1]) if snapshots else ({},{})
            pressure[run.name+'/'+path.parent.name]=dict(pressure_levels=sorted({p.get('memory_pressure','') for p in snapshots}),
                swap_first=first.get('swap'),swap_last=last.get('swap'),
                swapins_pages_delta=(vmcounter('Swapins',last)-vmcounter('Swapins',first)) if vmcounter('Swapins',first) is not None else None,
                swapouts_pages_delta=(vmcounter('Swapouts',last)-vmcounter('Swapouts',first)) if vmcounter('Swapouts',first) is not None else None)
    ast={};data=rows(ast_run/'baseline/reazon.jsonl')
    for seed in (11,29):
        output=rows(ast_run/'AST-G4-OFFICIAL/quality'/f'mt-seed{seed}.jsonl');assert len(output)==88
        for source,result in zip(data,output):
            assert all(source[k]==result[k] for k in ('source_id','start_sample','end_sample','released_at_sample','pcm_path'))
            assert result['pcm_sha256']==digest_slice(result)
            audit=result['audio_audit']
            assert audit['tower']['calls']==audit['projection']['calls']==1
            assert audit['audio_placeholders']==audit['projection']['output_shape'][-2] and audit['features']['finite']
        times=[r['ast_s'] for r in output]
        ast[str(seed)]=dict(rows=88,valid=sum(r['filtered_zh'] is not None for r in output),
            invalid_source_ids=[r['source_id'] for r in output if r['filtered_zh'] is None],
            total_s=sum(times),p50_s=percentile(times,.5),p95_s=percentile(times,.95),max_s=max(times),
            generation_tokens=sum(r['generation_tokens'] for r in output),
            timing_scope='stream_generate includes WAV reload/features/tower/projection/LM/all JA+ZH; excludes initial PCM cut extraction/validation/WAV creation, template and seed; resource phase includes whole loop')
    native=json.loads((vox_run/'NATIVE-VOX-960/quality/asr-summary.json').read_text())
    assets=json.loads((vox_run/'asset-manifest.json').read_text())['assets'];assert len(native['sessions'])==len(assets)==9
    for s,a in zip(native['sessions'],assets):assert s['clip']==a['id'] and s['samples']==a['samples'] and s['pcm_sha256']==a['sha256'] and s['decoder_steps']<=8192 and s['drain_s']<=8
    source=rows(vox_run/'NATIVE-VOX-960/quality/reazon.jsonl')
    from .b3_protocol import source_seed
    original_asr=rows(vox_run/'baseline/reazon.jsonl')
    for r in source:
        assert 0<=r['start_sample']<r['end_sample']<=r['accepted_samples']
        assert source_seed(r,original_asr)==r['mt_seed_index']
    mt={}
    for seed in (11,29):
        output=rows(vox_run/'NATIVE-VOX-960/mt'/f'mt-seed{seed}.jsonl');assert len(output)==len(source)
        assert all(a['text_ja']==b['input_ja'] and a['source_id']==b['source_id'] for a,b in zip(source,output))
        times=[r['mt_s'] for r in output]
        mt[str(seed)]=dict(source_submissions=len(output),nonempty_model_calls=sum(bool(r['input_ja']) for r in output),empty_source_noops=sum(not r['input_ja'] for r in output),total_s=sum(times),p50_s=percentile(times,.5),p95_s=percentile(times,.95),max_s=max(times),invalid=sum(r['filtered_zh'] is None for r in output))
    corrected=json.loads((confirmation_run/'NATIVE-VOX-960/quality/asr-summary.json').read_text()) if confirmation_run else native
    if confirmation_run:
        assert digest(confirmation_run/'NATIVE-VOX-960/quality/reazon.jsonl')==digest(vox_run/'NATIVE-VOX-960/quality/reazon.jsonl')
        assert json.loads((confirmation_run/'reviews/full-session-parity.json').read_text())['all9_token_ids_identical']
    dump(out/'timing-offline.json',dict(ast=ast,native_sessions=[{k:v for k,v in session.items() if k!='text'} for session in corrected['sessions']],native_asr_total_s=sum(s['decode_s'] for s in corrected['sessions']),
        prior_observer_retention_native_asr_total_s=sum(s['decode_s'] for s in native['sessions']),
        native_source_units=len(source),native_mt=mt,not_realtime=True,not_word_timestamps=True))
    dump(out/'resource-export.json',dict(rows=resource,analysis_source_sha256=digest(Path(__file__)),
        units='CPU100%=one core; footprint/RSS/MLX/all-host GPU shared memory overlap; never sum; idle GPU delta is not dedicated model VRAM'))
    columns=['attempt','group','phase','wall_s','exit_code','samples','process_cpu_avg_pct','process_cpu_p95_pct','process_cpu_peak_pct',
        'process_footprint_peak_mib','footprint_median_mib','rss_sum_peak_mib','host_gpu_avg_pct','host_gpu_p95_pct','host_gpu_mem_peak_mib',
        'idle_host_gpu_avg_pct','idle_host_gpu_mem_median_mib','host_gpu_mem_peak_minus_idle_mib','cpu_seconds_sampled','scope']
    with (out/'resource-comparison.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(resource)
    dump(out/'memory-pressure-summary.json',pressure)
    dump(out/'validation.json',dict(attempts=verification,AST_2x88_audio_shape_path_checked=True,Vox9causal_input_sha_checked=True,
        native_resource_confirmation=confirmation_run.name if confirmation_run else 'NOT_RUN',
        native_source_units=len(source),resource_groups=len({(r['attempt'],r['group']) for r in resource}),resource_rows=len(resource),
        postanalysis_source_sha256=digest(Path(__file__)),human_verified=False,pending_listen=15,holdout='sealed',inference_rerun=False))
    return dict(resource_rows=len(resource),native_source_units=len(source))


def digest_slice(row):
    from hashlib import sha256
    with Path(row['pcm_path']).open('rb') as f:
        f.seek(row['start_sample']*2);raw=f.read((row['end_sample']-row['start_sample'])*2)
    return sha256(raw).hexdigest()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--ast-run',type=Path,required=True);p.add_argument('--vox-run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--confirmation-run',type=Path)
    a=p.parse_args();print(json.dumps(export(a.ast_run.resolve(),a.vox_run.resolve(),a.out.resolve(),a.confirmation_run.resolve() if a.confirmation_run else None)))
