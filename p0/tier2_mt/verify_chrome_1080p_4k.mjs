// Real Chrome + real Extension + live subtitle-server (m1-pro-hq) E2E test for 1080p (core jump + mid-playback jump) & 4K.
import { spawn } from 'node:child_process';
import { createReadStream, createWriteStream } from 'node:fs';
import { appendFile, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const ROOT = resolve(import.meta.dirname, '../..');
const OUT_DIR = process.env.CHROME_E2E_OUT || join(ROOT, 'p0/evidence/m1-pro-hq-browser-20261123');
const MEDIA_DIR = process.env.MEDIA_DIR || join(OUT_DIR, 'media');
const EXT_DIR = process.env.EXT_DIST_DIR || join(ROOT, 'extension/dist');
const PROFILE_ID = process.env.TEST_PROFILE || 'm1-pro-hq';
const MP4_1080P = join(MEDIA_DIR, 'brush-up-life-ep01-1080p.mp4');
const MP4_4K = join(MEDIA_DIR, 'brush-up-life-4k-35s.mp4');
const JUMP1_S = 275.0; // 04:35.0 (right before 04:35.676-05:20.676 19-cue dense dialogue)
const JUMP2_S = 725.0; // 12:05.0 (karaoke reunion 4-person dense dialogue)

await mkdir(OUT_DIR, { recursive: true });
const len1080 = (await stat(MP4_1080P)).size;
const len4k = (await stat(MP4_4K)).size;

function serveFile(req, res, filePath, length) {
  const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range ?? '');
  const start = range ? +range[1] : 0;
  const end = range?.[2] ? Math.min(+range[2], length - 1) : length - 1;
  const headers = {
    'Content-Type': 'video/mp4',
    'Accept-Ranges': 'bytes',
    'Content-Length': end - start + 1,
  };
  if (range) headers['Content-Range'] = `bytes ${start}-${end}/${length}`;
  res.writeHead(range ? 206 : 200, headers);
  createReadStream(filePath, { start, end }).pipe(res);
}

const site = createServer((req, res) => {
  if (req.url === '/video-1080p.mp4') return serveFile(req, res, MP4_1080P, len1080);
  if (req.url === '/video-4k.mp4') return serveFile(req, res, MP4_4K, len4k);
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  const src = req.url === '/4k' ? '/video-4k.mp4' : '/video-1080p.mp4';
  const label = req.url === '/4k' ? '4K 2160p' : '1080p';
  res.end(`<!doctype html><html><head><title>Brush Up Life ${label}</title></head>
    <body style="margin:0;background:#111;display:flex;justify-content:center;align-items:center;height:100vh;">
      <video id="v" style="width:100%;max-height:100vh;" controls preload="auto" src="${src}"></video>
    </body></html>`);
});
await new Promise((r) => site.listen(0, '127.0.0.1', r));
const baseUrl = `http://127.0.0.1:${site.address().port}`;

const chromeProfile = join(OUT_DIR, 'chrome-profile');
await rm(chromeProfile, { recursive: true, force: true });
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--remote-debugging-pipe',
  '--enable-unsafe-extension-debugging',
  '--user-data-dir=' + chromeProfile,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--window-size=1440,900',
  'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });

const calls = new Map();
const diagnostics = [];
let nextId = 0;
let pending = '';
chrome.stdio[4].setEncoding('utf8').on('data', (chunk) => {
  pending += chunk;
  for (let end; (end = pending.indexOf('\0')) >= 0;) {
    const event = JSON.parse(pending.slice(0, end));
    pending = pending.slice(end + 1);
    if (event.method === 'Runtime.consoleAPICalled' && event.params.args[0]?.value === 'subtitle-diagnostic') {
      try { diagnostics.push(JSON.parse(event.params.args[1].value)); } catch {}
    }
    const call = calls.get(event.id);
    if (call) {
      calls.delete(event.id);
      clearTimeout(call.timer);
      event.error ? call.reject(Error(event.error.message)) : call.resolve(event.result);
    }
  }
});

function cdp(method, params = {}, sessionId) {
  return new Promise((resolvePromise, rejectPromise) => {
    const id = ++nextId;
    const timer = setTimeout(() => {
      calls.delete(id);
      rejectPromise(Error(`${method} timeout`));
    }, 20000);
    calls.set(id, { resolve: resolvePromise, reject: rejectPromise, timer });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + '\0');
  });
}

