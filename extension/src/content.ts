import type { Owner, Player, View } from './internal';

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
  let lastNotice: Player | undefined;
  let ticker: number | undefined;
  let hide: number | undefined;
  let bindings: AbortController | undefined;
  const observer = new ResizeObserver(position);
  const host = document.createElement('div');
  host.dataset.localVideoSubtitle = '';
  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; position: fixed; display: flex; z-index: 2147483647;
      align-items: flex-end; justify-content: center; pointer-events: none; box-sizing: border-box; }
    span { font: 600 clamp(16px, 2.4vw, 34px)/1.4 system-ui, sans-serif; display: inline-block;
      max-width: 86%; text-align: center; color: white; background: rgba(0,0,0,.78);
      padding: .22em .55em; margin-bottom: 5%; border-radius: .28em; white-space: pre-wrap;
      overflow-wrap: anywhere; }
    span:empty { display: none; }
    span[data-status] { font-size: 14px; color: #fff4cf; }
  `;
  const label = document.createElement('span');
  label.lang = 'zh-Hans';
  shadow.append(style, label);

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

  function show(text = '', status = false) {
    clearTimeout(hide);
    label.textContent = text;
    label.toggleAttribute('data-status', status);
    if (text) hide = window.setTimeout(() => { label.textContent = ''; }, 4000);
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
    clearTimeout(hide);
    video = undefined;
    lastNotice = undefined;
    host.remove();
    label.textContent = '';
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
      case 'caption':
        if (view.cue.session_id === sessionId && view.cue.seq > seq) {
          seq = view.cue.seq;
          show(view.cue.text_zh);
        }
        break;
      case 'stopped':
        if (view.text) {
          show(view.text, true);
          owner = undefined;
          bindings?.abort();
          observer.disconnect();
          clearInterval(ticker);
          clearTimeout(hide);
          hide = window.setTimeout(() => { host.remove(); }, 4000);
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
