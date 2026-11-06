export const HTTP = 'http://127.0.0.1:18765';
export const WS = 'ws://127.0.0.1:18765/v1/captions';
export const MAX_BUFFERED_BYTES = 64_000;
export const SAMPLES_PER_FRAME = 3200;

export interface Profile { id: string; label: string; available: boolean }
export interface Catalog { default_profile_id: string; profiles: Profile[] }
export interface Settings { token?: string; profile_id?: string }
export interface Caption {
  type: 'caption'; session_id: string; seq: number; text_zh: string;
  start_sample: number; end_sample: number;
}
export type ServerEvent =
  | { type: 'ready'; session_id: string; version: 1; profile_id: string; language: 'zh-Hans' }
  | Caption
  | { type: 'warning'; session_id: string; code: string }
  | { type: 'finished'; session_id: string; accepted_samples: number }
  | { type: 'error'; session_id?: string; code: string; reason?: string };

const object = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x);
const text = (x: unknown): x is string => typeof x === 'string' && x.length > 0;
const integer = (x: unknown): x is number => typeof x === 'number' && Number.isSafeInteger(x) && x >= 0;

export function start(profile_id: string, token: string) {
  return { type: 'start', version: 1, profile_id, token,
    audio: { format: 's16le', sample_rate: 16000, channels: 1 } } as const;
}

export function serverEvent(raw: unknown): ServerEvent | undefined {
  if (typeof raw !== 'string') return;
  let obj: unknown;
  try { obj = JSON.parse(raw); } catch { return; }
  if (!object(obj)) return;
  if (obj.type === 'error' && text(obj.code) && (obj.session_id === undefined || text(obj.session_id))
      && (obj.reason === undefined || text(obj.reason))) {
    return { type: 'error', code: obj.code, session_id: obj.session_id, reason: obj.reason };
  }
  if (!text(obj.session_id)) return;
  const session_id = obj.session_id;
  switch (obj.type) {
    case 'ready':
      if (obj.version === 1 && obj.language === 'zh-Hans' && text(obj.profile_id))
        return { type: 'ready', session_id, version: 1, profile_id: obj.profile_id, language: 'zh-Hans' };
      break;
    case 'caption':
      if (integer(obj.seq) && obj.seq > 0 && integer(obj.start_sample) && integer(obj.end_sample) &&
          obj.end_sample > obj.start_sample && text(obj.text_zh)) {
        return { type: 'caption', session_id, seq: obj.seq, start_sample: obj.start_sample,
          end_sample: obj.end_sample, text_zh: obj.text_zh };
      }
      break;
    case 'warning':
      if (text(obj.code)) return { type: 'warning', session_id, code: obj.code };
      break;
    case 'finished':
      if (integer(obj.accepted_samples)) return { type: 'finished', session_id, accepted_samples: obj.accepted_samples };
  }
}

export async function loadCatalog(token: string): Promise<Catalog> {
  const response = await fetch(HTTP + '/v1/profiles', {
    headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(3000),
  });
  if (response.status === 401) throw new Error('unauthorized');
  if (!response.ok) throw new Error('service_unreachable');
  const value: unknown = await response.json();
  if (!object(value) || !text(value.default_profile_id) || !Array.isArray(value.profiles)) throw new Error('invalid_message');
  const profiles = value.profiles.map((p: unknown): Profile => {
    if (!object(p) || !text(p.id) || !text(p.label) || typeof p.available !== 'boolean') throw new Error('invalid_message');
    return { id: p.id, label: p.label, available: p.available };
  });
  return { default_profile_id: value.default_profile_id, profiles };
}

const messages: Record<string, string> = {
  unauthorized: '本地服务令牌无效，请检查扩展设置。',
  profile_unavailable: '当前字幕配置不可用，请检查本地服务。',
  unknown_profile: '找不到已选配置，请在扩展设置中重新选择。',
  service_unreachable: '连接不到本地字幕服务。',
  busy: '本地服务正在处理或结束另一条字幕会话。',
  overloaded: '处理跟不上播放，字幕已停止。',
  drain_timeout: '最后一段字幕处理超时。',
  backend_error: '本地识别或翻译失败。',
  capture_failed: '无法采集这个标签页的音频。',
  translation_invalid: '这一段未生成可用的中文字幕。',
  adapter_unaligned: '这一段字幕无法对齐，已跳过。',
};
export function displayError(code: string, reason?: string) {
  if (code === 'profile_unavailable') {
    if (reason === 'disabled') return '所选字幕配置已停用，请在扩展设置中重新选择。';
    if (reason === 'not_ready') return '字幕模型尚未就绪，请检查本地服务。';
    if (reason === 'upstream_unreachable') return '无法连接所选字幕引擎，请检查后端是否已启动。';
  }
  return Object.hasOwn(messages, code) ? messages[code] : '字幕连接出现异常。';
}
