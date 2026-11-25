// Stage 6 Stage 1: Real Chrome + diagnostic Extension + m1-pro-nemo full-episode capture & interactive/pause soak.
import { execFileSync, spawn } from 'node:child_process';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const ROOT = resolve(import.meta.dirname, '../..');
const OUT_DIR = resolve(process.env.OUT_DIR || join(ROOT, 'p0/evidence/stage6-ep01-20261126'));
const ARCHIVE_DIR = join(ROOT, 'docs/experiments/stage6-ep01-2026-11-26');
const MP4_PATH = process.env.MP4_PATH || '/tmp/stage6-ep01/ep01.mp4';
const EXT_DIR = process.env.EXT_DIAG_DIR || '/tmp/stage6-ep01/ext-diag';
const PROFILE_ID = process.env.TEST_PROFILE || 'm1-pro-nemo';
const MAX_RUN_A_S = Number(process.env.MAX_RUN_A_S || '0'); // 0 = full episode until ended
const SKIP_RUN_B = process.env.SKIP_RUN_B === '1';

await mkdir(OUT_DIR, { recursive: true });
await mkdir(ARCHIVE_DIR, { recursive: true });
const videoLen = (await stat(MP4_PATH)).size;

function serveVideo(req, res) {
  const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range ?? '');
  const start = range ? +range[1] : 0;
  const end = range?.[2] ? Math.min(+range[2], videoLen - 1) : videoLen - 1;
  const headers = {
    'Content-Type': 'video/mp4',
    'Accept-Ranges': 'bytes',
    'Content-Length': end - start + 1,
  };
  if (range) headers['Content-Range'] = `bytes ${start}-${end}/${videoLen}`;
  res.writeHead(range ? 206 : 200, headers);
  createReadStream(MP4_PATH, { start, end }).pipe(res);
}

const site = createServer((req, res) => {
  if (req.url === '/ep01.mp4') return serveVideo(req, res);
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(`<!doctype html><html><head><title>Brush Up Life EP01 Stage 6</title></head>
    <body style="margin:0;background:#111;display:flex;justify-content:center;align-items:center;height:100vh;">
      <video id="v" style="width:100%;max-height:100vh;" controls preload="auto" src="/ep01.mp4"></video>
    </body></html>`);
});
await new Promise((r) => site.listen(0, '127.0.0.1', r));
const baseUrl = `http://127.0.0.1:${site.address().port}`;

const chromeProfile = join('/tmp/stage6-ep01', `chrome-profile-${process.pid}`);
await rm(chromeProfile, { recursive: true, force: true });
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--remote-debugging-pipe',
  '--enable-unsafe-extension-debugging',
  '--user-data-dir=' + chromeProfile,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--autoplay-policy=no-user-gesture-required',
  '--disable-renderer-backgrounding',
  '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows',
  '--disable-features=CalculateNativeWinOcclusion',
  '--window-size=1440,900',
  'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });

const cdpDiagStream = createWriteStream(join(OUT_DIR, 'cdp_diagnostics.jsonl'));
const domEventsStream = createWriteStream(join(OUT_DIR, 'dom_events.jsonl'));
const clockAnchorsStream = createWriteStream(join(OUT_DIR, 'clock_anchors.jsonl'));
const procSamplesStream = createWriteStream(join(OUT_DIR, 'proc_samples.jsonl'));

const calls = new Map();
const targetSessions = new Map();
const attachedSessions = new Set();
const diagCounts = { first_pcm_sent: 0, ws_received: 0, dom_visible: 0, dom_removed: 0 };
const domEventCounts = { total: 0, streaming: 0, final: 0, status: 0, empty: 0 };
const runBSeekLogs = [];
let currentPhase = 'init';
let nextId = 0;
let pending = '';

function cdp(method, params = {}, sessionId = undefined, timeoutMs = 20000) {
  return new Promise((resolvePromise, rejectPromise) => {
    const id = ++nextId;
    const timer = setTimeout(() => {
      calls.delete(id);
      rejectPromise(Error(`${method} timeout`));
    }, timeoutMs);
    calls.set(id, { resolve: resolvePromise, reject: rejectPromise, timer });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + '\0');
  });
}

