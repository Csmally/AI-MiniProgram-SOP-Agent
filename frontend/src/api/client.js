const BASE = '/api';

export async function createSession() {
  const res = await fetch(`${BASE}/sessions`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to create session');
  return res.json();
}

export async function getSession(id) {
  const res = await fetch(`${BASE}/sessions/${id}`);
  if (!res.ok) throw new Error('Session not found');
  return res.json();
}

export async function uploadPrd(sessionId, file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE}/sessions/${sessionId}/prd`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) throw new Error('Upload failed');
  return res.json();
}

export async function generateSop(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/generate`, { method: 'POST' });
  if (!res.ok) throw new Error('Generate failed');
  return res.json();
}

export async function approveChecklist(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/approve`, { method: 'POST' });
  if (!res.ok) throw new Error('Approve failed');
  return res.json();
}

export async function getCheckItems(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/check-items`);
  if (!res.ok) throw new Error('Failed to get check items');
  return res.json();
}

export async function updateCheckItem(sessionId, itemId, data) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/check-items/${itemId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Update failed');
  return res.json();
}

export async function deleteCheckItem(sessionId, itemId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/check-items/${itemId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Delete failed');
  return res.json();
}

export async function createCheckItem(sessionId, data) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/check-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Create failed');
  return res.json();
}

export async function runChecks(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/run`, { method: 'POST' });
  if (!res.ok) throw new Error('Run failed');
  return res.json();
}

export async function getReport(sessionId) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/report`);
  if (!res.ok) throw new Error('Report not found');
  return res.json();
}

export async function deleteSession(id) {
  const res = await fetch(`${BASE}/sessions/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Delete failed');
  return res.json();
}

export async function sendChat(sessionId, message) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error('Chat failed');
  return res.json();
}

// SSE 流式执行（run/approve）：逐事件回调 onEvent({type: 'phase'|'item'|'report'|'done'|'error', ...})
export async function streamRun(sessionId, nextAction = 'run', onEvent) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      next_action: nextAction,
      approval: nextAction === 'approve' ? 'approved' : null,
    }),
  });
  if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop();
    for (const part of parts) {
      for (const line of part.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (data === '[DONE]') continue;
        if (!data || data === '{}') continue; // 心跳
        try { onEvent(JSON.parse(data)); } catch { /* 忽略无法解析的行 */ }
      }
    }
  }
}
