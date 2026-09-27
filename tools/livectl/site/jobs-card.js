import { stamp } from './format.js';

// The last job's output (Terraform, go live, go off air, scan), full width under the endpoints.
// Collapsed it shows the newest lines; expanded it scrolls through the whole run.

// "2:13" or "1:02:13": how long a job has run, or took.
export function duration(startedAt, finishedAt) {
  const seconds = Math.max(0, Math.round(((finishedAt ? Date.parse(finishedAt) : Date.now()) - Date.parse(startedAt)) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, '0');
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + s : m + ':' + s;
}

export class JobsCard {
  constructor({ title, state, log, toggle, meta, progress, bar }) {
    this.title = title;
    this.state = state;
    this.meta = meta;
    this.progress = progress;
    this.bar = bar;
    this.job = null;           // the latest summary, for the stopwatch between polls
    this.log = log;
    this.toggle = toggle;
    this.key = null;
    this.offset = 0;
    this.lastLine = '';        // what the job is doing now, for the pill in the header
    this.toggle.addEventListener('click', () => {
      const expanded = this.log.classList.toggle('expanded');
      this.toggle.textContent = expanded ? 'Show less' : 'Show all';
      this.toggle.setAttribute('aria-expanded', String(expanded));
      this.log.scrollTop = this.log.scrollHeight;
    });
  }

  // Called every second as well as on each poll, so the stopwatch runs smoothly.
  tick() {
    const job = this.job;
    if (!job) return;
    const clock = job.state === 'running' ? duration(job.started_at) : 'took ' + duration(job.started_at, job.finished_at);
    const progress = job.progress;
    const parts = [];
    if (progress) parts.push(progress.label, progress.percent + '%');
    parts.push(clock);
    this.meta.textContent = parts.join(' · ');
  }

  show(job) {
    this.job = job;
    if (!job) {
      if (!this.log.textContent) this.log.textContent = 'No job has run since the console started.';
      return;
    }
    if (job.key !== this.key) {       // a new job replaces the previous output; the server sent it from line 0
      this.key = job.key;
      this.log.textContent = '';
      this.lastLine = '';
      this.offset = 0;
    }
    // Two polls can overlap and both answer from the same line; keep only the lines this card does not have yet.
    const skip = Math.max(0, this.offset - job.start);
    const fresh = job.lines.slice(skip);
    const stamps = (job.stamps || []).slice(skip);
    if (fresh.length) {
      const latest = fresh.filter((line) => line.trim()).pop();
      if (latest) this.lastLine = latest.length > 80 ? latest.slice(0, 79) + '…' : latest;
      this.log.textContent += fresh.map((line, i) => (stamps[i] ? stamp(stamps[i]) + '  ' : '') + line).join('\n') + '\n';
      this.log.scrollTop = this.log.scrollHeight;
    }
    this.offset = Math.max(this.offset, job.offset);
    this.title.textContent = 'Last job: ' + job.name;
    this.state.textContent = job.state === 'running' ? 'running…' : job.state;
    this.state.dataset.health = job.state === 'succeeded' ? 'ok' : job.state === 'failed' ? 'bad' : 'busy';

    // Blue while running, green when done, red if it failed. Before Terraform has a plan, the bar just moves.
    const progress = job.progress;
    this.progress.hidden = !progress;
    if (progress) {
      this.progress.dataset.state = job.state;
      this.progress.dataset.indeterminate = String(progress.total === null && job.state === 'running');
      this.progress.setAttribute('aria-valuenow', String(progress.percent));
      this.bar.style.width = progress.percent + '%';
    }
    this.tick();
  }
}
