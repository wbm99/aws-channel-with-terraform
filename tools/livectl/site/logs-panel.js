// The Live page's log panel: one tab per resource, newest line first. Only the visible tab is polled.
import { getLogs } from './api.js';
import { el, metricRow, stamp } from './format.js';

const TABS = [
  ['all', 'All'], ['srt', 'Source (SRT)'], ['mediaconnect', 'MediaConnect'], ['medialive', 'MediaLive'],
  ['mediapackage', 'MediaPackage'], ['cloudfront', 'CloudFront'],
];
// Tabs without logs show their resource's CloudWatch figures instead.
const NO_LOGS = {
  mediapackage: ['mediapackage_channel', ['mp_ingress_bytes', 'mp_egress_bytes', 'mp_egress_requests', 'mp_egress_5xx'],
    'Per-minute figures from CloudWatch, one to three minutes behind. MediaPackage access logs are not enabled.'],
  cloudfront: ['cloudfront_cdn', ['cf_requests', 'cf_bytes_downloaded', 'cf_4xx_rate', 'cf_5xx_rate'],
    'Per-minute figures from CloudWatch, one to three minutes behind. Viewer counts and edge locations need '
    + 'CloudFront access logs, which are not enabled.'],
};
export const TAB_FOR_NODE = {
  srt_source: 'srt', mediaconnect_flow: 'mediaconnect', medialive_input: 'medialive', medialive_channel: 'medialive',
  mediapackage_channel: 'mediapackage', cloudfront_cdn: 'cloudfront', player: 'all',
};
const LOG_POLL_MS = 5000;
const KEEP_LINES = 1000;

export class LogsPanel {
  constructor(tabs, list) {
    this.tabs = tabs;
    this.list = list;
    this.active = 'all';
    this.buffers = {};       // tab -> { after, lines: [line], keys: Set, notes: [str], busy: bool }
    this.nodes = {};         // node id -> node, for the tabs that show figures
    this.filter = '';
    this.tabs.replaceChildren(...TABS.map(([id, label]) => el('button', {
      type: 'button', role: 'tab', 'data-tab': id, onclick: () => this.select(id),
    }, label)));
    this.select('all');
    setInterval(() => this.refresh(), LOG_POLL_MS);
  }

  select(id) {
    this.active = id;
    for (const tab of this.tabs.querySelectorAll('[role=tab]')) {
      tab.setAttribute('aria-selected', String(tab.dataset.tab === id));
    }
    this.draw();
    this.refresh();
  }

  setFilter(text) {
    this.filter = text.trim();
    this.draw();
  }

  setNodes(nodes) {
    this.nodes = Object.fromEntries(nodes.map((n) => [n.id, n]));
    if (NO_LOGS[this.active]) this.draw();
  }

  figures(tab) {
    const [nodeId, keys, note] = NO_LOGS[tab];
    const node = this.nodes[nodeId];
    const rows = keys.map((key) => {
      const value = node && node.health !== 'off' ? node.metrics[key] : null;
      return value === null || value === undefined ? [metricRow(key, 0)[0], '—'] : metricRow(key, value);
    });
    return [el('dl', { class: 'facts figures' }, rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)])),
      el('p', { class: 'note' }, note)];
  }

  buffer(tab) {
    return this.buffers[tab] || (this.buffers[tab] = { after: 0, lines: [], keys: new Set(), notes: [], busy: false });
  }

  async refresh() {
    const tab = this.active;
    if (NO_LOGS[tab]) return;
    const buffer = this.buffer(tab);
    // Selecting a tab and the timer can both ask at once; two answers from the same cursor would append twice.
    if (buffer.busy) return;
    buffer.busy = true;
    try {
      const data = await getLogs(tab, buffer.after);
      for (const line of data.lines) {
        const key = line.at_ms + '|' + line.text;
        if (buffer.keys.has(key)) continue;
        buffer.keys.add(key);
        buffer.lines.push(line);
      }
      buffer.lines = buffer.lines.slice(-KEEP_LINES);
      buffer.after = data.after;
      buffer.notes = data.notes;
    } catch (error) {
      buffer.notes = ['Could not read logs: ' + error.message];
    } finally {
      buffer.busy = false;
    }
    if (this.active === tab) this.draw();
  }

  draw() {
    const tab = this.active;
    const atTop = this.list.scrollTop < 24;
    if (NO_LOGS[tab]) {
      this.list.replaceChildren(...this.figures(tab));
    } else {
      const buffer = this.buffer(tab);
      const needle = this.filter.toLowerCase();
      const shown = needle ? buffer.lines.filter((line) => line.text.toLowerCase().includes(needle)) : buffer.lines;
      const empty = buffer.lines.length ? 'No line matches "' + this.filter + '".' : 'No events in the last 30 minutes.';
      this.list.replaceChildren(
        ...buffer.notes.map((note) => el('p', { class: 'note' }, note)),
        ...(shown.length ? shown.slice().reverse().map((line) => this.line(line)) : [el('p', { class: 'note' }, empty)]),
      );
    }
    // Newest first: stay at the top for new lines, unless the reader has scrolled down to read older ones.
    if (atTop) this.list.scrollTop = 0;
  }

  line(line) {
    const node = el('div', { class: 'line' + (line.raw ? ' has-raw' : '') },
      el('time', { datetime: new Date(line.at_ms).toISOString(), title: 'UTC' }, stamp(line.at_ms)), '  ', line.text);
    if (line.raw) {
      node.addEventListener('click', () => {
        const open = node.querySelector('.raw');
        if (open) open.remove();
        else node.append(el('pre', { class: 'raw' }, JSON.stringify(JSON.parse(line.raw), null, 2)));
      });
    }
    return node;
  }
}
