// 「上次交代的事」跟进卡：把研伴上一轮给过的行动清单与它的真实状态摆在对话旁边。
//
// 为什么要有它：规划阶段每轮都产出了带「完成判据」的动作，但这些字说完就散了——
// 下一次见面时 agent 认不出自己交代过什么，学生也得从头讲。导师不是这样的：
// 他记得上次留的活，也会在你回来说"做完了"的时候把那一格划掉。
//
// 纪律：这张卡不自己定文案。状态名由后端 commitments.STATUS_LABELS 给（meta.commitments 带过来），
// 时间说法由后端 _age_phrase 给，卡上没有任何一处把产品常量抄第二遍。
export type CommitmentItem = { do: string; check: string; status: string };

export type Ledger = {
  situation: string;
  age_phrase: string;
  age_hours: number;
  artifact: string;
  open: number;
  total: number;
  items: CommitmentItem[];
  labels: Record<string, string>;
};

const MARK: Record<string, string> = { done: "✓", blocked: "!", dropped: "—", open: "○" };

export default function CommitmentCard({ ledger, onReport, onAssumption, variant = "panel" }: {
  ledger: Ledger | null;
  onReport: (index: number, status: string) => void;
  /** 学生对「它这么理解我」表态：同意或纠正。认知强制函数要有可点的东西，不然只是解释。 */
  onAssumption: (agree: boolean) => void;
  /** panel = 右栏（宽屏）；inline = 对话流末尾（窄屏）。同一份状态、同一个组件，只是摆位不同。
      390px 实测右栏收起后这张卡完全不可见——跟进是这条改动的全部意义，看不见等于没有。 */
  variant?: "panel" | "inline";
}) {
  if (!ledger || !ledger.items.length) return null;
  const shell = variant === "inline" ? "lg:hidden" : "hidden lg:block";
  return (
    <div className={`rounded-xl bg-white p-3 shadow-sm ${shell}`} data-testid="commitment-card"
      data-variant={variant}>
      <div className="flex items-baseline justify-between">
        <span className="text-[12.5px] font-medium text-gray-700">上次交代的事</span>
        <span className="text-[11px] text-gray-500">{ledger.age_phrase} · {ledger.total - ledger.open}/{ledger.total} 已结</span>
      </div>
      {ledger.situation && (
        <div className="mt-1.5 rounded-lg bg-gray-50 px-2 py-1.5">
          <div className="label-caps text-[11px] text-gray-500">它这么理解你</div>
          <div className="text-[11.5px] leading-5 text-gray-600">{ledger.situation}</div>
          <div className="mt-1 flex gap-1">
            <button type="button" onClick={() => onAssumption(true)}
              className="rounded-md border border-gray-200 px-1.5 py-0.5 text-[11px] text-gray-500 hover:bg-white">
              理解对了
            </button>
            <button type="button" onClick={() => onAssumption(false)}
              className="rounded-md border border-gray-200 px-1.5 py-0.5 text-[11px] text-gray-500 hover:bg-white">
              不太对，我补一句
            </button>
          </div>
        </div>
      )}
      <ul className="mt-1.5 space-y-1.5">
        {ledger.items.map((it, i) => {
          const label = ledger.labels[it.status] || it.status;
          return (
            <li key={`${i}-${it.do}`} className="text-[11.5px] leading-5">
              <div className="flex items-start gap-1.5">
                <span aria-hidden
                  className={`mt-[3px] inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full text-[9px] ${
                    it.status === "done" ? "bg-emerald-100 text-emerald-700"
                      : it.status === "open" ? "bg-amber-100 text-amber-700"
                        : "bg-gray-100 text-gray-600"}`}>{MARK[it.status] || "○"}</span>
                <span className={`flex-1 ${it.status === "open" ? "text-gray-700" : "text-gray-500"}`}>
                  {it.do}
                  {it.status === "open" && it.check && (
                    <span className="text-gray-500">（判据：{it.check}）</span>
                  )}
                </span>
                {/* 结掉的那条要把"怎么结的"写出来：✓ / ! / — 三种字形加颜色差别，
                    屏幕阅读器读不到（glyph 是 aria-hidden），学生也只看得见一行灰字。
                    文案仍是后端那份 labels，前端不抄第二份。 */}
                {it.status !== "open" && (
                  <span className="shrink-0 text-[10.5px] text-gray-500">{label}</span>
                )}
              </div>
              {it.status === "open" && (
                <div className="mt-1 flex gap-1 pl-5">
                  {/* 按钮集合也来自后端那份 labels：前端再抄一份 ["done","blocked","dropped"]，
                      产品加一个状态时这里就会静默少一个按钮（文案不抄、集合也得不抄）。 */}
                  {Object.keys(ledger.labels).filter(s => s !== "open").map(s => (
                    <button key={s} type="button"
                      onClick={() => onReport(i, s)}
                      className="rounded-md border border-gray-200 px-1.5 py-0.5 text-[11px] text-gray-500 hover:bg-gray-50">
                      {ledger.labels[s]}
                    </button>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {ledger.artifact && (
        <div className="mt-2 text-[11px] text-gray-500">这一段要留下的东西：<span className="text-gray-600">{ledger.artifact}</span></div>
      )}
    </div>
  );
}
