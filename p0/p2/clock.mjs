// Map Node's hrtime to Python's host monotonic clock; macOS epochs differ.
import { spawn } from 'node:child_process';

export class HostClock {
  constructor(python) {
    this.child = spawn(python, ['-u', '-c',
      'import sys,time\nfor line in sys.stdin:\n print(time.monotonic_ns(),flush=True)'],
      { stdio:['pipe','pipe','inherit'] });
    this.pending = [];
    let input = '';
    this.child.stdout.on('data', chunk => {
      input += chunk;
      for (let end; (end=input.indexOf('\n'))>=0;) {
        const line=input.slice(0,end); input=input.slice(end+1);
        const call=this.pending.shift();
        if (call) { clearTimeout(call.timer); call.resolve(Number(line)); }
      }
    });
    this.child.on('exit', () => {
      for (const call of this.pending.splice(0)) { clearTimeout(call.timer); call.reject(Error('host clock exited')); }
    });
  }
  async calibrate() {
    const samples=[];
    for (let i=0;i<10;i++) {
      const start=Number(process.hrtime.bigint());
      const host=await new Promise((resolve,reject)=>{
        const call={resolve,reject,timer:setTimeout(()=>reject(Error('host clock timeout')),10000)};
        this.pending.push(call);this.child.stdin.write('\n');
      });
      const end=Number(process.hrtime.bigint());
      samples.push({node_start_ns:start,node_end_ns:end,host_ns:host});
    }
    const best=samples.reduce((a,b)=>a.node_end_ns-a.node_start_ns < b.node_end_ns-b.node_start_ns ? a:b);
    return {samples,offset_ns:best.host_ns-(best.node_start_ns+best.node_end_ns)/2,
      uncertainty_ns:(best.node_end_ns-best.node_start_ns)/2};
  }
  close() { this.child.stdin.end(); }
}
