// Startup, polling, the two pages and the wiring between them.
// Each region re-renders only when its data changed, so a poll never steals focus or wipes a half-typed word.
import { getPipeline, post } from './api.js';
import { renderChain } from './chain.js';
import { renderEndpoints } from './endpoints.js';
import { ago } from './format.js';
import { JobsCard, duration } from './jobs-card.js';
import { renderStats } from './live.js';
import { LogsPanel, TAB_FOR_NODE } from './logs-panel.js';
import { renderDetail } from './node-detail.js';
import { renderLiveActions, renderMaintenance, renderSteps } from './steps.js';

const POLL_MS = 2000;
const PAGES = ['control', 'live', 'logs'];
const $ = (id) => document.getElementById(id);
const logs = new LogsPanel($('tabs'), $('log'));
const jobs = new JobsCard({
  title: $('job-title'), state: $('job-state'), log: $('job-log'), toggle: $('job-toggle'),
  meta: $('job-meta'), progress: $('job-progress'), bar: $('job-bar'),
});
const seen = {};
let data = null;
let selected = null;           // the node whose details are open in the drawer
let lastUpdate = null;
let billingSince = null;
let lastFailedJob = null;
let playerHealth = null;
let polling = false;         // a poll in flight; the next tick waits for it instead of piling up
let lostContact = false;     // the banner currently says the console is not answering
let chosenPattern = 'testcard';  // the pattern the next Send test source will use

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

// --- pages ------------------------------------------------------------------------------

function page() {
  const name = location.hash.replace('#', '');
  return PAGES.includes(name) ? name : 'control';
}

function showPage() {
  const current = page();
  // One log panel, moved between the Live page and the full-height Logs page, so tabs and filter carry over.
  const panel = $('panel');
  $(current === 'logs' ? 'logs-home' : 'live-logs-slot').append(panel);
  panel.classList.toggle('full', current === 'logs');
  $('log-expand').textContent = current === 'logs' ? 'Back to Live' : 'Expand';
  $('log-expand').setAttribute('href', current === 'logs' ? '#live' : '#logs');
  for (const view of document.querySelectorAll('[data-view]')) view.hidden = view.dataset.view !== current;
  for (const link of document.querySelectorAll('.menu [data-page]')) {
    if (link.dataset.page === current) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  if (data) render();
}

// --- header -----------------------------------------------------------------------------

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

  renderPill();
}

// A running job is never out of sight: its name, progress, stopwatch and latest line, on every page.
function renderPill() {
  const pill = $('job-pill');
  const job = data && data.job;
  pill.hidden = !job;
  if (!job) return;
  const running = job.state === 'running';
  const percent = job.progress ? ' · ' + job.progress.percent + '%' : '';
  pill.dataset.state = job.state;
  pill.textContent = running
    ? job.name + percent + ' · ' + duration(job.started_at) + (jobs.lastLine ? ' · ' + jobs.lastLine : '')
    : job.name + ' ' + job.state + ' · took ' + duration(job.started_at, job.finished_at);
  pill.title = 'Show the job output';
}

// --- regions ------------------------------------------------------------------------------

function renderPlayer() {
  const frame = $('player-frame');
  // embed=1 asks the player page for the video alone, without its title, clocks and notes.
  const base = data.endpoints.player;
  const url = base ? base + (base.includes('?') ? '&' : '?') + 'embed=1' : null;
  if (url && frame.getAttribute('src') !== url) frame.setAttribute('src', url);
  // The player page gives up on the 404s it gets before the first segment exists. When the console sees the
  // playlist start advancing, it reloads the frame once so the player picks the stream up without a manual refresh.
  const node = data.nodes.find((n) => n.id === 'player');
  const health = node ? node.health : null;
  if (url && playerHealth !== null && playerHealth !== 'ok' && health === 'ok') {
    frame.setAttribute('src', url);
    frame.dataset.reloads = String(Number(frame.dataset.reloads || 0) + 1);
  }
  playerHealth = health;
  frame.hidden = !url;
  $('player-empty').hidden = Boolean(url);
}

function renderDrawer() {
  const node = data.nodes.find((n) => n.id === selected);
  $('drawer').hidden = !node;
  if (node) renderDetail($('detail'), node, data.metrics_age);
}

