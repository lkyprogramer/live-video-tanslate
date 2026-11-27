// Stage 8 Parameterized Real Chrome Runner
// Reads frozen manifest.json; never overrides manifest fields; writes exclusively to manifest.execution.output_dir.
import { execFileSync, spawn } from 'node:child_process';
import { createReadStream, createWriteStream, existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--manifest') out.manifest = argv[++i];
    else if (a === '--arm') out.arm = argv[++i];
    else if (a === '--out') out.out = argv[++i];
    else if (a === '--duration-s') out.durationS = Number(argv[++i]);
    else throw Error(`Unknown argument: ${a}`);
  }
  if (!out.manifest) throw Error('Missing required --manifest <path>');
  return out;
}

const args = parseArgs(process.argv.slice(2));
const ROOT = resolve(import.meta.dirname, '../..');
const ENGINE_ROOT = process.env.ENGINE_ROOT ? resolve(process.env.ENGINE_ROOT) : ROOT;
const manifestPath = resolve(args.manifest);
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));

if (manifest.template_only === true) {
  console.error('Refusing template_only manifest');
  process.exit(2);
}
if (args.arm !== undefined && args.arm !== manifest.arm?.arm_id) {
  console.error(`--arm=${args.arm} disagrees with manifest.arm.arm_id=${manifest.arm?.arm_id}`);
  process.exit(2);
}
const outDir = resolve(manifest.execution?.output_dir || '');
if (args.out !== undefined && resolve(args.out) !== outDir) {
  console.error(`--out=${resolve(args.out)} disagrees with manifest.execution.output_dir=${outDir}`);
  process.exit(2);
}
if (
  outDir.includes('stage6-ep01-2026-11-26') ||
  outDir.includes('stage6-ep01-20261126') ||
  outDir.includes('docs/experiments/stage6')
) {
  console.error(`Refusing to write inside Stage 6 archive directory: ${outDir}`);
  process.exit(2);
}
const manifestDur = manifest.execution?.duration_s;
if (args.durationS !== undefined && Number(manifestDur) !== args.durationS) {
  console.error(`--duration-s=${args.durationS} disagrees with manifest.execution.duration_s=${manifestDur}`);
  process.exit(2);
}

const MAX_PLAY_S = Number(manifestDur || 0);
const BASELINE_ROLE = manifest.arm?.baseline_role || 'corrected';
const SUBTITLES_OFF = BASELINE_ROLE === 'subtitles_off';
const PROFILE_ID = process.env.TEST_PROFILE || 'm1-pro-nemo';
const PYTHON_BIN = process.env.PYTHON_BIN || '/Users/luo/Documents/github/jp-live-video-tanslate/engine/.venv/bin/python';
const CACHE_ROOT = process.env.MODEL_CACHE_ROOT || (
  existsSync(join(ROOT, '.model-cache'))
    ? join(ROOT, '.model-cache')
    : '/Users/luo/Documents/github/jp-live-video-tanslate/.model-cache'
);

await mkdir(outDir, { recursive: true });
await writeFile(join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
const scratchDir = await mkdtemp(join(tmpdir(), 'stage8-chrome-'));
const extDir = process.env.EXT_DIAG_DIR || join(scratchDir, 'ext-diag');
if (!process.env.EXT_DIAG_DIR) {
  execFileSync('node', ['build.mjs'], {
    cwd: join(ENGINE_ROOT, 'extension'),
    env: { ...process.env, SUBTITLE_DIAGNOSTIC_DIST: extDir },
    stdio: 'inherit',
  });
}

let mediaPath = manifest.data?.media_path;
if (!mediaPath || !existsSync(mediaPath)) {
  console.error(`Media file missing: ${mediaPath}`);
  await rm(scratchDir, { recursive: true, force: true });
  process.exit(2);
}
if (!mediaPath.toLowerCase().endsWith('.mp4')) {
  const remuxMp4 = join(scratchDir, 'media.mp4');
  const ffArgs = ['-v', 'error', '-y'];
  if (MAX_PLAY_S > 0) ffArgs.push('-t', String(MAX_PLAY_S + 15));
  ffArgs.push('-i', mediaPath, '-c', 'copy', remuxMp4);
  execFileSync('ffmpeg', ffArgs, { stdio: 'inherit' });
  mediaPath = remuxMp4;
}
const videoLen = (await stat(mediaPath)).size;

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
  createReadStream(mediaPath, { start, end }).pipe(res);
}

