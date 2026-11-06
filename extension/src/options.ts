import { displayError, loadCatalog, type Settings } from './protocol';

const token = document.querySelector<HTMLInputElement>('#token')!;
const profile = document.querySelector<HTMLSelectElement>('#profile')!;
const status = document.querySelector<HTMLElement>('#status')!;
const ready = chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });

async function refresh(selected = profile.value) {
  try {
    const catalog = await loadCatalog(token.value.trim());
    profile.replaceChildren(new Option('使用服务默认配置', ''));
    for (const p of catalog.profiles) {
      const option = new Option(p.available ? p.label : p.label + '（不可用）', p.id);
      option.disabled = !p.available;
      profile.add(option);
    }
    if (selected && ![...profile.options].some(option => option.value === selected)) {
      const unavailable = new Option('已保存的配置不可用', selected);
      unavailable.disabled = true;
      profile.add(unavailable);
    }
    profile.value = selected;
    status.textContent = '本地服务已连接。';
  } catch (error) {
    status.textContent = token.value ? displayError(error instanceof Error ? error.message : 'service_unreachable') : '请录入本地服务令牌。';
  }
}

document.querySelector('#refresh')!.addEventListener('click', () => { void refresh(); });
document.querySelector('form')!.addEventListener('submit', async event => {
  event.preventDefault();
  await ready;
  if (profile.selectedOptions[0]?.disabled) {
    status.textContent = '请选择可用的配置。';
    return;
  }
  await chrome.storage.local.set({ token: token.value.trim(), profile_id: profile.value });
  status.textContent = '已保存。';
});

void ready.then(async () => {
  const settings = await chrome.storage.local.get(['token', 'profile_id']) as Settings;
  token.value = settings.token ?? '';
  await refresh(settings.profile_id ?? '');
});
