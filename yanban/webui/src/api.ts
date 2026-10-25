// Leader API 类型与访问层——字段以 leader.py / orchestrator.py / quality_gate.py 原文为准（R8）
// 注意：运行期 subresults 为空数组（orchestrator._execute_plan 完成后一次性赋值）

export interface PlanStage {
  partner: string;
  input: string;
  key?: string;
  depends?: string[];
}

export interface SubResult {
  partner: string;
  state: string; // TaskState.* 或 timeout / skipped
  duration_ms: number;
  rpc_calls: number;
  error: string;
  source: string; // static | discovery
  stage_key?: string;
}

export interface GateRound {
  round: number;
  verdict: string; // accept | minor | major | reject
  revised: boolean;
  review_summary: string;
}

export interface QualityGate {
  enabled: boolean;
  rounds: GateRound[];
  final_verdict: string;
}

export interface TaskDetail {
  task_id: string;
  status: "running" | "completed" | "failed";
  query: string;
  plan: PlanStage[];
  subresults: SubResult[];
  final_report: string;
  quality_gate: QualityGate;
  error: string;
  elapsed_ms: number;
}

export async function submitTask(query: string): Promise<string> {
  const r = await fetch("/task", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!r.ok) throw new Error(`提交失败（HTTP ${r.status}）`);
  const d = (await r.json()) as { task_id: string };
  return d.task_id;
}

export class LeaderApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function fetchTask(taskId: string): Promise<TaskDetail> {
  const r = await fetch(`/task/${taskId}`);
  if (!r.ok) throw new LeaderApiError(`查询失败（HTTP ${r.status}）`, r.status);
  return (await r.json()) as TaskDetail;
}
