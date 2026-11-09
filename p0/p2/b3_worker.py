"""Isolated B3 native-audio capability and offline probes; one MLX owner."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import time
import traceback
import wave

from . import b1
from .freeze import digest,dump,rows
from .b3_protocol import ChineseProtocol,StreamCommitter,validate_audio,source_seed


def emit(path,item):
    with path.open('a') as f:f.write(json.dumps(item,ensure_ascii=False,allow_nan=False)+'\n')


def vox_session(model,pcm_path,cuts,clip,out):
    import mlx.core as mx
    import numpy as np
    s=model.create_streaming_session(max_tokens=8192,temperature=0.,transcription_delay_ms=960)
    committer=StreamCommitter(cuts);accepted=0;pieces=[];records=[];fed=sha256();started=time.monotonic();cap=False
    native=s._detokenizer
    import codecs
    byte_observer=codecs.getincrementaldecoder('utf-8')(errors='replace')
    pending_step=None;intervals=[]
    # Local observer classes can form GC cycles; retain no strong Session/model reference.
    import weakref
    observed_session=weakref.proxy(s)
    token_bytes=model._tokenizer.token_bytes
    class Observed:
        def __getattr__(self,key):return getattr(native,key)
        def add_token(self,token):
            nonlocal pending_step
            payload=token_bytes(token)
            if payload:
                if pending_step is None:pending_step=len(observed_session.generated)-1
                byte_observer.decode(payload,final=False)
            native.add_token(token)
        @property
        def last_segment(self):
            nonlocal pending_step
            text=native.last_segment
            if text:
                start=pending_step if pending_step is not None else max(0,len(observed_session.generated)-1)
                pieces.append((min(accepted,start*1280),min(accepted,len(observed_session.generated)*1280),text))
                intervals.append(dict(start_decoder_step=start,end_decoder_step=len(observed_session.generated),text=text,
                    estimated_start_sample=min(accepted,start*1280),estimated_end_sample=min(accepted,len(observed_session.generated)*1280)))
                pending_step=len(observed_session.generated)-1 if byte_observer.getstate()[0] else None
            return text
    s._detokenizer=Observed()
    def compute(eof=False):
        nonlocal cap
        previous=len(s.generated);pieces.clear();intervals.clear();tic=time.monotonic()
        remaining=8192-previous
        if remaining<=0:cap=True;raise RuntimeError('native cumulative8192 decoder-step cap')
        deltas=b1.bounded_translate(lambda:s.step(max_decode_tokens=min(4,remaining)))
        mx.synchronize()
        if len(s.generated)>8192:raise ValueError('native step exceeded adapter cap')
        frontier=min(accepted,len(s.generated)*1280)
        ingested=accepted-sum(len(p) for p in s._audio_q)
        result=committer.advance(accepted=accepted,frontier=frontier,pieces=list(pieces),eof=eof and s.done,pending_text_start=pending_step*1280 if pending_step is not None else None)
        for item in result:
            row=item|dict(clip=clip,segment_id=len(records)+1,source_id=f'{clip}/native-{len(records)+1}',
                accepted_samples=accepted,decoder_frontier_sample=frontier,pcm_path=str(pcm_path),
                native_generated_steps=len(s.generated),input_ja=item['text_ja'])
            # VAD is the external speech gate. Keep raw hallucinations in trace, never translate silence-only input.
            if not cuts:row.update(vad_suppressed=True,text_ja='',input_ja='')
            records.append(row);emit(out/'native-units.jsonl',row)
        emit(out/'native-events.jsonl',dict(clip=clip,event='step',accepted_samples=accepted,ingested_samples=ingested,
            generated_before=previous,generated_after=len(s.generated),token_ids=s.generated[previous:],
            decoder_frontier_sample=frontier,pieces=list(pieces),stable_decoder_intervals=list(intervals),delta=deltas,done=s.done,closed=s._audio_closed,
            step_s=time.monotonic()-tic,mlx_active_bytes=mx.get_active_memory(),mlx_peak_bytes=mx.get_peak_memory()))
        return len(s.generated)>previous
    # Read only each currently released packet. Model receives no path, full waveform or reference text.
    with Path(pcm_path).open('rb') as source:
        while raw:=source.read(6400):
            if s.done:raise RuntimeError('native decoder terminated before input EOF')
            pcm=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
            start=accepted;accepted+=len(pcm);fed.update(raw)
            s.feed(pcm.copy())
            if sum(len(p) for p in s._audio_q)>64000:raise RuntimeError('pending PCM exceeded64000')
            emit(out/'native-events.jsonl',dict(clip=clip,event='feed',start_sample=start,end_sample=accepted,
                packet_sha256=sha256(raw).hexdigest(),prefix_sha256=fed.hexdigest(),pending_pcm_samples=sum(len(p) for p in s._audio_q)))
            while compute():
                if s.done:break
    s.close();drain_started=time.monotonic()
    while not s.done:
        if time.monotonic()-drain_started>8:raise TimeoutError('native EOF drain exceeded8s')
        compute(eof=True)
        if time.monotonic()-drain_started>8:raise TimeoutError('native EOF drain exceeded8s')
    for item in committer.advance(accepted=accepted,frontier=min(accepted,len(s.generated)*1280),pieces=[],eof=True):
        row=item|dict(clip=clip,segment_id=len(records)+1,source_id=f'{clip}/native-{len(records)+1}',accepted_samples=accepted,
            decoder_frontier_sample=min(accepted,len(s.generated)*1280),pcm_path=str(pcm_path),native_generated_steps=len(s.generated),input_ja=item['text_ja'])
        if not cuts:row.update(vad_suppressed=True,text_ja='',input_ja='')
        records.append(row);emit(out/'native-units.jsonl',row)
    if committer.endpoint<len(cuts):raise ValueError('uncommitted VAD endpoints at EOF')
    if committer.last_committed_piece<len(committer.pieces):raise ValueError('unmapped emitted native suffix at EOF')
    if fed.hexdigest()!=digest(pcm_path):raise ValueError('causal feed SHA differs from PCM')
    return records,dict(clip=clip,samples=accepted,pcm_sha256=fed.hexdigest(),decoder_steps=len(s.generated),
        text=native.text,finish_reason='eos' if s.generated and s.generated[-1]==2 else 'audio_exhausted_no_eos',
        eos=bool(s.generated and s.generated[-1]==2),cap=cap,drain_s=time.monotonic()-drain_started,decode_s=time.monotonic()-started,
        session_reset='per continuous video, not per VAD cut',mt_queue='NOT_RUN here: separate fixed-text phase',
        left_pad_tokens=s._n_left,delay_tokens=s._n_delay,prompt_len=s._prompt_len,
        mapping='generated[k] predicted from audio slot L+D+k; estimated[k*1280,(k+1)*1280), not word timestamps')


def vox(run,out,capability):
    from p0.monitor import mark
    from mlx_audio.stt.utils import load
    import mlx.core as mx
    paths=b1.model_paths(run)
    mark('native-load','start');model=load(paths['voxtral']);mx.synchronize();mark('native-load','end')
    baseline=rows(run/'baseline/reazon.jsonl');assets=json.loads((run/'asset-manifest.json').read_text())['assets']
    probes=json.loads((run/'inputs/probes.json').read_text())
    music=next(c for c in probes['controls'] if c['id']=='synthetic-instrumental')
    selected=([next(a for a in assets if a['id']=='first-photo'),next(a for a in assets if a['id']=='silence'),
        dict(id=music['id'],path=music['path'],samples=Path(music['path']).stat().st_size//2)] if capability else assets)
    summaries=[];output=[];mark('native-capability' if capability else 'native-continuous9','start')
    for asset in selected:
        records,summary=vox_session(model,asset['path'],[r for r in baseline if r['clip']==asset['id']],asset['id'],out)
        for row in records:row['mt_seed_index']=source_seed(row,baseline)
        output.extend(records);summaries.append(summary);dump(out/f"session-{asset['id']}.json",summary)
        print(json.dumps(dict(stage='vox',clip=asset['id'],units=len(records),elapsed=summary['decode_s'])),flush=True)
    mark('native-capability' if capability else 'native-continuous9','end')
    b1.write_rows(out/'reazon.jsonl',output)
    result=dict(status='ready_for_quality' if capability else 'complete',sessions=summaries,rows=len(output),
        actual_audio_s=sum(a['samples'] for a in selected)/16000,causal_packets=True,realtime_paced=False,
        finish='native done not assumed EOS',full_chain_resource_pass=False)
    dump(out/('capability.json' if capability else 'asr-summary.json'),result)
    return result


def gemma(run,out,capability,*,instruction=None,parser=None,expected_prompt_sha256=None):
    import mlx.core as mx
    import numpy as np
    from mlx.utils import tree_flatten
    from mlx_vlm import load
    from mlx_vlm.generate import stream_generate
    import importlib
    dispatch=importlib.import_module('mlx_vlm.generate.dispatch')
    from mlx_vlm.prompt_utils import apply_chat_template
    from p0.monitor import mark
    mark('ast-load','start');model,processor=load(str(b1.model_paths(run)['gemma4']));mx.synchronize();mark('ast-load','end')
    if processor.feature_extractor is None or model.audio_tower is None or model.embed_audio is None:
        raise ValueError('missing full audio processor/tower/projector')
    if instruction is None:instruction=json.loads((run/'sources/official-prompt.json').read_text())['prompt']
    prompt=apply_chat_template(processor,model.config,instruction,num_audios=1,enable_thinking=False)
    if expected_prompt_sha256 is not None and sha256(prompt.encode()).hexdigest()!=expected_prompt_sha256:
        raise ValueError('rendered prompt differs from preregistered lock')
    dump(out/'prompt.json',dict(instruction=instruction,rendered=prompt,sha256=sha256(prompt.encode()).hexdigest(),num_audios=1,enable_thinking=False))
    # One bounded runtime repair: match ceil(N/640) placeholders with real encoder frames.
    # Original samples and attention masks are preserved; only internal invalid right padding changes.
    extractor=processor.feature_extractor;extractor_class=type(extractor);original_extract=extractor_class.__call__
    def aligned_extract(self,*args,**kwargs):
        if self is extractor:kwargs['pad_to_multiple_of']=640
        return original_extract(self,*args,**kwargs)
    extractor_class.__call__=aligned_extract
    dump(out/'runtime-repair.json',dict(reason='observed179 placeholders versus178 projected frames with128-sample padding',
        before=128,after=640,scope='feature extractor internal invalid right padding; original PCM and mask length retained',
        targeted_repair_number=1,source_commit_unchanged=True))
    weights={}
    for name,module in (('audio_tower',model.audio_tower),('embed_audio',model.embed_audio)):
        tensors=tree_flatten(module.parameters())
        if not tensors:raise ValueError('empty audio weights')
        weights[name]=dict(tensors=len(tensors),dtypes=sorted({str(v.dtype) for _,v in tensors}),
            elements=sum(v.size for _,v in tensors),examples=[dict(name=k,shape=list(v.shape),dtype=str(v.dtype)) for k,v in tensors[:5]])
    dump(out/'audio-weights.json',dict(processor=type(processor).__name__,feature_extractor=type(processor.feature_extractor).__name__,weights=weights))
    audit={};original_prepare=dispatch.prepare_inputs
    def tensor(value):
        arr=np.array(value.astype(mx.float32))
        return dict(shape=list(value.shape),dtype=str(value.dtype),finite=bool(np.isfinite(arr).all()),
            sha256=sha256(arr.tobytes()).hexdigest(),nonzero=int(np.count_nonzero(arr)))
    def prepare(*args,**kwargs):
        inputs=original_prepare(*args,**kwargs)
        if 'input_features' in inputs:
            audit['features']=tensor(inputs['input_features']);audit['mask']=tensor(inputs['input_features_mask'])
            audit['audio_placeholders']=int(mx.sum(inputs['input_ids']==model.config.audio_token_id).item())
            audit['input_ids_shape']=list(inputs['input_ids'].shape)
        return inputs
    dispatch.prepare_inputs=prepare
    # Observe the actual existing module class entry, without inserting/replacing any network layer or weights.
    patched=[]
    for name,module in (('tower',model.audio_tower),('projection',model.embed_audio)):
        cls=type(module);old=cls.__call__
        def wrap(self,*args,_old=old,_name=name,_target=module,**kwargs):
            result=_old(self,*args,**kwargs)
            if self is _target:
                value=result[0] if isinstance(result,tuple) else result
                audit[_name]=dict(calls=audit.get(_name,{}).get('calls',0)+1,output_shape=list(value.shape),dtype=str(value.dtype))
                if capability:audit[_name].update(tensor(value))
            return result
        cls.__call__=wrap;patched.append((cls,old))
    data=rows(run/'baseline/reazon.jsonl');probes=json.loads((run/'inputs/probes.json').read_text())
    selected=probes['speech']+probes['controls'] if capability else data
    parser=parser or ChineseProtocol();outputs=[]
    try:
        for seed in ((11,) if capability else (11,29)):
            mark(f'ast-{"g0" if capability else "88"}-seed{seed}','start')
            current=[]
            for index,row in enumerate(selected):
                audit.clear();control='path' in row;path=row['path'] if control else row['pcm_path']
                samples=np.fromfile(path,dtype='<i2')
                if not control:samples=samples[row['start_sample']:row['end_sample']]
                audio=samples.astype(np.float32)/32768;validate_audio(audio)
                wav=out/f'input-{index:03}.wav'
                if not wav.exists():
                    with wave.open(str(wav),'wb') as target:
                        target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000);target.writeframes(samples.tobytes())
                request_seed=seed+(row.get('mt_seed_index',0) if not capability else 0)
                mx.random.seed(request_seed)
                processor.tokenizer.stopping_criteria.reset(model.config.eos_token_id)
                started=time.monotonic();events=[]
                def generate():
                    final=None;raw=''
                    # No prompt_cache/vision_cache/APC: runtime allocates a new KV for every request.
                    for event in stream_generate(model,processor,prompt=prompt,audio=[str(wav)],max_tokens=256,temperature=1.,top_p=.95,top_k=64,seed=request_seed):
                        raw+=event.text;final=event
                        events.append(dict(t_s=time.monotonic()-started,text=event.text,tokens=event.generation_tokens,finish_reason=event.finish_reason))
                    mx.synchronize();return raw,final
                try:
                    raw,final=b1.bounded_translate(generate)
                except Exception as exc:
                    dump(out/f'request-seed{seed}-{index:03}-interrupted.json',dict(raw_output=''.join(e['text'] for e in events),events=events,
                        audio_audit=audit,error=type(exc).__name__+': '+str(exc),elapsed_s=time.monotonic()-started))
                    raise
                generation_end_s=time.monotonic()-started
                if final is None:raise ValueError('missing AST termination')
                dump(out/f'request-seed{seed}-{index:03}-precheck.json',dict(raw_output=raw,events=events,audio_audit=audit,finish_reason=final.finish_reason,elapsed_s=time.monotonic()-started))
                if not audit.get('tower') or not audit.get('projection') or not audit.get('features',{}).get('finite'):
                    raise ValueError('audio did not traverse actual tower/projection')
                # The generic masked_scatter silently repeats/truncates mismatched source; explicitly reject here.
                if audit['audio_placeholders']!=audit['projection']['output_shape'][-2]:raise ValueError('audio placeholder/projected-frame mismatch')
                if final.cached_tokens:raise ValueError('unexpected cross-request KV reuse')
                parsed=parser.parse(raw,final.finish_reason)
                item=row|parsed|dict(raw_output=raw,finish_reason=final.finish_reason,ast_s=time.monotonic()-started,
                    generation_end_s=generation_end_s,generation_tokens=final.generation_tokens,prompt_tokens=final.prompt_tokens,terminal_token=final.token,
                    token_ids=final.token_ids,seed=seed,audio_audit=json.loads(json.dumps(audit)),events=events,
                    pcm_sha256=sha256(samples.tobytes()).hexdigest(),mlx_active_bytes=mx.get_active_memory(),mlx_peak_bytes=mx.get_peak_memory())
                emit(out/f'rows-seed{seed}.jsonl',item);current.append(item);outputs.append(item)
                print(json.dumps(dict(stage='gemma',seed=seed,row=index+1,protocol=parsed['protocol_status'],elapsed=item['ast_s'])),flush=True)
            mark(f'ast-{"g0" if capability else "88"}-seed{seed}','end')
            if not capability:b1.write_rows(out/f'mt-seed{seed}.jsonl',current)
        if capability:
            # Missing audio is rejected at our external boundary, before constructing an audio placeholder.
            try:
                validate_audio(None)
                raise AssertionError('missing audio was accepted')
            except ValueError as exc:
                missing=dict(status='rejected_by_adapter',reason=str(exc),executed=True)
            valid=[r for r in outputs[:3] if r['filtered_zh']]
            changed=len({r['audio_audit']['features']['sha256'] for r in outputs[:3]})==3
            content_changed=len({r['raw_output'] for r in outputs[:3]})>1
            result=dict(status='ready_for_quality' if len(valid)>=2 and changed and content_changed else 'failed_task',
                speech_protocol_valid=len(valid),speech_probes=3,feature_changed=changed,content_changed=content_changed,
                missing_audio=missing,controls=[dict(id=r['id'],raw_output=r['raw_output'],protocol_status=r['protocol_status']) for r in outputs[3:]],
                structure_valid=True,diagnostic_synchronization=True,manual_listening=False,natural_music=False)
            dump(out/'capability.json',result);return result
        result=dict(status='complete',rows_per_seed=88,seeds=[11,29],valid_chinese=sum(r['filtered_zh'] is not None for r in outputs),
            fresh_kv=True,full_chain_resource_pass=False,actual_audio_s=sum((r['end_sample']-r['start_sample'])/16000 for r in data)*2)
        dump(out/'probe.json',result);return result
    finally:
        extractor_class.__call__=original_extract
        dispatch.prepare_inputs=original_prepare
        for cls,old in patched:cls.__call__=old


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--experiment',choices=('NATIVE-VOX-960','AST-G4-OFFICIAL'),required=True)
    parser.add_argument('--capability',action='store_true');args=parser.parse_args()
    from .b3 import verify
    verify(args.run_dir,role='vox' if args.experiment=='NATIVE-VOX-960' else 'gemma')
    out=args.run_dir/args.experiment/('g0' if args.capability else 'quality');out.mkdir(parents=True,exist_ok=True)
    try:
        result=(vox if args.experiment=='NATIVE-VOX-960' else gemma)(args.run_dir,out,args.capability)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except Exception as exc:
        dump(out/'failure.json',dict(status='blocked_runtime',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(),
            denominator_unchanged=34,quality_eligible=False,partial_outputs_retained=True))
        raise


if __name__=='__main__':main()
