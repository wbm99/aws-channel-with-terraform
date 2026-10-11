// The Configuration page: settings that reach AWS at the next deploy. Ads is its first tab: an optional clip for the
// player to show during a break, and whether a break lasts the clip's length or is chosen on the Live page.
const MAX_BYTES = 50 * 1024 * 1024;
const UNPLAYABLE = 'This file does not play in the browser; use an H.264 MP4.';

const $ = (id) => document.getElementById(id);

// The clip's length as the browser reads it, which also proves the browser can play it.
function clipSeconds(file) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    const url = URL.createObjectURL(file);
    const done = (fn, value) => { URL.revokeObjectURL(url); fn(value); };
    video.preload = 'metadata';
    video.addEventListener('loadedmetadata', () => (Number.isFinite(video.duration) && video.duration > 0
      ? done(resolve, video.duration) : done(reject, new Error(UNPLAYABLE))));
    video.addEventListener('error', () => done(reject, new Error(UNPLAYABLE)));
    video.src = url;
  });
}

function size(bytes) {
  return bytes >= 1024 * 1024 ? (bytes / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
}

export class ConfigPage {
  constructor({ post, uploadClip, removeClip, refresh }) {
    Object.assign(this, { post, uploadClip, removeClip, refresh });
    this.shown = null;            // what the page was last drawn from, so a poll redraws only on a change
    this.busy = false;
    this.input = $('ads-clip-input');
    this.input.addEventListener('change', () => this.onPick());
    $('ads-clip-remove').addEventListener('click', () => this.onRemove());
    for (const radio of document.querySelectorAll('#ads-mode input')) {
      radio.addEventListener('change', () => this.onMode(radio.value));
    }
  }

  render(data) {
    const view = { ads: data.ads ? { ...data.ads, cue: undefined } : null,
      refusal: data.actions ? data.actions.config : null, busy: this.busy };
    const json = JSON.stringify(view);
    if (json === this.shown) return;
    this.shown = json;
    this.draw(view.ads, view.refusal);
  }

  draw(ads, refusal) {
    const form = $('ads-form');
    if (!ads) return;
    if (ads.error) {                // a file the console will not overwrite: say so instead of offering a form
      form.hidden = true;
      this.error(ads.error);
      this.status('');
      return;
    }
    form.hidden = false;
    const saved = ads.saved;
    $('ads-clip-empty').hidden = saved.clip;
    $('ads-clip-saved').hidden = !saved.clip;
    if (saved.clip) {
      $('ads-clip-summary').textContent = 'clip.mp4 · ' + saved.clip_seconds.toFixed(1) + ' s · ' + size(ads.clip_bytes);
    }
    const locked = Boolean(refusal) || this.busy;
    this.input.disabled = locked;
    $('ads-clip-remove').disabled = locked;
    // A break lasts 5 to 180 s; a clip outside that range is used per trigger, looped or cut.
    const fits = saved.clip && saved.clip_seconds >= 5 && saved.clip_seconds <= 180;
    $('ads-mode-clip').disabled = locked || !fits;
    $('ads-mode-hint').textContent = saved.clip && !fits
      ? 'This clip lasts ' + saved.clip_seconds.toFixed(1) + ' s and a break lasts 5 to 180 s, so it is used per '
        + 'trigger: looped through a longer break, cut by a shorter one.'
      : 'A break longer than the clip loops it; a shorter one cuts it off.';
    $('ads-mode-per-trigger').disabled = locked;
    $('ads-mode-clip').checked = saved.duration_mode === 'clip';
    $('ads-mode-per-trigger').checked = saved.duration_mode === 'per_trigger';
    if (refusal) this.status(refusal);
    else if (ads.pending) this.status('Saved — deploy to apply.');
    else if (ads.deployed) this.status('Matches what is deployed.');
    else this.status('Applied by the next deploy.');
  }

  error(text) { $('ads-error').textContent = text || ''; }

  status(text) { $('ads-status').textContent = text || ''; }

  async run(work) {
    this.busy = true;
    this.shown = null;
    this.error('');
    try {
      const result = await work();
      if (result && !result.ok) this.error(result.data.error || 'Refused (' + result.status + ').');
    } catch (error) {
      this.error(error.message);
    } finally {
      this.busy = false;
      this.shown = null;          // redraw from the next poll, which carries what was saved
      await this.refresh();
    }
  }

  onPick() {
    const file = this.input.files[0];
    if (!file) return;
    this.run(async () => {
      try {
        if (file.size > MAX_BYTES) throw new Error('The clip is larger than 50 MB.');
        const seconds = await clipSeconds(file);
        return await this.uploadClip(file, seconds);
      } finally {
        this.input.value = '';    // picking the same file again still fires a change
      }
    });
  }

  onRemove() { this.run(() => this.removeClip()); }

  onMode(mode) { this.run(() => this.post('config-ads', { duration_mode: mode })); }
}
