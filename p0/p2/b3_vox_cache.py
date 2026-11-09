"""One registered Vox deployment diagnosis: bound MLX free cache at512MiB.

Existing B3 inference source stays immutable. This launcher changes allocator policy only;
its own source and policy must be frozen in a fresh attempt before any model output.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import traceback

from . import b1,b3
from .freeze import dump


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True);parser.add_argument('--capability',action='store_true')
    parser.add_argument('--worker',action='store_true');args=parser.parse_args();run=args.run_dir.resolve()
    policy=json.loads((run/'locks/vox-allocator-policy.json').read_text())
    if policy['cache_limit_bytes']!=536870912 or policy['targeted_vox_repair_number']!=1:raise ValueError('unregistered allocator policy')
    out=run/b3.IDS[0]/('g0' if args.capability else 'quality');out.mkdir(parents=True,exist_ok=True)
    if args.worker:
        b3.verify(run,role='vox')
        import mlx.core as mx
        from .b3_worker import vox
        old=mx.set_cache_limit(policy['cache_limit_bytes'])
        audit=dict(previous_cache_limit_bytes=old,cache_limit_bytes=policy['cache_limit_bytes'],
            scope='free allocator cache only; model/PCM/delay/greedy/steps/source commit unchanged',
            api_doc=mx.set_cache_limit.__doc__)
        dump(out/'allocator-policy-observed.json',audit)
        try:
            result=vox(run,out,args.capability)
        except Exception as exc:
            dump(out/'failure.json',dict(status='blocked_runtime',error=type(exc).__name__+': '+str(exc),
                traceback=traceback.format_exc(),quality_eligible=False,partial_outputs_retained=True,denominator_unchanged=34))
            raise
        finally:
            audit.update(end_cache_bytes=mx.get_cache_memory(),end_active_bytes=mx.get_active_memory(),peak_active_bytes=mx.get_peak_memory())
            dump(out/'allocator-policy-observed.json',audit)
        print(json.dumps(result,ensure_ascii=False));return
    state=json.loads((run/'state.json').read_text())['steps']
    control=state.get('B0-CURRENT-fixed-text',{})
    if state.get('B0-CURRENT-fixed-cuts',{}).get('status')!='passed' or not all(control.get('result',{}).get('parity',{}).get(str(s),False) for s in (11,29)):
        raise ValueError('same-attempt paired ASR and both M0 rounds required')
    if not args.capability and state.get(b3.IDS[0]+'-capability',{}).get('result',{}).get('status')!='ready_for_quality':raise ValueError('successful bounded-cache G0 required')
    def operation():
        code=subprocess.run([str(b3.PYTHONS['vox']),'-m','p0.p2.b3_vox_cache','--worker','--run-dir',str(run)]+(['--capability'] if args.capability else []),cwd=b3.ROOT).returncode
        if code:return json.loads((out/'failure.json').read_text()) if (out/'failure.json').exists() else dict(status='blocked_runtime',returncode=code)
        return json.loads((out/('capability.json' if args.capability else 'asr-summary.json')).read_text())
    result=b1.execute(run,b3.IDS[0]+('-capability' if args.capability else '-causal-stream'),operation,out,verify_run=b3.verify)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
