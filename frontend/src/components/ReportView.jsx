import ReactMarkdown from 'react-markdown';

export default function ReportView({ report }) {
  if (!report) {
    return (
      <div className="report-view">
        <h2>检查报告</h2>
        <p>报告尚未生成</p>
      </div>
    );
  }

  const { summary, report_content } = report;

  return (
    <div className="report-view">
      <h2>检查报告</h2>

      {summary && (
        <div className="report-summary">
          <div className="summary-card total">
            <span className="summary-num">{summary.total || 0}</span>
            <span className="summary-label">总计</span>
          </div>
          <div className="summary-card passed">
            <span className="summary-num">{summary.passed || 0}</span>
            <span className="summary-label">通过</span>
          </div>
          <div className="summary-card failed">
            <span className="summary-num">{summary.failed || 0}</span>
            <span className="summary-label">失败</span>
          </div>
          <div className="summary-card rate">
            <span className="summary-num">{summary.pass_rate || 'N/A'}</span>
            <span className="summary-label">通过率</span>
          </div>
        </div>
      )}

      {report_content && (
        <div className="report-content">
          <ReactMarkdown>{report_content}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}
