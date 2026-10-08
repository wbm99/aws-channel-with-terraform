// The only module that talks to the livectl server.

const PATHS = { 'source-start': 'source/start', 'source-stop': 'source/stop', 'source-settings': 'source/settings' };

export async function getPipeline(offset, jobKey) {
  // The offset only means something for the job it was counted on, so the job's key travels with it.
  const job = jobKey ? '&job=' + encodeURIComponent(jobKey) : '';
  // A request that never answers (the network changed under it) would otherwise hold up every poll after it.
  const response = await fetch('api/pipeline?offset=' + offset + job, { cache: 'no-store', signal: AbortSignal.timeout(8000) });
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

// What MediaConnect parsed from the incoming stream; polled only by the Source page.
export async function getReceived() {
  const response = await fetch('api/source/received', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error('the console answered ' + response.status);
  return response.json();
}

export async function getLogs(tab, after) {
  const response = await fetch('api/logs?tab=' + tab + '&after=' + (after || 0), { cache: 'no-store' });
  if (!response.ok) throw new Error('logs answered ' + response.status);
  return response.json();
}
