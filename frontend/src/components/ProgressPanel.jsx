const CATEGORY_LABELS = { ui: 'UI', api: 'API' };
const STATUS_ICONS = { pending: '○', running: '◌', passed: '✓', failed: '✗', skipped: '−' };
const STATUS_COLORS = { pending: '#9ca3af', running: '#3b82f6', passed: '#22c55e', failed: '#ef4444', skipped: '#6b7280' };

// agentProgress: SSE 实时进度（key=item_id → {status, agent}），无事件时回退到 item 自身状态
export default function ProgressPanel({ items, agentProgress }) {
  const itemStatus = (item) => agentProgress?.[item.id]?.status || item.status || 'pending';

  const passed = items.filter(i => itemStatus(i) === 'passed').length;
  const failed = items.filter(i => itemStatus(i) === 'failed').length;
  const running = items.filter(i => itemStatus(i) === 'running').length;
  const total = items.length;

  return (
    <div className="progress-panel">
      <div className="progress-header">
        <h2>检查执行中</h2>
        <div className="progress-bar-container">
          <div className="progress-bar">
            <div className="progress-fill pass" style={{ width: `${(passed / total) * 100}%` }} />
            <div className="progress-fill fail" style={{ width: `${(failed / total) * 100}%`, left: `${(passed / total) * 100}%` }} />
          </div>
        </div>
        <span className="progress-stats">
          {passed} 通过 / {failed} 失败 / {total} 总计{running > 0 ? ` / ${running} 执行中` : ''}
        </span>
      </div>

      <div className="progress-items">
        {items.map(item => {
          const status = itemStatus(item);
          const agent = agentProgress?.[item.id]?.agent;
          return (
            <div key={item.id} className="progress-item">
              <span className="progress-status" style={{ color: STATUS_COLORS[status] || '#9ca3af' }}>
                {STATUS_ICONS[status] || '○'}
              </span>
              <span className="progress-category" style={{ background: item.category === 'ui' ? '#3b82f6' : '#8b5cf6' }}>
                {CATEGORY_LABELS[item.category]}
              </span>
              <span className="progress-desc">{item.description}</span>
              {agent && <span className="progress-agent">{agent}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
