// Everything the console knows about the selected resource.
import { ago, el, metricRow } from './format.js';

export function renderDetail(section, node, metricsAge) {
  if (!node) {
    section.replaceChildren(el('p', { class: 'placeholder' }, 'Nothing is deployed, so there are no resources to show.'));
    return;
  }
  const rows = [];
  for (const [key, value] of Object.entries(node.details)) {
    if (value === null || value === undefined || (Array.isArray(value) && value.length === 0)) continue;
    rows.push([key, Array.isArray(value) ? value.join('\n') : String(value)]);
  }
  for (const [key, value] of Object.entries(node.metrics)) {
    if (value !== null && value !== undefined) rows.push(metricRow(key, value));
  }
  const hasMetrics = Object.values(node.metrics).some((v) => v !== null && v !== undefined);
  // replaceChildren would print a null as the text "null", so absent parts are filtered out first.
  section.replaceChildren(...[
    el('h2', { class: 'card-title' }, node.title,
      el('span', { class: 'badge', 'data-health': node.health }, node.state || node.health)),
    el('p', { class: 'summary' }, node.summary),
    node.error ? el('p', { class: 'error' }, node.error) : null,
    rows.length ? el('dl', { class: 'facts' }, rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)])) : null,
    hasMetrics ? el('p', { class: 'note' }, 'Metrics from CloudWatch, ' + ago(metricsAge)) : null,
    node.console_url ? el('a', { href: node.console_url, target: '_blank', rel: 'noopener' }, 'Open in the AWS console') : null,
  ].filter(Boolean));
}
