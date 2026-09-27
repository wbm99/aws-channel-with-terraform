// The seven resources, left to right, each a button that selects it.
import { el } from './format.js';

export function renderChain(list, nodes, selected, onSelect) {
  list.replaceChildren(...nodes.map((node, index) => el('li', { class: 'node-wrap' },
    el('button', {
      class: 'node', type: 'button', 'data-node': node.id, 'data-health': node.health,
      'aria-pressed': String(node.id === selected), onclick: () => onSelect(node.id),
    },
      el('span', { class: 'node-title' }, node.title),
      el('span', { class: 'node-state', 'data-health': node.health }, el('i', { class: 'dot' }), node.state || '—'),
      el('span', { class: 'node-summary' }, node.summary)),
    index < nodes.length - 1 ? el('span', { class: 'arrow', 'aria-hidden': 'true' }, '→') : null)));
}
