// The Source page: every setting of the FFmpeg test source, edited together and applied with one restart, beside
// what MediaConnect says it received. The form is built from the server's choices, so no allowed value lives here.
import { el } from './format.js';

const RECEIVED_MS = 5000;
const RESTARTING = 'Restarting FFmpeg — SRT reconnects, expect a few seconds of slate.';
const GROUPS = ['Video', 'Audio', 'MPEG-TS', 'SRT'];

// MediaConnect names codecs its own way; compare on one spelling. Unknown names are shown, never flagged.
const CODEC = { h264: 'h264', avc: 'h264', aac: 'aac', mp2: 'mp2', mp1: 'mp2', 'mpeg audio': 'mp2', ac3: 'ac3',
  'dolby digital': 'ac3' };
const codec = (name) => CODEC[String(name || '').toLowerCase()] || null;

const $ = (id) => document.getElementById(id);

export class SourcePage {
  constructor({ post, getReceived, act }) {
    this.post = post;
    this.getReceived = getReceived;
    this.act = act;
    this.form = $('source-form');
    this.apply = $('source-apply');
    this.discard = $('source-discard');
    this.stop = $('source-stop');
    this.reset = $('source-reset');
    this.note = $('source-note');
    this.data = null;
    this.choices = null;
    this.synced = null;         // the server's settings the form was last brought in line with
    this.received = null;
    this.timer = null;
    this.busy = false;
    this.apply.addEventListener('click', () => this.onApply());
    this.discard.addEventListener('click', () => { this.fill(this.synced); this.clearErrors(); this.update(); });
    this.reset.addEventListener('click', () => { this.fill(this.data.source_settings.defaults); this.update(); });
    this.stop.addEventListener('click', () => this.act('source-stop'));
    this.form.addEventListener('input', (event) => this.edited(event));
    this.form.addEventListener('change', (event) => this.edited(event));
    this.form.addEventListener('submit', (event) => { event.preventDefault(); this.onApply(); });
  }

  // --- the form ---------------------------------------------------------------------------------------------

  build(choices) {
    this.choices = choices;
    this.form.replaceChildren(...GROUPS.map((group) => el('fieldset', { class: 'source-group' },
      el('legend', {}, group),
      ...choices.filter((c) => c.group === group).map((c) => this.field(c)))));
  }

  field(choice) {
    const id = 'source-' + choice.key;
    let control;
    if (choice.kind === 'enum') {
      control = el('select', { id, name: choice.key },
        choice.values.map((v) => el('option', { value: String(v.id) }, v.label)));
    } else if (choice.kind === 'range' && choice.key === 'video_kbps') {
      control = el('input', { id, name: choice.key, type: 'number', inputmode: 'decimal', min: choice.min / 1000,
        max: choice.max / 1000, step: 0.1 });
    } else if (choice.kind === 'range') {
      control = el('input', { id, name: choice.key, type: 'number', inputmode: 'numeric', min: choice.min,
        max: choice.max, step: choice.step });
    } else {
      control = el('input', { id, name: choice.key, type: 'text', maxlength: choice.max_length, autocomplete: 'off',
        spellcheck: 'false' });
    }
    const unit = choice.key === 'video_kbps' ? 'Mbps' : choice.unit;
    return el('div', { class: 'source-field', 'data-key': choice.key },
      el('label', { for: id }, choice.label),
      el('div', { class: 'source-control' }, control, unit ? el('span', { class: 'unit' }, unit) : null,
        el('span', { class: 'changed-mark', 'aria-hidden': 'true' })),
      el('p', { class: 'field-error', 'data-error-for': choice.key, id: id + '-error', 'aria-live': 'polite' }));
  }

  control(key) { return this.form.elements.namedItem(key); }

