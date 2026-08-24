import { useState, useEffect, useCallback } from 'react';
import * as api from '../api/client';

export function useAuth() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);   // 启动时恢复登录态中

  // 启动：有存量 token 则验证恢复；任何 401（token 过期等）广播后回登录页
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!api.getToken()) {
        setLoading(false);
        return;
      }
      try {
        const me = await api.fetchMe();
        if (!cancelled) setUser(me);
      } catch {
        /* 401 已由 client 清理 token 并广播 */
      }
      if (!cancelled) setLoading(false);
    })();

    const onUnauthorized = () => setUser(null);
    api.onUnauthorized(onUnauthorized);
    return () => {
      cancelled = true;
      window.removeEventListener('sop-unauthorized', onUnauthorized);
    };
  }, []);

  const doLogin = useCallback(async (username, password) => {
    const data = await api.login(username, password);
    api.setToken(data.token);
    setUser({ username: data.username });
  }, []);

  const doRegister = useCallback(async (username, password) => {
    const data = await api.register(username, password);
    api.setToken(data.token);
    setUser({ username: data.username });
  }, []);

  const logout = useCallback(() => {
    api.clearToken();
    setUser(null);
  }, []);

  return { user, loading, doLogin, doRegister, logout };
}
