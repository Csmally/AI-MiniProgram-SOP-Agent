import ChecklistView from './ChecklistView';
import ProgressPanel from './ProgressPanel';
import ReportView from './ReportView';

// 右侧面板：按会话 phase 路由到清单/进度/报告视图
export default function RightPanel({ session }) {
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
      return <ProgressPanel items={session.checkItems} agentProgress={session.agentProgress} />;
    case 'completed':
      return <ReportView report={session.report} />;
    default:
      return (
        <div className="right-placeholder">
          <p>上传 PRD 后将在此显示检查清单</p>
        </div>
      );
  }
}
