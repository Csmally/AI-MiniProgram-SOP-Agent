import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { motion, AnimatePresence } from 'framer-motion';

export default function ChatPanel({ messages, onSend, onUploadPrd, loading, phase, onGenerateSop, onRunChecks }) {
  const [input, setInput] = useState('');
  const bottomRef = useRef(null);
  const fileRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!loading) inputRef.current?.focus();
  }, [loading]);

  const handleSend = () => {
    if (!input.trim()) return;
    onSend(input.trim());
    setInput('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file) onUploadPrd(file);
    e.target.value = '';
  };

  return (
    <section className="chat-panel">
      <div className="chat-messages">
        <AnimatePresence>
          {messages.map((m, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className={`message ${m.role}`}
            >
              <div className="message-content">
                <ReactMarkdown>{m.content}</ReactMarkdown>
                {m.role === 'assistant' && loading && i === messages.length - 1 && (
                  <span className="cursor-blink">|</span>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
        {loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="message assistant orb-loading"
          >
            <div className="message-content">
              <div className="siri-orb" />
            </div>
          </motion.div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-actions">
        <input
          type="file"
          ref={fileRef}
          onChange={handleFileChange}
          accept=".md,.txt"
          style={{ display: 'none' }}
        />
        <button className="btn-action" onClick={() => fileRef.current?.click()} disabled={loading}>
          上传 PRD
        </button>
        {phase === 'prd_uploaded' && (
          <button className="btn-action btn-primary" onClick={onGenerateSop} disabled={loading}>
            生成检查清单
          </button>
        )}
        {(phase === 'sop_generated' || phase === 'ready') && (
          <button className="btn-action btn-primary" onClick={onRunChecks} disabled={loading}>
            开始检查
          </button>
        )}
      </div>

      <div className="chat-input">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息，Enter 发送..."
          rows={2}
          disabled={loading}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>
          发送
        </button>
      </div>
    </section>
  );
}
