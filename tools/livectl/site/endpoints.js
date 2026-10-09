// Endpoints with copy buttons. Copy uses the value itself, never the text on screen (the old page copied a stray ↗).
import { el } from './format.js';

const ROWS = [
  ['ingest', 'SRT ingest'],
  ['player', 'Player'],
  ['manifest', 'HLS manifest'],
  ['passphrase_secret_arn', 'SRT passphrase (Secrets Manager)'],
];

export async function copy(button, value) {
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = 'Copied';
  } catch (error) {
    button.textContent = 'Copy blocked';
  }
  setTimeout(() => { button.textContent = 'Copy'; }, 1200);
}

export function renderEndpoints(list, endpoints) {
  list.replaceChildren(...ROWS.flatMap(([key, label]) => {
    const value = endpoints[key];
    return [
      el('dt', {}, label),
      el('dd', {},
        el('span', { class: 'value', title: value || '' }, value || '—'),
        value ? el('button', { class: 'copy', type: 'button', 'data-copy': key,
          onclick: (event) => copy(event.currentTarget, value) }, 'Copy') : null,
        value && key === 'player' ? el('a', { href: value, target: '_blank', rel: 'noopener' }, 'Open') : null),
    ];
  }));
}