  // The value a field holds, typed as the server wants it; null when a number field is empty or unreadable.
  read(choice) {
    const raw = this.control(choice.key).value;
    if (choice.kind === 'enum') {
      const match = choice.values.find((v) => String(v.id) === raw);
      return match ? match.id : raw;
    }
    if (choice.kind === 'range') {
      const number = Number.parseFloat(raw);
      if (raw.trim() === '' || Number.isNaN(number)) return null;
      return choice.key === 'video_kbps' ? Math.round(number * 1000) : number;
    }
    return raw;
  }

  write(choice, value) {
    this.control(choice.key).value = choice.key === 'video_kbps' ? String(value / 1000) : String(value);
  }

  fill(settings) {
    for (const choice of this.choices) this.write(choice, settings[choice.key]);
  }

  changes() {
    const out = {};
    for (const choice of this.choices) {
      const value = this.read(choice);
      if (value !== this.synced[choice.key]) out[choice.key] = value;
    }
    return out;
  }

  edited(event) {
    const key = event.target.name;
    if (key) this.showError(key, '');
    if (this.note.dataset.kind === 'restarting') this.say('');
    this.update();
  }

  say(text, kind = '') {
    this.note.textContent = text;
    this.note.dataset.kind = kind;
  }

  showError(key, message) {
    const box = this.form.querySelector(`[data-error-for="${key}"]`);
    if (!box) return;
    box.textContent = message;
    const control = this.control(key);
    if (message) control.setAttribute('aria-invalid', 'true');
    else control.removeAttribute('aria-invalid');
    control.setAttribute('aria-describedby', box.id);
  }

  clearErrors() {
    for (const choice of this.choices) this.showError(choice.key, '');
  }

  // --- state from the poll ----------------------------------------------------------------------------------

  render(data) {
    this.data = data;
    const settings = data.source_settings;
    if (!settings) return;
    if (!this.choices) {
      this.build(settings.choices);
      this.fill(settings.current);
      this.synced = settings.current;
    } else if (JSON.stringify(settings.current) !== JSON.stringify(this.synced)) {
      // Another tab (or our own Apply) changed the settings: take the new values into every field the person has
      // not edited and is not typing in.
      const focused = document.activeElement;
      for (const choice of this.choices) {
        const control = this.control(choice.key);
        const untouched = this.read(choice) === this.synced[choice.key];
        if (untouched && control !== focused) this.write(choice, settings.current[choice.key]);
      }
      this.synced = settings.current;
    }
    this.update();
    this.renderReceived();
  }

  running() {
    return Boolean(this.data && this.data.source && ['starting', 'running'].includes(this.data.source.state));
  }

  update() {
    if (!this.data || !this.choices) return;
    const changes = Object.keys(this.changes());
    for (const row of this.form.querySelectorAll('.source-field')) {
      const changed = changes.includes(row.dataset.key);
      row.toggleAttribute('data-changed', changed);
      row.querySelector('.changed-mark').textContent = changed ? 'changed' : '';
    }
    const running = this.running();
    const refusal = this.data.actions && this.data.actions['source-start'];
    const count = changes.length;
    if (running) {
      this.apply.textContent = count ? `Apply ${count} change${count === 1 ? '' : 's'}` : 'Apply';
      this.apply.disabled = this.busy || count === 0;
    } else {
      this.apply.textContent = 'Send test source';
      this.apply.disabled = this.busy || Boolean(refusal);
    }
    this.discard.disabled = count === 0;
    this.stop.hidden = !running;
    if (!running && refusal) this.say(refusal, 'refusal');
    else if (this.note.dataset.kind === 'refusal') this.say('');
  }

  async onApply() {
    if (this.busy || !this.choices) return;
    const changes = this.changes();
    const running = this.running();
    this.busy = true;
    this.update();
    try {
      if (Object.keys(changes).length) {
        const result = await this.post('source-settings', changes);
        if (!result.ok) {
          const field = result.data.field;
          if (field && this.control(field)) {
            this.showError(field, result.data.error);
            this.control(field).focus();
          } else {
            this.say(result.data.error || 'The settings were refused (' + result.status + ').');
          }
          return;
        }
        this.clearErrors();
        this.synced = result.data.current;
        if (result.data.restarted) this.say(RESTARTING, 'restarting');
        else this.say('');
      }
      if (!running) await this.act('source-start');
    } finally {
      this.busy = false;
      this.update();
    }
  }

