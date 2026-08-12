import { useState } from 'react';
import Modal from './Modal';

const PHASE_LABELS = {
  idle: '空闲', prd_uploaded: 'PRD已上传', sop_generated: '清单已生成',
  ready: '就绪', running: '检查中', completed: '已完成',
};

export default function SessionList({ sessions, currentId, onSelect, onNew, onDelete }) {
  const [deleteTarget, setDeleteTarget] = useState(null);

  const handleDelete = (e, sid) => {
    e.stopPropagation();
    setDeleteTarget(sid);
  };

  const confirmDelete = async () => {
    if (deleteTarget) {
      await onDelete(deleteTarget);
      setDeleteTarget(null);
    }
  };

  return (
    <div className="session-list">
      <button className="btn-new-session" onClick={onNew}>
        + 新建会话
      </button>
      <div className="session-items">
        {sessions.map(s => (
          <div
            key={s.session_id}
            className={`session-item ${s.session_id === currentId ? 'active' : ''}`}
            onClick={() => onSelect(s.session_id)}
          >
            <div className="session-item-top">
              <span className={`phase-dot phase-${s.current_phase}`} />
              <span className="session-phase">{PHASE_LABELS[s.current_phase] || s.current_phase}</span>
              <button
                className="btn-delete-session"
                onClick={(e) => handleDelete(e, s.session_id)}
                title="删除"
              >
                ×
              </button>
            </div>
            <div className="session-item-meta">
              {s.features_count > 0 && <span>{s.features_count} 功能</span>}
              {s.check_items_count > 0 && <span>{s.check_items_count} 检查项</span>}
            </div>
            <div className="session-item-id">{s.session_id}</div>
          </div>
        ))}
      </div>
      <Modal
        open={!!deleteTarget}
        title="删除会话"
        message={`确定要删除此会话吗？此操作不可撤销。`}
        confirmText="删除"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