function select(id) {
  selected = selected === id ? null : id;
  if (selected && page() !== 'control') logs.select(TAB_FOR_NODE[selected] || 'all');
  delete seen.nodes;
  render();
}

function render() {
  $('aws-note').textContent = data.aws_note || '';
  $('aws-note').hidden = !data.aws_note;
  jobs.show(data.job);           // first, so the header's job pill shows this poll's latest line
  renderHeader();
  const note = $('deploy-note');
  note.hidden = data.deployed && !data.note;
  note.textContent = data.note || (data.deployed ? '' : 'Nothing is deployed yet. Deploy stack creates it (a few minutes).');

  if (selected && !data.nodes.some((n) => n.id === selected)) selected = null;
  // checked_at moves every second; leaving it out keeps the chain from re-rendering (and losing focus) on each poll.
  const stable = data.nodes.map(({ checked_at, ...rest }) => rest);
  if (changed('nodes', [stable, selected, data.metrics_age === null])) {
    renderChain($('chain'), data.nodes, selected, select);
    renderDrawer();
    renderStats($('stats'), data.nodes);
    logs.setNodes(data.nodes);
  }

  const sourceRunning = Boolean(data.source && ['starting', 'running'].includes(data.source.state));
  const job = data.job && data.job.state === 'running' ? data.job.name : null;
  const pattern = sourceRunning && data.source.pattern ? data.source.pattern : chosenPattern;
  const state = { actions: data.actions, next: data.next, nodes: stable, deployed: data.deployed, sourceRunning, job,
    patterns: data.patterns || [], pattern };
  if (changed('actions', state)) {
    renderSteps($('steps'), state, act);
    renderMaintenance($('maintenance'), state, act);
    renderLiveActions($('live-actions'), state, act);
  }
  if (changed('endpoints', data.endpoints)) renderEndpoints($('endpoints'), data.endpoints);
  renderPlayer();

  // Once the stream plays, point at the Live page if that is not where the person is.
  const playing = data.verdict.text === 'On air · playing';
  document.querySelector('.menu [data-page="live"]').classList.toggle('next', playing && page() !== 'live');

  const last = data.job;
  if (last && last.state === 'failed' && lastFailedJob !== job.started_at) {
    lastFailedJob = last.started_at;
    banner(last.name + ' failed: ' + (last.error || 'see the job output on the Control page'));
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    data = await getPipeline(jobs.offset, jobs.key);
    lastUpdate = Date.now();
    if (lostContact) {           // back in touch: take the warning down without a reload
      lostContact = false;
      banner('');
    }
    render();
  } catch (error) {
    lostContact = true;
    banner('Lost contact with the console (' + error.message + '). Retrying every few seconds; '
      + 'if it does not come back, check that livectl ui is still running.');
  } finally {
    polling = false;
  }
}

async function act(name, body) {
  if (name === 'source-pattern') {
    chosenPattern = body.pattern;
    const running = data && data.source && ['starting', 'running'].includes(data.source.state);
    if (!running) { delete seen.actions; render(); return; }   // just remembered for the next start
  }
  if (name === 'source-start' && !body) body = { pattern: chosenPattern };
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
  // The test source's output is in its log tab; everything else runs as a job, shown on the Control page.
  if (name.startsWith('source-')) logs.select('srt');
  delete seen.actions;
  poll();
}

$('confirm-go').addEventListener('click', async () => {
  await act('teardown', { confirm: $('confirm-input').value.trim() });
  $('confirm-input').value = '';
  $('confirm').hidden = true;
});
$('confirm-cancel').addEventListener('click', () => { $('confirm').hidden = true; });
$('job-pill').addEventListener('click', () => {
  location.hash = '#control';
  $('job-card').scrollIntoView({ block: 'nearest' });
});
$('drawer-close').addEventListener('click', () => select(selected));
$('log-filter').addEventListener('input', (event) => logs.setFilter(event.target.value));
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && selected) select(selected); });
window.addEventListener('hashchange', showPage);
setInterval(() => {
  if (lastUpdate !== null) $('freshness').textContent = 'Updated ' + ago((Date.now() - lastUpdate) / 1000);
  jobs.tick();                  // stopwatches run between polls too
  renderPill();
}, 1000);

showPage();
poll();
setInterval(poll, POLL_MS);
