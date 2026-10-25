import { useState } from "react";
import Markdown from "./Markdown";

export type Report = {
  title: string; body: string; words: number; papers: number; used_matrix: boolean;
  removed_titles: string[];
};

/** 「## 标题 + 正文」的粗切分；开头若有非标题内容（比如 [降级] 标记）记在 head="" 下。 */
function splitSections(body: string): { head: string; text: string }[] {
  const out: { head: string; text: string }[] = [];
  body.split(/^## /m).forEach((chunk, i) => {
    if (i === 0) {
      if (chunk.trim()) out.push({ head: "", text: chunk });
      return;
    }
    const nl = chunk.indexOf("\n");
    out.push({ head: chunk.slice(0, nl).trim(), text: nl < 0 ? "" : chunk.slice(nl + 1) });
  });
  return out;
}

const LOCKED = "引用论文";   // 这节是自动生成的双链清单，重写只会编出假链接

export default function ReportView({ report, onRewrite }: {
  report: Report;
  onRewrite: (section: string, instruction: string) => Promise<void>;
}) {
  const [hint, setHint] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");

  const run = async (head: string) => {
    setBusy(head);
    try { await onRewrite(head, hint[head] || ""); } finally { setBusy(""); }
  };

  return (
    <div className="report-view mt-2">
      {splitSections(report.body).map((s, i) => (
        <div key={i} className="mb-2.5" data-section={s.head}>
          {s.head && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[12.5px] font-semibold text-gray-700">{s.head}</span>
              {s.head !== LOCKED && (
                <>
                  <input
                    value={hint[s.head] || ""}
                    onChange={e => setHint(h => ({ ...h, [s.head]: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter" && !busy) void run(s.head); }}
                    placeholder="想怎么改这一节？可留空"
                    className="min-w-0 flex-1 rounded-lg border border-gray-200 px-2 py-0.5 text-[11.5px] outline-none focus:border-primary-400"
                  />
                  <button onClick={() => void run(s.head)} disabled={!!busy}
                    className="rounded-lg bg-gray-50 px-2 py-0.5 text-[11.5px] text-gray-600 hover:bg-gray-100 disabled:opacity-50">
                    {busy === s.head ? "改写中…" : "重写这节"}
                  </button>
                </>
              )}
            </div>
          )}
          {/* Markdown 组件自带 report-prose，这里不再套一层 */}
          <Markdown text={s.head ? `## ${s.head}\n${s.text}` : s.text} />
        </div>
      ))}
    </div>
  );
}
