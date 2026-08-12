import { useEffect, useRef, useCallback } from 'react';
import { useSession } from './hooks/useSession';
import SessionList from './components/SessionList';
import ChatPanel from './components/ChatPanel';
import ChecklistView from './components/ChecklistView';
import ProgressPanel from './components/ProgressPanel';
import ReportView from './components/ReportView';

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

  const rightPanel = () => {
    switch (session.phase) {
      case 'sop_generated':
      case 'ready':
        return (
          <ChecklistView
            items={session.checkItems}
            onUpdate={session.updateItem}
            onDelete={session.deleteItem}
            onAdd={session.addItem}
            onApprove={session.approveChecklist}
            onRegenerate={session.generateSop}
            loading={session.loading}
          />
        );
      case 'running':
        return <ProgressPanel items={session.checkItems} />;
      case 'completed':
        return <ReportView report={session.report} />;
      default:
        return (
          <div className="right-placeholder">
            <p>上传 PRD 后将在此显示检查清单</p>
          </div>
        );
    }
  };

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
        />
        <aside className="app-sidebar">
          {rightPanel()}
        </aside>
      </main>
    </div>
  );
}

export default App;
