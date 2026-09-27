// The right-hand panel: one tab per resource, plus Jobs. Only the visible tab is polled.
import { getLogs } from './api.js';
import { el } from './format.js';

const TABS = [
  ['all', 'All'], ['srt', 'SRT Source'], ['mediaconnect', 'MediaConnect'], ['medialive', 'MediaLive'],
  ['mediapackage', 'MediaPackage'], ['cloudfront', 'CloudFront'], ['jobs', 'Jobs'],
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

export class LogsPanel {
  constructor(tabs, list) {
    this.tabs = tabs;
    this.list = list;
    this.active = 'all';
    this.buffers = {};       // tab -> { after, lines: [line], notes: [str] }
    this.jobText = '';
    this.jobKey = null;
    this.offset = 0;
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

  async refresh() {
    const tab = this.active;
    if (tab === 'jobs' || NO_LOGS[tab]) return;
    const buffer = this.buffers[tab] || (this.buffers[tab] = { after: 0, lines: [], notes: [] });
    try {
      const data = await getLogs(tab, buffer.after);
      buffer.lines = buffer.lines.concat(data.lines).slice(-1000);
      buffer.after = data.after;
      buffer.notes = data.notes;
    } catch (error) {
      buffer.notes = ['Could not read logs: ' + error.message];
    }
    if (this.active === tab) this.draw();
  }

  draw() {
    const tab = this.active;
    if (tab === 'jobs') {
      this.list.replaceChildren(el('div', { class: 'line' }, this.jobText || 'No job has run since the console started.'));
    } else if (NO_LOGS[tab]) {
      this.list.replaceChildren(el('p', { class: 'note' }, NO_LOGS[tab]));
    } else {
      const buffer = this.buffers[tab] || { lines: [], notes: [] };
      this.list.replaceChildren(
        ...buffer.notes.map((note) => el('p', { class: 'note' }, note)),
        ...(buffer.lines.length ? buffer.lines.map((line) => this.line(line))
          : [el('p', { class: 'note' }, 'No events in the last 30 minutes.')]),
      );
    }
    this.list.scrollTop = this.list.scrollHeight;
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

  showJob(job) {
    if (!job) return;
    const key = job.name + '@' + job.started_at;
    if (key !== this.jobKey) {
      this.jobKey = key;
      this.jobText = '';
    }
    if (job.lines.length) this.jobText += job.lines.join('\n') + '\n';
    this.offset = job.offset;
    if (this.active === 'jobs') this.draw();
  }
}
