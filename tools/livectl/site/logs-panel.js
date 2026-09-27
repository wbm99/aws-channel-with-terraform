// The right-hand panel. Plan 6 has the Jobs tab; Plan 7 adds one tab per resource.
import { el } from './format.js';

export class LogsPanel {
  constructor(tabs, pre) {
    this.tabs = tabs;
    this.pre = pre;
    this.active = 'jobs';
    this.jobKey = null;
    this.offset = 0;
    this.renderTabs([['jobs', 'Jobs']]);
  }

  renderTabs(list) {
    this.tabs.replaceChildren(...list.map(([id, label]) => el('button', {
      type: 'button', role: 'tab', 'data-tab': id, 'aria-selected': String(id === this.active),
      onclick: () => this.select(id),
    }, label)));
  }

  select(id) {
    this.active = id;
    for (const tab of this.tabs.querySelectorAll('[role=tab]')) tab.setAttribute('aria-selected', String(tab.dataset.tab === id));
  }

  showJob(job) {
    if (!job) {
      if (!this.pre.textContent) this.pre.textContent = 'No job has run since the console started.';
      return;
    }
    const key = job.name + '@' + job.started_at;
    if (key !== this.jobKey) {
      this.jobKey = key;
      this.pre.textContent = '';
      this.offset = 0;
    }
    if (job.lines.length) {
      this.pre.textContent += job.lines.join('\n') + '\n';
      this.pre.scrollTop = this.pre.scrollHeight;
    }
    this.offset = job.offset;
  }
}
