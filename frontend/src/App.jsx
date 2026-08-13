import { useEffect, useRef, useCallback } from 'react';
import { useSession } from './hooks/useSession';
import SessionList from './components/SessionList';
import ChatPanel from './components/ChatPanel';
import RightPanel from './components/RightPanel';

function App() {
  const session = useSession();
  const initialized = useRef(false);

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      session.loadLatest();
    }
  }, []);

  const handleNew = useCallback(() => {
    session.init();
  }, [session]);

  const handleSelect = useCallback((sid) => {
    session.load(sid);
  }, [session]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>微信小程序 SOP Agent</h1>
        <span className={`badge phase-${session.phase}`}>{session.phase}</span>
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
