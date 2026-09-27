// Every button the console offers, placed where it belongs: the three steps to a playing stream, maintenance
// (scan, tear down) apart from them, and the stop buttons on the Live page. A button exists only when the server
// says its action is allowed now, and the recommended next step pulses.
import { el } from './format.js';

const HELP = {
  deploy: 'terraform apply: create or update the whole stack (a few minutes).',
  teardown: 'terraform destroy: remove the whole stack.',
  scan: 'List billable resources still carrying the project name.',
  'go-live': 'Start the flow, then the channel. Hourly billing starts (about 2 min).',
  'go-off-air': 'Stop the test source, the channel, then the flow (about 2 min).',
  'source-start': 'Push the FFmpeg test pattern with its UTC clock to the ingest.',
  'source-stop': 'Stop the FFmpeg test source.',
};
const LABEL = {
  deploy: 'Deploy stack', teardown: 'Tear down stack', scan: 'Scan for leftovers', 'go-live': 'Go live',
  'go-off-air': 'Go off air', 'source-start': 'Send test source', 'source-stop': 'Stop test source',
};

function allowed(state, name) {
  return state.actions[name] === null && (name !== 'source-stop' || state.sourceRunning);
}

function button(state, name, kind, onAction) {
  const next = name === state.next;
  return el('button', {
    class: 'act ' + kind + (next ? ' next' : ''), type: 'button', 'data-action': name,
    'data-next': next ? 'true' : null, title: (next ? 'Next step: ' : '') + HELP[name],
    onclick: () => onAction(name),
  }, LABEL[name]);
}

function buttons(state, list, onAction) {
  return list.filter(([name]) => allowed(state, name)).map(([name, kind]) => button(state, name, kind, onAction));
}

// What each step has achieved, read from the chain: used for its tick and its one-line status.
function progress(state) {
  const node = (id) => state.nodes.find((n) => n.id === id) || {};
  const onAir = node('mediaconnect_flow').state === 'ACTIVE' && node('medialive_channel').state === 'RUNNING';
  const connected = node('srt_source').state === 'CONNECTED';
  return {
    deploy: [state.deployed, state.deployed ? 'Stack deployed.' : 'Nothing is deployed yet.'],
    live: [onAir, onAir ? 'Flow and channel are running; billing by the hour.' : 'Flow and channel are stopped.'],
    source: [connected, connected ? 'A source is connected to the ingest.'
      : state.sourceRunning ? 'The test source is starting…' : 'No source is connected.'],
  };
}

// The step a running job belongs to, and how to say it: the step itself says what is happening.
const RUNNING = {
  deploy: ['deploy', 'Deploying the stack…'], teardown: ['deploy', 'Tearing the stack down…'],
  'go-live': ['live', 'Starting the flow, then the channel…'], 'go-off-air': ['live', 'Stopping the source, channel and flow…'],
};

export function renderSteps(list, state, onAction) {
  const done = progress(state);
  const [busyStep, busyText] = RUNNING[state.job] || [];
  const steps = [
    ['deploy', 'Deploy the stack', done.deploy, [['deploy', 'primary']]],
    ['live', 'Go live', done.live, [['go-live', 'primary'], ['go-off-air', '']]],
    ['source', 'Send a source', done.source, [['source-start', 'primary'], ['source-stop', '']]],
  ];
  list.replaceChildren(...steps.map(([id, title, [complete, status], actions], index) => {
    const current = actions.some(([name]) => name === state.next);
    const busy = id === busyStep;
    const phase = busy ? 'busy' : complete ? 'done' : current ? 'current' : 'todo';
    return el('li', { class: 'step', 'data-step': id, 'data-status': phase },
      el('span', { class: 'step-number', 'aria-hidden': 'true' }, busy ? '…' : complete ? '✓' : String(index + 1)),
      el('div', { class: 'step-body' },
        el('h3', {}, title),
        el('p', { class: 'step-status' }, busy ? busyText + ' Progress is in Last job below.' : status),
        el('div', { class: 'actions' }, buttons(state, actions, onAction))));
  }));
}

export function renderMaintenance(container, state, onAction) {
  const shown = buttons(state, [['scan', ''], ['teardown', 'danger']], onAction);
  container.replaceChildren(shown.length
    ? el('div', { class: 'actions' }, shown)
    : el('p', { class: 'none' }, 'Scan and tear down are unavailable while a job runs or while on air.'));
}

// On the Live page only the ways to stop: billing can always be ended from wherever you are.
export function renderLiveActions(container, state, onAction) {
  container.replaceChildren(...buttons(state, [['source-stop', ''], ['go-off-air', '']], onAction));
}
