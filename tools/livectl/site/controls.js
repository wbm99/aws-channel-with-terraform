// Controls grouped by what they act on. A button exists only when the server says the action is allowed now.
import { el } from './format.js';

export const GROUPS = [
  ['Infrastructure', [
    ['deploy', 'Deploy stack', 'primary', 'terraform apply: create or update the whole stack (a few minutes).'],
    ['teardown', 'Tear down stack', 'danger', 'terraform destroy: remove the whole stack.'],
    ['scan', 'Scan for leftovers', '', 'List billable resources still carrying the project name.'],
  ]],
  ['Broadcast', [
    ['go-live', 'Go live', 'primary', 'Start the flow, then the channel. Hourly billing starts (about 2 min).'],
    ['go-off-air', 'Go off air', '', 'Stop the channel, then the flow (about 2 min).'],
    ['source-start', 'Send test source', '', 'Push the FFmpeg test pattern with its UTC clock to the ingest.'],
    ['source-stop', 'Stop test source', '', 'Stop the FFmpeg test source.'],
  ]],
];

export function renderControls(container, actions, sourceRunning, next, onAction) {
  const visible = (name) => actions[name] === null && (name !== 'source-stop' || sourceRunning);
  container.replaceChildren(el('div', { class: 'groups' }, GROUPS.map(([title, items]) => {
    const shown = items.filter(([name]) => visible(name));
    return el('div', { class: 'group' },
      el('h3', {}, title),
      shown.length
        ? el('div', { class: 'actions' }, shown.map(([name, label, kind, help]) => el('button', {
          // The recommended next step pulses: deploy, then go live, then send the test source.
          class: 'act ' + kind + (name === next ? ' next' : ''), type: 'button', 'data-action': name,
          'data-next': name === next ? 'true' : null, title: name === next ? 'Next step: ' + help : help,
          onclick: () => onAction(name),
        }, label)))
        : el('p', { class: 'none' }, 'Nothing to do here right now.'));
  })));
}
