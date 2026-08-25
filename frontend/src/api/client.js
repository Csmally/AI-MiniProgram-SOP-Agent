const BASE = '/api';
const TOKEN_KEY = 'sop_token';

// ── token 存取 ──
export function getToken() { return localStorage.getItem(TOKEN_KEY); }
export function setToken(token) { localStorage.setItem(TOKEN_KEY, token); }
export function clearToken() { localStorage.removeItem(TOKEN_KEY); }

// 401 统一处理：清 token 并广播登出事件（App 监听后回登录页）
export function onUnauthorized(handler) {
  window.addEventListener('sop-unauthorized', handler);
}

function authHeaders(extra = {}) {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function handleError(res) {
  const data = await res.json().catch(() => ({}));
  return new Error(data.detail || `请求失败 (${res.status})`);
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, { ...options, headers: authHeaders(options.headers) });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('sop-unauthorized'));
    throw await handleError(res);
  }
  if (!res.ok) throw await handleError(res);
  return res.json();
}

// SSE 流式请求：只返回原始 response（401/错误处理同上，body 由调用方读流）
async function requestStream(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, { ...options, headers: authHeaders(options.headers) });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('sop-unauthorized'));
    throw await handleError(res);
  }
  if (!res.ok || !res.body) throw await handleError(res);
  return res;
}

// ── 认证 ──
export async function register(username, password) {
  return request('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
}

export async function login(username, password) {
  return request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
}

export async function fetchMe() {
  return request('/auth/me');
}

// ── 会话 ──
export async function createSession() {
  return request('/sessions', { method: 'POST' });
}

export async function getSession(id) {
  return request(`/sessions/${id}`);
}

export async function listSessions() {
  return request('/sessions');
}

export async function deleteSession(id) {
  return request(`/sessions/${id}`, { method: 'DELETE' });
}

export async function uploadPrd(sessionId, file) {
  const form = new FormData();
  form.append('file', file);
  return request(`/sessions/${sessionId}/prd`, { method: 'POST', body: form });
}

export async function generateSop(sessionId) {
  return request(`/sessions/${sessionId}/generate`, { method: 'POST' });
}

export async function updateCheckItem(sessionId, itemId, data) {
  return request(`/sessions/${sessionId}/check-items/${itemId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

export async function deleteCheckItem(sessionId, itemId) {
  return request(`/sessions/${sessionId}/check-items/${itemId}`, { method: 'DELETE' });
}

export async function createCheckItem(sessionId, data) {
  return request(`/sessions/${sessionId}/check-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

// SSE 流式执行（run/approve）：逐事件回调 onEvent({type: 'phase'|'item'|'report'|'done'|'error', ...})
export async function streamRun(sessionId, nextAction = 'run', onEvent) {
  const res = await requestStream(`/sessions/${sessionId}/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      next_action: nextAction,
      approval: nextAction === 'approve' ? 'approved' : null,
    }),
  });
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

// SSE 流式聊天：逐 token 回调 onToken
export async function streamChat(sessionId, message, onToken) {
  const res = await requestStream(`/sessions/${sessionId}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    for (const line of chunk.split('\n')) {
      if (line.startsWith('data: ')) {
        const token = line.slice(6);
        if (token !== '[DONE]') onToken(token);
      }
    }
  }
}
