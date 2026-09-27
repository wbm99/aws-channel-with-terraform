// DOM and number helpers. Data is always written as text, never as HTML.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function ago(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const s = Math.round(seconds);
  if (s < 2) return 'just now';
  if (s < 90) return s + ' s ago';
  return Math.round(s / 60) + ' min ago';
}

const METRICS = {
  src_connected: ['Connected', (v) => (v >= 1 ? 'yes' : 'no')],
  src_bitrate: ['Bitrate', (v) => (v / 1e6).toFixed(1) + ' Mbps'],
  src_rtt: ['Round trip', (v) => v.toFixed(0) + ' ms'],
  src_not_recovered: ['Unrecovered packets / min', (v) => v.toFixed(0)],
  src_cc_errors: ['Continuity errors / min', (v) => v.toFixed(0)],
  ml_alerts: ['Active alerts', (v) => v.toFixed(0)],
  ml_fps: ['Input frame rate', (v) => v.toFixed(1) + ' fps'],
  ml_input_loss: ['Input loss', (v) => v.toFixed(0) + ' s / min'],
  mp_ingress_bytes: ['Ingest', (v) => ((v * 8) / 60 / 1e6).toFixed(1) + ' Mbps'],
  mp_egress_5xx: ['Egress 5xx / min', (v) => v.toFixed(0)],
  mp_egress_bytes: ['Egress', (v) => ((v * 8) / 60 / 1e6).toFixed(1) + ' Mbps'],
  mp_egress_requests: ['Egress requests / min', (v) => v.toFixed(0)],
  cf_requests: ['Requests / min', (v) => v.toFixed(0)],
  cf_5xx_rate: ['5xx rate', (v) => v.toFixed(1) + ' %'],
  cf_4xx_rate: ['4xx rate', (v) => v.toFixed(1) + ' %'],
  cf_bytes_downloaded: ['Delivered to viewers', (v) => ((v * 8) / 60 / 1e6).toFixed(1) + ' Mbps'],
};

export function metricRow(key, value) {
  const [label, show] = METRICS[key] || [key, String];
  return [label, show(value)];
}

// "2026-09-27 02:54:39", in UTC like the burned-in clock and the player's clock. Takes epoch ms or an ISO string.
export function stamp(value) {
  return new Date(value).toISOString().slice(0, 19).replace('T', ' ');
}
