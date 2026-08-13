import { useState } from 'react';

const PRIORITY_COLORS = { critical: '#ef4444', high: '#f97316', medium: '#eab308', low: '#22c55e' };
const CATEGORY_LABELS = { ui: 'UI', api: 'API' };

export default function ChecklistView({ items, onUpdate, onDelete, onAdd, onApprove, onRegenerate, loading }) {
  const [editId, setEditId] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({ category: 'ui', description: '', priority: 'medium', check_steps: '', expected_result: '' });

  const startEdit = (item) => {
    setEditId(item.id);
    setEditForm({ ...item });
  };

  const saveEdit = () => {
    onUpdate(editId, editForm);
    setEditId(null);
  };

  const handleAdd = () => {
    onAdd({
      ...addForm,
      check_steps: addForm.check_steps.split('\n').filter(s => s.trim()),
    });
    setShowAdd(false);
    setAddForm({ category: 'ui', description: '', priority: 'medium', check_steps: '', expected_result: '' });
  };

  const uiCount = items.filter(i => i.category === 'ui').length;
  const apiCount = items.filter(i => i.category === 'api').length;

  return (
    <div className="checklist-view">
      <div className="checklist-header">
        <h2>检查清单</h2>
        <span className="checklist-stats">UI: {uiCount} | API: {apiCount} | 共 {items.length} 项</span>
      </div>

      <div className="checklist-actions">
        <button className="btn-primary" onClick={onApprove} disabled={loading || items.length === 0}>
          确认并开始检查
        </button>
        <button className="btn-secondary" onClick={onRegenerate} disabled={loading}>
          重新生成
        </button>
        <button className="btn-secondary" onClick={() => setShowAdd(true)}>
          手动添加
        </button>
      </div>

      {showAdd && (
        <div className="edit-form">
          <h3>新增检查项</h3>
          <select value={addForm.category} onChange={e => setAddForm({ ...addForm, category: e.target.value })}>
            <option value="ui">UI 检查</option>
            <option value="api">API 检查</option>
          </select>
          <input placeholder="描述" value={addForm.description} onChange={e => setAddForm({ ...addForm, description: e.target.value })} />
          <select value={addForm.priority} onChange={e => setAddForm({ ...addForm, priority: e.target.value })}>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <textarea placeholder="检查步骤（每行一步）" value={addForm.check_steps} onChange={e => setAddForm({ ...addForm, check_steps: e.target.value })} rows={3} />
          <input placeholder="预期结果" value={addForm.expected_result} onChange={e => setAddForm({ ...addForm, expected_result: e.target.value })} />
          <div>
            <button className="btn-primary" onClick={handleAdd}>添加</button>
            <button className="btn-secondary" onClick={() => setShowAdd(false)}>取消</button>
          </div>
        </div>
      )}

      <div className="checklist-items">
        {items.map(item => (
          <div key={item.id} className={`check-item-card priority-${item.priority}`}>
            {editId === item.id ? (
              <div className="edit-form">
                <select value={editForm.priority} onChange={e => setEditForm({ ...editForm, priority: e.target.value })}>
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
                <input value={editForm.description} onChange={e => setEditForm({ ...editForm, description: e.target.value })} />
                <textarea value={(editForm.check_steps || []).join('\n')} onChange={e => setEditForm({ ...editForm, check_steps: e.target.value.split('\n') })} rows={3} />
                <input placeholder="预期结果" value={editForm.expected_result || ''} onChange={e => setEditForm({ ...editForm, expected_result: e.target.value })} />
                <div>
                  <button className="btn-primary" onClick={saveEdit}>保存</button>
                  <button className="btn-secondary" onClick={() => setEditId(null)}>取消</button>
                </div>
              </div>
            ) : (
              <>
                <div className="item-header">
                  <span className="item-category" style={{ background: item.category === 'ui' ? '#3b82f6' : '#8b5cf6' }}>
                    {CATEGORY_LABELS[item.category]}
                  </span>
                  <span className="item-priority" style={{ color: PRIORITY_COLORS[item.priority] }}>
                    {item.priority}
                  </span>
                  <span className={`item-status status-${item.status}`}>{item.status}</span>
                </div>
                <p className="item-desc">{item.description}</p>
                {item.check_steps?.length > 0 && (
                  <ol className="item-steps">
                    {item.check_steps.map((s, i) => <li key={i}>{s}</li>)}
                  </ol>
                )}
                {item.expected_result && <p className="item-expected">预期: {item.expected_result}</p>}
                <div className="item-actions">
                  <button className="btn-small" onClick={() => startEdit(item)}>编辑</button>
                  <button className="btn-small btn-danger" onClick={() => onDelete(item.id)}>删除</button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
