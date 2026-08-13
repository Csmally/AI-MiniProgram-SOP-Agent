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
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  // 本轮执行中各 Agent 的实时进度（SSE item 事件驱动，key=item_id）
  const [agentProgress, setAgentProgress] = useState({});

  const load = useCallback(async (sid) => {
    setLoading(true);
    try {
      const data = await api.getSession(sid);
      setSessionId(data.session_id);
      setPhase(data.current_phase);
      setFeatures(data.features || []);
      setCheckItems(data.check_items || []);
      setMessages(data.messages || []);
      setAgentProgress({});
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

  const refreshSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions');
      const data = await res.json();
      const list = data.sessions || [];
      setSessions(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  const init = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.createSession();
      setSessionId(data.session_id);
      setPhase(data.current_phase);
      setFeatures(data.features || []);
      setCheckItems(data.check_items || []);
      setMessages(data.messages || []);
      setReport(null);
      setAgentProgress({});
      await refreshSessions();
    } finally {
      setLoading(false);
    }
  }, [refreshSessions]);

  const loadLatest = useCallback(async () => {
    const list = await refreshSessions();
    if (list.length > 0) {
      await load(list[0].session_id);
    }
  }, [refreshSessions, load]);

  const deleteSession = useCallback(async (sid) => {
    await api.deleteSession(sid);
    await refreshSessions();
  }, [refreshSessions]);

  // SSE 事件统一处理：phase 与结果一律以服务端 checkpoint 为准
  const handleRunEvent = useCallback((event) => {
    switch (event.type) {
      case 'phase':
        setPhase(event.phase);
        break;
      case 'item':
        if (event.item_id) {
          setAgentProgress(prev => ({
            ...prev,
            [event.item_id]: { status: event.status, agent: event.agent },
          }));
        }
        break;
      case 'report':
        setReport(prev => ({
          ...(prev || { summary: {} }),
          report_content: event.content,
        }));
        break;
      case 'done':
        if (event.state) {
          const results = event.state.check_results || [];
          const passed = results.filter(r => r.status === 'passed').length;
          const failed = results.filter(r => r.status === 'failed').length;
          setPhase(event.state.current_phase || 'completed');
          setCheckResults(results);
          setReport({
            report_content: event.state.report_content || '',
            summary: {
              total: results.length,
              passed,
              failed,
              pass_rate: results.length ? `${Math.round((passed / results.length) * 100)}%` : 'N/A',
            },
          });
          if (Array.isArray(event.state.messages)) setMessages(event.state.messages);
        }
        break;
      default:
        break;
    }
  }, []);

  const uploadPrd = useCallback(async (file) => {
    if (!sessionId) return;
    setLoading(true);
    try {
      await api.uploadPrd(sessionId, file);
      // phase/清单以服务端为准：上传已链式「解析+生成清单」，回刷完整状态
      await load(sessionId);
    } finally {
      setLoading(false);
    }
  }, [sessionId, load]);

  const generateSop = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      await api.generateSop(sessionId);
      await load(sessionId); // 重新生成后回刷（以服务端 checkpoint 为准）
    } finally {
      setLoading(false);
    }
  }, [sessionId, load]);

  const approveChecklist = useCallback(async () => {
    if (!sessionId || checkItems.length === 0) return;
    setLoading(true);
    setAgentProgress({});
    setPhase('running');
    try {
      await api.streamRun(sessionId, 'approve', handleRunEvent);
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', content: '检查执行失败: ' + e.message }]);
      await load(sessionId);
    } finally {
      setLoading(false);
    }
  }, [sessionId, checkItems.length, handleRunEvent, load]);

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
    if (!sessionId || checkItems.length === 0) return;
    setLoading(true);
    setAgentProgress({});
    setPhase('running');
    try {
      await api.streamRun(sessionId, 'run', handleRunEvent);
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', content: '检查执行失败: ' + e.message }]);
      await load(sessionId);
    } finally {
      setLoading(false);
    }
  }, [sessionId, checkItems.length, handleRunEvent, load]);

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
    sessionId, phase, features, checkItems, checkResults, report, messages, sessions, loading, agentProgress,
    init, loadLatest, load, uploadPrd, generateSop, approveChecklist, updateItem, deleteItem, addItem, runChecks, sendMessage, deleteSession,
  };
}
