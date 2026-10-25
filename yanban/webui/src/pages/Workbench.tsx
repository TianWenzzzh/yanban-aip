import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTask, LeaderApiError, submitTask, type PlanStage, type SubResult, type TaskDetail } from "../api";
import { Badge, StatusBadge } from "../components/Badge";
import Markdown from "../components/Markdown";
import Owl from "../components/Owl";
import {
  EXAMPLE_CHIPS,
  HERO_SUB,
  HERO_TITLE,
  PARTNER_LABELS,
  VERDICT_LABELS,
  WIZARD_STEPS,
} from "../constants";
import { upsertRecord } from "../lib/storage";

// plan 阶段 ↔ subresult 配对：DAG 路径按 stage_key，LLM 分解路径按 partner/序号兜底
function matchSub(plan: PlanStage[], i: number, subs: SubResult[]): SubResult | undefined {
  const stage = plan[i];
  if (!stage) return subs[i];
  if (stage.key) {
    const byKey = subs.find((s) => s.stage_key === stage.key);
    if (byKey) return byKey;
  }
  const byPartner = subs.filter((s) => s.partner === stage.partner);
  return byPartner[i] ?? byPartner[0] ?? subs[i];
}

function fmtMs(ms: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 90_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m} 分 ${s} 秒`;
}

export default function Workbench({ reopenTaskId }: { reopenTaskId: string | null }) {
  const [step, setStep] = useState(0);
  const [topic, setTopic] = useState("");
  const [constraints, setConstraints] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const reopenedRef = useRef(false);

  // 回看历史任务；支持 ?task=<id> 深链（可分享任务链接）
  useEffect(() => {
    if (reopenedRef.current) return;
    const fromUrl = new URLSearchParams(window.location.search).get("task");
    const id = reopenTaskId ?? fromUrl;
    if (id) {
      reopenedRef.current = true;
      setTaskId(id);
    }
  }, [reopenTaskId]);

  // 轮询：2.5s，与旧 ui.html 一致；终态自停
  useEffect(() => {
    if (!taskId) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const d = await fetchTask(taskId);
        if (stopped) return;
        setDetail(d);
        setLoadError("");
        if (d.status !== "running") {
          upsertRecord(d);
          if (timer) window.clearInterval(timer);
        }
      } catch (e) {
        if (e instanceof LeaderApiError && e.status === 404) {
          if (timer) window.clearInterval(timer);
          setLoadError("任务不存在——Leader 重启后内存中的任务会清空，历史记录仅保留本机摘要。");
        }
        /* 其他错误（网络抖动）：下个周期重试 */
      }
    };
    void tick();
    timer = window.setInterval(tick, 2500);
    return () => {
      stopped = true;
      if (timer) window.clearInterval(timer);
    };
  }, [taskId]);

  const composedQuery = constraints.trim() ? `${topic.trim()}\n补充要求：${constraints.trim()}` : topic.trim();

  const doSubmit = useCallback(async () => {
    setSubmitting(true);
    setError("");
    try {
      const id = await submitTask(composedQuery);
      setTaskId(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }, [composedQuery]);

  const reset = () => {
    setStep(0);
    setTopic("");
    setConstraints("");
    setTaskId(null);
    setDetail(null);
    setError("");
    reopenedRef.current = false;
  };

  const running = detail?.status === "running";

  // ── 右栏：流水线面板（P3）───────────────────────────────
  const pipeline = detail && (
    <PipelinePanel detail={detail} />
  );

  return (
    <div className="flex h-screen flex-1 flex-col overflow-hidden">
      {!taskId && !detail ? (
        // ── 空态：Hero + 三步向导 ──
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-2xl flex-col items-center px-6 pb-16 pt-14">
            <Owl size={88} />
            <h1 className="mt-4 text-center text-[26px] font-bold text-gray-900">{HERO_TITLE}</h1>
            <p className="mt-2 text-center text-[14px] leading-6 text-gray-500">{HERO_SUB}</p>
            <div className="mt-8 w-full rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
              <Steps step={step} />
              {step === 0 && (
                <div>
                  <p className="text-[13px] text-gray-500">{WIZARD_STEPS[0].hint}</p>
                  <textarea
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder={WIZARD_STEPS[0].placeholder}
                    className="mt-3 h-24 w-full resize-y rounded-xl border border-gray-200 p-3 text-[14px] outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
                  />
                  <div className="mt-2 flex flex-wrap gap-2">
                    {EXAMPLE_CHIPS.map((c) => (
                      <button
                        key={c}
                        onClick={() => setTopic(c)}
                        className="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-[12px] leading-none text-gray-600 transition-colors hover:border-primary-200 hover:bg-primary-50 hover:text-primary-700"
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                  <div className="mt-4 flex justify-end">
                    <button
                      disabled={!topic.trim()}
                      onClick={() => setStep(1)}
                      className="btn-primary disabled:cursor-not-allowed"
                    >
                      下一步
                    </button>
                  </div>
                </div>
              )}
              {step === 1 && (
                <div>
                  <p className="text-[13px] text-gray-500">{WIZARD_STEPS[1].hint}</p>
                  <textarea
                    value={constraints}
                    onChange={(e) => setConstraints(e.target.value)}
                    placeholder={WIZARD_STEPS[1].placeholder}
                    className="mt-3 h-24 w-full resize-y rounded-xl border border-gray-200 p-3 text-[14px] outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
                  />
                  <div className="mt-4 flex justify-between">
                    <button onClick={() => setStep(0)} className="btn-ghost">
                      上一步
                    </button>
                    <button
                      onClick={() => setStep(2)}
                      className="btn-primary"
                    >
                      {constraints.trim() ? "下一步" : "跳过，去确认"}
                    </button>
                  </div>
                </div>
              )}
              {step === 2 && (
                <div>
                  <p className="text-[13px] text-gray-500">{WIZARD_STEPS[2].hint}</p>
                  <div className="mt-3 rounded-xl bg-gray-50 p-4 text-[13.5px] leading-6 text-gray-700">
                    <div className="mb-1 text-[12px] font-medium text-gray-500">即将发送的任务</div>
                    <pre className="whitespace-pre-wrap font-sans">{composedQuery}</pre>
                  </div>
                  <div className="mt-4 flex justify-between">
                    <button onClick={() => setStep(1)} className="btn-ghost">
                      返回修改
                    </button>
                    <button
                      disabled={submitting}
                      onClick={doSubmit}
                      className="btn-primary"
                    >
                      {submitting ? "提交中…" : "提交给研伴 🚀"}
                    </button>
                  </div>
                </div>
              )}
            </div>
            {error && <div className="mt-4 w-full rounded-xl bg-red-50 p-3 text-[13px] text-red-600">{error}</div>}
          </div>
        </div>
      ) : (
        // ── 任务视图：主区报告 + 右栏流水线 ──
        <div className="grid flex-1 grid-cols-1 gap-0 overflow-hidden lg:grid-cols-[1fr_340px]">
          <div className="overflow-y-auto px-8 py-6">
            <div className="mx-auto max-w-3xl">
              <div className="flex items-center justify-between">
                <h2 className="text-[16px] font-semibold text-gray-800">
                  任务 <span className="font-mono text-[14px] tracking-tight text-gray-600">{detail?.task_id ?? taskId}</span>
                </h2>
                <button onClick={reset} className="btn-secondary">
                  ＋ 新任务
                </button>
              </div>
              {(detail?.query || composedQuery) && (
                <div className="mt-2 rounded-xl bg-white p-4 text-[13px] leading-6 text-gray-600 shadow-sm">
                  {detail?.query ?? composedQuery}
                </div>
              )}
              {detail && (
                <div className="mt-3 flex items-center gap-2 text-[13px] text-gray-500">
                  <StatusBadge status={detail.status} />
                  {running && <span>Leader 编排中（并发执行子任务）…</span>}
                  {detail.status === "failed" && detail.error && (
                    <span className="text-red-600">编排失败：{detail.error}</span>
                  )}
                </div>
              )}
              {detail?.final_report && (
                <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-[15px] font-semibold text-gray-800">📄 聚合报告</div>
                    <GateBadge verdict={detail.quality_gate.final_verdict} enabled={detail.quality_gate.enabled} />
                  </div>
                  <Markdown text={detail.final_report} />
                  <div className="tnum mt-4 border-t border-gray-100 pt-3 text-[12px] text-gray-500">
                    端到端耗时 {fmtMs(detail.elapsed_ms)} · 跨智能体 RPC {detail.subresults.reduce((a, s) => a + s.rpc_calls, 0)} 次 · 报告 {detail.final_report.length} 字
                  </div>
                </div>
              )}
              {loadError ? (
                <div className="mt-6 rounded-xl bg-amber-50 p-4 text-[13px] leading-6 text-amber-700">{loadError}</div>
              ) : (
                !detail && (
                  <div className="mt-6 flex items-center gap-3 text-[14px] text-gray-500">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
                    正在获取任务状态…
                  </div>
                )
              )}
            </div>
          </div>
          <div className="overflow-y-auto border-l border-gray-100 bg-gray-50/60 px-4 py-6">{pipeline}</div>
        </div>
      )}
    </div>
  );
}

function Steps({ step }: { step: number }) {
  return (
    <div className="mb-5 flex items-center gap-2">
      {WIZARD_STEPS.map((s, i) => (
        <div key={s.title} className="flex items-center gap-2">
          <div
            className={`tnum flex h-6 w-6 items-center justify-center rounded-full text-[12px] font-medium ${
              i < step ? "bg-green-100 text-green-600" : i === step ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-600"
            }`}
          >
            {i < step ? "✓" : i + 1}
          </div>
          <span className={`text-[13px] ${i === step ? "font-medium text-gray-800" : "text-gray-500"}`}>{s.title}</span>
          {i < WIZARD_STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-gray-200" />}
        </div>
      ))}
    </div>
  );
}

function GateBadge({ verdict, enabled }: { verdict: string; enabled: boolean }) {
  if (!enabled) return <span className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-600">质量闭环未启用</span>;
  const good = verdict === "accept" || verdict === "minor";
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${good ? "bg-green-100 text-green-700" : "bg-purple-100 text-purple-700"}`}>
      质量闭环：{VERDICT_LABELS[verdict] ?? verdict}
    </span>
  );
}