async function openTab(url) {
  const { targetId } = await cdp('Target.createTarget', { url });
  const { sessionId } = await cdp('Target.attachToTarget', { targetId, flatten: true });
  await cdp('Runtime.enable', {}, sessionId);
  return { targetId, sessionId };
}

async function evaluate(tab, expression, userGesture = false) {
  const res = await cdp('Runtime.evaluate', { expression, userGesture, returnByValue: true, awaitPromise: true }, tab.sessionId);
  if (res.exceptionDetails) throw Error(res.exceptionDetails.exception?.description || res.exceptionDetails.text);
  return res.result.value;
}

async function until(fn, timeoutMs, name) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await sleep(150);
  }
  throw Error(`${name} timeout`);
}

function sampleProcStats(pidListFn) {
  const samples = [];
  let stop = false;
  const timer = (async () => {
    while (!stop) {
      try {
        const { execFileSync } = await import('node:child_process');
        const out = execFileSync('ps', ['-axo', 'pid=,ppid=,rss=,%cpu=,comm='], { encoding: 'utf8' });
        const rows = out.trim().split('\n').map((ln) => {
          const m = ln.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(.+)$/);
          return m ? { pid: +m[1], ppid: +m[2], rss_kb: +m[3], cpu: +m[4], comm: m[5] } : null;
        }).filter(Boolean);
        samples.push(pidListFn(rows));
      } catch {}
      await sleep(500);
    }
  })();
  return {
    stop: async () => { stop = true; await timer; return samples; },
  };
}

async function installCaptionObserver(tab) {
  await evaluate(tab, `(() => {
    const v = document.querySelector('video');
    window.__capLog = [];
    let lastText = '';
    setInterval(() => {
      const hosts = [...document.querySelectorAll('div')];
      let text = '';
      let prevText = '';
      for (const h of hosts) {
        if (!h.hasAttribute('data-local-video-subtitle')) continue;
        const lbl = h.shadowRoot?.querySelector('span[data-current]') || h.shadowRoot?.querySelector('span');
        const prev = h.shadowRoot?.querySelector('span[data-prev]');
        if (lbl && lbl.textContent && !lbl.hasAttribute('data-status')) {
          text = lbl.textContent.trim();
          prevText = prev?.textContent?.trim() || '';
          break;
        }
      }
      if (text && text !== lastText) {
        lastText = text;
        window.__capLog.push({ t_ms: Date.now(), media_time: Number(v.currentTime.toFixed(2)), text_zh: text, prev_zh: prevText });
      }
    }, 100);
  })()`);
}

let service;
const summary = { profile_id: PROFILE_ID, scenarios: {} };

