import { useEffect, useState } from "react";

// 协议演示页（P5）：同一问题走 AIP 面与 A2A 面左右并排；AMP 审计尾巴
// 两个面都经 Leader 只读演示端点转发（R5），前端不直连网关避免 CORS/协议细节
interface FaceResult {
  face: "AIP" | "A2A";
  round_trips: number;
  elapsed_ms: number;
  result_text: string;
  endpoint: string;
  error: string;
}
interface AmpRecord {
  file: string;
  caller: string;
  action: string;
  duration_ms: number | null;
  code: number;
  ts: string;
}

const DEMO_QUERY = "multi-agent collaboration";

export default function ProtocolDemo() {
  const [query, setQuery] = useState(DEMO_QUERY);
  const [aip, setAip] = useState<FaceResult | null>(null);
  const [a2a, setA2a] = useState<FaceResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [amp, setAmp] = useState<AmpRecord[]>([]);

  const loadAmp = () =>
    fetch("/amp/latest")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setAmp(d.records ?? []))
      .catch(() => setAmp([]));

  useEffect(() => {
    void loadAmp();
  }, []);

  const runDemo = async () => {
    setRunning(true);
    setError("");
    setAip(null);
    setA2a(null);
    const q = encodeURIComponent(query.trim() || DEMO_QUERY);
    const grab = (url: string): Promise<FaceResult> =>
      fetch(url).then((r) => (r.ok ? r.json() : r.json().then((d) => ({ ...(d as FaceResult), error: `HTTP ${r.status}` }))));
    try {
      // 两个面顺序执行（同一路径先 AIP 后 A2A），结果等价性由网关同一 MCP 工具保证
      const r1 = await grab(`/protocol/aip?query=${q}`);
      setAip(r1);
      const r2 = await grab(`/protocol/a2a?query=${q}`);
      setA2a(r2);
      void loadAmp();
    } catch (e) {
      setError(e instanceof Error ? e.message : "演示失败");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="h-screen flex-1 overflow-y-auto px-8 py-8">
      <div className="mx-auto max-w-4xl">
        <h1 className="text-[20px] font-bold text-gray-900">双协议面演示</h1>
        <p className="mt-1 text-[13px] text-gray-500">
          同一个能力（万桥网关的文献检索工具），分别用 AIP（国标 GB/Z 185 Direct RPC）与 A2A 两种协议面调用，结果应当等价。
        </p>

        <div className="mt-5 flex gap-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="演示用检索问题"
            className="h-10 flex-1 rounded-xl border border-gray-200 px-3.5 text-[14px] outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
          />
          <button
            disabled={running}
            onClick={runDemo}
            className="btn-primary"
          >
            {running ? "演示执行中…" : "运行双协议演示"}
          </button>
        </div>
        {error && <div className="mt-3 rounded-xl bg-red-50 p-3 text-[13px] text-red-600">{error}</div>}

        <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
          <FaceCard title="AIP 协议面" sub="POST /rpc · 国标 Direct RPC" r={aip} loading={running && !aip} />
          <FaceCard title="A2A 协议面" sub="AgentCard + A2aClient tasks/send" r={a2a} loading={running && !a2a && !!aip} />
        </div>

        <div className="mt-3 rounded-xl bg-primary-50 p-4 text-center text-[13px] text-primary-700">
          同一能力，两种协议面，一个结果 —— 等价性语义与 bench_interop.py「D2 全回环」（A2A→AIP→MCP→原路回包）一致
        </div>

        {/* AMP 审计尾巴 */}
        <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="text-[14px] font-semibold text-gray-800">AMP 审计尾巴</div>
            <button onClick={() => void loadAmp()} className="btn-secondary !h-[30px] !px-3 !text-[12px] text-gray-500">
              刷新
            </button>
          </div>
          <p className="mt-1 text-[11.5px] text-gray-500">
            logs/ 下 AMP NDJSON 最新 {amp.length} 行（amp_access_*.jsonl），展示最近调用方 / 动作 / 耗时 / 结果码。
          </p>
          <div className="mt-3 max-h-72 overflow-y-auto">
            {amp.length === 0 ? (
              <div className="py-6 text-center text-[13px] text-gray-500">暂无 AMP 记录（服务运行后自动累积）</div>
            ) : (
              <table className="w-full text-[12px]">
                <thead className="sticky top-0 bg-white">
                  <tr className="text-left text-[11px] font-medium tracking-[0.02em] text-gray-500">
                    <th className="pb-2 font-medium">时间</th>
                    <th className="pb-2 font-medium">调用方 (AIC 尾段)</th>
                    <th className="pb-2 font-medium">动作</th>
                    <th className="pb-2 font-medium">耗时</th>
                    <th className="pb-2 font-medium">结果码</th>
                  </tr>
                </thead>
                <tbody>
                  {amp.map((r, i) => (
                    <tr key={i} className="border-t border-gray-50">
                      <td className="tnum py-1.5 text-gray-500">{r.ts}</td>
                      <td className="py-1.5 font-mono text-gray-600">{r.caller}</td>
                      <td className="py-1.5 text-gray-600">{r.action}</td>
                      <td className="tnum py-1.5 text-gray-500">{r.duration_ms != null ? `${r.duration_ms}ms` : "—"}</td>
                      <td className={`tnum py-1.5 font-medium ${r.code >= 200 && r.code < 400 ? "text-green-600" : "text-red-500"}`}>{r.code}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function FaceCard({ title, sub, r, loading }: { title: string; sub: string; r: FaceResult | null; loading: boolean }) {
  const isAip = title.includes("AIP");
  return (
    <div className={`card overflow-hidden border-t-2 p-5 ${isAip ? "border-t-primary-600" : "border-t-violet-500"}`}>
      <div className="flex items-baseline justify-between">
        <div className="flex items-center gap-2">
          <span className={`text-[14px] font-semibold ${isAip ? "text-primary-700" : "text-violet-600"}`}>{title}</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium leading-none ${isAip ? "bg-primary-50 text-primary-600" : "bg-violet-50 text-violet-600"}`}>
            {isAip ? "GB/Z 185" : "A2A"}
          </span>
        </div>
        <div className="text-[11px] text-gray-500">{sub}</div>
      </div>
      {!r && !loading && <div className="py-8 text-center text-[13px] text-gray-500">等待演示</div>}
      {loading && (
        <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-gray-500">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
          调用中…
        </div>
      )}
      {r && (
        <div className="mt-3">
          <div className="tnum flex gap-4 text-[12.5px] text-gray-500">
            <span>
              往返 <b className="font-semibold text-gray-800">{r.round_trips}</b> 次
            </span>
            <span>
              耗时 <b className="font-semibold text-gray-800">{(r.elapsed_ms / 1000).toFixed(1)}s</b>
            </span>
          </div>
          {r.error ? (
            <div className="mt-2 rounded-lg bg-red-50 p-3 text-[12px] leading-5 text-red-600">{r.error}</div>
          ) : (
            <pre className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-[12px] leading-5 text-gray-700">
              {r.result_text}
            </pre>
          )}
          <div className="mt-1.5 font-mono text-[10.5px] text-gray-500">{r.endpoint}</div>
        </div>
      )}
    </div>
  );
}
