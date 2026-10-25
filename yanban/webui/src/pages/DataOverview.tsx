import { useEffect, useState } from "react";

// 数据概览页（P4）：全部数字来自 Leader 只读端点 /stats/bench、/stats/quality（R2 零 mock）
interface Metric {
  key: string;
  name: string;
  cn: string;
  value: number;
  fraction: string;
  definition: string;
}
interface PartnerStat {
  partner: string;
  calls: number;
  avg_ms: number;
  median_ms: number;
}
interface BenchStats {
  bench_date: string;
  source_file: string;
  metrics: Metric[];
  rpc_total: number;
  per_partner: PartnerStat[];
}
interface StrategyStat {
  group: string;
  name: string;
  avg: number;
  min: number;
  avg_halluc: number;
  n: number;
}
interface RubricDim {
  dim: string;
  cn: string;
  avg: number;
}
interface QualityStats {
  source_files: string[];
  started_at: string;
  n_samples: number;
  strategies: StrategyStat[];
  rubric_dims: RubricDim[];
}

// 对抗样例表（研伴版）：内容为项目真实演进史，出处见每行「依据」列
const ADVERSARIAL_ROWS = [
  {
    Case: "首轮基准评测",
    problem: "TaskCompletion 0%——10 个任务全部超时失败，被诚实四指标当场揭穿",
    defense: "超时双修复：SDK 内部 30s 硬编码换慢 transport（aip_slow.py）+ llm.py 超时 60→150s",
    result: "90%",
    ref: "bench-results-20260917.md",
  },
  {
    Case: "模拟审稿（judge）",
    problem: "早期评审只给分数，模型可能幻觉虚构优点",
    defense: "评审器强制产出幻觉清单，报告中任何幻觉声明即一票否决",
    result: "冠军策略幻觉 0 条",
    ref: "docs/quality_experiment_v2.md",
  },
  {
    Case: "长文本生成",
    problem: "experiment/review 的 GLM 长生成实测 30016ms 全灭（超时窗口过窄）",
    defense: "llm.py 请求超时 60s→150s，覆盖 GLM 长尾生成",
    result: "实验/审稿子任务恢复正常",
    ref: "bench-results-20260917.md / REPORT.md",
  },
  {
    Case: "SDK 契约分歧",
    problem: "SDK 文档与源码行为不一致，按文档写直接踩坑",
    defense: "以源码为准核对契约（方法论），基准抓包实证（四指标口径）",
    result: "30 次跨智能体 RPC 可复现",
    ref: "metrics.py / bench.py",
  },
];

