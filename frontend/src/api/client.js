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