// 流水线面板：plan 阶段卡 + quality_gate 时间线（数据全部来自 GET /task/{id}，无编造）
function PipelinePanel({ detail }: { detail: TaskDetail }) {
  const subs = detail.subresults ?? [];
  const running = detail.status === "running";
  const hasDag = detail.plan.some((p) => p.depends?.length);
  return (
    <div>
      <div className="mb-3 flex items-center justify-between px-1">
        <div className="text-[14px] font-semibold text-gray-800">流水线</div>
        <StatusBadge status={detail.status} />
      </div>
      {detail.plan.length === 0 && (
        <div className="rounded-xl bg-white p-4 shadow-sm">
          {running ? (
            <div className="animate-pulse space-y-3">
              <div className="flex items-center gap-2 text-[13px] text-gray-500">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
                Leader 正在做意图分析与任务分解…
              </div>
              {[64, 52, 58].map((w, i) => (
                <div key={i} className="h-3 rounded bg-gray-100" style={{ width: `${w}%` }} />
              ))}
            </div>
          ) : (
            <div className="text-[13px] text-gray-500">无计划信息</div>
          )}
        </div>
      )}
      <div className="space-y-2.5">
        {detail.plan.map((p, i) => {
          const s = matchSub(detail.plan, i, subs);
          return (
            <div key={i} className="rounded-xl bg-white p-3.5 shadow-sm transition-shadow hover:shadow-md" title={p.input}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-[13.5px] font-medium text-gray-800">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-50 text-[11px] text-primary-700">
                    {i + 1}
                  </span>
                  {PARTNER_LABELS[p.partner] ?? p.partner}
                  <span className="text-[11px] font-normal text-gray-500">{p.partner}</span>
                </div>
                <Badge state={s?.state ?? "pending"} />
              </div>
              <p className="mt-1.5 line-clamp-2 text-[12px] leading-5 text-gray-500">{p.input}</p>
              {hasDag && p.depends?.length ? (
                <div className="mt-1.5 text-[11px] text-gray-500">
                  依赖：{p.depends.map((d) => PARTNER_LABELS[d] ?? d).join("、")}
                </div>
              ) : null}
              {s && (
                <div className="tnum mt-1.5 text-[11px] text-gray-500">
                  耗时 {fmtMs(s.duration_ms)} · RPC {s.rpc_calls} 次 · 端点来源 {s.source}
                  {s.error ? <span className="text-red-500"> · {s.error.slice(0, 80)}</span> : null}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {detail.quality_gate?.rounds?.length ? (
        <div className="mt-4">
          <div className="mb-2 px-1 text-[14px] font-semibold text-gray-800">质量闭环</div>
          <div className="space-y-2">
            {detail.quality_gate.rounds.map((r) => (
              <div key={r.round} className="rounded-xl border-l-4 border-primary-500 bg-white p-3 shadow-sm">
                <div className="text-[12.5px] text-gray-700">
                  第 {r.round} 轮审稿判定「{VERDICT_LABELS[r.verdict] ?? r.verdict}」
                  {r.revised ? <span className="text-primary-600"> → 已自动按意见修订</span> : <span className="text-green-600"> → 通过</span>}
                </div>
                {r.review_summary && (
                  <p className="mt-1 line-clamp-3 text-[11.5px] leading-5 text-gray-500">{r.review_summary}</p>
                )}
              </div>
            ))}
          </div>
          {detail.quality_gate.final_verdict && (
            <div className="mt-2 px-1 text-[11.5px] text-gray-500">
              最终判定：{VERDICT_LABELS[detail.quality_gate.final_verdict] ?? detail.quality_gate.final_verdict}
            </div>
          )}
        </div>
      ) : null}
      {running && (
        <div className="mt-4 flex items-center gap-2 px-1 text-[12px] text-gray-500">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
          每 2.5 秒自动刷新
        </div>
      )}
    </div>
  );
}
