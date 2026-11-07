// Isolated installed Chrome with real tabCapture, local HTML5 audio and a real service.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { setTimeout as sleep } from 'node:timers/promises';
import { resolve, join } from 'node:path';
import { tmpdir } from 'node:os';

const root = resolve(import.meta.dirname, '../..');
const evidence = resolve(process.argv[2] ?? 'p0/evidence/v1-browser');
const switchProfiles = process.argv.includes('--switch');
const tryYoutube = process.argv.includes('--youtube');
const lifecycle = process.argv.includes('--lifecycle');
const tryWikimedia = process.argv.includes('--wikimedia');
const diagnosticDist = process.env.SUBTITLE_DIAGNOSTIC_DIST;
await mkdir(evidence, { recursive: false });
const temporary = await mkdtemp(join(tmpdir(), 'subtitle-chrome-'));
const clip = join(temporary, 'csj.mp4');
execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x20334e:s=640x360:r=15',
  '-f', 's16le', '-ar', '16000', '-ac', '1', '-i', join(root, 'p0/fixtures/csj-task.pcm'),
  '-c:v', 'libx264', '-c:a', 'aac', '-shortest', '-movflags', '+faststart', clip]);
const length = (await stat(clip)).size;
const site = createServer((req, res) => {
  if (req.url === '/clip.mp4') {
    const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range ?? '');
    const start = range ? +range[1] : 0;
    const end = range?.[2] ? Math.min(+range[2], length - 1) : length - 1;
    const headers = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1 };
    if (range) headers['Content-Range'] = `bytes ${start}-${end}/${length}`;
    res.writeHead(range ? 206 : 200, headers);
    createReadStream(clip, { start, end }).pipe(res);
  } else {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end('<!doctype html><title>本地视频验收</title><main id="player" style="width:640px"><video width="640" height="360" controls preload="auto" src="/clip.mp4"></video></main>');
  }
});
await new Promise(r => site.listen(0, '127.0.0.1', r));
const pageUrl = `http://127.0.0.1:${site.address().port}`;
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--remote-debugging-pipe', '--enable-unsafe-extension-debugging',
  `--user-data-dir=${join(temporary, 'chrome')}`, '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', 'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });

let next = 0;
let input = '';
const calls = new Map();
const received = [];
const diagnosticEvents = [];
chrome.stdio[4].setEncoding('utf8').on('data', chunk => {
  input += chunk;
  for (let i; (i = input.indexOf('\0')) >= 0;) {
    const message = JSON.parse(input.slice(0, i));
    input = input.slice(i + 1);
    if (message.method === 'Network.webSocketFrameReceived' && message.params.response.opcode === 1) {
      try { received.push(JSON.parse(message.params.response.payloadData)); } catch {}
    }
    if (diagnosticDist && message.method === 'Runtime.consoleAPICalled' &&
        message.params.args[0]?.value === 'subtitle-diagnostic') {
      diagnosticEvents.push(JSON.parse(message.params.args[1].value));
    }
    const reply = calls.get(message.id);
    if (reply) {
      calls.delete(message.id);
      clearTimeout(reply.timer);
      if (message.error) reply.reject(Error(message.error.message));
      else reply.resolve(message.result);
    }
  }
});
function cdp(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++next;
    const timer = setTimeout(() => { calls.delete(id); reject(Error(method + ' timed out')); }, 15000);
    calls.set(id, { resolve, reject, timer });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + '\0');
  });
}
async function page(url) {
  const { targetId } = await cdp('Target.createTarget', { url });
  const { sessionId } = await cdp('Target.attachToTarget', { targetId, flatten: true });
  return { targetId, sessionId };
}
async function evalIn(tab, expression, userGesture = false) {
  const { result, exceptionDetails } = await cdp('Runtime.evaluate', {
    expression, userGesture, returnByValue: true, awaitPromise: true,
  }, tab.sessionId);
  if (exceptionDetails) throw Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}
