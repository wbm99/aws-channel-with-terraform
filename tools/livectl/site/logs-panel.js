// The right-hand panel: one tab per resource. Only the visible tab is polled. Job output has its own card.
import { getLogs } from './api.js';
import { el } from './format.js';

const TABS = [
  ['all', 'All'], ['srt', 'SRT Source'], ['mediaconnect', 'MediaConnect'], ['medialive', 'MediaLive'],
  ['mediapackage', 'MediaPackage'], ['cloudfront', 'CloudFront'],
];
const NO_LOGS = {
  mediapackage: 'MediaPackage access logs are not enabled in this project. Its ingest rate and 5xx count are in the node details.',
  cloudfront: 'CloudFront access logs are not enabled in this project. Requests per minute and the 5xx rate are in the node details.',
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
    const atBottom = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 24;
    if (NO_LOGS[tab]) {
      this.list.replaceChildren(el('p', { class: 'note' }, NO_LOGS[tab]));
    } else {
      const buffer = this.buffer(tab);
      this.list.replaceChildren(
        ...buffer.notes.map((note) => el('p', { class: 'note' }, note)),
        ...(buffer.lines.length ? buffer.lines.map((line) => this.line(line))
          : [el('p', { class: 'note' }, 'No events in the last 30 minutes.')]),
      );
    }
    // Follow new lines only if the reader was already at the bottom, so scrolling back to read is not undone.
    if (atBottom) this.list.scrollTop = this.list.scrollHeight;
  }

  line(line) {
    const node = el('div', { class: 'line' + (line.raw ? ' has-raw' : '') }, line.text);
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
