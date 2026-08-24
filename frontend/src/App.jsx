import { useEffect, useRef, useCallback } from 'react';
import { useAuth } from './hooks/useAuth';
import { useSession } from './hooks/useSession';
import LoginView from './components/LoginView';
import SessionList from './components/SessionList';
import ChatPanel from './components/ChatPanel';
import RightPanel from './components/RightPanel';

function App() {
  const auth = useAuth();
  const session = useSession();
  const initialized = useRef(false);

  // 登录态就绪后加载最近会话（登录后 / 启动恢复后各触发一次）
  useEffect(() => {
    if (!auth.loading && auth.user && !initialized.current) {
      initialized.current = true;
      session.loadLatest();
    }
    // initialized ref 守卫保证只执行一次；session 加入 deps 仅为满足 lint
  }, [auth.loading, auth.user, session]);

  const handleNew = useCallback(() => {
    session.init();
  }, [session]);

  const handleSelect = useCallback((sid) => {
    session.load(sid);
  }, [session]);

  const handleLogout = useCallback(() => {
    // 清内存态防止换账号登录后串会话（sessions/messages 等全部重置）
    session.reset();
    initialized.current = false;
    auth.logout();
  }, [session, auth]);

  if (auth.loading) {
    return <div className="app-loading">加载中…</div>;
  }
  if (!auth.user) {
    return <LoginView onLogin={auth.doLogin} onRegister={auth.doRegister} />;
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>微信小程序 SOP Agent</h1>
        <span className={`badge phase-${session.phase}`}>{session.phase}</span>
        <div className="header-user">
          <span className="user-name" title="当前用户">{auth.user.username}</span>
          <button type="button" className="btn-logout" onClick={handleLogout}>退出</button>
        </div>
      </header>
      <main className="app-main">
        <SessionList
          sessions={session.sessions}
          currentId={session.sessionId}
          onSelect={handleSelect}
          onNew={handleNew}
          onDelete={session.deleteSession}
        />
        <ChatPanel
          messages={session.messages}
          onSend={session.sendMessage}
          onUploadPrd={session.uploadPrd}
          loading={session.loading}
          phase={session.phase}
          onGenerateSop={session.generateSop}
          onRunChecks={session.runChecks}
          canRun={session.checkItems.length > 0}
        />
        <aside className="app-sidebar">
          <RightPanel session={session} />
        </aside>
      </main>
    </div>
  );
}

export default App;
