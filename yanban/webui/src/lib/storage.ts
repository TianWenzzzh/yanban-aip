// 任务记录持久化（P4）：localStorage，记录本轮提交过的任务，支持回看
import type { TaskDetail } from "../api";

const KEY = "yanban_task_history_v1";

export interface TaskRecord {
  task_id: string;
  query: string;
  status: TaskDetail["status"];
  elapsed_ms: number;
  rpc_total: number;
  submitted_at: number; // epoch ms
}

export function loadHistory(): TaskRecord[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const list = JSON.parse(raw) as TaskRecord[];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function upsertRecord(d: TaskDetail): TaskRecord[] {
  const list = loadHistory().filter((r) => r.task_id !== d.task_id);
  list.unshift({
    task_id: d.task_id,
    query: d.query,
    status: d.status,
    elapsed_ms: d.elapsed_ms,
    rpc_total: d.subresults.reduce((a, s) => a + s.rpc_calls, 0),
    submitted_at: Date.now(),
  });
  const trimmed = list.slice(0, 50);
  localStorage.setItem(KEY, JSON.stringify(trimmed));
  return trimmed;
}

export function removeRecord(taskId: string): TaskRecord[] {
  const list = loadHistory().filter((r) => r.task_id !== taskId);
  localStorage.setItem(KEY, JSON.stringify(list));
  return list;
}
