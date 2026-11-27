import type { Owner, Player, View } from './internal';
import { shouldApplyDeltaUpdate, type DeltaKind } from './preview_guard';

declare const SUBTITLE_DIAGNOSTICS: boolean;

const global = globalThis as typeof globalThis & { subtitleContentInstalled?: boolean };
if (!global.subtitleContentInstalled) {
  global.subtitleContentInstalled = true;
  install();
}

function install() {
  let owner: Owner | undefined;
  let video: HTMLVideoElement | undefined;
  let source = '';
  let serial = 0;
  let version = 0;
  let epoch = -1;
  let sessionId = '';
  let seq = 0;
  let activeDeltaSeq = 0;
  let activeDeltaKind: DeltaKind | undefined;
  let lastNotice: Player | undefined;
  let ticker: number | undefined;
  let hideCurr: number | undefined;
  let hidePrev: number | undefined;
  let currShownAt = 0;
  let currDwellMs = 4000;
  let bindings: AbortController | undefined;
  const observer = new ResizeObserver(position);
  const host = document.createElement('div');
  host.dataset.localVideoSubtitle = '';
  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; position: fixed; display: flex; flex-direction: column; z-index: 2147483647;
      align-items: center; justify-content: flex-end; gap: .32em; pointer-events: none; box-sizing: border-box;
      padding-bottom: 5%; }
    span { font: 600 clamp(16px, 2.4vw, 34px)/1.4 system-ui, sans-serif; display: inline-block;
      max-width: 86%; text-align: center; color: white; background: rgba(0,0,0,.78);
      padding: .22em .55em; border-radius: .28em; white-space: pre-wrap;
      overflow-wrap: anywhere; }
    span[data-prev] { font-size: clamp(14px, 2.0vw, 29px); opacity: .78; }
    span[data-streaming] { opacity: .88; }
    span:empty { display: none; }
    span[data-status] { font-size: 14px; color: #fff4cf; }
  `;
  const prevLabel = document.createElement('span');
  prevLabel.dataset.prev = '';
  prevLabel.lang = 'zh-Hans';
  const label = document.createElement('span');
  label.dataset.current = '';
  label.lang = 'zh-Hans';
  shadow.append(style, prevLabel, label);

  type DiagnosticCue = {
    session_id: string;
    seq: number;
    epoch: number;
    kind: 'preview' | 'final_token_stream' | 'final';
    text_zh: string;
  };
  let currCue: DiagnosticCue | undefined;
  let prevCue: DiagnosticCue | undefined;
  let displayedCues = new Map<string, DiagnosticCue>();
  const cueRemoveReasons = new Map<string, 'dwell_expired' | 'replaced' | 'cleared'>();
  const cueKey = (cue: { session_id: string; seq: number; epoch: number }) =>
    `${cue.session_id}:${cue.seq}:${cue.epoch}`;
  if (SUBTITLE_DIAGNOSTICS) {
    const isElementVisible = (el: HTMLElement) => {
      const rect = el.getBoundingClientRect();
      return el.isConnected && rect.width > 0 && rect.height > 0 &&
        rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth && !document.hidden;
    };
    const measureVisibility = () => {
      const nextVisible = new Map<string, DiagnosticCue>();
      if (currCue && label.textContent && !label.hasAttribute('data-status') && isElementVisible(label)) {
        nextVisible.set(cueKey(currCue), { ...currCue });
      }
      if (prevCue && prevLabel.textContent && isElementVisible(prevLabel)) {
        nextVisible.set(cueKey(prevCue), { ...prevCue });
      }
      const perfNowMs = performance.now();
      const t = performance.timeOrigin + perfNowMs;
      for (const [key, cue] of displayedCues) {
        if (!nextVisible.has(key)) {
          const removeReason = cueRemoveReasons.get(key) ?? 'replaced';
          cueRemoveReasons.delete(key);
          console.debug('subtitle-diagnostic', JSON.stringify({ stage: 'dom_removed', t, perf_now_ms: perfNowMs,
            remove_reason: removeReason, ...cue,
            media_time: video?.currentTime, playback_rate: video?.playbackRate }));
        }
      }
      for (const [key, cue] of nextVisible) {
        const prev = displayedCues.get(key);
        if (!prev) {
          console.debug('subtitle-diagnostic', JSON.stringify({ stage: 'dom_visible', t, perf_now_ms: perfNowMs,
            ...cue, media_time: video?.currentTime, playback_rate: video?.playbackRate }));
        } else if (prev.text_zh !== cue.text_zh || prev.kind !== cue.kind) {
          console.debug('subtitle-diagnostic', JSON.stringify({ stage: 'dom_update', t, perf_now_ms: perfNowMs,
            previous_text_zh: prev.text_zh, previous_kind: prev.kind, ...cue,
            media_time: video?.currentTime, playback_rate: video?.playbackRate }));
        }
      }
      displayedCues = nextVisible;
      requestAnimationFrame(measureVisibility);
    };
    requestAnimationFrame(measureVisibility);
  }

  function position() {
    const fullscreen = document.fullscreenElement;
    if (!owner || !video || fullscreen instanceof HTMLVideoElement || (fullscreen && !fullscreen.contains(video))) {
      host.remove();
      return;
    }
    const parent = fullscreen ?? document.documentElement;
    if (host.parentElement !== parent) parent.append(host);
    const rect = video.getBoundingClientRect();
    Object.assign(host.style, { left: rect.left + 'px', top: rect.top + 'px',
      width: rect.width + 'px', height: rect.height + 'px' });
  }

  function clearBoth(reason: 'dwell_expired' | 'replaced' | 'cleared' = 'cleared') {
    clearTimeout(hideCurr);
    clearTimeout(hidePrev);
    activeDeltaSeq = 0;
    activeDeltaKind = undefined;
    if (SUBTITLE_DIAGNOSTICS) {
      if (currCue) cueRemoveReasons.set(cueKey(currCue), reason);
      if (prevCue) cueRemoveReasons.set(cueKey(prevCue), reason);
      currCue = undefined;
      prevCue = undefined;
    }
    prevLabel.textContent = '';
    label.textContent = '';
    label.removeAttribute('data-status');
    label.removeAttribute('data-streaming');
  }

  function showDelta(text: string, cueSeq: number, deltaKind?: DeltaKind) {
    const effectiveKind: DeltaKind = deltaKind ?? 'preview';
    if (
      activeDeltaSeq === cueSeq &&
      activeDeltaKind &&
      !shouldApplyDeltaUpdate(
        { seq: activeDeltaSeq, kind: activeDeltaKind, text: label.textContent ?? '' },
        { seq: cueSeq, kind: effectiveKind, text },
      )
    ) {
      return;
    }
    clearTimeout(hideCurr);
    if (activeDeltaSeq !== cueSeq) {
      clearTimeout(hidePrev);
      if (SUBTITLE_DIAGNOSTICS && prevCue) {
        cueRemoveReasons.set(cueKey(prevCue), 'replaced');
      }
      if (label.textContent && !label.hasAttribute('data-status')) {
        prevLabel.textContent = label.textContent;
        if (SUBTITLE_DIAGNOSTICS) prevCue = currCue ? { ...currCue } : undefined;
        const elapsed = performance.now() - currShownAt;
        const prevDwell = Math.min(3500, Math.max(1800, currDwellMs - elapsed));
        hidePrev = window.setTimeout(() => {
          if (SUBTITLE_DIAGNOSTICS && prevCue) cueRemoveReasons.set(cueKey(prevCue), 'dwell_expired');
          prevLabel.textContent = '';
          if (SUBTITLE_DIAGNOSTICS) prevCue = undefined;
        }, prevDwell);
      } else {
        prevLabel.textContent = '';
        if (SUBTITLE_DIAGNOSTICS) prevCue = undefined;
      }
      activeDeltaSeq = cueSeq;
    }
    activeDeltaKind = effectiveKind;
    if (SUBTITLE_DIAGNOSTICS) {
      currCue = {
        session_id: sessionId,
        seq: cueSeq,
        epoch,
        kind: effectiveKind,
        text_zh: text,
      };
    }
    label.textContent = text;
    label.removeAttribute('data-status');
    label.toggleAttribute('data-streaming', true);
    currShownAt = performance.now();
    hideCurr = window.setTimeout(() => {
      if (SUBTITLE_DIAGNOSTICS && currCue) cueRemoveReasons.set(cueKey(currCue), 'dwell_expired');
      label.textContent = '';
      label.removeAttribute('data-streaming');
      activeDeltaSeq = 0;
      activeDeltaKind = undefined;
      if (SUBTITLE_DIAGNOSTICS) currCue = undefined;
    }, 4500);
    position();
  }

  function show(text = '', status = false, finalizeSeq = 0) {
    if (!text) {
      clearBoth('cleared');
      position();
      return;
    }
    if (status) {
      clearBoth('replaced');
      label.textContent = text;
      label.toggleAttribute('data-status', true);
      hideCurr = window.setTimeout(() => { label.textContent = ''; }, 4000);
      position();
      return;
    }
    clearTimeout(hideCurr);
    const finalizedInPlace = finalizeSeq > 0 && finalizeSeq === activeDeltaSeq;
    activeDeltaSeq = 0;
    activeDeltaKind = undefined;
    if (!finalizedInPlace) {
      clearTimeout(hidePrev);
      if (SUBTITLE_DIAGNOSTICS && prevCue) {
        cueRemoveReasons.set(cueKey(prevCue), 'replaced');
      }
      if (label.textContent && !label.hasAttribute('data-status')) {
        prevLabel.textContent = label.textContent;
        if (SUBTITLE_DIAGNOSTICS) prevCue = currCue ? { ...currCue } : undefined;
        const elapsed = performance.now() - currShownAt;
        const prevDwell = Math.min(3500, Math.max(1800, currDwellMs - elapsed));
        hidePrev = window.setTimeout(() => {
          if (SUBTITLE_DIAGNOSTICS && prevCue) cueRemoveReasons.set(cueKey(prevCue), 'dwell_expired');
          prevLabel.textContent = '';
          if (SUBTITLE_DIAGNOSTICS) prevCue = undefined;
        }, prevDwell);
      } else {
        prevLabel.textContent = '';
        if (SUBTITLE_DIAGNOSTICS) prevCue = undefined;
      }
    }
    if (SUBTITLE_DIAGNOSTICS) currCue = { session_id: sessionId, seq, epoch, kind: 'final', text_zh: text };
    label.textContent = text;
    label.removeAttribute('data-status');
    label.removeAttribute('data-streaming');
    currShownAt = performance.now();
    currDwellMs = Math.min(6500, Math.max(2500, Math.round(text.length * 170)));
    hideCurr = window.setTimeout(() => {
      if (SUBTITLE_DIAGNOSTICS && currCue) cueRemoveReasons.set(cueKey(currCue), 'dwell_expired');
      label.textContent = '';
      if (SUBTITLE_DIAGNOSTICS) currCue = undefined;
    }, currDwellMs);
    position();
  }

  function currentState(): Player {
    const state = !video ? 'paused' : video.ended ? 'ended' :
      video.seeking ? 'seeking' : video.paused || video.readyState < 3 ? 'paused' : 'playing';
    return { serial: ++serial, version, state };
  }

  function notify(force = false) {
    if (!owner) return;
    const player = currentState();
    if (!force && lastNotice?.version === player.version && lastNotice?.state === player.state) return;
    lastNotice = player;
    void chrome.runtime.sendMessage({ to: 'worker', type: 'player', ...owner, player }).catch(() => undefined);
  }

  function scan() {
    const candidates = [...document.querySelectorAll('video')].filter(v => {
      const rect = v.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(v).visibility !== 'hidden';
    });
    candidates.sort((a, b) => Number(a.paused) - Number(b.paused) ||
      b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);
    const next = candidates[0];
    const url = new URL(location.href);
    const nextSource = (next?.currentSrc ?? '') + '|' + url.pathname + '|' + (url.searchParams.get('v') ?? '');
    if (next !== video || nextSource !== source) {
      bindings?.abort();
      observer.disconnect();
      video = next;
      source = nextSource;
      version++;
      if (next) {
        bindings = new AbortController();
        observer.observe(next);
        for (const event of ['playing', 'pause', 'seeking', 'seeked', 'ended', 'emptied', 'loadeddata']) {
          next.addEventListener(event, () => {
            if (event === 'emptied' || event === 'seeking') {
              // Invalidate immediately, before a burst of subsequent seeked/playing events.
              version++;
              notify(true);
            } else {
              scan();
              notify();
            }
          }, { signal: bindings.signal });
        }
      }
      position();
      notify(true);
    }
  }

  function unbind() {
    owner = undefined;
    bindings?.abort();
    observer.disconnect();
    clearInterval(ticker);
    video = undefined;
    lastNotice = undefined;
    host.remove();
    clearBoth();
  }

  function update(view: View) {
    if (!owner || view.activation !== owner.activation || view.tabId !== owner.tabId || view.epoch < epoch) return;
    if (view.epoch > epoch) {
      epoch = view.epoch;
      sessionId = '';
      seq = 0;
      show();
    }
    switch (view.kind) {
      case 'clear': show(); break;
      case 'ready': sessionId = view.sessionId; seq = 0; show(); break;
      case 'status':
        if (!label.textContent || label.hasAttribute('data-status')) show(view.text, true);
        break;
      case 'caption_delta':
        if (view.cue.session_id === sessionId && view.cue.seq > seq) {
          showDelta(view.cue.text_zh, view.cue.seq, view.cue.delta_kind);
        }
        break;
      case 'caption_discard':
        if (view.cue.session_id === sessionId && view.cue.seq > seq) {
          seq = view.cue.seq;
          if (activeDeltaSeq === view.cue.seq) {
            clearTimeout(hideCurr);
            if (SUBTITLE_DIAGNOSTICS && currCue) cueRemoveReasons.set(cueKey(currCue), 'cleared');
            label.textContent = '';
            label.removeAttribute('data-status');
            label.removeAttribute('data-streaming');
            activeDeltaSeq = 0;
            activeDeltaKind = undefined;
            if (SUBTITLE_DIAGNOSTICS) currCue = undefined;
            position();
          }
        }
        break;
      case 'caption':
        if (view.cue.session_id === sessionId && view.cue.seq > seq) {
          seq = view.cue.seq;
          show(view.cue.text_zh, false, view.cue.seq);
        }
        break;
      case 'stopped':
        if (view.text) {
          show(view.text, true);
          owner = undefined;
          bindings?.abort();
          observer.disconnect();
          clearInterval(ticker);
          clearTimeout(hideCurr);
          clearTimeout(hidePrev);
          hideCurr = window.setTimeout(() => { host.remove(); }, 4000);
        } else unbind();
    }
  }

  document.addEventListener('fullscreenchange', () => {
    position();
    if (owner) void chrome.runtime.sendMessage({ to: 'worker', type: 'fullscreen', ...owner,
      unsupported: document.fullscreenElement instanceof HTMLVideoElement ||
        Boolean(document.fullscreenElement && video && !document.fullscreenElement.contains(video)),
    }).catch(() => undefined);
  });
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, { passive: true });
  window.addEventListener('pagehide', () => {
    if (!owner) return;
    void chrome.runtime.sendMessage({ to: 'worker', type: 'pagehide', ...owner }).catch(() => undefined);
    unbind();
  });
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg?.to !== 'content' || sender.id !== chrome.runtime.id || sender.tab) return;
    if (msg.type === 'bind') {
      unbind();
      owner = { tabId: msg.tabId, activation: msg.activation };
      epoch = -1;
      sessionId = '';
      seq = 0;
      scan();
      ticker = window.setInterval(scan, 500);
      reply(currentState());
    } else if (msg.type === 'view') update(msg.view as View);
    else if (msg.type === 'ownsDocument') reply(owner?.activation === msg.activation);
  });
}
