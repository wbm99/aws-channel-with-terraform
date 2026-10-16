// "Send from your own encoder": the SRT address, the passphrase and one URL that carries both, for OBS or any other
// SRT caller, and the same for the backup listener when the stack has one. The passphrase is fetched on each Show or
// Copy and dropped on Hide; the poll never carries it.
import { copy } from './endpoints.js';

const MASK = '••••••••••••';
const $ = (id) => document.getElementById(id);

export class EncoderCard {
  constructor({ getConnection }) {
    this.getConnection = getConnection;
    this.address = $('encoder-address');
    this.passphrase = $('encoder-passphrase');
    this.url = $('encoder-url');
    this.backupAddress = $('encoder-backup-address');
    this.backupUrl = $('encoder-backup-url');
    this.backupRows = [...document.querySelectorAll('#encoder-card .encoder-backup')];
    this.latency = $('encoder-latency');
    this.error = $('encoder-error');
    this.show = $('encoder-show');
    this.buttons = [...document.querySelectorAll('#encoder-card button')];
    this.shown = null;          // the connection while the passphrase is on screen
    this.ingest = undefined;     // undefined until the first poll, so that one always paints
    this.backup = null;
    this.latencyMs = undefined;
    this.show.addEventListener('click', () => (this.shown ? this.hide() : this.reveal()));
    $('encoder-copy-address').addEventListener('click', (e) => copy(e.currentTarget, this.ingest));
    $('encoder-copy-passphrase').addEventListener('click', (e) => this.copyFresh(e.currentTarget, 'passphrase'));
    $('encoder-copy-url').addEventListener('click', (e) => this.copyFresh(e.currentTarget, 'url'));
    $('encoder-copy-backup-address').addEventListener('click', (e) => copy(e.currentTarget, this.backup));
    $('encoder-copy-backup-url').addEventListener('click', (e) => this.copyFresh(e.currentTarget, 'backup_url'));
  }

  render(data) {
    const ingest = (data.endpoints && data.endpoints.ingest) || null;
    const backup = (ingest && data.endpoints.ingest_backup) || null;
    const settings = data.source_settings && data.source_settings.current;
    const latencyMs = settings ? settings.latency_ms : null;
    if (ingest === this.ingest && backup === this.backup && latencyMs === this.latencyMs) return;
    this.ingest = ingest;
    this.backup = backup;
    for (const row of this.backupRows) row.hidden = !backup;
    this.latencyMs = latencyMs;
    for (const button of this.buttons) button.disabled = !ingest;
    if (!ingest) this.hide();
    if (this.shown) this.reveal();   // the latency changed: fetch the URL again
    else this.paint();
  }

  paint() {
    const shown = this.shown;
    this.address.textContent = this.ingest || 'Deploy the stack first';
    this.passphrase.textContent = shown ? shown.passphrase : (this.ingest ? MASK : '—');
    const masked = (address) => `${address}?mode=caller&passphrase=${MASK}&pbkeylen=32&pkt_size=1316&latency=${this.latencyMs * 1000}`;
    this.url.textContent = shown ? shown.url : (this.ingest ? masked(this.ingest) : '—');
    this.backupAddress.textContent = this.backup || '—';
    this.backupUrl.textContent = (shown && shown.backup_url) || (this.backup ? masked(this.backup) : '—');
    this.latency.textContent = this.latencyMs == null ? '' : String(this.latencyMs);
    this.show.textContent = shown ? 'Hide' : 'Show';
    this.show.setAttribute('aria-pressed', String(Boolean(shown)));
  }

  async fetch() {
    try {
      const answer = await this.getConnection();
      this.error.textContent = '';
      return answer;
    } catch (error) {
      this.error.textContent = error.message;
      return null;
    }
  }

  async reveal() {
    const answer = await this.fetch();
    if (answer) this.shown = answer;
    this.paint();
  }

  hide() {
    this.shown = null;
    this.paint();
  }

  async copyFresh(button, key) {
    const answer = await this.fetch();
    if (answer) await copy(button, answer[key]);
  }
}