  // --- sent vs received -------------------------------------------------------------------------------------

  visible(on) {
    if (on && !this.timer) {
      this.pollReceived();
      this.timer = setInterval(() => this.pollReceived(), RECEIVED_MS);
    } else if (!on && this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  async pollReceived() {
    try {
      this.received = await this.getReceived();
    } catch (error) {
      this.received = { state: 'error', note: 'Could not reach the console (' + error.message + ').' };
    }
    this.renderReceived();
  }

  renderReceived() {
    const table = $('source-received');
    const note = $('source-received-note');
    const answer = this.received;
    const program = answer && answer.state === 'ok' ? answer.programs.find((p) => p.streams.length) : null;
    if (!answer || !program) {
      table.hidden = true;
      note.textContent = !answer ? 'Checking what MediaConnect receives…'
        : answer.state !== 'ok' ? answer.note
          : ['MediaConnect has not parsed a stream yet.', ...answer.messages].join(' ');
    } else {
      note.textContent = '';
      table.hidden = false;
      const sent = this.running() ? this.data.source.settings : null;
      $('source-received-body').replaceChildren(...rows(sent, program, this.choices));
    }
    const status = this.data && this.data.source;
    $('source-ffmpeg').textContent = !status ? ''
      : status.state === 'exited' ? 'FFmpeg exited: ' + (status.last_error || 'code ' + status.exit_code)
        : 'FFmpeg: ' + status.state + (status.progress && this.running() ? ' · ' + status.progress : '');
  }
}

function rows(sent, program, choices) {
  const video = program.streams.find((s) => s.type === 'video') || {};
  const audio = program.streams.find((s) => s.type === 'audio') || {};
  const label = (key, id) => ((choices.find((c) => c.key === key) || {}).values || []).find((v) => v.id === id);
  const [width, height] = sent ? sent.size.split('x').map(Number) : [];
  const line = (...parts) => parts.filter((p) => p !== null && p !== undefined && p !== '').join(' · ');
  const known = (value) => value !== null && value !== undefined;
  return [
    row('video', 'Video',
      sent && line('H.264', width + '×' + height, sent.fps + ' fps'),
      line(video.codec, known(video.width) ? video.width + '×' + video.height : null,
        known(video.fps) ? video.fps + ' fps' : null),
      sent && ((codec(video.codec) && codec(video.codec) !== 'h264')
        || (known(video.width) && (video.width !== width || video.height !== height))
        || (known(video.fps) && Math.abs(video.fps - sent.fps) > 0.01))),
    row('audio', 'Audio',
      sent && line(label('audio_codec', sent.audio_codec).label, '2 ch', '48 kHz'),
      line(audio.codec, known(audio.channels) ? audio.channels + ' ch' : null,
        known(audio.sample_rate) ? audio.sample_rate / 1000 + ' kHz' : null),
      sent && ((codec(audio.codec) && codec(audio.codec) !== sent.audio_codec)
        || (known(audio.channels) && audio.channels !== 2)
        || (known(audio.sample_rate) && audio.sample_rate !== 48000))),
    row('program', 'Program',
      sent && line('"' + sent.service_name + '"', '#' + sent.program_number),
      line(known(program.name) ? '"' + program.name + '"' : null, known(program.number) ? '#' + program.number : null),
      sent && (program.name !== sent.service_name || program.number !== sent.program_number)),
  ];
}

function row(id, name, sent, got, mismatch) {
  return el('tr', { 'data-row': id, 'data-mismatch': Boolean(mismatch) },
    el('th', { scope: 'row' }, name), el('td', {}, sent || '—'), el('td', {}, got || '—'));
}
