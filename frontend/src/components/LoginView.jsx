import { useState } from 'react';

function LoginView({ onLogin, onRegister }) {
  const [mode, setMode] = useState('login');   // login | register
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const switchMode = (m) => { setMode(m); setError(''); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!username.trim()) { setError('用户名不能为空'); return; }
    if (password.length < 6) { setError('密码至少 6 位'); return; }
    if (mode === 'register' && password !== confirm) { setError('两次输入的密码不一致'); return; }
    setSubmitting(true);
    try {
      if (mode === 'login') await onLogin(username.trim(), password);
      else await onRegister(username.trim(), password);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>微信小程序 SOP Agent</h1>
        <p className="login-subtitle">新版本上线前的自动化 SOP 检查平台</p>
        <div className="login-tabs">
          <button
            type="button"
            className={mode === 'login' ? 'active' : ''}
            onClick={() => switchMode('login')}
          >登录</button>
          <button
            type="button"
            className={mode === 'register' ? 'active' : ''}
            onClick={() => switchMode('register')}
          >注册</button>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <input
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            maxLength={64}
            autoFocus
          />
          <input
            type="password"
            placeholder="密码（至少 6 位）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            maxLength={128}
          />
          {mode === 'register' && (
            <input
              type="password"
              placeholder="确认密码"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              maxLength={128}
            />
          )}
          {error && <div className="login-error">{error}</div>}
          <button type="submit" className="btn-primary login-submit" disabled={submitting}>
            {submitting ? '请稍候…' : mode === 'login' ? '登 录' : '注册并登录'}
          </button>
        </form>
      </div>
    </div>
  );
}

export default LoginView;
