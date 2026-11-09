"""Post-inference export for a B4 candidate stopped at its frozen G0 gate."""
import argparse
import csv
import json
from pathlib import Path

from . import b4
from .b4_protocol import ChineseOnlyProtocol,stream_chinese_time
from .freeze import digest,dump,rows


def export(run,out):
    manifest=b4.verify(run);out.mkdir(parents=True,exist_ok=True)
    state=json.loads((run/'state.json').read_text())['steps']
    candidate=state[b4.ID+'-capability']['result']
    if candidate['status']!='failed_task':raise ValueError('this exporter records only frozen G0 task failures')
    if b4.ID+'-fixed-cuts' in state or (run/b4.ID/'quality').exists():raise ValueError('unexpected full quality execution')
    parity=json.loads((run/b4.CONTROL/'quality/parity.json').read_text());assert parity['exact_rows']==176
    data=rows(run/b4.ID/'g0/rows-seed11.jsonl');old=rows(b4.PARENT/b4.CONTROL/'g0/rows-seed11.jsonl');assert len(data)==len(old)==5
    diagnostic=[]
    for a,r in zip(old,data):
        control='path' in r;source=Path(r['path'] if control else r['pcm_path'])
        with source.open('rb') as f:
            if not control:f.seek(r['start_sample']*2)
            samples=f.read() if control else f.read((r['end_sample']-r['start_sample'])*2)
        from hashlib import sha256
        assert sha256(samples).hexdigest()==r['pcm_sha256']==a['pcm_sha256']
        for key in ('features','mask','tower','projection'):assert r['audio_audit'][key]==a['audio_audit'][key]
        audit=r['audio_audit'];assert audit['tower']['calls']==audit['projection']['calls']==1
        assert audit['audio_placeholders']==audit['projection']['output_shape'][-2]
        parsed=ChineseOnlyProtocol().parse(r['raw_output'],r['finish_reason'])
        assert parsed['filtered_zh']==r['filtered_zh'] and parsed['protocol_status']==r['protocol_status']
        assert r['filtered_zh'] is None
        diagnostic.append(dict(source_id=r.get('source_id',r.get('id')),kind='nonspeech' if control else 'speech',
            finish_reason=r['finish_reason'],protocol_status=r['protocol_status'],valid_chinese=False,
            generation_tokens=r['generation_tokens'],generation_end_s=r['generation_end_s'],
            raw_internal_first_han_s=stream_chinese_time(r['events'],'zh_only'),valid_first_readable_chinese_s=None,
            pcm_sha256=r['pcm_sha256'],features_sha256=audit['features']['sha256'],placeholders=audit['audio_placeholders'],
            audio_path_calls=dict(tower=1,projection=1),parent_g0_features_identical=True))
    decision=dict(status='COMPLETE_FAILED_TASK_AT_G0',phase='B4',experiment=b4.ID,slot='B4-1',registered_routes=['Q'],
        stage_outcome='failed_task',audio_structure_valid=True,speech_protocol_valid=0,speech_probes=3,
        full_quality=dict(status='NOT_RUN',planned_rows_per_seed=88,planned_seeds=[11,29],semantic_denominator=34,hits=None,
            reason='frozen speech protocol G0 failed; no prompt/parser rescue, no0/34 score'),
        cost_hypothesis=dict(status='NOT_EVALUATED',reason='full88x2 paired candidate absent; no performance gain claim'),
        Q=False,Q_assessed=False,R_registered=False,engineering_eligible=False,
        safety=dict(protocol_valid_nonspeech_captions=0,diagnostic_nonspeech_probes=2,not_general_safety_proof=True,
            raw_behavior='English request-for-audio meta responses; not automatically fabricated speech'),
        controls=dict(reazon88_exact=True,m0_two62_exact=True,official176_exact=parity),
        human_verified=False,pending_listen=15,holdout='sealed',production_default='reazon-hy unchanged',
        next_action='stop_this_frozen_configuration; retain B4-1 reservation and two remaining slots',
        provenance=dict(manifest_sha256=digest(run/'run-manifest.json'),analysis_source_sha256=digest(Path(__file__)),
            capability_sha256=digest(run/b4.ID/'g0/capability.json')))
    dump(run/'decision.json',decision);dump(out/'summary.json',decision)
    dump(out/'g0-diagnostic.json',dict(rows=diagnostic,full_raw_outputs='local ignored evidence only',seed=11))
    exported=[];pressure={}
    for path in sorted((run/'resources').glob('*/comparison.json')):
        report=json.loads(path.read_text());meta=json.loads(path.with_name('resources.json').read_text());opened={};durations={};count={}
        for r in rows(path.with_name('phases.jsonl')):
            if r['phase']=='start':opened[r['clip']]=r['t']
            elif r['clip'] in opened:
                count[r['clip']]=count.get(r['clip'],0)+1;durations[f"{r['clip']}#{count[r['clip']]}"]=r['t']-opened.pop(r['clip'])
        idle=report['baseline'];idle_mem=idle.get('host_gpu_mem_median_mib')
        for phase,value in {'whole_run':report['whole_run'],**report['intervals']}.items():
            peak=value.get('host_gpu_mem_peak_mib')
            exported.append(value|dict(group=path.parent.name,phase=phase,wall_s=durations.get(phase),exit_code=meta['exit_code'],
                execution_scope='G0 five actual AST requests only' if path.parent.name=='zh-g0' else report['scope'],
                idle_host_gpu_avg_pct=idle.get('host_gpu_avg_pct'),idle_host_gpu_mem_median_mib=idle_mem,
                host_gpu_mem_peak_minus_idle_mib=peak-idle_mem if peak is not None and idle_mem is not None else None))
        points=[r['system_memory'] for r in rows(path.with_name('samples.jsonl')) if r.get('system_memory')]
        import re
        def counter(r,key):
            found=re.search(rf'^{key}:\s*([0-9]+)',r.get('vm_stat',''),re.MULTILINE)
            return int(found.group(1)) if found else None
        first,last=points[0],points[-1]
        pressure[path.parent.name]=dict(levels=sorted({r.get('memory_pressure','') for r in points}),swap_first=first['swap'],swap_last=last['swap'],
            swapins_pages_delta=counter(last,'Swapins')-counter(first,'Swapins') if counter(first,'Swapins') is not None else None,
            swapouts_pages_delta=counter(last,'Swapouts')-counter(first,'Swapouts') if counter(first,'Swapouts') is not None else None)
    dump(out/'resource-export.json',dict(rows=exported,analysis_source_sha256=digest(Path(__file__)),
        units='CPU100%=one core; footprint/RSS/MLX/all-host GPU shared memory overlap; never sum or call dedicatedVRAM',
        warning='official176 and candidate5 G0 are different workloads; not a resource or speed improvement comparison'))
    columns=['group','phase','wall_s','exit_code','samples','process_cpu_avg_pct','process_cpu_peak_pct','process_footprint_peak_mib',
        'footprint_median_mib','rss_sum_peak_mib','host_gpu_avg_pct','host_gpu_mem_peak_mib','idle_host_gpu_avg_pct',
        'idle_host_gpu_mem_median_mib','host_gpu_mem_peak_minus_idle_mib','cpu_seconds_sampled','execution_scope']
    with (out/'resource-comparison.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(exported)
    dump(out/'memory-pressure-summary.json',pressure)
    official={}
    for seed in (11,29):
        rs=rows(run/b4.CONTROL/'quality'/f'mt-seed{seed}.jsonl')
        official[str(seed)]=dict(requests=len(rs),valid=sum(r['filtered_zh'] is not None for r in rs),generation_tokens=sum(r['generation_tokens'] for r in rs),
            generation_call_sum_s=sum(r['generation_end_s'] for r in rs),first_chinese='per-call internal only; no1x timing')
    dump(out/'timing-offline.json',dict(official=official,candidate_G0=diagnostic,candidate_full_quality='NOT_RUN',protocol_cost_signal='NOT_EVALUATED'))
    dump(out/'validation.json',dict(fingerprint=manifest['fingerprint'],frozen_current_source_models_inputs_receipts=True,
        G0_five_PCM_feature_masks_tower_projection_match_parent=True,protocol_filter_recomputed=True,official176_parity=parity,
        candidate_full_quality_absent=True,tests=dict(targeted_passed=20,includes_B4_protocol=3),
        resource_groups=len(pressure),resource_rows=len(exported),postanalysis_source_sha256=digest(Path(__file__)),human_verified=False))
    dump(out/'budget.json',json.loads((run/'budget-execution.json').read_text())|dict(model_rerun=False,
        stopped_by='failed_task G0, not timeout; orchestrator exit1 retains outcome',whole_limit_s=7200,quality_limit_s=3600))
    dump(out/'model-runtime-lock.json',dict(runtime=json.loads((run/'locks/runtime.json').read_text()),
        protocols=json.loads((run/'locks/protocols.json').read_text()),model_revision='238767527555cb75a05732a84dff5d6ba0dd6809',
        runtime_commit='a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4',slot='B4-1',slots_remaining=2))
    return dict(status=decision['status'],resource_groups=len(pressure),resource_rows=len(exported))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(export(a.run_dir.resolve(),a.out.resolve())))
