import type { Owner, Player, View } from './internal';
import { displayError, type Settings } from './protocol';

const offscreenUrl = chrome.runtime.getURL('offscreen.html');
const trustedStorage = chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
let toggling = false;

async function existing(): Promise<boolean> {
  return (await chrome.runtime.getContexts({
    contextTypes: [chrome.runtime.ContextType.OFFSCREEN_DOCUMENT], documentUrls: [offscreenUrl],
  })).length > 0;
}

function offscreen(message: Record<string, unknown>) {
  return chrome.runtime.sendMessage({ to: 'offscreen', ...message });
}

async function owner(): Promise<Owner | undefined> {
  if (await existing()) return (await offscreen({ type: 'owner' })) ?? undefined;
}

async function toTab(tabId: number, message: Record<string, unknown>) {
  try { return await chrome.tabs.sendMessage(tabId, { to: 'content', ...message }, { frameId: 0 }); }
  catch { return undefined; }
}

chrome.action.onClicked.addListener(async tab => {
  if (toggling || tab.id === undefined) return;
  const tabId = tab.id;
  toggling = true;
  let activation: string | undefined;
  try {
    await trustedStorage;
    const active = await owner();
    if (active) {
      await offscreen({ type: 'stop', ...active });
      if (active.tabId === tabId) return;
    }
    const settings = await chrome.storage.local.get(['token', 'profile_id']) as Settings;
    if (!settings.token) {
      await chrome.runtime.openOptionsPage();
      return;
    }
    if (!await existing()) {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html', reasons: [chrome.offscreen.Reason.USER_MEDIA],
        justification: '捕获用户选中的标签页音频并继续播放原声',
      });
    }
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
    activation = crypto.randomUUID();
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
    const result = await offscreen({ type: 'open', tabId, activation, streamId, settings });
    if (!result?.ok) throw new Error(result?.code ?? 'capture_failed');
    const state = await toTab(tabId, { type: 'bind', tabId, activation }) as Player | undefined;
    if (!state) throw new Error('capture_failed');
    await offscreen({ type: 'player', tabId, activation, player: state });
    await chrome.action.setBadgeText({ tabId, text: '中' });
    await chrome.action.setTitle({ tabId, title: '停止中文字幕' });
  } catch (error) {
    if (activation) await offscreen({ type: 'stop', tabId, activation }).catch(() => undefined);
    await chrome.action.setBadgeText({ tabId, text: '!' }).catch(() => undefined);
    await chrome.action.setTitle({ tabId, title: displayError(error instanceof Error ? error.message : 'capture_failed') })
      .catch(() => undefined);
  } finally {
    toggling = false;
  }
});

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message?.to !== 'worker' || sender.id !== chrome.runtime.id) return;
  if (message.type === 'view' && sender.url === offscreenUrl) {
    const view = message.view as View;
    void toTab(view.tabId, { type: 'view', view });
    if (view.kind === 'stopped') {
      void chrome.action.setBadgeText({ tabId: view.tabId, text: view.text ? '!' : '' }).catch(() => undefined);
      void chrome.action.setTitle({ tabId: view.tabId, title: view.text || '启动或停止中文字幕' }).catch(() => undefined);
    }
  }
  if (message.type === 'player' && sender.tab?.id === message.tabId && sender.frameId === 0) {
    offscreen({ type: 'player', tabId: message.tabId, activation: message.activation, player: message.player })
      .then(reply, () => reply(undefined));
    return true;
  }
  if (message.type === 'pagehide' && sender.tab?.id === message.tabId && sender.frameId === 0) {
    void offscreen({ type: 'stop', tabId: message.tabId, activation: message.activation }).catch(() => undefined);
  }
  if (message.type === 'fullscreen' && sender.tab?.id === message.tabId && sender.frameId === 0) {
    void (async () => {
      const active = await owner();
      if (active?.activation !== message.activation) return;
      await chrome.action.setBadgeText({ tabId: message.tabId, text: message.unsupported ? '!' : '中' });
      await chrome.action.setTitle({ tabId: message.tabId, title: message.unsupported
        ? '此全屏模式无法显示中文字幕。退出全屏后恢复显示。' : '停止中文字幕' });
    })().catch(() => undefined);
  }
});

async function stopTab(tabId: number) {
  const active = await owner();
  if (active?.tabId === tabId) await offscreen({ type: 'stop', ...active });
}

chrome.tabs.onRemoved.addListener(tabId => { void stopTab(tabId).catch(() => undefined); });
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status !== 'complete') return;
  void (async () => {
    const active = await owner();
    if (active?.tabId === tabId && !await toTab(tabId, { type: 'ownsDocument', activation: active.activation })) {
      await offscreen({ type: 'stop', ...active });
    }
  })().catch(() => undefined);
});