chrome.stdio[4].setEncoding('utf8').on('data', (chunk) => {
  pending += chunk;
  for (let end; (end = pending.indexOf('\0')) >= 0;) {
    const event = JSON.parse(pending.slice(0, end));
    pending = pending.slice(end + 1);
    if (event.method === 'Target.attachedToTarget' && event.params?.sessionId) {
      const tid = event.params.targetInfo?.targetId;
      const sid = event.params.sessionId;
      if (tid && !targetSessions.has(tid)) {
        targetSessions.set(tid, sid);
        attachedSessions.add(sid);
        cdp('Runtime.enable', {}, sid).catch(() => {});
      }
    } else if (event.method === 'Runtime.consoleAPICalled' && event.params?.args?.length >= 2) {
      const tag = event.params.args[0]?.value;
      const raw = event.params.args[1]?.value;
      if (typeof raw === 'string') {
        if (tag === 'subtitle-diagnostic') {
          cdpDiagStream.write(raw + '\n');
          try {
            const obj = JSON.parse(raw);
            if (obj.stage && Object.hasOwn(diagCounts, obj.stage)) diagCounts[obj.stage]++;
          } catch {}
        } else if (tag === 'stage6-dom-event') {
          domEventsStream.write(raw + '\n');
          domEventCounts.total++;
          try {
            const obj = JSON.parse(raw);
            if (obj.is_status) domEventCounts.status++;
            else if (!obj.text_zh) domEventCounts.empty++;
            else if (obj.is_streaming) domEventCounts.streaming++;
            else domEventCounts.final++;
          } catch {}
        } else if (tag === 'stage6-clock-anchor') {
          clockAnchorsStream.write(raw + '\n');
        }
      }
    }
    const call = calls.get(event.id);
    if (call) {
      calls.delete(event.id);
      clearTimeout(call.timer);
      event.error ? call.reject(Error(event.error.message)) : call.resolve(event.result);
    }
  }
});

async function openTab(url) {
  const { targetId } = await cdp('Target.createTarget', { url });
  await sleep(50);
  let sessionId = targetSessions.get(targetId);
  if (!sessionId) {
    const res = await cdp('Target.attachToTarget', { targetId, flatten: true });
    sessionId = targetSessions.get(targetId) || res.sessionId;
    targetSessions.set(targetId, sessionId);
  }
  attachedSessions.add(sessionId);
  await cdp('Runtime.enable', {}, sessionId);
  return { targetId, sessionId };
}

async function ensureOffscreenRuntimeEnabled(extId) {
  const { targetInfos } = await cdp('Target.getTargets', {});
  for (const t of targetInfos) {
    if (t.url?.includes(extId) && t.url?.includes('offscreen.html') && !targetSessions.has(t.targetId)) {
      try {
        const { sessionId } = await cdp('Target.attachToTarget', { targetId: t.targetId, flatten: true });
        if (!targetSessions.has(t.targetId)) {
          targetSessions.set(t.targetId, sessionId);
          attachedSessions.add(sessionId);
          await cdp('Runtime.enable', {}, sessionId);
        }
      } catch {}
    }
  }
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

function startProcSampler(srvPid, chromePid) {
  let stop = false;
  const samples = [];
  const timer = (async () => {
    while (!stop) {
      try {
        const out = execFileSync('ps', ['-axo', 'pid=,ppid=,rss=,%cpu=,comm='], { encoding: 'utf8' });
        const rows = out.trim().split('\n').map((ln) => {
          const m = ln.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(.+)$/);
          return m ? { pid: +m[1], ppid: +m[2], rss_kb: +m[3], cpu: +m[4], comm: m[5] } : null;
        }).filter(Boolean);
        const srv = rows.find((r) => r.pid === srvPid);
        const chromeRows = rows.filter((r) => r.pid === chromePid || r.ppid === chromePid || r.comm.includes('Google Chrome'));
        const row = {
          phase: currentPhase,
          t_ms: Date.now(),
          srv_rss_mib: srv ? +(srv.rss_kb / 1024).toFixed(1) : 0,
          srv_cpu_pct: srv ? srv.cpu : 0,
          chrome_rss_mib: +chromeRows.reduce((s, x) => s + x.rss_kb / 1024, 0).toFixed(1),
          chrome_cpu_pct: +chromeRows.reduce((s, x) => s + x.cpu, 0).toFixed(1),
        };
        samples.push(row);
        procSamplesStream.write(JSON.stringify(row) + '\n');
      } catch {}
      await sleep(1000);
    }
  })();
  return {
    stop: async () => {
      stop = true;
      await timer;
      return samples;
    },
    samples,
  };
}

