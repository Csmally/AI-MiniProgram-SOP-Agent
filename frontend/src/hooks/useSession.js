import { useState, useCallback } from 'react';
import * as api from '../api/client';

export function useSession() {
  const [sessionId, setSessionId] = useState(null);
  const [phase, setPhase] = useState('idle');
  const [features, setFeatures] = useState([]);
  const [checkItems, setCheckItems] = useState([]);
  const [checkResults, setCheckResults] = useState([]);
  const [report, setReport] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  const init = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.createSession();
      setSessionId(data.session_id);
      setPhase(data.current_phase);
      setFeatures(data.features || []);
      setCheckItems(data.check_items || []);
      setMessages(data.messages || []);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions');
      const data = await res.json();
      const list = data.sessions || [];
      if (list.length > 0) {
        const latest = list[0];
        await load(latest.session_id);
      }
    } catch {}
  }, []);

  const load = useCallback(async (sid) => {
    setLoading(true);
    try {
      const data = await api.getSession(sid);
      setSessionId(data.session_id);
      setPhase(data.current_phase);
      setFeatures(data.features || []);
      setCheckItems(data.check_items || []);
      setMessages(data.messages || []);
      if (data.report_content) {
        setReport({
          report_content: data.report_content,
          summary: { total: data.check_results?.length || 0, passed: 0, failed: 0, pass_rate: 'N/A' },
        });
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const uploadPrd = useCallback(async (file) => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const data = await api.uploadPrd(sessionId, file);
      setFeatures(data.features || []);
      const msg = data.message;
      setMessages(prev => [...prev, { role: 'user', content: `[上传 PRD: ${file.name}]` }, { role: 'assistant', content: msg }]);
      setPhase('prd_uploaded');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const generateSop = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const data = await api.generateSop(sessionId);
      setCheckItems(data.check_items || []);
      setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
      setPhase('sop_generated');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const approveChecklist = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const data = await api.approveChecklist(sessionId);
      setPhase(data.current_phase);
      setMessages(prev => [...prev, ...(data.messages || [])]);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const updateItem = useCallback(async (itemId, data) => {
    if (!sessionId) return;
    await api.updateCheckItem(sessionId, itemId, data);
    setCheckItems(prev => prev.map(i => i.id === itemId ? { ...i, ...data } : i));
  }, [sessionId]);

  const deleteItem = useCallback(async (itemId) => {
    if (!sessionId) return;
    await api.deleteCheckItem(sessionId, itemId);
    setCheckItems(prev => prev.filter(i => i.id !== itemId));
  }, [sessionId]);

  const addItem = useCallback(async (data) => {
    if (!sessionId) return;
    const res = await api.createCheckItem(sessionId, data);
    setCheckItems(prev => [...prev, res.item]);
  }, [sessionId]);

  const runChecks = useCallback(async () => {
    if (!sessionId) return;
    setPhase('running');
    setLoading(true);
    try {
      const data = await api.runChecks(sessionId);
      setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
      // 获取报告
      const rep = await api.getReport(sessionId);
      setCheckResults(rep.summary);
      setReport(rep);
      setPhase('completed');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const sendMessage = useCallback(async (text) => {
    if (!sessionId) return;
    setMessages(prev => [...prev, { role: 'user', content: text }, { role: 'assistant', content: '' }]);
    setLoading(true);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let full = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const token = line.slice(6);
            if (token === '[DONE]') continue;
            full += token;
            setMessages(prev => {
              const copy = [...prev];
              copy[copy.length - 1] = { role: 'assistant', content: full };
              return copy;
            });
          }
        }
      }
    } catch (e) {
      setMessages(prev => {
        const copy = [...prev];
        copy[copy.length - 1] = { role: 'assistant', content: '回复失败: ' + e.message };
        return copy;
      });
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  return {
    sessionId, phase, features, checkItems, checkResults, report, messages, loading,
    init, loadLatest, load, uploadPrd, generateSop, approveChecklist, updateItem, deleteItem, addItem, runChecks, sendMessage,
  };
}
