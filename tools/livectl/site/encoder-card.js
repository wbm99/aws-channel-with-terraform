// "Send from your own encoder": the SRT address, the passphrase and one URL that carries both, for OBS or any other
// SRT caller. The passphrase is fetched on each Show or Copy and dropped on Hide; the poll never carries it.
import { copy } from './endpoints.js';

const MASK = '••••••••••••';
const $ = (id) => document.getElementById(id);

export class EncoderCard {
  constructor({ getConnection }) {
    this.getConnection = getConnection;
    this.address = $('encoder-address');
    this.passphrase = $('encoder-passphrase');
    this.url = $('encoder-url');
    this.latency = $('encoder-latency');
    this.error = $('encoder-error');
    this.show = $('encoder-show');
    this.buttons = [...document.querySelectorAll('#encoder-card button')];
    this.shown = null;          // the connection while the passphrase is on screen
    this.ingest = undefined;     // undefined until the first poll, so that one always paints
    this.latencyMs = undefined;
    this.show.addEventListener('click', () => (this.shown ? this.hide() : this.reveal()));
    $('encoder-copy-address').addEventListener('click', (e) => copy(e.currentTarget, this.ingest));
    $('encoder-copy-passphrase').addEventListener('click', (e) => this.copyFresh(e.currentTarget, 'passphrase'));
    $('encoder-copy-url').addEventListener('click', (e) => this.copyFresh(e.currentTarget, 'url'));
  }

  render(data) {
    const ingest = (data.endpoints && data.endpoints.ingest) || null;
    const settings = data.source_settings && data.source_settings.current;
    const latencyMs = settings ? settings.latency_ms : null;
    if (ingest === this.ingest && latencyMs === this.latencyMs) return;
    this.ingest = ingest;
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
    this.url.textContent = shown ? shown.url : (this.ingest
      ? `${this.ingest}?mode=caller&passphrase=${MASK}&pbkeylen=32&pkt_size=1316&latency=${this.latencyMs * 1000}`
      : '—');
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
