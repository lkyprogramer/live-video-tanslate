"""Reuse the fixed full-audio worker for official and Chinese-only protocols."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import traceback

from . import b3,b4
from .b3_protocol import ChineseProtocol
from .b4_protocol import ChineseOnlyProtocol,ZH_PROMPT
from .freeze import dump


def render_locks(run):
    from mlx_vlm.utils import load_config
    from mlx_vlm.models.gemma4 import Gemma4Processor
    from mlx_vlm.prompt_utils import apply_chat_template
    snapshot=json.loads((run/'sources/model-paths.json').read_text())['gemma4']
    processor=Gemma4Processor.from_pretrained(snapshot);config=load_config(Path(snapshot));result={}
    official=json.loads((run/'sources/official-prompt.json').read_text())['prompt']
    for name,instruction in ((b4.CONTROL,official),(b4.ID,ZH_PROMPT)):
        rendered=apply_chat_template(processor,config,instruction,num_audios=1,enable_thinking=False)
        result[name]=dict(instruction=instruction,rendered=rendered,rendered_sha256=sha256(rendered.encode()).hexdigest(),
            num_audios=1,thinking=False,output_protocol='official' if name==b4.CONTROL else 'zh_only')
    dump(run/'locks/protocols.json',result)
    print(json.dumps(dict(status='rendered_without_model_inference',hashes={k:v['rendered_sha256'] for k,v in result.items()})))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--experiment',choices=(b4.ID,b4.CONTROL));parser.add_argument('--capability',action='store_true');parser.add_argument('--render-locks',action='store_true')
    args=parser.parse_args();run=args.run_dir.resolve()
    if args.render_locks:return render_locks(run)
    if not args.experiment:parser.error('--experiment required')
    b4.verify(run,role='gemma')
    spec=json.loads((run/'locks/protocols.json').read_text())[args.experiment]
    out=run/args.experiment/('g0' if args.capability else 'quality');out.mkdir(parents=True,exist_ok=True)
    try:
        from .b3_worker import gemma
        result=gemma(run,out,args.capability,instruction=spec['instruction'],
            parser=ChineseProtocol() if args.experiment==b4.CONTROL else ChineseOnlyProtocol(),expected_prompt_sha256=spec['rendered_sha256'])
        result['experiment_id']=args.experiment
        if args.capability:
            unwanted=[r['id'] for r in result['controls'] if r['protocol_status']=='valid']
            result.update(speech_structure_ready=result['status']=='ready_for_quality',safety_pass=not unwanted,
                nonspeech_valid_captions=unwanted,nonspeech_semantic_hallucination='requires separate AI text attribution; meta acknowledgements are not fabricated speech',engineering_eligible=False)
            if unwanted and result['speech_structure_ready']:result['status']='ready_for_offline_diagnostic_with_safety_fail'
        dump(out/('capability.json' if args.capability else 'probe.json'),result)
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        dump(out/'failure.json',dict(status='blocked_runtime',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(),
            quality_eligible=False,partial_outputs_retained=True,denominator_unchanged=34))
        raise


if __name__=='__main__':main()
