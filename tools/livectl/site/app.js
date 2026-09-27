// Startup, polling and wiring. Each region re-renders only when its data changed, so a poll never steals focus.
import { getPipeline, post } from './api.js';
import { renderChain } from './chain.js';
import { renderControls } from './controls.js';
import { renderEndpoints } from './endpoints.js';
import { ago } from './format.js';
import { LogsPanel } from './logs-panel.js';
import { renderDetail } from './node-detail.js';

const POLL_MS = 2000;
const $ = (id) => document.getElementById(id);
const logs = new LogsPanel($('tabs'), $('log'));
const seen = {};
let data = null;
let selected = 'medialive_channel';
let lastUpdate = null;
let billingSince = null;
let lastFailedJob = null;

function changed(key, value) {
  const json = JSON.stringify(value);
  if (seen[key] === json) return false;
  seen[key] = json;
  return true;
}

function banner(text) {
  $('banner').textContent = text || '';
  $('banner').hidden = !text;
}

function renderHeader() {
  $('verdict').dataset.health = data.verdict.health;
  $('verdict-text').textContent = data.verdict.text;
  if (data.rate > 0) {
    if (billingSince === null) billingSince = Date.now();
    const minutes = Math.round((Date.now() - billingSince) / 60000);
    $('cost').textContent = '$' + data.rate.toFixed(2) + ' / h · ' + minutes + ' min';
  } else {
    billingSince = null;
    $('cost').textContent = '$0.00 / h';
  }
}

function renderPlayer() {
  const frame = $('player-frame');
  const url = data.endpoints.player;
  if (url && frame.getAttribute('src') !== url) frame.setAttribute('src', url);
  frame.hidden = !url;
  $('player-empty').hidden = Boolean(url);
}

function render() {
  renderHeader();
  const note = $('deploy-note');
  note.hidden = data.deployed && !data.note;
  note.textContent = data.note || (data.deployed ? '' : 'Nothing is deployed yet. Deploy stack creates it (a few minutes).');

  if (!data.nodes.some((n) => n.id === selected) && data.nodes.length) selected = data.nodes[0].id;
  // checked_at moves every second; leaving it out keeps the chain from re-rendering (and losing focus) on each poll.
  const stable = data.nodes.map(({ checked_at, ...rest }) => rest);
  if (changed('nodes', [stable, selected])) {
    renderChain($('chain'), data.nodes, selected, (id) => { selected = id; render(); });
    renderDetail($('detail'), data.nodes.find((n) => n.id === selected), data.metrics_age);
  }
  renderPlayer();
  const sourceRunning = Boolean(data.source && data.source.state === 'running');
  if (changed('actions', [data.actions, sourceRunning])) renderControls($('controls'), data.actions, sourceRunning, act);
  if (changed('endpoints', data.endpoints)) renderEndpoints($('endpoints'), data.endpoints);

  logs.showJob(data.job);
  const job = data.job;
  if (job && job.state === 'failed' && lastFailedJob !== job.started_at) {
    lastFailedJob = job.started_at;
    banner(job.name + ' failed: ' + (job.error || 'see the Jobs tab'));
  }
}

async function poll() {
  try {
    data = await getPipeline(logs.offset);
    lastUpdate = Date.now();
    render();
  } catch (error) {
    banner('The console is not answering (' + error.message + '). Is livectl ui still running?');
  }
}

async function act(name, body) {
  if (name === 'teardown' && !body) {
    $('confirm').hidden = false;
    $('confirm-input').focus();
    return;
  }
  const result = await post(name, body);
  if (!result.ok) {
    banner(result.data.error || name + ' was refused (' + result.status + ')');
    return;
  }
  banner('');
  delete seen.actions;
  poll();
}

$('confirm-go').addEventListener('click', async () => {
  await act('teardown', { confirm: $('confirm-input').value.trim() });
  $('confirm-input').value = '';
  $('confirm').hidden = true;
});
$('confirm-cancel').addEventListener('click', () => { $('confirm').hidden = true; });
$('panel-toggle').addEventListener('click', () => {
  const hidden = $('layout').classList.toggle('panel-hidden');
  $('panel-toggle').setAttribute('aria-expanded', String(!hidden));
});
setInterval(() => {
  if (lastUpdate !== null) $('freshness').textContent = 'Updated ' + ago((Date.now() - lastUpdate) / 1000);
}, 1000);

poll();
setInterval(poll, POLL_MS);
