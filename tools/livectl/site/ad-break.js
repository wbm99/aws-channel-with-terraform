// The Live page's ad break control: cue a SCTE-35 break, then follow it until its marker reaches the playlist.
// The elements are in the page from the start and only their properties change, so a poll never wipes a half-typed
// duration or takes focus away.
import { clockText } from './clocks.js';

const MIN_S = 5;
const MAX_S = 180;
const NOT_SEEN = 'not seen in manifest after 60 s — check SCTE settings in package/encode';

const $ = (id) => document.getElementById(id);
const secondsText = (value) => String(Number(Number(value).toFixed(1))) + ' s';
const utc = (ms) => clockText(ms, 0);

function trace(cue, trigger) {
  if (!cue) return '';
  const parts = ['cued ' + utc(cue.cued_at_ms)];
  if (cue.state === 'not_seen') return parts.concat(NOT_SEEN).join(' · ');
  parts.push(cue.state === 'seen' ? 'in manifest ' + utc(cue.seen_at_ms) : 'waiting for the manifest');
  parts.push(secondsText(cue.seconds));
  if (trigger && trigger.marker_only) parts.push('marker only');
  return parts.join(' · ');
}

export class AdBreakControl {
  constructor({ post, refresh }) {
    Object.assign(this, { post, refresh });
    this.busy = false;
    this.trigger = null;
    $('ad-break-cue').addEventListener('click', () => this.onCue());
    $('ad-break-seconds').addEventListener('input', () => { $('ad-break-error').textContent = ''; });
  }

  render(data) {
    const ads = data.ads || {};
    const trigger = ads.trigger || null;
    const cue = ads.cue || null;
    const refusal = data.actions ? data.actions['ad-break'] : null;
    const oldStack = data.deployed && !trigger;
    const left = cue && cue.seconds_left ? Math.ceil(cue.seconds_left) : null;
    this.trigger = trigger;

    const perTrigger = !trigger || trigger.mode !== 'clip';
    $('ad-break-seconds-label').hidden = !perTrigger || !trigger;
    $('ad-break-mode').textContent = !trigger ? 'Ad break'
      : 'Ad break · ' + (perTrigger ? 'chosen per trigger' : 'clip length') + (trigger.marker_only ? ' · marker only' : '');

    const button = $('ad-break-cue');
    button.disabled = this.busy || Boolean(refusal) || oldStack || left !== null;
    button.textContent = left !== null ? 'Break running · ' + left + ' s left'
      : trigger && !perTrigger ? 'Cue ad break · ' + secondsText(trigger.seconds) : 'Cue ad break';
    $('ad-break-status').textContent = refusal || (oldStack ? 'Deploy once more to enable ad breaks.' : trace(cue, trigger));
  }

  async onCue() {
    const error = $('ad-break-error');
    const body = {};
    if (!this.trigger || this.trigger.mode !== 'clip') {
      const seconds = Number($('ad-break-seconds').value);
      if (!Number.isFinite(seconds) || seconds < MIN_S || seconds > MAX_S) {
        error.textContent = 'Choose a break of ' + MIN_S + ' to ' + MAX_S + ' seconds.';
        $('ad-break-seconds').focus();
        return;
      }
      body.seconds = seconds;
    }
    this.busy = true;
    $('ad-break-cue').disabled = true;
    try {
      const result = await this.post('ad-break', body);
      error.textContent = result.ok ? '' : result.data.error || 'The cue was refused (' + result.status + ').';
    } finally {
      this.busy = false;
      await this.refresh();
    }
  }
}
