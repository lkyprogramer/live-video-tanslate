import type { Owner, Player, View } from './internal';
import { displayError, loadCatalog, MAX_BUFFERED_BYTES, serverEvent, start, WS, type Settings } from './protocol';

type TabAudio = MediaTrackConstraints & { mandatory: { chromeMediaSource: string; chromeMediaSourceId: string } };
type Payload<T> = T extends unknown ? Omit<T, keyof Owner | 'epoch'> : never;

// The service lease is shared across activations, including a handoff to another tab.
let retiredAt = -Infinity;

class Activation {
  private stream?: MediaStream;
  private context?: AudioContext;
  private worklet?: AudioWorkletNode;
  private epoch = 0;
  private closed = false;
  private profile?: Promise<string>;
  private current?: Player;
  private socket?: {
    ws: WebSocket; epoch: number; sessionId?: string; finishing: boolean; timer: number;
  };

  constructor(readonly owner: Owner, private readonly settings: Settings) {}

  private view(payload: Payload<View>) {
    void chrome.runtime.sendMessage({ to: 'worker', type: 'view',
      view: { ...this.owner, epoch: this.epoch, ...payload } }).catch(() => undefined);
  }

  async open(streamId: string) {
    const audio: TabAudio = { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } };
    const stream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
    if (this.closed) { stream.getTracks().forEach(track => track.stop()); return; }
    this.stream = stream;
    for (const track of stream.getAudioTracks()) track.addEventListener('ended', () => this.stop('capture_failed'));
    const context = this.context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    source.connect(context.destination);
    await context.resume();
    await context.audioWorklet.addModule(chrome.runtime.getURL('worklet.js'));
    if (this.closed) return;
    const worklet = this.worklet = new AudioWorkletNode(context, 'subtitle-pcm', { outputChannelCount: [1] });
    source.connect(worklet);
    worklet.connect(context.destination);
    worklet.port.onmessage = ({ data }) => {
      const connection = this.socket;
      if (!connection?.sessionId || connection.epoch !== data.epoch || connection.ws.readyState !== WebSocket.OPEN) return;
      if (data.type === 'pcm' && data.frame instanceof ArrayBuffer) {
        if (connection.ws.bufferedAmount + data.frame.byteLength > MAX_BUFFERED_BYTES) this.stop('overloaded');
        else connection.ws.send(data.frame);
      } else if (data.type === 'flushed' && connection.finishing) {
        connection.ws.send('{"type":"finish"}');
        clearTimeout(connection.timer);
        connection.timer = window.setTimeout(() => this.stop('drain_timeout'), 9000);
      }
    };
    worklet.onprocessorerror = () => this.stop('capture_failed');
  }

  private reset() {
    this.epoch++;
    const previous = this.socket;
    this.socket = undefined;
    if (previous) {
      clearTimeout(previous.timer);
      previous.ws.close();
      retiredAt = performance.now();
    }
    this.worklet?.port.postMessage({ type: 'discard' });
    this.view({ kind: 'clear' });
    return Boolean(previous);
  }

  update(player: Player) {
    if (this.closed || (this.current && player.serial <= this.current.serial)) return;
    const previous = this.current;
    this.current = player;
    const changedVideo = previous?.version !== player.version;
    if (!changedVideo && player.state === 'ended') {
      const conn = this.socket;
      if (conn?.sessionId && !conn.finishing) {
        conn.finishing = true;
        this.worklet?.port.postMessage({ type: 'finish', epoch: conn.epoch });
        conn.timer = window.setTimeout(() => this.stop('drain_timeout'), 1500);
      } else if (conn && !conn.finishing) {
        this.reset();
      }
      return;
    }
    if (!changedVideo && player.state === previous?.state) return;
    this.reset();
    if (player.state === 'playing') void this.connect(this.epoch, performance.now() - retiredAt < 2000);
  }

  private async connect(epoch: number, retry: boolean) {
    try {
      this.view({ kind: 'status', text: '正在连接本地字幕服务…' });
      const token = this.settings.token!;
      this.profile ??= this.settings.profile_id
        ? Promise.resolve(this.settings.profile_id)
        : loadCatalog(token).then(catalog => catalog.default_profile_id);
      const profile = await this.profile;
      if (this.closed || epoch !== this.epoch) return;
      const ws = new WebSocket(WS);
      const conn = { ws, epoch, sessionId: undefined as string | undefined, finishing: false,
        timer: window.setTimeout(() => this.stop('service_unreachable'), 20000) };
      this.socket = conn;
      const current = () => !this.closed && this.socket === conn && this.epoch === epoch;
      ws.onopen = () => { if (current()) ws.send(JSON.stringify(start(profile, token))); };
      ws.onmessage = ({ data }) => {
        if (!current()) return;
        const event = serverEvent(data);
        if (!event) { this.stop('invalid_message'); return; }
        if (event.type === 'error') {
          if (conn.sessionId && event.session_id !== conn.sessionId) return;
          if (event.code === 'busy' && retry && !conn.sessionId) {
            clearTimeout(conn.timer);
            this.socket = undefined;
            ws.close();
            setTimeout(() => {
              if (!this.closed && this.epoch === epoch) void this.connect(epoch, false);
            }, 500);
          } else this.stop(event.code, event.reason);
          return;
        }
        if (event.type === 'ready') {
          if (conn.sessionId || event.profile_id !== profile) { this.stop('invalid_message'); return; }
          conn.sessionId = event.session_id;
          clearTimeout(conn.timer);
          this.view({ kind: 'ready', sessionId: event.session_id });
          this.worklet?.port.postMessage({ type: 'begin', epoch });
          return;
        }
        if (event.session_id !== conn.sessionId) return;
        if (event.type === 'caption') this.view({ kind: 'caption', cue: event });
        if (event.type === 'warning') this.view({ kind: 'status', text: displayError(event.code) });
        if (event.type === 'finished') {
          clearTimeout(conn.timer);
          this.socket = undefined;
          ws.close();
          retiredAt = performance.now();
        }
      };
      ws.onclose = () => { if (current()) this.stop('service_unreachable'); };
    } catch (error) {
      if (!this.closed && this.epoch === epoch) this.stop(error instanceof Error ? error.message : 'service_unreachable');
    }
  }

  stop(code?: string, reason?: string) {
    if (this.closed) return;
    this.closed = true;
    if (active === this) active = undefined;
    this.reset();
    this.worklet?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    if (this.context) void this.context.close().catch(() => undefined);
    this.view({ kind: 'stopped', text: code ? displayError(code, reason) : undefined });
  }
}

let active: Activation | undefined;
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.to !== 'offscreen' || sender.id !== chrome.runtime.id || sender.tab) return;
  if (msg.type === 'owner') { reply(active?.owner ?? null); return; }
  if (msg.type === 'open') {
    active?.stop();
    const activation = active = new Activation({ tabId: msg.tabId, activation: msg.activation }, msg.settings);
    activation.open(msg.streamId).then(
      () => reply({ ok: active === activation }),
      () => { activation.stop('capture_failed'); reply({ ok: false, code: 'capture_failed' }); },
    );
    return true;
  }
  const target = active;
  if (target && target.owner.tabId === msg.tabId && target.owner.activation === msg.activation) {
    if (msg.type === 'stop') target.stop();
    if (msg.type === 'player') target.update(msg.player);
  }
  reply({ ok: true });
});
