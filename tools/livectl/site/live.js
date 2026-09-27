// The figures worth watching during a broadcast, under the player. Each comes from one node's CloudWatch metrics.
import { el, metricRow } from './format.js';

const FIGURES = [
  ['srt_source', 'src_bitrate'],
  ['srt_source', 'src_rtt'],
  ['srt_source', 'src_not_recovered'],
  ['medialive_channel', 'ml_fps'],
  ['medialive_channel', 'ml_alerts'],
  ['mediapackage_channel', 'mp_ingress_bytes'],
  ['cloudfront_cdn', 'cf_requests'],
];

export function renderStats(list, nodes) {
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  list.replaceChildren(...FIGURES.map(([nodeId, key]) => {
    const node = byId[nodeId];
    // A resource that is off has no live figure, only the last datapoint CloudWatch still holds.
    const value = node && node.health !== 'off' ? node.metrics[key] : null;
    const [label, text] = value === null || value === undefined ? [metricRow(key, 0)[0], '—'] : metricRow(key, value);
    return el('div', { class: 'stat', 'data-metric': key }, el('dt', {}, label), el('dd', {}, text));
  }));
}