async function installStage6DomProbe(tab) {
  await evaluate(tab, `(() => {
    if (window.__stage6ProbeInstalled) return;
    window.__stage6ProbeInstalled = true;
    window.__stage6Phase = 'init';
    window.__stage6Seq = 0;
    const v = document.querySelector('video');
    const observedShadows = new WeakSet();
    let lastSig = '';

    function emitDom(shadow) {
      const lbl = shadow.querySelector('span[data-current]');
      const prev = shadow.querySelector('span[data-prev]');
      if (!lbl) return;
      const text = (lbl.textContent || '').trim();
      const isStreaming = lbl.hasAttribute('data-streaming');
      const isStatus = lbl.hasAttribute('data-status');
      const prevText = (prev?.textContent || '').trim();
      const sig = text + '|' + (isStreaming ? '1' : '0') + '|' + (isStatus ? '1' : '0');
      if (sig === lastSig) return;
      lastSig = sig;
      const seqIdx = ++window.__stage6Seq;
      const tMut = performance.timeOrigin + performance.now();
      const mediaMut = Number(v.currentTime.toFixed(3));
      requestAnimationFrame(() => {
        const tPaint = performance.timeOrigin + performance.now();
        console.debug('stage6-dom-event', JSON.stringify({
          seq_idx: seqIdx,
          phase: window.__stage6Phase,
          t_mut_ms: tMut,
          t_paint_ms: tPaint,
          media_time_mut: mediaMut,
          media_time_paint: Number(v.currentTime.toFixed(3)),
          paused: v.paused,
          playback_rate: v.playbackRate,
          is_streaming: isStreaming,
          is_status: isStatus,
          text_zh: text,
          prev_zh: prevText,
        }));
      });
    }

    function attachHost(host) {
      const sr = host?.shadowRoot;
      if (!sr || observedShadows.has(sr)) return;
      observedShadows.add(sr);
      const mo = new MutationObserver(() => emitDom(sr));
      mo.observe(sr, {
        childList: true,
        characterData: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['data-streaming', 'data-status'],
      });
      emitDom(sr);
    }

    const docMo = new MutationObserver(() => {
      for (const h of document.querySelectorAll('div[data-local-video-subtitle]')) attachHost(h);
    });
    docMo.observe(document.documentElement, { childList: true, subtree: true });
    setInterval(() => {
      for (const h of document.querySelectorAll('div[data-local-video-subtitle]')) attachHost(h);
    }, 500);

    setInterval(() => {
      const q = v.getVideoPlaybackQuality();
      console.debug('stage6-clock-anchor', JSON.stringify({
        phase: window.__stage6Phase,
        t_ms: performance.timeOrigin + performance.now(),
        media_time: Number(v.currentTime.toFixed(4)),
        paused: v.paused,
        ended: v.ended,
        playback_rate: v.playbackRate,
        total_frames: q.totalVideoFrames,
        dropped_frames: q.droppedVideoFrames,
      }));
    }, 5000);
  })()`);
}

let service;
const summary = {
  profile_id: PROFILE_ID,
  max_run_a_s: MAX_RUN_A_S,
  skip_run_b: SKIP_RUN_B,
  run_a: {},
  run_b: {},
};