async function until(fn, ms = 20000, message = 'condition') {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const value = await fn();
    if (value) return value;
    await sleep(150);
  }
  throw Error(message + ' timed out');
}
const overlay = `document.querySelector('[data-local-video-subtitle]')?.shadowRoot.querySelector('span')`;
const video = 'document.querySelector("video")';
let service;
let inspected;
const results = {};
try {
  const version = await cdp('Browser.getVersion');
  results.chrome = version.product;
  const { id } = await cdp('Extensions.loadUnpacked', { path: diagnosticDist || join(root, 'extension/dist') });
  const config = (await readFile(join(root, 'engine/config/profiles.example.toml'), 'utf8'))
    .replace('<extension-id>', id).replace('../../.model-cache', join(root, '.model-cache'))
    .replace('enabled = false', switchProfiles ? 'enabled = true' : 'enabled = false');
  await writeFile(join(evidence, 'profiles.toml'), config);
  const tokenPath = join(temporary, 'token');
  async function launchService() {
    service = spawn(join(root, 'engine/.venv/bin/subtitle-server'),
      ['--config', join(evidence, 'profiles.toml')],
      { env: { ...process.env, SUBTITLE_TOKEN_FILE: tokenPath,
        ...(diagnosticDist ? { SUBTITLE_BENCH_TRACE: join(evidence, 'model.jsonl') } : {}) },
        stdio: ['ignore', 'pipe', 'pipe'] });
    const log = createWriteStream(join(evidence, 'server.log'), { flags: 'a' });
    service.stdout.pipe(log, { end: false });
    service.stderr.pipe(log, { end: false });
    service.once('close', () => log.end());
    await until(async () => {
      try { return (await fetch('http://127.0.0.1:18765/v1/health')).ok; } catch { return false; }
    }, 30000, 'service readiness');
  }
  await launchService();
  const token = (await readFile(tokenPath, 'utf8')).trim();
  const options = await page(`chrome-extension://${id}/options.html`);
  await until(() => evalIn(options, '!!document.querySelector("#token")'));
  await evalIn(options, `document.querySelector('#token').value = ${JSON.stringify(token)};
      document.querySelector('form').requestSubmit()`);
  await until(() => evalIn(options, 'document.querySelector("#status").textContent === "已保存。"'));
  await evalIn(options, 'document.querySelector("#refresh").click()');
  await until(() => evalIn(options, 'document.querySelector("#status")?.textContent.includes("已连接")'), 8000, 'options catalog');
  results.profiles = await evalIn(options, '[...document.querySelector("#profile").options].map(x => x.value)');
  const tab = await page(pageUrl);
  inspected = tab;
  await cdp('Target.activateTarget', { targetId: tab.targetId });
  await until(() => evalIn(tab, `${video}?.readyState >= 3`), 10000, 'local video load');
  const trigger = async t => {
    const { targetInfo } = await cdp('Target.getTargetInfo', { targetId: t.targetId });
    const { targetInfos } = await cdp('Target.getTargets', { filter: [{ type: 'tab' }, { exclude: true }] });
    const target = targetInfos.find(info => info.url === targetInfo.url);
    if (!target) throw Error('No matching Chrome tab target: ' + JSON.stringify(targetInfos.map(({type,url}) => ({type,url}))));
    return cdp('Extensions.triggerAction', { id, targetId: target.targetId });
  };
  async function startAndRead(t, seek = 0) {
    await evalIn(t, `${video}.currentTime = ${seek}; void ${video}.play()`, true);
    await trigger(t);
    return until(() => evalIn(t, `(() => { const s=${overlay}; return s && !s.hasAttribute('data-status')
      && /照[片相]|名字|不认识|戴着眼镜/.test(s.textContent) ? s.textContent : null; })()`), 24000, 'visible translated sentence');
  }
  if (diagnosticDist) {
    await cdp('Runtime.enable', {}, tab.sessionId);
    await evalIn(tab, `(() => {
      const v=${video}, frames=window.subtitleDiagnosticFrames=[];
      const capture=(now,m) => {
        frames.push({stage:'video_frame', t:performance.timeOrigin + m.expectedDisplayTime,
          media_time:m.mediaTime, current_time:v.currentTime, playback_rate:v.playbackRate});
        v.requestVideoFrameCallback(capture);
      };
      v.requestVideoFrameCallback(capture);
      v.addEventListener('ended', () => frames.push({stage:'video_ended', t:performance.timeOrigin + performance.now(),
        media_time:v.currentTime, playback_rate:v.playbackRate}), {once:true});
    })()`);
    diagnosticEvents.push({ stage: 'start_action', t: performance.timeOrigin + performance.now() });
    await evalIn(tab, `void ${video}.play()`, true);
    await trigger(tab);
    const target = await until(async () => (await cdp('Target.getTargets')).targetInfos
      .find(t => t.url === `chrome-extension://${id}/offscreen.html`), 10000, 'offscreen diagnostics');
    const { sessionId: audioSession } = await cdp('Target.attachToTarget', { targetId: target.targetId, flatten: true });
    await cdp('Runtime.enable', {}, audioSession);
    await until(() => evalIn(tab, `${video}.ended`), 47000, 'diagnostic video ends');
    await until(() => diagnosticEvents.some(e => e.stage === 'ws_received' && e.type === 'finished'), 9000, 'WS drain');
    await until(() => evalIn(tab, `!(${overlay})?.textContent`), 6000, 'tail subtitle disappears');
    const frames = await evalIn(tab, 'window.subtitleDiagnosticFrames');
    await writeFile(join(evidence, 'browser.jsonl'), [...diagnosticEvents, ...frames].sort((a, b) => a.t - b.t)
      .map(e => JSON.stringify(e)).join('\n') + '\n');
    const visible = diagnosticEvents.filter(e => e.stage === 'dom_visible');
    const arrivals = diagnosticEvents.filter(e => e.stage === 'ws_received' && e.type === 'caption');
    assert.ok(visible.length > 0 && frames.length > 0);
    assert.ok(diagnosticEvents.some(e => e.stage === 'first_pcm_sent'));
    for (const v of visible) {
      assert.ok(arrivals.some(a => a.session_id === v.session_id && a.seq === v.seq && a.t <= v.t));
      assert.ok(diagnosticEvents.some(e => e.stage === 'dom_removed' && e.session_id === v.session_id &&
        e.seq === v.seq && e.t >= v.t));
    }
    results.diagnostics = { wsCaptions: arrivals.length, visibleCaptions: visible.length, videoFrames: frames.length };
    await trigger(tab);
    await until(() => evalIn(tab, '!document.querySelector("[data-local-video-subtitle]")'), 5000, 'diagnostic overlay stops');
  }
  const starting = performance.now();
  results.local = await startAndRead(tab);
  results.firstVisibleMs = Math.round(performance.now() - starting);
  const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab.sessionId);
  await writeFile(join(evidence, 'local-caption.png'), Buffer.from(data, 'base64'));
  await evalIn(tab, `${video}.pause();`);
  await until(() => evalIn(tab, `!(${overlay})?.textContent`), 5000, 'caption clears on pause');
  await evalIn(tab, `${video}.currentTime = 9; ${video}.currentTime = 13; ${video}.currentTime = 3;`);
  assert.equal(await evalIn(tab, `(${overlay})?.textContent ?? ''`), '');
  await evalIn(tab, `void ${video}.play();`, true);
  results.afterSeek = await until(() => evalIn(tab, `(() => { const s=${overlay}; return s && !s.hasAttribute('data-status')
    && /照[片相]|图片|名字|不认识|研究|眼镜/.test(s.textContent) ? s.textContent : null; })()`), 20000, 'caption after seek');
  await cdp('Page.bringToFront', {}, tab.sessionId);
  async function fullscreen(selector) {
    // Keep real fullscreen behavior without depending on the test window's button coordinates.
    await evalIn(tab, `document.querySelector(${JSON.stringify(selector)}).requestFullscreen()`, true);
  }
  await fullscreen('#player');
  await until(() => evalIn(tab, 'document.fullscreenElement?.querySelector("[data-local-video-subtitle]") !== null && document.fullscreenElement !== null'),
    5000, 'fullscreen overlay');
  await evalIn(tab, 'document.exitFullscreen()');
  results.fullscreen = true;
  if (lifecycle) {
    const badge = async () => evalIn(options, `(async () => {
      const t=(await chrome.tabs.query({active:true,currentWindow:true}))[0];
      return chrome.action.getBadgeText({tabId:t.id});
    })()`);
    await fullscreen('video');
    await until(() => evalIn(tab, 'document.fullscreenElement?.tagName === "VIDEO"'), 5000, 'native video fullscreen');
    await until(async () => await badge() === '!', 5000, 'native fullscreen badge');
    await evalIn(tab, 'document.exitFullscreen()');
    await until(async () => await badge() === '中', 5000, 'restored subtitle badge');
    results.nativeFullscreenNotice = true;
  }
  await trigger(tab);
  await until(() => evalIn(tab, '!document.querySelector("[data-local-video-subtitle]")'), 5000, 'stopped overlay');
  if (switchProfiles) {
    results.switches = [];
    for (const profile of ['whisper-small', 'reazon-hy']) {
      await evalIn(options, `document.querySelector('#profile').value = ${JSON.stringify(profile)};
        document.querySelector('form').requestSubmit()`);
      await until(() => evalIn(options, 'document.querySelector("#status").textContent === "已保存。"'));
      await cdp('Target.activateTarget', { targetId: tab.targetId });
      results.switches.push({ profile, caption: await startAndRead(tab) });
      await trigger(tab);
      await until(() => evalIn(tab, '!document.querySelector("[data-local-video-subtitle]")'));
    }
  }
  if (tryYoutube) {
    let youtube;
    try {
      youtube = await page('https://www.youtube.com/watch?v=sVROs8Mje3Y');
      await cdp('Target.activateTarget', { targetId: youtube.targetId });
      await until(() => evalIn(youtube, `!!${video}`), 18000, 'YouTube video element');
      await evalIn(youtube, `void ${video}.play().catch(() => undefined)`, true);
      await until(() => evalIn(youtube, `${video}?.readyState >= 3 && !${video}.paused`), 15000, 'YouTube playback');
      await trigger(youtube);
      results.youtube = await until(() => evalIn(youtube, `(() => { const s=${overlay}; return s && !s.hasAttribute('data-status')
        && /[一-龯]/.test(s.textContent) ? s.textContent : null; })()`), 20000, 'YouTube caption');
      await trigger(youtube);
    } catch (error) {
      results.youtube = { error: String(error) };
      if (youtube) results.youtube.page = await evalIn(youtube, `({
        title: document.title, ready: ${video}?.readyState,
        url: location.href, message: document.body?.innerText?.slice(0, 220)
      })`).catch(() => undefined);
    }
  }
  if (tryWikimedia) {
    const online = await page('https://commons.wikimedia.org/wiki/File:Offsiderule.webm');
    await cdp('Target.activateTarget', { targetId: online.targetId });
    try {
      await until(() => evalIn(online, `!!${video}`), 18000, 'Wikimedia video element');
      await evalIn(online, `void ${video}.play().catch(() => undefined)`, true);
      await until(() => evalIn(online, `${video}.readyState >= 3 && !${video}.paused`), 18000, 'Wikimedia playback');
      await trigger(online);
      results.wikimedia = await until(() => evalIn(online, `(() => { const s=${overlay}; return s && !s.hasAttribute('data-status')
        && s.textContent.includes('越位') ? s.textContent : null; })()`), 25000, 'Wikimedia caption');
      const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, online.sessionId);
      await writeFile(join(evidence, 'wikimedia-caption.png'), Buffer.from(data, 'base64'));
      await trigger(online);
    } catch (error) {
      results.wikimedia = { error: String(error) };
      results.wikimedia.page = await evalIn(online, `({ title: document.title, ready: ${video}?.readyState,
        message: document.body?.innerText?.slice(0, 120) })`).catch(() => undefined);
    }
  }
  if (lifecycle) {
    results.lifecycle = {};
    await cdp('Target.activateTarget', { targetId: tab.targetId });
    await startAndRead(tab);
    const { targetInfos } = await cdp('Target.getTargets');
    const offscreenTarget = targetInfos.find(t => t.url === `chrome-extension://${id}/offscreen.html`);
    assert.ok(offscreenTarget, 'Chrome must expose the existing offscreen target');
    const { sessionId: audioSession } = await cdp('Target.attachToTarget', { targetId: offscreenTarget.targetId, flatten: true });
    await cdp('Network.enable', {}, audioSession);
    const ready = () => received.filter(x => x.type === 'ready').at(-1)?.session_id;
    const second = await page(pageUrl + '/second');
    inspected = second;
    await until(() => evalIn(second, `${video}?.readyState >= 3`));
    assert.equal(await evalIn(second, '!document.querySelector("[data-local-video-subtitle]")'), true);
    await cdp('Target.activateTarget', { targetId: second.targetId });
    results.lifecycle.takeover = await startAndRead(second);
    await until(() => evalIn(tab, '!document.querySelector("[data-local-video-subtitle]")'), 6000, 'previous tab clears');
    const previousSession = ready();
    assert.ok(previousSession, 'takeover has a new WebSocket');
    await evalIn(second, 'history.pushState({}, "", "/second?v=another");');
    await until(() => ready() && ready() !== previousSession, 5000, 'source change opens a new session');
    results.lifecycle.sourceChange = true;
    const beforeRate = ready();
    await evalIn(second, `${video}.playbackRate=1.5; ${video}.currentTime=0;`);
    await until(() => ready() && ready() !== beforeRate, 5000, 'new session at 1.5x');
    const rateSession = ready();
    await until(() => received.filter(x => x.type === 'caption' && x.session_id === rateSession).length >= 2,
      26000, 'ordered captions at 1.5x');
    const atRate = received.filter(x => x.type === 'caption' && x.session_id === rateSession);
    assert.deepEqual(atRate.map(x => x.seq), atRate.map((_, i) => i + 1));
    results.lifecycle.rate15 = atRate.map(x => x.text_zh);
    const workers = (await cdp('Target.getTargets')).targetInfos;
    const worker = workers.find(t => t.type === 'service_worker' && t.url.endsWith(id + '/worker.js'));
    assert.ok(worker, 'Chrome must expose the worker target');
    await cdp('ServiceWorker.enable', {}, options.sessionId);
    await cdp('ServiceWorker.stopAllWorkers', {}, options.sessionId);
    await until(async () => !(await cdp('Target.getTargets')).targetInfos.some(t => t.targetId === worker.targetId), 5000, 'worker stops');
    const beforeEof = received.filter(x => x.type === 'finished').length;
    await evalIn(second, `${video}.playbackRate=1; ${video}.currentTime=36; ${video}.play()`, true);
    await until(() => evalIn(second, `${video}.ended`), 9000, 'video ends');
    await until(() => received.filter(x => x.type === 'finished').length > beforeEof, 8000, 'tail drain');
    results.lifecycle.eof = true;
    await evalIn(second, `${video}.currentTime = 3; void ${video}.play()`, true);
    await until(() => evalIn(second, `(() => { const s=${overlay}; return s && !s.hasAttribute('data-status')
      && /照[片相]|图片|名字|不认识|研究|眼镜/.test(s.textContent); })()`), 18000, 'restart after EOF');
    assert.ok((await cdp('Target.getTargets')).targetInfos.some(t => t.url === worker.url && t.targetId !== worker.targetId));
    results.lifecycle.workerRestart = true;
    const exited = once(service, 'exit');
    service.kill('SIGTERM');
    await until(() => evalIn(second, `(${overlay})?.hasAttribute('data-status') && (${overlay}).textContent.includes('连接不到')`),
      5000, 'disconnect status');
    results.lifecycle.disconnect = true;
    await exited;
    await launchService();
    results.lifecycle.serviceRestart = await startAndRead(second);
    await cdp('Page.navigate', { url: pageUrl + '/hard-navigation' }, second.sessionId);
    await until(() => evalIn(options, '(async () => !(await chrome.tabCapture.getCapturedTabs())' +
      '.some(t => t.status === "active" || t.status === "pending"))()'), 8000, 'hard navigation releases capture');
    results.lifecycle.navigationStopsCapture = true;
  }
  await writeFile(join(evidence, 'result.json'), JSON.stringify(results, null, 2) + '\n');
  process.stdout.write(JSON.stringify(results) + '\n');
  if ((tryYoutube && typeof results.youtube !== 'string') ||
      (tryWikimedia && typeof results.wikimedia !== 'string')) process.exitCode = 1;
} catch (error) {
  const player = inspected && await evalIn(inspected, `({ url: location.href, paused: ${video}?.paused,
    ready: ${video}?.readyState, t: ${video}?.currentTime, subtitle: (${overlay})?.textContent,
    fullscreen: document.fullscreenElement?.tagName ?? null })`).catch(() => undefined);
  const sessionEvents = received.filter(event => ['ready', 'error', 'finished'].includes(event.type));
  const captionsBySession = Object.groupBy(received.filter(event => event.type === 'caption'), event => event.session_id);
  await writeFile(join(evidence, 'result.json'), JSON.stringify({ ...results, player, sessionEvents, captionsBySession,
    error: String(error) }, null, 2) + '\n');
  throw error;
} finally {
  if (service?.exitCode === null) service.kill('SIGTERM');
  await cdp('Browser.close').catch(() => undefined);
  chrome.kill();
  site.closeAllConnections();
  site.close();
  await sleep(300);
  await rm(temporary, { recursive: true, force: true }).catch(() => undefined);
}
