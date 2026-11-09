"""Frozen B4 review linkage, gates and descriptive paired protocol-cost export."""
import argparse
import csv
import json
from pathlib import Path

from . import b4,b3_postprocess
from .b4_protocol import stream_chinese_time
from .freeze import digest,dump,rows
from p0.review import percentile


def packet(run):
    official={str(s):rows(run/b4.CONTROL/'quality'/f'mt-seed{s}.jsonl') for s in (11,29)}
    return b3_postprocess.packet(run,verify_run=b4.verify,candidate_folders=((b4.ID,'quality'),),paired_official=official)


def score(run):
    result=b3_postprocess.score(run,verify_run=b4.verify,experiments=(b4.ID,),phase='B4')
    candidate=result['candidates'][b4.ID]
    g0=json.loads((run/b4.ID/'g0/capability.json').read_text())
    candidate['G0_safety_pass']=g0['safety_pass']
    candidate['provisional_engineering_eligible']=bool(candidate['Q'] and g0['safety_pass'])
    if candidate['Q'] and not g0['safety_pass']:candidate['next_stage']='stop_engineering_safety_fail'
    result.update(slot='B4-1',parent_official_hits=[6,8],R_registered=False,R_primary=None,R_formal='NOT_REGISTERED: Q-only',
        protocol_cost='separate descriptive mechanism; never grants R or Q',human_verified=False)
    dump(run/'decision.json',result);return result


def distribution(values):
    actual=[v for v in values if v is not None]
    return dict(count=len(actual),missing=len(values)-len(actual),denominator=len(values),
        p50_s=percentile(actual,.5) if actual else None,p95_s=percentile(actual,.95) if actual else None)


