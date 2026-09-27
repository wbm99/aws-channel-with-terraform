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
    if (job.key !== this.key) {       // a new job replaces the previous output; the server sent it from line 0
      this.key = job.key;
      this.log.textContent = '';
      this.lastLine = '';
      this.offset = 0;
    }
    // Two polls can overlap and both answer from the same line; keep only the lines this card does not have yet.
    const fresh = job.lines.slice(Math.max(0, this.offset - job.start));
    if (fresh.length) {
      const latest = fresh.filter((line) => line.trim()).pop();
      if (latest) this.lastLine = latest.length > 80 ? latest.slice(0, 79) + '…' : latest;
      this.log.textContent += fresh.join('\n') + '\n';
      this.log.scrollTop = this.log.scrollHeight;
    }
    this.offset = Math.max(this.offset, job.offset);
    this.title.textContent = 'Last job: ' + job.name;
    this.state.textContent = job.state === 'running' ? 'running…' : job.state;
    this.state.dataset.health = job.state === 'succeeded' ? 'ok' : job.state === 'failed' ? 'bad' : 'busy';
  }
}
