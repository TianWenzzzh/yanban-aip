// 右栏四个面板共用的「研究进度」卡。
//
// 为什么要有它：四个面板在过去只有一行灰字可看（"聊到信息足够时…"），
// 而"到底还差什么才会去检索"这个信息后端每轮都算好了（gaps / 放行线 / 八态机），只是没地方显示。
// 于是新用户看到的不是一句承诺，而是缺 3 项、理解度多少、线画在哪——补哪一项由他自己决定。
//
// 纪律：这张卡不定义任何产品常量。八步的名字由调用方传（FLOW 只有一份），
// 放行线由 meta.ready_threshold 给（前端连注释都不抄那个数），缺口文案由 meta.gap_labels 给。
export type GapLabel = { field: string; label: string };

const pct = (v: number) => `${Math.round(v * 100)}%`;

export default function ProgressCard({ steps, flow, confidence, threshold, gaps, topic }: {
  steps: [string, string][];
  flow: string;
  /** 还没开口时是 null：那时没有任何真实数字可显示，卡上只讲流程，不假装在读什么。 */
  confidence: number | null;
  /** null = 事实已够 6 项，本来就不该拿线拦人（与后端 search_threshold 同义）。 */
  threshold: number | null;
  gaps: GapLabel[];
  topic: string;
}) {
  const started = confidence !== null;
  const at = steps.findIndex(([k]) => k === flow);
  // 已过线还说"就去查"是轮 153 看图抓到的：卡上同时印着理解度七成、状态已是最后一步，
  // 右边那句未来时只会读成"还没查"。线由 meta 给，这里只比大小，不抄数字。
  const past = threshold !== null && confidence !== null && confidence >= threshold;
  const lineState = threshold === null ? "信息够了，直接开检索"
    : past ? `已过 ${pct(threshold)} 的线` : `到 ${pct(threshold)} 就去查`;
  return (
    <div className="rounded-xl bg-white p-3 shadow-sm">
      <div className="flex items-baseline justify-between">
        <span className="text-[12.5px] font-medium text-gray-700">研究进度</span>
        <span className="text-[11px] text-gray-500">{started && at >= 0 ? `第 ${at + 1} / ${steps.length} 步` : "还没开始"}</span>
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        {steps.map(([k, label], i) => (
          <span key={k}
            className={`rounded-md px-1.5 py-0.5 text-[11px] ${
              i < at ? "bg-primary-100 text-primary-700" : i === at ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-600"}`}>
            {label}
          </span>
        ))}
      </div>

      {started ? (
        <>
          <div className="mt-2.5 flex items-center justify-between text-[11.5px]">
            <span className="text-gray-500">理解度 <b className="text-gray-800">{pct(confidence as number)}</b></span>
            <span className="text-gray-500">{lineState}</span>
          </div>
          <div className="relative mt-1 h-2 rounded-full bg-gray-100">
            <div className="h-2 rounded-full bg-primary-600 transition-all" style={{ width: pct(confidence as number) }} />
            {threshold !== null && (
              <div className="absolute -top-[2px] h-3 w-[2px] rounded bg-gray-500" style={{ left: pct(threshold as number) }} />
            )}
          </div>
        </>
      ) : (
        <div className="mt-2.5 text-[11.5px] leading-5 text-gray-500">
          说一句你想研究的东西，哪怕很模糊。研伴一边接话一边把研究处境补齐，凑够了就去查论文。
        </div>
      )}

      {gaps.length > 0 && (
        <div className="mt-2.5">
          <div className="label-caps text-[11px] text-gray-500">还差 {gaps.length} 项</div>
          <ul className="mt-1 space-y-1">
            {gaps.map(g => (
              <li key={g.field} className="flex items-center gap-1.5 text-[11.5px] text-gray-600">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />{g.label}
              </li>
            ))}
          </ul>
        </div>
      )}
      {topic && <div className="mt-2 text-[11.5px] text-gray-500">目前听到的主题：<b className="text-gray-700">{topic}</b></div>}
    </div>
  );
}