try {
  summary.chrome = (await cdp('Browser.getVersion')).product;
  const { id: extId } = await cdp('Extensions.loadUnpacked', { path: EXT_DIR });
  summary.extension_id = extId;

  const baseToml = await readFile(join(ROOT, 'engine/config/profiles.toml'), 'utf8');
  const tempToml = baseToml
    .replace(/allowed_origin = "[^"]*"/, `allowed_origin = "chrome-extension://${extId}"`)
    .replace('../../.model-cache', join(ROOT, '.model-cache'));
  const tempTomlPath = join(OUT_DIR, 'profiles.toml');
  await writeFile(tempTomlPath, tempToml);

  const tokenFile = join(OUT_DIR, 'private.token');
  const modelTrace = join(OUT_DIR, 'model.jsonl');
  await writeFile(modelTrace, '');

  service = spawn(join(ROOT, 'engine/.venv/bin/python'), ['-m', 'subtitle_service.cli', '--config', tempTomlPath], {
    cwd: ROOT,
    env: {
      ...process.env,
      HF_HOME: join(ROOT, '.model-cache'),
      SUBTITLE_TOKEN_FILE: tokenFile,
      SUBTITLE_BENCH_TRACE: modelTrace,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const srvLog = createWriteStream(join(OUT_DIR, 'server.log'));
  service.stdout.pipe(srvLog, { end: false });
  service.stderr.pipe(srvLog, { end: false });

  await until(async () => {
    try {
      const r = await fetch('http://127.0.0.1:18765/v1/health');
      return r.ok;
    } catch { return false; }
  }, 60000, 'server ready');

  const token = (await readFile(tokenFile, 'utf8')).trim();
  const optTab = await openTab(`chrome-extension://${extId}/options.html`);
  await until(() => evaluate(optTab, '!!document.querySelector("#token")'), 10000, 'options page');
  const okProfile = await evaluate(optTab, `(async () => {
    await chrome.storage.local.set({ token: ${JSON.stringify(token)}, profile_id: ${JSON.stringify(PROFILE_ID)} });
    const r = await fetch('http://127.0.0.1:18765/v1/profiles', { headers: { Authorization: 'Bearer ' + ${JSON.stringify(token)} } });
    const data = await r.json();
    const hq = data.profiles.find(p => p.id === ${JSON.stringify(PROFILE_ID)});
    return hq?.available === true;
  })()`);
  if (!okProfile) throw Error(`${PROFILE_ID} not available in /v1/profiles`);

  const sampler = sampleProcStats((rows) => {
    const srv = rows.find((r) => r.pid === service.pid);
    const chromeRows = rows.filter((r) => r.pid === chrome.pid || r.ppid === chrome.pid || r.comm.includes('Google Chrome'));
    return {
      srv_rss_mib: srv ? +(srv.rss_kb / 1024).toFixed(1) : 0,
      srv_cpu_pct: srv ? srv.cpu : 0,
      chrome_rss_mib: +chromeRows.reduce((s, x) => s + x.rss_kb / 1024, 0).toFixed(1),
      chrome_cpu_pct: +chromeRows.reduce((s, x) => s + x.cpu, 0).toFixed(1),
    };
  });

  // --- Scenario 1: 1080p full episode (jump to 04:35.0 core dialogue, then mid-playback jump to 12:05.0) ---
  const tab1080 = await openTab(`${baseUrl}/1080p`);
  await cdp('Target.activateTarget', { targetId: tab1080.targetId });
  await cdp('Page.bringToFront', {}, tab1080.sessionId);
  await until(() => evaluate(tab1080, 'document.querySelector("video")?.readyState >= 3'), 15000, '1080p load');
  await evaluate(tab1080, `(() => {
    const v = document.querySelector('video');
    v.currentTime = ${JUMP1_S};
    return new Promise((res) => v.addEventListener('seeked', () => res(true), { once: true }));
  })()`);
  await installCaptionObserver(tab1080);

  const { targetInfo: tInfo1080 } = await cdp('Target.getTargetInfo', { targetId: tab1080.targetId });
  const extTarget1080 = (await cdp('Target.getTargets', { filter: [{ type: 'tab' }, { exclude: true }] })).targetInfos.find((t) => t.url === tInfo1080.url);
  await evaluate(tab1080, 'document.querySelector("video").play()', true);
  await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget1080.targetId });

  // Wait up to 46s while collecting captions, grab screenshot as soon as a caption renders after 10s
  let shot1080Core = false;
  for (let i = 0; i < 46; i++) {
    await sleep(1000);
    if (!shot1080Core && i >= 10) {
      const hasCap = await evaluate(tab1080, 'window.__capLog.length > 0');
      if (hasCap) {
        const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab1080.sessionId);
        await writeFile(join(OUT_DIR, '1080p-core-caption.png'), Buffer.from(data, 'base64'));
        shot1080Core = true;
      }
    }
  }
  const capsBeforeJump = await evaluate(tab1080, 'window.__capLog.length');

  // Mid-playback live jump to 12:05.0
  await evaluate(tab1080, `(() => {
    const v = document.querySelector('video');
    v.currentTime = ${JUMP2_S};
    return new Promise((res) => v.addEventListener('seeked', () => res(true), { once: true }));
  })()`);
  let shot1080Jump = false;
  for (let i = 0; i < 24; i++) {
    await sleep(1000);
    if (!shot1080Jump && i >= 8) {
      const lenNow = await evaluate(tab1080, 'window.__capLog.length');
      if (lenNow > capsBeforeJump) {
        const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab1080.sessionId);
        await writeFile(join(OUT_DIR, '1080p-jumped-caption.png'), Buffer.from(data, 'base64'));
        shot1080Jump = true;
      }
    }
  }
  const res1080 = await evaluate(tab1080, `(() => {
    const v = document.querySelector('video');
    const q = v.getVideoPlaybackQuality();
    return {
      width: v.videoWidth, height: v.videoHeight,
      currentTime: Number(v.currentTime.toFixed(2)),
      totalFrames: q.totalVideoFrames, droppedFrames: q.droppedVideoFrames,
      captions: window.__capLog,
    };
  })()`);
  // Stop capture on 1080p tab
  await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget1080.targetId });
  await sleep(3000);
  await evaluate(tab1080, 'document.querySelector("video").pause()');
  summary.scenarios['1080p_core_and_jump'] = {
    ...res1080,
    captions_before_jump: capsBeforeJump,
    captions_after_jump: res1080.captions.length - capsBeforeJump,
    core_screenshot: shot1080Core,
    jumped_screenshot: shot1080Jump,
  };

  // --- Scenario 2: 4K 2160p H.264 35s concurrent decode ---
  const tab4k = await openTab(`${baseUrl}/4k`);
  await cdp('Target.activateTarget', { targetId: tab4k.targetId });
  await cdp('Page.bringToFront', {}, tab4k.sessionId);
  await until(() => evaluate(tab4k, 'document.querySelector("video")?.readyState >= 3'), 15000, '4k load');
  await installCaptionObserver(tab4k);

  const { targetInfo: tInfo4k } = await cdp('Target.getTargetInfo', { targetId: tab4k.targetId });
  const extTarget4k = (await cdp('Target.getTargets', { filter: [{ type: 'tab' }, { exclude: true }] })).targetInfos.find((t) => t.url === tInfo4k.url);
  await evaluate(tab4k, 'document.querySelector("video").play()', true);
  await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget4k.targetId });

  let shot4k = false;
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    const ended = await evaluate(tab4k, 'document.querySelector("video").ended');
    if (!shot4k && i >= 10) {
      const hasCap = await evaluate(tab4k, 'window.__capLog.length > 0');
      if (hasCap) {
        const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab4k.sessionId);
        await writeFile(join(OUT_DIR, '4k-caption.png'), Buffer.from(data, 'base64'));
        shot4k = true;
      }
    }
    if (ended) break;
  }
  await sleep(5000); // Allow tail drain to render final caption
  if (!shot4k) {
    const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab4k.sessionId);
    await writeFile(join(OUT_DIR, '4k-caption.png'), Buffer.from(data, 'base64'));
  }
  const res4k = await evaluate(tab4k, `(() => {
    const v = document.querySelector('video');
    const q = v.getVideoPlaybackQuality();
    return {
      width: v.videoWidth, height: v.videoHeight, ended: v.ended,
      totalFrames: q.totalVideoFrames, droppedFrames: q.droppedVideoFrames,
      captions: window.__capLog,
    };
  })()`);
  await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget4k.targetId });
  await sleep(2000);
  summary.scenarios['4k_2160p'] = { ...res4k, screenshot: shot4k };

  const procSamples = await sampler.stop();
  summary.resources = {
    samples: procSamples.length,
    server_peak_rss_mib: Math.max(...procSamples.map((s) => s.srv_rss_mib)),
    server_mean_cpu_pct: +(procSamples.reduce((a, b) => a + b.srv_cpu_pct, 0) / Math.max(1, procSamples.length)).toFixed(1),
    server_peak_cpu_pct: Math.max(...procSamples.map((s) => s.srv_cpu_pct)),
    chrome_peak_rss_mib: Math.max(...procSamples.map((s) => s.chrome_rss_mib)),
    chrome_mean_cpu_pct: +(procSamples.reduce((a, b) => a + b.chrome_cpu_pct, 0) / Math.max(1, procSamples.length)).toFixed(1),
    chrome_peak_cpu_pct: Math.max(...procSamples.map((s) => s.chrome_cpu_pct)),
  };

  summary.diagnostics = diagnostics;
  await writeFile(join(OUT_DIR, 'chrome_e2e_summary.json'), JSON.stringify(summary, null, 2) + '\n');
  console.log('CHROME_E2E_DONE', JSON.stringify({
    '1080p': {
      res: `${res1080.width}x${res1080.height}`,
      frames: `${res1080.totalFrames} (dropped ${res1080.droppedFrames})`,
      caps_before_jump: capsBeforeJump,
      caps_after_jump: res1080.captions.length - capsBeforeJump,
    },
    '4k': {
      res: `${res4k.width}x${res4k.height}`,
      ended: res4k.ended,
      frames: `${res4k.totalFrames} (dropped ${res4k.droppedFrames})`,
      caps: res4k.captions.length,
    },
    resources: summary.resources,
  }));
} finally {
  if (service?.exitCode === null) service.kill('SIGTERM');
  await cdp('Browser.close').catch(() => undefined);
  chrome.kill();
  site.closeAllConnections();
  site.close();
  await sleep(500);
  await rm(chromeProfile, { recursive: true, force: true });
}
