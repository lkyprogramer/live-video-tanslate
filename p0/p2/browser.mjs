// B0's isolated Chrome playback/component controls. No fullscreen or holdout tests.
import { spawn, execFileSync } from 'node:child_process';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, readFile, writeFile, appendFile, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { resolve, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { HostClock } from './clock.mjs';

const root = resolve(import.meta.dirname, '../..');
const run = resolve(process.argv[2]);
const mode = process.argv[3];
if (!['play', 'asr', 'mt', 'full'].includes(mode)) throw Error('Expected play|asr|mt|full');
execFileSync(join(root, 'engine/.venv/bin/python'), ['-m','p0.p2.cli','--run-dir',run,'preflight','--experiment','B0'], {cwd:root});
const output = join(run, 'browser-' + mode);
await mkdir(output);
const clip = join(run, 'inputs/browser-csj-task.mp4');
const length = (await stat(clip)).size;
const mono = () => Number(process.hrtime.bigint());
const hostClock = new HostClock(join(root,'engine/.venv/bin/python'));
const hostBefore = await hostClock.calibrate();
async function mark(clip, phase) {
  if (process.env.P0_RESOURCE_PHASES) await appendFile(process.env.P0_RESOURCE_PHASES,
    JSON.stringify({ t: (mono()+hostBefore.offset_ns)/1e9, clip, phase }) + '\n');
}
await mark('browser-load', 'start');
const site = createServer((req, res) => {
  if (req.url === '/clip.mp4') {
    const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range ?? '');
    const start = range ? +range[1] : 0;
    const end = range?.[2] ? Math.min(+range[2], length-1) : length-1;
    const headers = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', 'Content-Length': end-start+1 };
    if (range) headers['Content-Range'] = `bytes ${start}-${end}/${length}`;
    res.writeHead(range ? 206 : 200, headers);
    createReadStream(clip, { start, end }).pipe(res);
  } else {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end('<!doctype html><title>P2 B0</title><video width="640" height="360" controls preload="auto" src="/clip.mp4"></video>');
  }
});
await new Promise(resolve => site.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${site.address().port}`;
const profile = join(output, 'chrome-profile');
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--remote-debugging-pipe', '--enable-unsafe-extension-debugging', '--user-data-dir=' + profile,
  '--no-first-run', '--no-default-browser-check', '--disable-background-networking', 'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
const calls = new Map(), diagnostics = [];
let next = 0, pending = '', service, component, token;
chrome.stdio[4].setEncoding('utf8').on('data', chunk => {
  pending += chunk;
  for (let end; (end = pending.indexOf('\0')) >= 0;) {
    const event = JSON.parse(pending.slice(0, end));
    pending = pending.slice(end+1);
    if (event.method === 'Runtime.consoleAPICalled' && event.params.args[0]?.value === 'subtitle-diagnostic') {
      diagnostics.push(JSON.parse(event.params.args[1].value));
    }
    const call = calls.get(event.id);
    if (call) {
      calls.delete(event.id); clearTimeout(call.timer);
      event.error ? call.reject(Error(event.error.message)) : call.resolve(event.result);
    }
  }
});
function cdp(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++next, timer = setTimeout(() => { calls.delete(id); reject(Error(method + ' timeout')); }, 15000);
    calls.set(id, { resolve, reject, timer });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + '\0');
  });
}
async function page(url) {
  const { targetId } = await cdp('Target.createTarget', { url });
  const { sessionId } = await cdp('Target.attachToTarget', { targetId, flatten: true });
  await cdp('Runtime.enable', {}, sessionId);
  return { targetId, sessionId };
}
async function evaluate(tab, expression, userGesture = false) {
  const response = await cdp('Runtime.evaluate', { expression, userGesture, returnByValue: true, awaitPromise: true }, tab.sessionId);
  if (response.exceptionDetails) throw Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  return response.result.value;
}
async function until(fn, timeout, name) {
  const end = Date.now()+timeout;
  while (Date.now()<end) { const value = await fn(); if (value) return value; await sleep(120); }
  throw Error(name + ' timeout');
}
async function calibrate(tab) {
  const browser = [], backend = [];
  for (let i=0; i<10; i++) {
    let start = mono();
    const ms = await evaluate(tab, 'performance.timeOrigin+performance.now()');
    browser.push({ node_start_ns:start, node_end_ns:mono(), browser_ms:ms });
    if (token) {
      start = mono();
      const response = await fetch('http://127.0.0.1:18765/p2/clock', { headers:{ Authorization:'Bearer '+token } });
      if (!response.ok) throw Error('backend clock unavailable');
      backend.push({ node_start_ns:start, node_end_ns:mono(), backend_ns:(await response.json()).monotonic_ns });
    }
  }
  return { browser, backend };
}
const result = { mode, input:'csj-task', scope:'isolated Chrome, 640x360 H264/AAC, 1x',
  resource_clock_before:hostBefore,
  semantic_status:'AI text review required; captured AAC/resampled PCM differs from fixed original PCM',
  fullscreen:'not_tested_in_B0', holdout_accessed:false };
try {
  result.chrome = (await cdp('Browser.getVersion')).product;
  const { id } = await cdp('Extensions.loadUnpacked', { path:join(run, 'diagnostic-dist') });
  if (mode === 'full') {
    const config = (await readFile(join(run, 'locks/profiles.toml'), 'utf8'))
      .replace(/allowed_origin = "[^"]*"/, `allowed_origin = "chrome-extension://${id}"`)
      .replace('../../.model-cache', join(root, '.model-cache'));
    await writeFile(join(output, 'profiles.toml'), config);
    service = spawn(join(root, 'engine/.venv/bin/python'), ['-m', 'p0.p2.server', '--config', join(output, 'profiles.toml')], {
      cwd:root, env:{ ...process.env, P2_B0_RUN:run, P2_B0_SEED:'11', SUBTITLE_TOKEN_FILE:join(output, 'private.token'),
        SUBTITLE_BENCH_TRACE:join(output, 'model.jsonl') }, stdio:['ignore','pipe','pipe'],
    });
    const log = createWriteStream(join(output, 'server.log'));
    service.stdout.pipe(log, { end:false }); service.stderr.pipe(log, { end:false });
    service.once('close', () => log.end());
    await until(async () => { try { return (await fetch('http://127.0.0.1:18765/v1/health')).ok; } catch { return false; } }, 45000, 'service');
    token = (await readFile(join(output, 'private.token'), 'utf8')).trim();
    const options = await page(`chrome-extension://${id}/options.html`);
    await until(() => evaluate(options, '!!document.querySelector("#token")'), 10000, 'options');
    const configured = await evaluate(options, `(async()=>{
      await chrome.storage.local.set({token:${JSON.stringify(token)},profile_id:'reazon-hy'});
      const saved=await chrome.storage.local.get(['token','profile_id']);
      return saved.token===${JSON.stringify(token)}&&saved.profile_id==='reazon-hy';})()`);
    if (!configured) throw Error('isolated extension configuration was not persisted');
    const connected = await evaluate(options, `(async()=>{
      const response=await fetch('http://127.0.0.1:18765/v1/profiles',{headers:{Authorization:'Bearer '+${JSON.stringify(token)}}});
      return response.ok;})()`);
    if (!connected) throw Error('configured extension cannot reach the authenticated catalog');
  } else if (mode !== 'play') {
    component = spawn(join(root, 'engine/.venv/bin/python'), ['-m', 'p0.p2.controls', run, output, mode], {
      cwd:root, env:process.env, stdio:['pipe','pipe','pipe'],
    });
    let text = '', ready = false;
    const log = createWriteStream(join(output, 'component.log'));
    component.stderr.pipe(log, { end:false });
    component.stdout.on('data', chunk => { text+=chunk; log.write(chunk); ready ||= text.includes('"type": "ready"'); });
    await until(() => ready || (component.exitCode !== null && Promise.reject(Error('component exited before ready'))), 45000, 'component warmup');
  }
  const tab = await page(url);
  await cdp('Target.activateTarget', { targetId:tab.targetId });
  await cdp('Page.bringToFront', {}, tab.sessionId);
  await until(() => evaluate(tab, 'document.querySelector("video")?.readyState>=3'), 10000, 'video load');
  await evaluate(tab, `(() => { const v=document.querySelector('video');window.p2frames=[];window.p2visibility=[];
    const visibility=()=>window.p2visibility.push({t:performance.timeOrigin+performance.now(),
      hidden:document.hidden,has_focus:document.hasFocus()});
    document.addEventListener('visibilitychange',visibility);visibility();
    const frame=(now,m)=>{window.p2frames.push({stage:'video_frame',t:performance.timeOrigin+m.expectedDisplayTime,
      media_time:m.mediaTime});v.requestVideoFrameCallback(frame)};v.requestVideoFrameCallback(frame); })()`);
  result.clock_before = await calibrate(tab);
  await mark('browser-load', 'end');
  await mark('playback', 'start');
  result.play_node_ns = mono();
  await evaluate(tab, 'document.querySelector("video").play()', true);
  if (component) component.stdin.end('go\n');
  if (mode === 'full') {
    const { targetInfo } = await cdp('Target.getTargetInfo', { targetId:tab.targetId });
    const target = (await cdp('Target.getTargets', { filter:[{type:'tab'},{exclude:true}] })).targetInfos.find(t=>t.url===targetInfo.url);
    if (!target) throw Error('No browser tab target');
    await cdp('Extensions.triggerAction', { id, targetId:target.targetId });
    const offscreen = await until(async () => (await cdp('Target.getTargets')).targetInfos.find(t=>t.url===`chrome-extension://${id}/offscreen.html`), 10000, 'offscreen');
    const attached = await cdp('Target.attachToTarget', { targetId:offscreen.targetId, flatten:true });
    await cdp('Runtime.enable', {}, attached.sessionId);
  }
  await until(() => evaluate(tab, 'document.querySelector("video").ended'), 55000, 'playback end');
  result.end_node_ns = mono();
  await mark('playback', 'end');
  if (mode === 'full') {
    await until(() => diagnostics.some(e=>e.stage==='ws_received' && e.type==='finished'), 10000, 'gateway EOF');
    await sleep(4200);
    result.ws_captions = diagnostics.filter(e=>e.stage==='ws_received' && e.type==='caption').length;
    result.dom_visible = diagnostics.filter(e=>e.stage==='dom_visible').length;
    result.first_pcm = diagnostics.find(e=>e.stage==='first_pcm_sent');
  }
  if (component) { await until(() => component.exitCode !== null, 10000, 'component finish'); if (component.exitCode) throw Error('component failed'); }
  result.visibility_events = await evaluate(tab, 'window.p2visibility');
  if (result.visibility_events.some(e=>e.hidden)) throw Error('playback page became hidden; retain invalid measurement');
  result.clock_after = await calibrate(tab);
  result.resource_clock_after = await hostClock.calibrate();
  result.resource_clock_drift_ns = result.resource_clock_after.offset_ns-hostBefore.offset_ns;
  if (Math.abs(result.resource_clock_drift_ns)>Math.max(1e6,hostBefore.uncertainty_ns+result.resource_clock_after.uncertainty_ns)) {
    throw Error('host clock mapping drift exceeded uncertainty');
  }
  const frames = await evaluate(tab, 'window.p2frames');
  result.video_frames = frames.length;
  result.video_state = await evaluate(tab, '({duration:document.querySelector("video").duration,ended:document.querySelector("video").ended,muted:document.querySelector("video").muted})');
  if (!result.video_state.ended || result.video_state.muted || frames.length < 500) throw Error('invalid fixed playback workload');
  await writeFile(join(output, 'browser.jsonl'), [...diagnostics, ...frames].sort((a,b)=>a.t-b.t).map(e=>JSON.stringify(e)).join('\n')+'\n');
  const { data } = await cdp('Page.captureScreenshot', { format:'png' }, tab.sessionId);
  await writeFile(join(output, 'page.png'), Buffer.from(data, 'base64'));
  result.completed = true;
} catch (error) {
  result.completed = false; result.error = String(error);
  await writeFile(join(output, 'browser-partial.jsonl'), diagnostics.map(e=>JSON.stringify(e)).join('\n')+'\n');
  process.exitCode = 1;
} finally {
  hostClock.close();
  await writeFile(join(output, 'result.json'), JSON.stringify(result, null, 2)+'\n');
  for (const child of [component, service]) if (child?.exitCode === null) child.kill('SIGTERM');
  await cdp('Browser.close').catch(()=>undefined);
  chrome.kill(); site.closeAllConnections(); site.close();
  await sleep(500); await rm(profile, { recursive:true, force:true });
}
console.log(JSON.stringify({ mode, completed:result.completed, captions:result.ws_captions, frames:result.video_frames, error:result.error }));
