// The only module that talks to the livectl server.

const PATHS = { 'source-start': 'source/start', 'source-stop': 'source/stop' };

export async function getPipeline(offset, jobKey) {
  // The offset only means something for the job it was counted on, so the job's key travels with it.
  const job = jobKey ? '&job=' + encodeURIComponent(jobKey) : '';
  const response = await fetch('api/pipeline?offset=' + offset + job, { cache: 'no-store' });
  if (!response.ok) throw new Error('the console answered ' + response.status);
  return response.json();
}

export async function post(action, body) {
  const response = await fetch('api/' + (PATHS[action] || action), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

export async function getLogs(tab, after) {
  const response = await fetch('api/logs?tab=' + tab + '&after=' + (after || 0), { cache: 'no-store' });
  if (!response.ok) throw new Error('logs answered ' + response.status);
  return response.json();
}