def export(run,out):
    manifest=b4.verify(run);out.mkdir(parents=True,exist_ok=True)
    original=rows(run/'baseline/reazon.jsonl');paired={};timing={};events=[];integrity={}
    for name in (b4.CONTROL,b4.ID):
        timing[name]={};paired[name]={};integrity[name]={}
        for seed in (11,29):
            data=rows(run/name/'quality'/f'mt-seed{seed}.jsonl');assert len(data)==88
            valid_first=[];full=[];raw_first=[]
            for a,r in zip(original,data):
                assert all(a[k]==r[k] for k in ('source_id','start_sample','end_sample','released_at_sample','mt_seed_index'))
                audit=r['audio_audit'];assert audit['features']['finite'] and audit['mask']['finite']
                assert audit['tower']['calls']==audit['projection']['calls']==1
                assert audit['audio_placeholders']==audit['projection']['output_shape'][-2]
                raw_t=stream_chinese_time(r['events'],'official' if name==b4.CONTROL else 'zh_only')
                t=raw_t if r['filtered_zh'] is not None else None
                raw_first.append(raw_t);valid_first.append(t);full.append(r['generation_end_s'] if r['filtered_zh'] is not None else None)
                events.append(dict(experiment=name,seed=seed,source_id=r['source_id'],protocol_valid=r['filtered_zh'] is not None,
                    protocol_status=r['protocol_status'],finish_reason=r['finish_reason'],generation_tokens=r['generation_tokens'],
                    prompt_tokens=r['prompt_tokens'],raw_internal_first_han_s=raw_t,valid_first_readable_chinese_s=t,
                    valid_complete_chinese_s=full[-1],generation_end_s=r['generation_end_s'],ast_s=r['ast_s']))
            calls=[r['generation_end_s'] for r in data];legacy=[r['ast_s'] for r in data]
            timing[name][str(seed)]=dict(requests=88,valid=sum(x['filtered_zh'] is not None for x in data),
                generation_tokens=sum(r['generation_tokens'] for r in data),prompt_tokens=sum(r['prompt_tokens'] for r in data),
                generation_call_total_s=sum(calls),generation_call=distribution(calls),legacy_ast_total_s=sum(legacy),
                valid_first_readable_chinese=distribution(valid_first),raw_internal_first_han=distribution(raw_first),
                valid_complete_chinese=distribution(full),length_terminations=sum(r['finish_reason']=='length' for r in data))
            paired[name][str(seed)]={r['source_id']:r for r in data}
            integrity[name][str(seed)]=dict(audio_requests=88,original_spans_seed_indexes=True,full_audio_path=True,all_placeholders_match=True)
    cost={}
    for seed in ('11','29'):
        a=timing[b4.CONTROL][seed];b=timing[b4.ID][seed];o=paired[b4.CONTROL][seed];z=paired[b4.ID][seed]
        assert set(o)==set(z)
        for key in o:
            assert o[key]['pcm_sha256']==z[key]['pcm_sha256']
            assert o[key]['audio_audit']['features']==z[key]['audio_audit']['features']
            assert o[key]['audio_audit']['mask']==z[key]['audio_audit']['mask']
        common=[key for key in o if o[key]['filtered_zh'] is not None and z[key]['filtered_zh'] is not None]
        token_gain=1-b['generation_tokens']/a['generation_tokens'];time_gain=1-b['generation_call_total_s']/a['generation_call_total_s']
        cost[seed]=dict(token_reduction=token_gain,generation_wall_reduction=time_gain,valid_coverage_not_reduced=b['valid']>=a['valid'],
            mechanism_signal=token_gain>=.2 and time_gain>=.2 and b['valid']>=a['valid'],
            paired_common_valid_source_ids=common,paired_common_valid_count=len(common),
            common_valid_official_call=distribution([o[k]['generation_end_s'] for k in common]),
            common_valid_zh_call=distribution([z[k]['generation_end_s'] for k in common]),
            full_denominator=88,not_quality_or_R_eligibility=True)
    dump(out/'timing-offline.json',dict(protocols=timing,cost=cost,full_chinese_event_rows=events,
        timing_scope='internal call, incl audio reread/features/encoder/projector/LM; initial cut extraction/WAV creation excluded but in whole phase',
        limitation='official-first sequential order; instruction length also changes; no1x,word/semantic-correct orDOM claim'))
    exported=[];pressure={}
    for path in sorted((run/'resources').glob('*/comparison.json')):
        data=json.loads(path.read_text());meta=json.loads(path.with_name('resources.json').read_text());opened={};duration={};count={}
        for e in rows(path.with_name('phases.jsonl')):
            if e['phase']=='start':opened[e['clip']]=e['t']
            elif e['clip'] in opened:
                count[e['clip']]=count.get(e['clip'],0)+1;duration[f"{e['clip']}#{count[e['clip']]}"]=e['t']-opened.pop(e['clip'])
        idle=data['baseline'];idle_mem=idle.get('host_gpu_mem_median_mib')
        for phase,value in {'whole_run':data['whole_run'],**data['intervals']}.items():
            value=dict(value);peak=value.get('host_gpu_mem_peak_mib')
            if meta['exit_code']!=0:value.pop('cpu_seconds_per_request',None)
            exported.append(value|dict(group=path.parent.name,phase=phase,wall_s=duration.get(phase),exit_code=meta['exit_code'],
                idle_host_gpu_avg_pct=idle.get('host_gpu_avg_pct'),idle_host_gpu_mem_median_mib=idle_mem,
                host_gpu_mem_peak_minus_idle_mib=peak-idle_mem if peak is not None and idle_mem is not None else None,
                requested_workload=data.get('normalization'),unclosed_phases=list(opened)))
        points=[p['system_memory'] for p in rows(path.with_name('samples.jsonl')) if p.get('system_memory')]
        import re
        def counter(snapshot,key):
            m=re.search(rf'^{key}:\s*([0-9]+)',snapshot.get('vm_stat',''),re.MULTILINE)
            return int(m.group(1)) if m else None
        first,last=points[0],points[-1]
        pressure[path.parent.name]=dict(levels=sorted({p.get('memory_pressure','') for p in points}),swap_first=first.get('swap'),swap_last=last.get('swap'),
            swapins_pages_delta=counter(last,'Swapins')-counter(first,'Swapins') if counter(first,'Swapins') is not None else None,
            swapouts_pages_delta=counter(last,'Swapouts')-counter(first,'Swapouts') if counter(first,'Swapouts') is not None else None)
    dump(out/'resource-export.json',dict(rows=exported,analysis_source_sha256=digest(Path(__file__)),
        units='CPU100%=one core; footprint/RSS/MLX/all-host GPU shared memory overlap, never sum; no dedicated VRAM claim'))
    columns=['group','phase','wall_s','exit_code','samples','process_cpu_avg_pct','process_cpu_peak_pct','process_footprint_peak_mib',
        'footprint_median_mib','rss_sum_peak_mib','host_gpu_avg_pct','host_gpu_p95_pct','host_gpu_mem_peak_mib','idle_host_gpu_avg_pct',
        'idle_host_gpu_mem_median_mib','host_gpu_mem_peak_minus_idle_mib','cpu_seconds_sampled','scope']
    with (out/'resource-comparison.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(exported)
    dump(out/'memory-pressure-summary.json',pressure)
    dump(out/'validation.json',dict(fingerprint=manifest['fingerprint'],original_inputs_checked=True,models_runtime_receipts_checked=True,
        official176_literal_parity=json.loads((run/b4.CONTROL/'quality/parity.json').read_text()),protocols=integrity,
        feature_mask_pcm_equal_between_protocols=True,resource_groups=len(pressure),resource_rows=len(exported),
        human_verified=False,pending_listen=15,holdout='sealed',source_sha256=digest(Path(__file__))))
    return dict(resource_groups=len(pressure),resource_rows=len(exported),mechanism_signal=all(r['mechanism_signal'] for r in cost.values()))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(export(args.run_dir.resolve(),args.out.resolve())))
