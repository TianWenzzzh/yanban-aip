import { useCallback, useEffect, useState } from "react";

// 插件工具面板：关键词检索 / 翻译 / 思维导图（Mermaid 渲染）
type Tab = "search" | "translate" | "mindmap";

const API = "/companion";

export default function ToolsPanel({ projectId }: { projectId: string | null }) {
  // 没有项目时落在翻译上：检索和导图查的都是"这次研究里的东西"，默认停在那儿就是一个点了没反应的按钮
  const [tab, setTab] = useState<Tab>(projectId ? "search" : "translate");
  const [q, setQ] = useState("");
  const [searchOut, setSearchOut] = useState<{ papers: { title: string }[]; notes: { note: string }[]; memories: unknown[] } | null>(null);
  const [trIn, setTrIn] = useState("");
  const [trOut, setTrOut] = useState("");
  const [trDir, setTrDir] = useState<"zh" | "en">("zh");
  const [mermaid, setMermaid] = useState("");
  const [svg, setSvg] = useState("");
  const [minCit, setMinCit] = useState(0);
  const [onlyStatus, setOnlyStatus] = useState("");
  const [stats, setStats] = useState<{ shown: number; total: number; dropped: number; top_cited: number } | null>(null);

  const doSearch = useCallback(async () => {
    if (!projectId || !q.trim()) return;
    const r = await fetch(`${API}/search/keywords?project_id=${projectId}&q=${encodeURIComponent(q.trim())}`);
    if (r.ok) setSearchOut(await r.json());
  }, [projectId, q]);

  const doTranslate = useCallback(async () => {
    if (!trIn.trim()) return;
    const r = await fetch(`${API}/translate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: trIn, target: trDir }),
    });
    if (r.ok) {
      const d = await r.json();
      setTrOut(d.translation);
    }
  }, [trIn, trDir]);

  const loadMindmap = useCallback(async () => {
    if (!projectId) return;
    const r = await fetch(`${API}/map/mindmap/${projectId}?min_citations=${minCit}&only_status=${onlyStatus}`);
    if (r.ok) {
      const d = await r.json();
      setMermaid(d.mermaid);
      setStats({ shown: d.shown, total: d.total, dropped: d.dropped, top_cited: d.top_cited });
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
        const { svg } = await mermaid.render(`mm-${Date.now()}`, d.mermaid);
        setSvg(svg);
      } catch {
        setSvg("");
      }
    }
  }, [projectId, minCit, onlyStatus]);

  useEffect(() => { if (tab === "mindmap" && projectId) void loadMindmap(); }, [tab, projectId, loadMindmap]);

  const tabBtn = (t: Tab, label: string) => {
    const off = t !== "translate" && !projectId;
    return (
      <button onClick={() => !off && setTab(t)} disabled={off} title={off ? "先聊一句，这两个工具查的是这次研究里的东西" : undefined}
        className={`rounded-lg px-2.5 py-1 text-[11.5px] ${
          tab === t && !off ? "bg-primary-600 text-white" : off ? "bg-gray-50 text-gray-500" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
        {label}
      </button>
    );
  };

  return (
    <div className="rounded-xl bg-white p-3 shadow-sm">
      <div className="mb-2 flex gap-1.5">
        {tabBtn("search", "检索")}
        {tabBtn("translate", "翻译")}
        {tabBtn("mindmap", "导图")}
      </div>
      {tab === "search" && (
        <div className="space-y-2">
          <div className="flex gap-1.5">
            <input value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => e.key === "Enter" && doSearch()}
              placeholder="关键词跨库检索…"
              className="h-8 flex-1 rounded-lg border border-gray-200 px-2.5 text-[12px] outline-none focus:border-primary-400" />
            <button onClick={doSearch} className="h-8 rounded-lg bg-primary-600 px-3 text-[11.5px] text-white hover:bg-primary-700">搜</button>
          </div>
          {searchOut && (
            <div className="space-y-1 text-[11.5px] text-gray-600">
              {searchOut.papers.map((p, i) => <div key={i} className="truncate">📄 {p.title}</div>)}
              {searchOut.notes.map((n, i) => <div key={i} className="truncate">📝 {n.note}</div>)}
              {searchOut.papers.length === 0 && searchOut.notes.length === 0 && <div className="text-gray-500">无命中</div>}
            </div>
          )}
        </div>
      )}
      {tab === "translate" && (
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-[11.5px] text-gray-500">
            <button onClick={() => setTrDir("zh")} className={`rounded px-2 py-0.5 ${trDir === "zh" ? "bg-primary-100 text-primary-700" : "bg-gray-100 text-gray-600"}`}>→中文</button>
            <button onClick={() => setTrDir("en")} className={`rounded px-2 py-0.5 ${trDir === "en" ? "bg-primary-100 text-primary-700" : "bg-gray-100 text-gray-600"}`}>→EN</button>
          </div>
          <textarea value={trIn} onChange={e => setTrIn(e.target.value)}
            placeholder="粘贴要翻译的段落…"
            className="h-16 w-full resize-y rounded-lg border border-gray-200 p-2 text-[12px] outline-none focus:border-primary-400" />
          <button onClick={doTranslate} className="h-8 w-full rounded-lg bg-primary-600 text-[12px] text-white hover:bg-primary-700">翻译</button>
          {trOut && <div className="rounded-lg bg-gray-50 p-2 text-[12px] leading-5 text-gray-700">{trOut}</div>}
        </div>
      )}
      {tab === "mindmap" && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-gray-500">
            <span>权重=被引</span>
            <select value={minCit} onChange={e => setMinCit(Number(e.target.value))}
              className="rounded-lg border border-gray-200 px-1.5 py-0.5 outline-none">
              <option value={0}>全部论文</option>
              <option value={50}>被引 ≥50</option>
              <option value={150}>被引 ≥150</option>
            </select>
            <select value={onlyStatus} onChange={e => setOnlyStatus(e.target.value)}
              className="rounded-lg border border-gray-200 px-1.5 py-0.5 outline-none">
              <option value="">所有状态</option>
              <option value="recommended">只看推荐</option>
              <option value="favorite">只看收藏</option>
              <option value="read">只看已读</option>
            </select>
            {stats && (
              <span className="text-gray-500">
                图上 {stats.shown}/{stats.total} 篇{stats.dropped ? `，滤掉 ${stats.dropped} 篇` : ""}
                {stats.top_cited ? `，最高被引 ${stats.top_cited}` : ""}
              </span>
            )}
            <button onClick={loadMindmap}
              className="ml-auto h-7 rounded-lg bg-primary-50 px-2.5 text-[11.5px] text-primary-700 hover:bg-primary-100">
              重新生成
            </button>
          </div>
          {svg ? <div className="overflow-auto rounded-lg bg-white p-2" dangerouslySetInnerHTML={{ __html: svg }} />
            : <div className="rounded-lg bg-gray-50 p-2 font-mono text-[10.5px] leading-4 text-gray-500">{mermaid || "点击生成后展示 Mermaid 源码"}</div>}
        </div>
      )}
    </div>
  );
}