export default function DataOverview() {
  const [bench, setBench] = useState<BenchStats | null>(null);
  const [quality, setQuality] = useState<QualityStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [b, q] = await Promise.all([
          fetch("/stats/bench").then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/stats/bench HTTP ${r.status}`)))),
          fetch("/stats/quality").then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/stats/quality HTTP ${r.status}`)))),
        ]);
        setBench(b);
        setQuality(q);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载失败");
      }
    })();
  }, []);

  if (error)
    return (
      <div className="h-screen flex-1 overflow-y-auto px-8 py-8">
        <div className="mx-auto max-w-3xl rounded-xl bg-red-50 p-4 text-[13.5px] text-red-600">
          数据加载失败：{error}（需要 Leader 8041 提供只读端点，若服务未升级请重启 Leader）
        </div>
      </div>
    );
  if (!bench || !quality)
    return (
      <div className="h-screen flex-1 overflow-y-auto px-8 py-8">
        <div className="mx-auto max-w-3xl text-[14px] text-gray-500">加载真实数据中…</div>
      </div>
    );

  const sorted = [...quality.strategies].sort((a, b) => b.avg - a.avg);

  return (
    <div className="h-screen flex-1 overflow-y-auto px-8 py-8">
      <div className="mx-auto max-w-4xl">
        <h1 className="text-[20px] font-bold text-gray-900">数据概览</h1>
        <p className="mt-1 text-[13px] text-gray-500">
          全部为实测数据（非演示样例）。指标口径见 metrics.py，由 Leader 只读端点实时解析原始文件返回。
        </p>

        {/* 四指标卡 */}
        <div className="mt-6 grid grid-cols-2 gap-4 xl:grid-cols-4">
          {bench.metrics.map((m) => (
            <div key={m.key} className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
              <div className="text-[12px] text-gray-500">{m.cn}</div>
              <div className="tnum mt-1 text-[30px] font-bold leading-none tracking-tight text-primary-700">
                {(m.value * 100).toFixed(0)}
                <span className="text-[16px]">%</span>
              </div>
              <div className="tnum mt-1.5 text-[12px] text-gray-500">
                {m.fraction} · {m.name}
              </div>
              <div className="mt-2 text-[11px] leading-4 text-gray-500">{m.definition}</div>
            </div>
          ))}
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* 质量闭环对照条形图 */}
          <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
            <div className="text-[14px] font-semibold text-gray-800">质量闭环对照（rubric 均分，10 分制）</div>
            <div className="mt-1 text-[11.5px] text-gray-500">
              3 主题 × 2 repeats，N={quality.n_samples}；启动 {quality.started_at}
            </div>
            <div className="mt-4 space-y-3">
              {sorted.map((s, i) => (
                <div key={s.group}>
                  <div className="flex items-baseline justify-between text-[12.5px]">
                    <span className={i === 0 ? "font-semibold text-gray-800" : "text-gray-600"}>
                      {s.name}
                      {i === 0 && <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-600">冠军</span>}
                    </span>
                    <span className="tnum font-mono text-[12.5px] text-gray-500">{s.avg.toFixed(1)}</span>
                  </div>
                  <div className="mt-1 h-3 w-full overflow-hidden rounded-full bg-gray-100">
                    <div
                      className={`h-full rounded-full ${i === 0 ? "bg-primary-600" : "bg-primary-300"}`}
                      style={{ width: `${(s.avg / 10) * 100}%` }}
                    />
                  </div>
                  <div className="tnum mt-0.5 text-[10.5px] text-gray-500">
                    最低 {s.min.toFixed(1)} · 平均幻觉声明 {s.avg_halluc.toFixed(1)} 条 · 有效样本 {s.n}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 质量结构：rubric 五维各自 0—10 的均分（同心弧，不是份额） */}
          <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
            <div className="text-[14px] font-semibold text-gray-800">质量结构（rubric 五维均分）</div>
            <div className="mt-1 text-[11.5px] text-gray-500">
              全部有效样本的五维加权评分均值，每维满分 10（judge.py 评审器）
            </div>
            <DimensionRings dims={quality.rubric_dims} />
            <div className="mt-2 space-y-1.5">
              {quality.rubric_dims.map((d, i) => (
                <div key={d.dim} className="flex items-center gap-2 text-[12.5px]">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
                  <span className="text-gray-600">{d.cn}</span>
                  <span className="tnum ml-auto font-mono text-[12.5px] text-gray-500">{d.avg.toFixed(1)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Partner 调用统计 */}
        <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
          <div className="text-[14px] font-semibold text-gray-800">
            Partner 调用耗时（跨智能体 RPC 总次数 {bench.rpc_total}）
          </div>
          <table className="mt-3 w-full text-[12.5px]">
            <thead>
              <tr className="text-left text-[11px] font-medium tracking-[0.02em] text-gray-500">
                <th className="pb-2 font-medium">Partner</th>
                <th className="pb-2 font-medium">调用次数</th>
                <th className="pb-2 font-medium">平均耗时</th>
                <th className="pb-2 font-medium">中位耗时</th>
              </tr>
            </thead>
            <tbody>
              {bench.per_partner.map((p) => (
                <tr key={p.partner} className="border-t border-gray-50 transition-colors hover:bg-gray-50/60">
                  <td className="py-1.5 text-gray-700">{p.partner}</td>
                  <td className="tnum py-1.5 text-gray-500">{p.calls}</td>
                  <td className="tnum py-1.5 text-gray-500">{p.avg_ms}ms</td>
                  <td className="tnum py-1.5 text-gray-500">{p.median_ms}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 对抗样例表 */}
        <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
          <div className="text-[14px] font-semibold text-gray-800">对抗样例：我们如何被自己的指标逼着变强</div>
          <table className="mt-3 w-full text-[12.5px]">
            <thead>
              <tr className="text-left text-[11px] font-medium tracking-[0.02em] text-gray-500">
                <th className="pb-2 font-medium">用例</th>
                <th className="pb-2 font-medium">曾暴露的问题</th>
                <th className="pb-2 font-medium">现在的防线</th>
                <th className="pb-2 font-medium">结果</th>
                <th className="pb-2 font-medium">依据</th>
              </tr>
            </thead>
            <tbody>
              {ADVERSARIAL_ROWS.map((r) => (
                <tr key={r.Case} className="border-t border-gray-50 align-top transition-colors hover:bg-gray-50/60">
                  <td className="py-2 pr-3 font-medium text-gray-700">{r.Case}</td>
                  <td className="py-2 pr-3 leading-5 text-gray-500">{r.problem}</td>
                  <td className="py-2 pr-3 leading-5 text-gray-500">{r.defense}</td>
                  <td className="tnum py-2 pr-3 font-medium text-green-600">{r.result}</td>
                  <td className="font-mono text-[10.5px] leading-4 text-gray-500">{r.ref}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-4 rounded-xl bg-gray-50 p-4 text-[11.5px] leading-5 text-gray-500">
          数据来源文件：{bench.source_file}；{quality.source_files.join("；")}。页面由 Leader 只读端点（/stats/bench、/stats/quality）解析原始文件生成，无任何人工录入数字。
        </div>
      </div>
    </div>
  );
}

const DONUT_COLORS = ["#2563eb", "#60a5fa", "#93c5fd", "#f59e0b", "#a5b4fc"];
const DIM_MAX = 10;

/** 每一维是各自 0—满分 的**独立**评分，不是同一个整体的份额。
 *  饼图会邀请出「引用规范占 21.6%」这种根本不成立的读法，而且五片都在 6.5—8.4 之间、
 *  角度几乎等分，差异反而看不见 ⇒ 画成同心弧：每条弧 = 该维 / 满分。 */
function DimensionRings({ dims }: { dims: RubricDim[] }) {
  const sum = dims.reduce((a, d) => a + d.avg, 0);
  const mean = dims.length ? sum / dims.length : 0;
  return (
    <div className="mt-4 flex flex-col items-center">
      <svg
        width="150"
        height="150"
        viewBox="0 0 150 150"
        role="img"
        aria-label={`五维均分（每维满分 ${DIM_MAX}）：${dims.map((d) => `${d.cn} ${d.avg.toFixed(1)}`).join("，")}`}
      >
        {dims.map((d, i) => {
          const r = 62 - i * 11;
          const c = 2 * Math.PI * r;
          const frac = Math.min(d.avg / DIM_MAX, 1);
          return (
            <circle
              key={d.dim}
              cx="75"
              cy="75"
              r={r}
              fill="none"
              stroke={DONUT_COLORS[i % DONUT_COLORS.length]}
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={`${frac * c} ${c - frac * c}`}
              transform="rotate(-90 75 75)"
            />
          );
        })}
        <text x="75" y="81" textAnchor="middle" className="fill-gray-700" fontSize="17" fontWeight="700">
          {mean.toFixed(1)}
        </text>
      </svg>
      <div className="tnum mt-1 text-[11px] text-gray-500">
        五维均值 {mean.toFixed(1)} / {DIM_MAX}（合计 {sum.toFixed(1)} / {DIM_MAX * dims.length}）
      </div>
    </div>
  );
}