try {
  await cdp('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true });
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
    const prof = data.profiles.find(p => p.id === ${JSON.stringify(PROFILE_ID)});
    return prof?.available === true;
  })()`);
  if (!okProfile) throw Error(`${PROFILE_ID} not available in /v1/profiles`);

  const sampler = startProcSampler(service.pid, chrome.pid);

  // Open video tab
  const tab = await openTab(`${baseUrl}/`);
  await cdp('Target.activateTarget', { targetId: tab.targetId });
  await cdp('Page.bringToFront', {}, tab.sessionId);
  await until(() => evaluate(tab, 'document.querySelector("video")?.readyState >= 3'), 20000, 'video ready');
  await installStage6DomProbe(tab);

  const { targetInfo } = await cdp('Target.getTargetInfo', { targetId: tab.targetId });
  const extTarget = (await cdp('Target.getTargets', { filter: [{ type: 'tab' }, { exclude: true }] }))
    .targetInfos.find((t) => t.url === targetInfo.url);

  // --- Run A: Full Episode Continuous 1x Playback ---
  currentPhase = 'run_a';
  await evaluate(tab, `window.__stage6Phase = 'run_a'`);
  const tRunA0 = Date.now();
  await evaluate(tab, 'const v=document.querySelector("video"); v.currentTime=0; v.playbackRate=1.0; v.play()', true);
  await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget.targetId });
  await sleep(1000);
  await ensureOffscreenRuntimeEnabled(extId);

  const shots = new Set();
  const shotMarks = MAX_RUN_A_S > 0 ? [] : [
    { at: 285, name: 'shot_04m45s.png' },
    { at: 1335, name: 'shot_22m15s.png' },
    { at: 2670, name: 'shot_44m30s.png' },
  ];
  let lastProgressT = -1;
  let stallTicks = 0;
  const maxWallMs = MAX_RUN_A_S > 0 ? (MAX_RUN_A_S + 30) * 1000 : 52 * 60 * 1000;

  while (Date.now() - tRunA0 < maxWallMs) {
    await sleep(1000);
    const st = await evaluate(tab, `(() => {
      const v = document.querySelector('video');
      const q = v.getVideoPlaybackQuality();
      return { ct: v.currentTime, ended: v.ended, paused: v.paused, tf: q.totalVideoFrames, df: q.droppedVideoFrames };
    })()`);
    if (MAX_RUN_A_S > 0 && st.ct >= MAX_RUN_A_S) {
      const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab.sessionId);
      await writeFile(join(OUT_DIR, 'shot_smoke.png'), Buffer.from(data, 'base64'));
      shots.add('shot_smoke.png');
      await evaluate(tab, 'document.querySelector("video").pause()');
      await sleep(3500);
      break;
    }
    for (const mk of shotMarks) {
      if (!shots.has(mk.name) && st.ct >= mk.at) {
        const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab.sessionId);
        await writeFile(join(OUT_DIR, mk.name), Buffer.from(data, 'base64'));
        shots.add(mk.name);
      }
    }
    if (st.ended) {
      await sleep(5000); // Allow final tail caption drain
      break;
    }
    if (st.ct <= lastProgressT + 0.05) {
      stallTicks++;
      if (stallTicks >= 30) throw Error(`Video playback stalled at ${st.ct}s`);
    } else {
      stallTicks = 0;
      lastProgressT = st.ct;
    }
  }

  const endStatsA = await evaluate(tab, `(() => {
    const v = document.querySelector('video');
    const q = v.getVideoPlaybackQuality();
    return { current_time: Number(v.currentTime.toFixed(2)), ended: v.ended, total_frames: q.totalVideoFrames, dropped_frames: q.droppedVideoFrames };
  })()`);
  const runASamples = sampler.samples.filter((s) => s.phase === 'run_a');
  summary.run_a = {
    wall_seconds: +((Date.now() - tRunA0) / 1000).toFixed(1),
    ...endStatsA,
    screenshots: [...shots],
    diag_counts: { ...diagCounts },
    dom_event_counts: { ...domEventCounts },
    srv_mean_cpu_pct: +(runASamples.reduce((a, b) => a + b.srv_cpu_pct, 0) / Math.max(1, runASamples.length)).toFixed(1),
    srv_peak_cpu_pct: Math.max(0, ...runASamples.map((s) => s.srv_cpu_pct)),
    srv_peak_rss_mib: Math.max(0, ...runASamples.map((s) => s.srv_rss_mib)),
    chrome_mean_cpu_pct: +(runASamples.reduce((a, b) => a + b.chrome_cpu_pct, 0) / Math.max(1, runASamples.length)).toFixed(1),
    chrome_peak_rss_mib: Math.max(0, ...runASamples.map((s) => s.chrome_rss_mib)),
  };

  // --- Run B: Interactive Seeks, Playback Rates, Toggles & 3-Min Pause Idle Check ---
  if (!SKIP_RUN_B) {
    const seekTargets = [275, 725, 55, 1320, 1800, 2400, 600, 1000, 1500, 2100];
    for (const target of seekTargets) {
      const ph = `run_b_seek_${target}`;
      currentPhase = ph;
      await evaluate(tab, `window.__stage6Phase = ${JSON.stringify(ph)}`);
      const beforeFinal = domEventCounts.final;
      const t0 = Date.now();
      await evaluate(tab, `(() => {
        const v = document.querySelector('video');
        v.playbackRate = 1.0;
        v.currentTime = ${target};
        return new Promise((res) => v.addEventListener('seeked', async () => { await v.play(); res(true); }, { once: true }));
      })()`, true);
      await ensureOffscreenRuntimeEnabled(extId);
      let firstCapMs = null;
      for (let i = 0; i < 20; i++) {
        await sleep(500);
        if (firstCapMs === null && domEventCounts.final > beforeFinal) {
          firstCapMs = Date.now() - t0;
        }
      }
      runBSeekLogs.push({ target_s: target, recovered: domEventCounts.final > beforeFinal, first_final_ms: firstCapMs, finals: domEventCounts.final - beforeFinal });
    }

    // Speed 1.25x & 1.5x
    const speedResults = {};
    for (const [rate, startS] of [[1.25, 275], [1.5, 725]]) {
      const ph = `run_b_speed_${rate}`;
      currentPhase = ph;
      await evaluate(tab, `window.__stage6Phase = ${JSON.stringify(ph)}`);
      const beforeFinal = domEventCounts.final;
      await evaluate(tab, `(() => {
        const v = document.querySelector('video');
        v.currentTime = ${startS};
        v.playbackRate = ${rate};
        return new Promise((res) => v.addEventListener('seeked', async () => { await v.play(); res(true); }, { once: true }));
      })()`, true);
      await sleep(45000);
      speedResults[`x${rate}`] = { start_s: startS, duration_s: 45, finals: domEventCounts.final - beforeFinal };
    }
    await evaluate(tab, 'document.querySelector("video").playbackRate = 1.0');

    // Rapid toggle off/on x 5 (starts enabled -> 5 off/on cycles ending enabled)
    currentPhase = 'run_b_toggle';
    await evaluate(tab, `window.__stage6Phase = 'run_b_toggle'`);
    await evaluate(tab, `(() => {
      const v = document.querySelector('video');
      v.currentTime = 275;
      return new Promise((res) => v.addEventListener('seeked', async () => { await v.play(); res(true); }, { once: true }));
    })()`, true);
    for (let i = 0; i < 5; i++) {
      await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget.targetId }); // off
      await sleep(1500);
      await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget.targetId }); // on
      await sleep(1500);
    }
    await ensureOffscreenRuntimeEnabled(extId);
    const beforeToggleRecover = domEventCounts.final;
    await sleep(15000);
    const toggleRecoverFinals = domEventCounts.final - beforeToggleRecover;

    // 3-minute paused idle check (extension remains active, video paused)
    currentPhase = 'run_b_pause_idle';
    await evaluate(tab, `window.__stage6Phase = 'run_b_pause_idle'`);
    await evaluate(tab, 'document.querySelector("video").pause()');
    await sleep(5000); // settle previous work
    const traceBeforeBytes = (await stat(modelTrace)).size;
    const tPauseStart = Date.now();
    await sleep(180000);
    const tPauseEnd = Date.now();
    const traceAfterBytes = (await stat(modelTrace)).size;
    const pauseSamples = sampler.samples.filter((s) => s.t_ms >= tPauseStart && s.t_ms <= tPauseEnd);

    summary.run_b = {
      seeks: runBSeekLogs,
      speeds: speedResults,
      rapid_toggle_x5: { recovered: toggleRecoverFinals > 0, post_toggle_finals_15s: toggleRecoverFinals },
      pause_idle_180s: {
        duration_s: +((tPauseEnd - tPauseStart) / 1000).toFixed(1),
        model_trace_bytes_delta: traceAfterBytes - traceBeforeBytes,
        srv_mean_cpu_pct: +(pauseSamples.reduce((a, b) => a + b.srv_cpu_pct, 0) / Math.max(1, pauseSamples.length)).toFixed(2),
        srv_peak_cpu_pct: Math.max(0, ...pauseSamples.map((s) => s.srv_cpu_pct)),
      },
    };
  }

  await sampler.stop();
  await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget.targetId }).catch(() => {});
  await sleep(1500);

  await writeFile(join(OUT_DIR, 'stage1_chrome_capture_summary.json'), JSON.stringify(summary, null, 2) + '\n');
  await writeFile(join(ARCHIVE_DIR, 'stage1_chrome_capture_summary.json'), JSON.stringify(summary, null, 2) + '\n');
  console.log('STAGE1_CHROME_DONE', JSON.stringify(summary));
} finally {
  if (service?.exitCode === null) service.kill('SIGTERM');
  await cdp('Browser.close').catch(() => undefined);
  chrome.kill();
  site.closeAllConnections();
  site.close();
  cdpDiagStream.end();
  domEventsStream.end();
  clockAnchorsStream.end();
  procSamplesStream.end();
  await sleep(500);
  await rm(chromeProfile, { recursive: true, force: true });
}