const site = createServer((req, res) => {
  if (req.url === '/media.mp4') return serveVideo(req, res);
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(`<!doctype html><html><head><title>Stage 8 Chrome Harness</title></head>
    <body style="margin:0;background:#111;display:flex;justify-content:center;align-items:center;height:100vh;">
      <video id="v" style="width:100%;max-height:100vh;" controls preload="auto" src="/media.mp4"></video>
    </body></html>`);
});
await new Promise((r) => site.listen(0, '127.0.0.1', r));
const baseUrl = `http://127.0.0.1:${site.address().port}`;

const chromeProfile = join(scratchDir, 'chrome-profile');
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

const cdpDiagStream = createWriteStream(join(outDir, 'cdp_diagnostics.jsonl'));
const domEventsStream = createWriteStream(join(outDir, 'dom_events.jsonl'));
const clockAnchorsStream = createWriteStream(join(outDir, 'clock_anchors.jsonl'));
const procSamplesStream = createWriteStream(join(outDir, 'proc_samples.jsonl'));

const calls = new Map();
const targetSessions = new Map();
const diagCounts = {
  first_pcm_sent: 0,
  ws_received: 0,
  dom_visible: 0,
  dom_update: 0,
  dom_removed: 0,
};
const domKindCounts = { preview: 0, final_token_stream: 0, final: 0, unknown: 0 };
const domEventCounts = { total: 0, streaming: 0, final: 0, status: 0, empty: 0 };
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
            if (obj.stage === 'dom_visible' || obj.stage === 'dom_update') {
              const k = obj.kind || 'unknown';
              domKindCounts[k] = (domKindCounts[k] || 0) + 1;
            }
          } catch {}
        } else if (tag === 'stage8-dom-event') {
          domEventsStream.write(raw + '\n');
          domEventCounts.total++;
          try {
            const obj = JSON.parse(raw);
            if (obj.is_status) domEventCounts.status++;
            else if (!obj.text_zh) domEventCounts.empty++;
            else if (obj.is_streaming) domEventCounts.streaming++;
            else domEventCounts.final++;
          } catch {}
        } else if (tag === 'stage8-clock-anchor') {
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
        const srv = srvPid ? rows.find((r) => r.pid === srvPid) : null;
        const chromeRows = rows.filter((r) => r.pid === chromePid || r.ppid === chromePid || r.comm.includes('Google Chrome'));
        const row = {
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

async function installStage8DomProbe(tab) {
  await evaluate(tab, `(() => {
    if (window.__stage8ProbeInstalled) return;
    window.__stage8ProbeInstalled = true;
    window.__stage8Seq = 0;
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
      const seqIdx = ++window.__stage8Seq;
      const tMut = performance.timeOrigin + performance.now();
      const mediaMut = Number(v.currentTime.toFixed(3));
      requestAnimationFrame(() => {
        const tPaint = performance.timeOrigin + performance.now();
        console.debug('stage8-dom-event', JSON.stringify({
          seq_idx: seqIdx,
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
      console.debug('stage8-clock-anchor', JSON.stringify({
        t_ms: performance.timeOrigin + performance.now(),
        perf_now_ms: performance.now(),
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
let extId;
let extTarget;
const summary = {
  schema_version: 'stage8-chrome-summary-v1',
  run_id: manifest.run_id,
  experiment_id: manifest.experiment_id,
  arm_id: manifest.arm?.arm_id,
  baseline_role: BASELINE_ROLE,
  profile_id: PROFILE_ID,
  duration_s: MAX_PLAY_S,
  output_dir: outDir,
};

try {
  await cdp('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true });
  summary.chrome = (await cdp('Browser.getVersion')).product;

  if (!SUBTITLES_OFF) {
    const loadedExt = await cdp('Extensions.loadUnpacked', { path: extDir });
    extId = loadedExt.id;
    summary.extension_id = extId;

    const profileSrcPath = manifest.bundle?.profile_path || join(ROOT, 'engine/config/profiles.example.toml');
    const baseToml = await readFile(profileSrcPath, 'utf8');
    const tempToml = baseToml
      .replace(/allowed_origin = "[^"]*"/, `allowed_origin = "chrome-extension://${extId}"`)
      .replace(/cache_root = "[^"]*"/, `cache_root = ${JSON.stringify(CACHE_ROOT)}`);
    const tempTomlPath = join(outDir, 'profiles.toml');
    await writeFile(tempTomlPath, tempToml);

    const tokenFile = join(scratchDir, 'private.token');
    const modelTrace = join(outDir, 'model.jsonl');
    await writeFile(modelTrace, '');

    service = spawn(PYTHON_BIN, ['-m', 'subtitle_service.cli', '--config', tempTomlPath], {
      cwd: ENGINE_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: `${ENGINE_ROOT}:${join(ENGINE_ROOT, 'engine/src')}`,
        HF_HOME: CACHE_ROOT,
        SUBTITLE_TOKEN_FILE: tokenFile,
        SUBTITLE_BENCH_TRACE: modelTrace,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const srvLog = createWriteStream(join(outDir, 'server.log'));
    service.stdout.pipe(srvLog, { end: false });
    service.stderr.pipe(srvLog, { end: false });

    await until(async () => {
      try {
        const r = await fetch('http://127.0.0.1:18765/v1/health');
        return r.ok;
      } catch {
        return false;
      }
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
  }

  const sampler = startProcSampler(service?.pid ?? null, chrome.pid);

  const tab = await openTab(`${baseUrl}/`);
  await cdp('Target.activateTarget', { targetId: tab.targetId });
  await cdp('Page.bringToFront', {}, tab.sessionId);
  await until(() => evaluate(tab, 'document.querySelector("video")?.readyState >= 3'), 20000, 'video ready');
  await installStage8DomProbe(tab);

  const t0 = Date.now();
  await evaluate(tab, 'const v=document.querySelector("video"); v.currentTime=0; v.playbackRate=1.0; v.play()', true);

  if (!SUBTITLES_OFF && extId) {
    const { targetInfo } = await cdp('Target.getTargetInfo', { targetId: tab.targetId });
    extTarget = (await cdp('Target.getTargets', { filter: [{ type: 'tab' }, { exclude: true }] }))
      .targetInfos.find((t) => t.url === targetInfo.url);
    await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget.targetId });
    await sleep(1000);
    await ensureOffscreenRuntimeEnabled(extId);
  }

  let lastProgressT = -1;
  let stallTicks = 0;
  const maxWallMs = MAX_PLAY_S > 0 ? (MAX_PLAY_S + 30) * 1000 : 52 * 60 * 1000;
  while (Date.now() - t0 < maxWallMs) {
    await sleep(1000);
    const st = await evaluate(tab, `(() => {
      const v = document.querySelector('video');
      const q = v.getVideoPlaybackQuality();
      return { ct: v.currentTime, ended: v.ended, tf: q.totalVideoFrames, df: q.droppedVideoFrames };
    })()`);
    if (MAX_PLAY_S > 0 && st.ct >= MAX_PLAY_S) {
      const { data } = await cdp('Page.captureScreenshot', { format: 'png' }, tab.sessionId);
      await writeFile(join(outDir, 'shot_end.png'), Buffer.from(data, 'base64'));
      await evaluate(tab, 'document.querySelector("video").pause()');
      await sleep(3000);
      break;
    }
    if (st.ended) {
      await sleep(4000);
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

  const endStats = await evaluate(tab, `(() => {
    const v = document.querySelector('video');
    const q = v.getVideoPlaybackQuality();
    return {
      current_time: Number(v.currentTime.toFixed(2)),
      ended: v.ended,
      total_frames: q.totalVideoFrames,
      dropped_frames: q.droppedVideoFrames,
    };
  })()`);
  const samples = await sampler.stop();
  if (!SUBTITLES_OFF && extId && extTarget) {
    await cdp('Extensions.triggerAction', { id: extId, targetId: extTarget.targetId }).catch(() => {});
    await sleep(1500);
  }

  summary.result = {
    wall_seconds: +((Date.now() - t0) / 1000).toFixed(1),
    ...endStats,
    drop_rate_pct: endStats.total_frames > 0 ? +((endStats.dropped_frames / endStats.total_frames) * 100).toFixed(3) : 0,
    diag_counts: diagCounts,
    dom_kind_counts: domKindCounts,
    dom_event_counts: domEventCounts,
    srv_peak_rss_mib: Math.max(0, ...samples.map((s) => s.srv_rss_mib)),
    srv_peak_cpu_pct: Math.max(0, ...samples.map((s) => s.srv_cpu_pct)),
    chrome_peak_rss_mib: Math.max(0, ...samples.map((s) => s.chrome_rss_mib)),
  };
  await writeFile(join(outDir, 'chrome_run_summary.json'), JSON.stringify(summary, null, 2) + '\n');
  console.log('STAGE8_CHROME_DONE', JSON.stringify(summary));
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
  await rm(scratchDir, { recursive: true, force: true });
}
