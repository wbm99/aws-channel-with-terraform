// The last job's output (Terraform, go live, go off air, scan), full width under the endpoints.
// Collapsed it shows the newest lines; expanded it scrolls through the whole run.

export class JobsCard {
  constructor({ title, state, log, toggle }) {
    this.title = title;
    this.state = state;
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

  show(job) {
    if (!job) {
      if (!this.log.textContent) this.log.textContent = 'No job has run since the console started.';
      return;
    }
    const key = job.name + '@' + job.started_at;
    if (key !== this.key) {           // a new job replaces the previous output
      this.key = key;
      this.log.textContent = '';
      this.lastLine = '';
    }
    if (job.lines.length) {
      const latest = job.lines.filter((line) => line.trim()).pop();
      if (latest) this.lastLine = latest.length > 80 ? latest.slice(0, 79) + '…' : latest;
      this.log.textContent += job.lines.join('\n') + '\n';
      this.log.scrollTop = this.log.scrollHeight;
    }
    this.offset = job.offset;
    this.title.textContent = 'Last job: ' + job.name;
    this.state.textContent = job.state === 'running' ? 'running…' : job.state;
    this.state.dataset.health = job.state === 'succeeded' ? 'ok' : job.state === 'failed' ? 'bad' : 'busy';
  }
}
