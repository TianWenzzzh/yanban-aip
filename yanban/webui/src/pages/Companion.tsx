import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "../components/Markdown";
import ProgressCard, { type GapLabel } from "../components/ProgressCard";
import CommitmentCard, { type Ledger } from "../components/CommitmentCard";
import ReportView from "../components/ReportView";
import MemoryPanel from "./MemoryPanel";
import ToolsPanel from "./ToolsPanel";

// 研伴 2.0 认知层主界面（/companion）：流式对话 + 论文卡 + 知识库 + 导图 + PDF Drawer
interface Intent {
  topic: string;
  research_stage: string;
  confidence: number;
  user_goal: string;
  // 后端 meta 事件带的是 ResearchIntent.model_dump()，extra 一直在里面（provider_outage / search_error 都挂这）
  extra?: Record<string, unknown>;
}
interface Paper {
  id?: string;
  title: string;
  year?: number;
  venue?: string;
  doi?: string;
  url?: string;
  citation_count?: number;
  open_access?: boolean;
  abstract?: string;
  tldr?: string;
  recommendation_reason?: string[];
  status?: string;
  user_feedback?: string;
}
interface Msg {
  role: string;
  text: string;
  /** 闸门改写前的那一版：append-only 地留着，不原地覆盖（学生可能已经读到了旧版） */
  superseded?: string;
  supersedeNote?: string;
  degraded?: boolean;
  /** 这一条回复的「文献从哪来」提示；declined=学生自己勾掉了检索，failure=查不动。两者不能长成一个样子 */
  searchNote?: { text: string; declined: boolean };
  error?: boolean;
  streaming?: boolean;
}
interface NoteMeta {
  note: string;
  bytes: number;
  links: string[];
  head: string;
}

/** 检索意图卡的数据：全部由后端 /intent/plan 给出（检索式尤其不许前端自己拼）。 */
interface SearchPlan {
  topic: string;
  query: string;
  year_from: number | null;
  open_access: boolean;
  changed?: string[];
}
interface PlanDraft { topic: string; year: string; openAccess: boolean }
const EMPTY_DRAFT: PlanDraft = { topic: "", year: "", openAccess: false };
const toDraft = (p: SearchPlan): PlanDraft => ({
  topic: p.topic || "",
  year: p.year_from == null ? "" : String(p.year_from),
  openAccess: !!p.open_access,
});
/** 年份留空＝不改；给了就必须是四位数字。同一把尺同时管「记下」与「启动」两颗按钮，
 *  否则会出现"点了启动没反应"——CDP 实测到的一次：上一格非法年份留在框里，启动就静默不发送。 */
const yearValid = (y: string) => !y.trim() || /^\d{4}$/.test(y.trim());

const API = "/companion";
const EXAMPLES = [
  { title: "我想研究钓鱼网站检测", sub: "第一次做科研，不知道从哪儿开始" },
  { title: "帮我把多智能体的文献梳理成综述提纲", sub: "已经有二十来篇在读" },
  { title: "分析这批 CSV 实验数据的相关性", sub: "不确定该用什么统计方法" },
];

// 回复里的 [1] [2] 变成可点的锚点，点一下跳到右栏对应论文卡。
// 🔴 链接文字必须把方括号留下（`\[n\]`）：markdown 的链接文字吃掉方括号后，
// 一句末尾的 [1][2] 会在屏上连读成「12」——多头引用糊成一个数，读者分不清是几颗角标（轮 202 看图抓到）。
const linkifyCitations = (t: string) => t.replace(/\[(\d{1,2})\]/g, "[\\[$1\\]](#paper-$1)");

/** 用户点的那颗角标所在的那一句。
 *  不能拿"正文里第一条含 [n] 的句子"当 claim：一条回复里同一个编号出现两次时
 *  （一句引对、一句其实没依据），第二次的依据会被错算到第一次头上——那正是这格要防的错配。
 *  句末标点与 mentor.SENT_SPLIT 同一套（。！？ 加半角 !?），两边口径不一致就会各切各的句。
 *  🔴 往两边要**跨节点**扩到句末标点，不能只读紧邻的那一个兄弟节点：
 *  "…这组数据 [1][2]。" 这种多头引用里两颗角标之间没有文本节点，旧写法把 claim 收成 "2。"，
 *  等于拿一句空话去问后端"这句有没有依据"——实测（轮 202，输出/_logs/r202_cite_runA_before_fix.log
 *  的 C10/C11）那一格会对**真有依据**的句子报「这句目前没有文献支撑」，是反方向的假诚实。 */
const sentenceOfAnchor = (a: Element, fallback: string): string => {
  const SENT = /[。！？!?]/;
  let head = "";
  for (let n = a.previousSibling, k = 0; n && k < 40 && !SENT.test(head); n = n.previousSibling, k++) {
    head = (n.textContent || "") + head;
  }
  let tail = "";
  for (let n = a.nextSibling, k = 0; n && k < 40 && !SENT.test(tail); n = n.nextSibling, k++) {
    tail += n.textContent || "";
  }
  const joined = head + (a.textContent || "") + tail;
  let start = 0;
  for (const part of joined.split(/(?<=[。！？!?])/)) {
    const end = start + part.length;
    if (head.length >= start && head.length < end) return part.trim().slice(0, 300);
    start = end;
  }
  return fallback.slice(0, 300);
};

/** 八态对话状态机——后端按转移表推进，这里把它显出来，不只在文档里宣称。 */
const FLOW: [string, string][] = [
  ["DISCOVER", "了解"], ["CLARIFY", "追问"], ["FORMULATE", "组织检索"], ["SEARCH", "检索"],
  ["SCREEN", "筛选"], ["READ", "精读"], ["REFINE", "收敛"], ["DELIVER", "交付"],
];

/** 阶段中文名取自 model.RESEARCH_STAGES（经 meta.stage_labels 带上），这里不另建一份表。
 *  unknown 不在那张表里（它是「还没定」而不是一个真阶段），所以在这里给一句人话。 */
const stageLabel = (s: string, labels: Record<string, string>) =>
  labels[s] || (s === "unknown" ? "还没定" : s);

export default function Companion() {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [intent, setIntent] = useState<Intent | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"papers" | "knowledge" | "map" | "memory">("papers");
  const [yearFrom, setYearFrom] = useState(0);
  const [oaOnly, setOaOnly] = useState(false);
  const [citMin, setCitMin] = useState(0);
  const [sortBy, setSortBy] = useState<"relevance" | "citations" | "year">("relevance");
  // U14：写侧的 rejected 态早就有，缺的是"看得见 + 撤得回"。这批只能问后端要
  // （?status=rejected）：papers 里那批换一轮 done 事件就可能不再回来，前端自己从 papers 数会数出 0。
  const [excluded, setExcluded] = useState<Paper[]>([]);
  const [showExcluded, setShowExcluded] = useState(false);
  const loadExcluded = useCallback(async () => {
    if (!projectId) { setExcluded([]); return; }
    try {
      const r = await fetch(`${API}/paper/${projectId}?status=rejected`);
      setExcluded(r.ok ? (((await r.json()).papers ?? []) as Paper[]) : []);
    } catch { setExcluded([]); }
  }, [projectId]);
  useEffect(() => { loadExcluded(); }, [loadExcluded]);
  const [drawerPaper, setDrawerPaper] = useState<Paper | null>(null);
  // 点角标时回连"这句的原文依据"（对标 Scite / Elicit 的引用上下文抽屉）。
  // status 由后端 /cite/context 判（grounded｜misnumbered｜true_gap），前端不自己推——
  // 一份事实两份口径迟早各说一份。
  const [citeEvidence, setCiteEvidence] = useState<
    { i: number; claim: string; status: string;
      spans: { text: string; source: string; found?: boolean; page?: number | null;
               section?: string | null }[]; note: string;
      supporter?: { idx: number; title: string; text: string; source: string; stance: string } | null;
      survey?: { checked: number; support: number; against: number } } | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [drawerPage, setDrawerPage] = useState<number | null>(null);
  const [drawerQa, setDrawerQa] = useState("");
  const [sections, setSections] = useState<{ name: string; chars: number }[]>([]);
  const [activeSection, setActiveSection] = useState("");
  const lastUserText = useRef("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  // 🔴 以前每条新字都无条件 scrollIntoView：学生在流式输出中途往上翻旧内容，会被一路拽回底部，
  //    等于"不许回看"。现在只在**本来就贴着底部**时跟随；往上翻了就给一颗"跳到最新"。
  const atBottomRef = useRef(true);
  const [offBottom, setOffBottom] = useState(false);
  // 🔴 量"离底部多远"必须先问**到底是谁在滚**：消息区自己有 `overflow-y-auto`，但它只有在
  //    内容高出可视高度时才真的滚；这份布局在多数视口下是整页在滚（消息区被内容撑开，
  //    clientHeight == scrollHeight ⇒ 内部永远"贴着底"）。只挂 log 的 onScroll 就是条死判据。
  const gapToBottom = () => {
    const el = logRef.current;
    if (el && el.scrollHeight - el.clientHeight > 24) return el.scrollHeight - el.scrollTop - el.clientHeight;
    const de = document.documentElement;
    return de.scrollHeight - window.scrollY - de.clientHeight;
  };
  const onLogScroll = () => {
    atBottomRef.current = gapToBottom() < 80;
    setOffBottom(!atBottomRef.current);
  };
  const jumpLatest = () => {
    atBottomRef.current = true;
    setOffBottom(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  };
  useEffect(() => {
    window.addEventListener("scroll", onLogScroll, { passive: true });
    return () => window.removeEventListener("scroll", onLogScroll);
  }, []);
  useEffect(() => {
    if (atBottomRef.current) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const [fallback, setFallback] = useState(false);
  // 顶部「证据底座」：这些数全部来自 meta/done 的 evidence_basis，前端不自己算、不抄常量。
  // null = 程序这一轮没这个数（显示"未记录"）；0 = 有，且确实是零。两者必须长得不一样。
  const [basis, setBasis] = useState<Record<string, number | null> | null>(null);
  // 检索意图卡（对标 U3）：plan 由后端 /intent/plan 给，query 绝不在前端拼。
  // 打开卡与「记下这个范围」都只读写 intent——一次检索都不发，这一点由 tests2/test_search_intent_card.py 钉住。
  const [plan, setPlan] = useState<SearchPlan | null>(null);
  const [draft, setDraft] = useState<PlanDraft>(EMPTY_DRAFT);
  const [planOpen, setPlanOpen] = useState(false);
  const [planBusy, setPlanBusy] = useState(false);
  const [planNote, setPlanNote] = useState("");
  const [lowRel, setLowRel] = useState<{ on: boolean; dropped: number }>({ on: false, dropped: 0 });
  const [steps, setSteps] = useState<{ id: string; label: string; status: string; count?: number }[]>([]);
  // composer 上方「这一步会做什么」清单（对标 U11）：文案、调用哪个工具、哪颗可勾掉、勾掉叫什么，
  // 全部来自后端 /chat/steps——前端自己抄一份就是第二个真相。
  const [toolSteps, setToolSteps] = useState<
    { id: string; label: string; tool: string; optional?: boolean; decline_label?: string }[]>([]);
  const [noSearch, setNoSearch] = useState(false);
  const [chips, setChips] = useState<{ field: string; question: string; options: { label: string; say: string }[] }[]>([]);
  const [suggests, setSuggests] = useState<{ label: string; say: string }[]>([]);
  const [usedMem, setUsedMem] = useState<{ source: string; tag: string; event_id?: number | null;
    text: string; score?: number }[]>([]);
  const [blocked, setBlocked] = useState<string[]>([]);
  // 「看这一条」的落点：记忆面板要高亮的那条 events 行 id（聊天行没有 id ⇒ 那颗按钮不出现，不拿 0 冒充）
  const [memFocus, setMemFocus] = useState<number | null>(null);
  const [flow, setFlow] = useState("DISCOVER");
  // 右栏进度卡要的三样，全部来自 meta 事件（后端派生，前端不定义常量）
  const [gapLabels, setGapLabels] = useState<GapLabel[]>([]);
  const [readyThreshold, setReadyThreshold] = useState<number | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);   // 上一轮交代的事（后端真记着的那份）
  const [stageLabels, setStageLabels] = useState<Record<string, string>>({});
  // null = 还没读到（面板没打开过）；0 = 读到了、确实是空的。两者在 tab 上必须长得不一样。
  const [noteCount, setNoteCount] = useState<number | null>(null);
  const [memCount, setMemCount] = useState<number | null>(null);
  const [fbEffects, setFbEffects] = useState<string[]>([]);
  const [showMatrix, setShowMatrix] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);   // 窄屏时右栏默认收起，否则会压住输入框
  const [matrixCols, setMatrixCols] = useState<{ label: string }[]>([]);
  const [newCol, setNewCol] = useState("");
  const [matrixBusy, setMatrixBusy] = useState(false);
  const [matrixErr, setMatrixErr] = useState("");
  const [matrix, setMatrix] = useState<{
    columns: string[][]; rows: {
      id: string; title: string; year?: number; basis?: string;
      cells: Record<string, string>; evidence?: Record<string, string>;
    }[];
    cached?: boolean; note?: string; fulltext_rows?: number;
  } | null>(null);

  const loadMatrix = async (extra: { label: string }[]) => {
    if (!projectId) return;
    setMatrixBusy(true); setMatrixErr("");
    try {
      const r = await fetch(`${API}/literature/${projectId}/matrix`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ columns: extra }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
      setMatrix(await r.json());
    } catch (e) { setMatrixErr(`对比表没抽出来：${(e as Error).message}`); }
    finally { setMatrixBusy(false); }
  };

  const addMatrixCol = () => {
    const label = newCol.trim();
    if (!label || matrixCols.length >= 3) return;
    const next = [...matrixCols, { label }];
    setMatrixCols(next); setNewCol(""); loadMatrix(next);
  };

  const [report, setReport] = useState<{
    title: string; body: string; words: number; papers: number; used_matrix: boolean;
    removed_titles: string[];
  } | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportErr, setReportErr] = useState("");
  const [secMsg, setSecMsg] = useState("");
  const [reportDraft, setReportDraft] = useState("");
  const [reportStep, setReportStep] = useState("");

  const makeReport = async () => {
    if (!projectId) return;
    setReportBusy(true); setReportErr(""); setReportDraft(""); setReportStep("");
    try {
      const r = await fetch(`${API}/report/${projectId}/stream`, { method: "POST" });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const rd = r.body.getReader(), dec = new TextDecoder();
      let buf = "", evt = "", acc = "";
      for (;;) {
        const { done, value } = await rd.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) {
            if (line.startsWith("event:")) evt = line.slice(6).trim();
            continue;
          }
          const d = JSON.parse(line.slice(5).trim());
          if (evt === "delta") { acc += d.text || ""; setReportDraft(acc); }
          else if (evt === "step") setReportStep(d.status === "doing" ? d.label : "");
          else if (evt === "done") { setReport(d); if (d.state) setFlow(d.state); }
          else if (evt === "error") throw new Error(d.note || "生成失败");
        }
      }
    } catch (e) { setReportErr(`综述没写成：${(e as Error).message}`); }
    finally { setReportBusy(false); setReportDraft(""); setReportStep(""); }
  };

  // 写之前先讨论（对标 U10）：问题、选项、每颗选项会改哪一节，全部由后端给——前端一份都不抄，
  // 抄一份就会在后端加选项时变成两套各说各话的口径。
  const [brief, setBrief] = useState<{
    questions: { key: string; text: string; options: { value: string; label: string; effect: string }[] }[];
    answers: Record<string, string>; answered: boolean; skipped: boolean; recorded: boolean;
  } | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [briefChoice, setBriefChoice] = useState<Record<string, string>>({});
  const [briefErr, setBriefErr] = useState("");

  const openBrief = async () => {
    if (!projectId) return;
    setBriefErr("");
    try {
      const r = await fetch(`${API}/report/${projectId}/brief`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setBrief(d); setBriefChoice({ ...(d.answers || {}) }); setBriefOpen(true);
    } catch (e) { setBriefErr(`简报没取到：${(e as Error).message}`); }
  };

  // skip=true 也要发到后端记账：没问与"问了但跳过"是两种状态，不能都演成"学生说过不用问"。
  const sendBrief = async (skip: boolean) => {
    if (!projectId) return;
    setBriefErr("");
    try {
      const r = await fetch(`${API}/report/${projectId}/brief`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(skip ? { skip: true } : { answers: briefChoice }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
      setBrief(await r.json()); setBriefOpen(false);
      await makeReport();
    } catch (e) { setBriefErr(`这两条没记上：${(e as Error).message}`); }
  };

  const rewriteSection = async (section: string, instruction: string) => {
    if (!projectId || !report) return;
    setSecMsg("");
    try {
      const r = await fetch(`${API}/report/${projectId}/section`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: report.title, section, instruction }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
      const d = await r.json();
      setReport({ ...report, body: d.body, words: d.words, removed_titles: d.removed_titles });
      setSecMsg(`「${section}」已重写` +
        (d.removed_titles.length ? `，剔除 ${d.removed_titles.length} 处论文列表之外的标题` : ""));
    } catch (e) { setSecMsg(`这一节没改成：${(e as Error).message}`); }
  };
  // 有 outage 时优先报「源挂了」，别让下面那条英译兜底提示抢着解释原因
  const outage = String((intent?.extra as Record<string, unknown> | null)?.provider_outage || "");
  const abortRef = useRef<AbortController | null>(null);
  // 检索不可用挂在「这一条回复」上，不是全局横幅：下一轮可能就好了
  const searchErrRef = useRef<string>("");
  const searchDeclinedRef = useRef(false);

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetch(`${API}/chat/steps`);
        if (r.ok) setToolSteps((await r.json()).steps || []);
      } catch { /* 拿不到就不显示这份清单，不摆一套自己编的步骤 */ }
    })();
  }, []);

  const absorb = useCallback((d: {
    project_id?: string; intent?: Intent; papers?: Paper[]; query_fallback?: boolean;
    low_relevance?: boolean; off_topic_dropped?: number; state?: string; search_error?: string;
    search_note?: string;
    gap_labels?: GapLabel[]; ready_threshold?: number | null; stage_labels?: Record<string, string>;
    commitments?: Ledger | null; evidence_basis?: Record<string, number | null> | null;
  }) => {
    if (d.project_id) setProjectId(d.project_id);
    if (d.intent) setIntent(d.intent);
    if (d.papers?.length) setPapers(d.papers);
    if (d.evidence_basis) setBasis(d.evidence_basis);
    if (d.query_fallback !== undefined) setFallback(Boolean(d.query_fallback));
    if (d.low_relevance !== undefined) setLowRel({ on: Boolean(d.low_relevance), dropped: d.off_topic_dropped || 0 });
    // 两个键分开是刻意的：search_error=查不动，search_note=你勾掉了。合成一个就分不清
    // 「系统出事了」和「学生不许系统查」——后者不该显示成红色的失败。
    if (d.search_error !== undefined) {
      searchErrRef.current = String(d.search_error || ""); searchDeclinedRef.current = false;
    }
    if (d.search_note !== undefined) {
      searchErrRef.current = String(d.search_note || ""); searchDeclinedRef.current = true;
    }
    if (d.state) setFlow(d.state);
    // 放行那一轮后端给的是空缺口：这里必须跟着清空，否则进度卡会一直挂着上一轮的「还差 3 项」
    if (d.gap_labels) setGapLabels(d.gap_labels);
    if (d.ready_threshold !== undefined) setReadyThreshold(d.ready_threshold);
    if (d.commitments !== undefined) setLedger(d.commitments);
    if (d.stage_labels) setStageLabels(d.stage_labels);
  }, []);

  // 闸门改写的原因，由 amend 事件里的真实计数派生（前端不自己数一遍）
  const amendNote = (d: { removed_titles?: string[]; question_count?: number }) => {
    const bits: string[] = [];
    if (d.removed_titles?.length) bits.push(`删了 ${d.removed_titles.length} 处查无依据的论文名`);
    if (typeof d.question_count === "number" && d.question_count > 1) bits.push("合并了多余的问题");
    return bits.join("、") || "按核对结果改写过";
  };

  const takeSearchNote = useCallback(() => {
    const v = searchErrRef.current, dec = searchDeclinedRef.current;
    searchErrRef.current = ""; searchDeclinedRef.current = false;
    return v ? { text: v, declined: dec } : undefined;
  }, []);
  const pushAssistant = useCallback((text: string, extra: Partial<Msg> = {}) => {
    setMessages(m => {
      const last = m[m.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        // 流式期间保持 streaming:true，否则下一个 delta 就不再替换、而是又开一个气泡
        return [...m.slice(0, -1), { ...last, text, ...extra, streaming: extra.streaming ?? true }];
      }
      return [...m, { role: "assistant", text, streaming: false, ...extra }];
    });
  }, []);

  const runTurn = useCallback(async (text: string) => {
    setMessages(m => [...m, { role: "user", text }, { role: "assistant", text: "", streaming: true }]);
    setBusy(true);
    // 发送前那份清单直接变成进度条：勾掉的那一步一上来就是 skipped，不装成在跑
    setSteps(toolSteps.map((s, i) => ({
      id: s.id, label: s.label,
      status: (s.optional && noSearch) ? "skipped" : (i === 0 ? "doing" : "pending"),
    })));
    setChips([]); setSuggests([]); setUsedMem([]); setFbEffects([]);
    let got = "";
    let superseded = "";
    let supersedeNote = "";
    let stopped = false;
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const r = await fetch(`${API}/chat/stream`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_text: text, project_id: projectId, no_search: noSearch }),
        signal: ac.signal,
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "", evt = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("event:")) evt = line.slice(6).trim();
          else if (line.startsWith("data:")) {
            const data = JSON.parse(line.slice(5).trim());
            if (evt === "delta") { got += data.text || ""; pushAssistant(got); }
            else if (evt === "amend") {
              if (data.reply && data.reply !== got) { superseded = got; supersedeNote = amendNote(data); }
              got = data.reply || got;
            }
            else if (evt === "step") {
              setSteps(prev => {
                const row = { id: data.id, label: data.label, status: data.status, count: data.count };
                const at = prev.findIndex(s => s.id === data.id);
                // 原位更新：把它们搬到队尾会让发送前那份清单在进度中途重排一次
                return at < 0 ? [...prev, row] : prev.map((s, i) => (i === at ? { ...s, ...row } : s));
              });
            }
            else if (evt === "meta" || evt === "done") {
              absorb(data);
              if (data.reply && data.reply !== got) {
                superseded = superseded || got;
                supersedeNote = supersedeNote || amendNote(data);
                got = data.reply;
              }
              if (data.chips) setChips(data.chips);
              if (data.suggestions) setSuggests(data.suggestions.filter((s: { say: string }) => s.say));
              if (data.used_memory) setUsedMem(data.used_memory);
              if (data.feedback_effects) setFbEffects(data.feedback_effects);
            }
          }
        }
      }
      pushAssistant(got || "（这次没答上来，你再说一遍？）",
        { degraded: got.startsWith("[降级]"), searchNote: takeSearchNote(), streaming: false,
          superseded, supersedeNote });
    } catch (err) {
      if (ac.signal.aborted) {
        stopped = true;
        pushAssistant(got ? got + " …（已停止）" : "（已停止）", { streaming: false });
      } else {
        try {
          const r = await fetch(`${API}/chat`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_text: text, project_id: projectId, no_search: noSearch }),
          });
          const d = await r.json();
          absorb(d);
          if (d.chips) setChips(d.chips);
          if (d.suggestions) setSuggests(d.suggestions.filter((s: { say: string }) => s.say));
          pushAssistant(d.reply || "", {
            degraded: String(d.reply || "").startsWith("[降级]"), searchNote: takeSearchNote(), streaming: false });
        } catch {
          pushAssistant("网络异常，研伴没能连上服务。", { error: true, streaming: false });
        }
      }
    } finally {
      if (!stopped) setSteps(prev => prev.map(s => (s.status === "skipped" ? s : { ...s, status: "done" })));
      setBusy(false);
      abortRef.current = null;
    }
  }, [projectId, absorb, pushAssistant, noSearch, toolSteps]);

  const answerWith = useCallback((say: string) => {
    if (!say || busy) return;
    setInput("");
    lastUserText.current = say;
    void runTurn(say);
  }, [busy, runTurn]);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    lastUserText.current = text;
    void runTurn(text);
  }, [input, busy, runTurn]);

  const retry = useCallback(() => {
    if (lastUserText.current && !busy) void runTurn(lastUserText.current);
  }, [busy, runTurn]);

  const loadPlan = useCallback(async (pid: string) => {
    try {
      const r = await fetch(`${API}/intent/plan/${pid}`);
      if (!r.ok) return;
      const d: SearchPlan = await r.json();
      setPlan(d); setDraft(toDraft(d));
    } catch { /* 卡读不到就干脆不摆，别摆一张空白卡让人以为能改 */ }
  }, []);
  // intent 每轮都会被 meta/done 换掉一次，卡跟着重读；新开场没有主题时 plan.topic 为空 ⇒ 不显示
  useEffect(() => { if (projectId) void loadPlan(projectId); }, [projectId, intent, loadPlan]);

  const savePlan = useCallback(async () => {
    if (!projectId || planBusy) return false;
    const y = draft.year.trim();
    if (!yearValid(y)) { setPlanNote("起始年份得是四位数字（比如 2022），留空表示不改"); return false; }
    setPlanBusy(true);
    try {
      const r = await fetch(`${API}/intent/plan/${projectId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: draft.topic.trim(), year_from: y, open_access: draft.openAccess }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: SearchPlan = await r.json();
      setPlan(d); setDraft(toDraft(d));
      setPlanNote(d.changed?.join("、")
        ? `已记下：${d.changed.join("、")}（这步没有去查，下一轮才按新范围查）`
        : "这几颗没改动，范围还是原来那份");
      return true;
    } catch (e) { setPlanNote(`范围没记上：${(e as Error).message}`); return false; }
    finally { setPlanBusy(false); }
  }, [projectId, draft, planBusy]);

  const startSearch = useCallback(async () => {
    if (await savePlan()) send();
  }, [savePlan, send]);

  const openPdf = useCallback(async (p: Paper, page: number | null = null) => {
    setDrawerPaper(p);
    setPdfUrl(null);
    setDrawerPage(page);
    setSections([]); setActiveSection("");
    try {
      const r = await fetch(`${API}/paper/fetch-pdf`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId, paper: p }),
      });
      if (!r.ok) { setPdfUrl("__none__"); return; }
      const d = await r.json();
      setPdfUrl(d.pdf_url);
      if (d.state) setFlow(d.state);
      const fn = String(d.pdf_url || "").split("/").pop();
      if (fn) {
        try {
          const s = await fetch(`${API}/paper/${projectId}/${encodeURIComponent(fn)}/sections`);
          if (s.ok) setSections((((await s.json()).sections) || []).slice(0, 12));
        } catch { setSections([]); }
      }
    } catch { setPdfUrl("__none__"); }
  }, [projectId]);

  const feedback = useCallback(async (p: Paper, status: string) => {
    if (!projectId) return;
    const reason = status === "rejected" ? window.prompt("为什么不感兴趣？（可留空）") || "" : "";
    const r = await fetch(`${API}/paper/feedback`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, paper: p, status, reason }),
    });
    if (r.ok) { const d = await r.json(); if (d.state) setFlow(d.state); void loadExcluded(); }
    setPapers(ps => ps.map(x => (x.id === p.id ? { ...x, status, user_feedback: reason } : x)));
  }, [projectId, loadExcluded]);

  const drawerQaAsk = useCallback(async () => {
    if (!drawerPaper || !drawerQa.trim() || !pdfUrl || pdfUrl === "__none__") return;
    const q = drawerQa.trim();
    const scope = activeSection || "全文";
    setMessages(m => [...m, { role: "user", text: `〔${drawerPaper.title.slice(0, 24)} · ${scope}〕${q}` }]);
    setDrawerQa("");
    setBusy(true);
    try {
      const r = await fetch(`${API}/paper/${projectId}/qa`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, filename: pdfUrl.split("/").pop() || "", section: activeSection }),
      });
      if (r.ok) {
        const d = await r.json();
        pushAssistant(`**结合「${scope}」**：${d.answer}`, { streaming: false });
      } else {
        pushAssistant("这一节没能问出结果，换一个章节或问全文试试。", { error: true, streaming: false });
      }
    } catch {
      pushAssistant("网络异常，这次问答没送出去。", { error: true, streaming: false });
    } finally { setBusy(false); }
  }, [drawerPaper, drawerQa, pdfUrl, projectId, activeSection, pushAssistant]);

  // 同值必须按 id 二次排序：Array.sort 稳定只保证"相等项维持输入顺序"，而 papers 的输入顺序会随
  // done 事件与刷新变 ⇒ 不点名二次键就会抖。二次键与后端 paper_memory.visible_papers 用同一套（id→标题）。
  const tie = (x: { p: Paper }) => String(x.p.id || x.p.title || "");
  const visiblePapers = papers
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => (!yearFrom || (p.year || 0) >= yearFrom)
                && (!oaOnly || p.open_access)
                && (p.citation_count ?? 0) >= citMin)
    .sort((a, b) => (sortBy === "citations"
                  ? (b.p.citation_count ?? 0) - (a.p.citation_count ?? 0) || tie(a).localeCompare(tie(b))
                  : sortBy === "year" ? (b.p.year ?? 0) - (a.p.year ?? 0) || tie(a).localeCompare(tie(b))
                  : a.i - b.i));

  // 三个导出键必须一起长：前台筛什么就导出什么，否则"表里 3 篇、下载下来 8 篇"。
  // 新增一颗筛选器时，判据会逼着这里同步加参数（tests2/test_results_table.py）。
  const exportUrl = (fmt: string) =>
    `${API}/paper/${projectId}/export?format=${fmt}`
    + `&year_from=${yearFrom}&open_access=${oaOnly ? 1 : 0}&min_citations=${citMin}&sort=${sortBy}`;

  // 对比表里的标题跳回论文卡：只有当前列表里能找到的才给锚点，找不到的不硬编一个会跳错的链接
  const paperNo = new Map(visiblePapers.map(({ p, i }) => [p.id || p.title, i + 1]));

  const locatePaper = useCallback((n: number) => {
    setTab("papers");
    // 窄屏时右栏是抽屉：不拉开就等于"点角标跳到那篇"跳进一个看不见的面板。
    // 1024 不是随手写的：它必须和 Tailwind 的 lg 断点一致，否则两个口径会各开各的。
    if (window.innerWidth < 1024) setPanelOpen(true);
    window.setTimeout(() => document.getElementById(`paper-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 60);
  }, []);

  // 角标点击：既跳到论文卡，也把"这句话"送去回连原文依据（找不到就如实显示找不到）
  const onCiteClick = (e: React.MouseEvent, text: string) => {
    const a = (e.target as Element).closest?.("a");
    const m = a && /^#paper-(\d{1,2})$/.exec(a.getAttribute("href") || "");
    if (!a || !m) return;
    e.preventDefault();
    const n = Number(m[1]);
    const claim = sentenceOfAnchor(a, text);
    locatePaper(n);
    if (!projectId) { setCiteEvidence({ i: n, claim, status: "true_gap", spans: [], note: "还没有项目，无法查依据" }); return; }
    fetch(`${API}/cite/context`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, paper_index: n, claim }),
    }).then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { status?: string; spans?: { text: string; source: string }[]; note?: string;
                  supporter?: { idx: number; title: string; text: string; source: string; stance: string } | null;
                  survey?: { checked: number; support: number; against: number } }) =>
        setCiteEvidence({ i: n, claim, status: d.status || "true_gap", spans: d.spans || [],
                          note: d.note || "", supporter: d.supporter || null, survey: d.survey }))
      .catch(() => setCiteEvidence({ i: n, claim, status: "true_gap", spans: [],
                                     note: "依据查询失败，不影响跳转" }));
  };

  // 四个面板共用的进度卡：谁空就给谁垫一张，说的都是这一轮真算出来的数
  const progress = (
    <ProgressCard steps={FLOW} flow={flow} confidence={intent ? intent.confidence : null}
      threshold={readyThreshold} gaps={gapLabels} topic={intent?.topic || ""} />
  );

  // 学生点按钮显式回报，比让模型猜"他做没做"可靠；写失败就维持原状，不把没记上的说成记上了
  const reportCommitment = (index: number, status: string) => {
    if (!projectId) return;
    fetch(`${API}/commitments/${projectId}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index, status }),
    }).then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { commitments?: Ledger }) => setLedger(d.commitments || null))
      .catch(() => {});
  };
  // 「它这么理解我」：先把假设摆出来，让学生能一眼同意或纠正（认知强制函数要有可点的动作）
  const onAssumption = (agree: boolean) => {
    if (!projectId) return;
    fetch(`${API}/commitments/${projectId}/assumption`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agree, text: agree ? "" : input }),
    }).catch(() => {});
    if (!agree) setInput("不是这样的，我想补一句：");
  };
  const followup = <CommitmentCard ledger={ledger} onReport={reportCommitment} onAssumption={onAssumption} />;
  // 窄屏时右栏是收着的：跟进卡与假设块必须在对话流里也出现一次，否则手机上永远看不到
  const followupInline = <CommitmentCard ledger={ledger} onReport={reportCommitment}
    onAssumption={onAssumption} variant="inline" />;

  return (
    <div className="flex h-screen flex-1 overflow-hidden">
      {/* 主对话区 */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-100 bg-white px-4 py-3 lg:px-6">
          <div>
          <div className="text-[15px] font-semibold text-gray-800">研伴 · 认知层</div>
          {intent ? (
            <div className="text-[11.5px] text-gray-500">
              主题：{intent.topic || "待明确"} · 阶段：{stageLabel(intent.research_stage, stageLabels)} · 理解度 {(intent.confidence * 100).toFixed(0)}%
            </div>
          ) : (
            <div className="text-[11.5px] text-gray-500">说一句你想研究的东西，哪怕很模糊</div>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1 text-[10.5px]" title={`八态对话状态机，当前：${flow}`}>
            {FLOW.map(([k, label], i) => {
              const cur = FLOW.findIndex(f => f[0] === flow);
              const on = k === flow, done = cur > i;
              return <span key={k} className={`rounded px-1.5 py-0.5 ${
                on ? "bg-primary-600 text-white" : done ? "bg-primary-50 text-primary-700" : "bg-gray-100 text-gray-600"}`}>
                {label}
              </span>;
            })}
            <span className="ml-1 text-gray-500">第 {Math.max(1, FLOW.findIndex(f => f[0] === flow) + 1)}/8 步</span>
          </div>
          </div>
          {/* 交付面的发现性：单文件报告此前只躺在第四个 tab 里（功能在，入口不显眼）。
              一旦有项目，表头就该给一个不依赖切 tab、不依赖右栏展开的出口。 */}
          {projectId && (
            <a href={`${API}/artifact/${projectId}`} target="_blank" rel="noreferrer"
              className="mt-0.5 shrink-0 rounded-lg border border-primary-200 px-2.5 py-1 text-[12px] text-primary-700 hover:bg-primary-50">
              报告产物 ↗
            </a>
          )}
          <button onClick={() => setPanelOpen(v => !v)}
            className="mt-0.5 shrink-0 rounded-lg border border-gray-200 px-2.5 py-1 text-[12px] text-gray-500 hover:bg-gray-50 lg:hidden">
            {panelOpen ? "收起面板" : "论文·知识库"}
          </button>
        </header>

        {/* 证据底座计数条（对标 undermind U2）：这些数一律取 meta/done 的 evidence_basis，
            前端不数 papers.length、不写常量；后端给 null 的格显示「未记录」而不是 0。 */}
        {basis && (
          <div data-testid="evidence-basis"
            className="flex shrink-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 border-b border-gray-100 bg-gray-50 px-4 py-1.5 text-[11px] text-gray-700 lg:px-6">
            <span className="font-medium">这一轮踩在多少料上</span>
            {([
              ["引擎返回", basis.search_engine_returned],
              ["入库", basis.search_stored],
              ["滤除", basis.off_topic_dropped],
              ["查询串", basis.query_variants],
              ["检索耗时 ms", basis.search_ms],
              ["库内 PDF", basis.library_pdfs],
              ["库内摘要", basis.library_abstracts],
            ] as [string, number | null][]).map(([label, value]) => (
              <span key={label} className="whitespace-nowrap">
                <span className="mr-1.5 text-slate-400">·</span>
                {label}{" "}
                {typeof value === "number"
                  ? <b className="tabular-nums">{value}</b>
                  : <i className="not-italic text-slate-500">未记录</i>}
              </span>
            ))}
          </div>
        )}

        <div ref={logRef} onScroll={onLogScroll}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-6 py-4"
          role="log" aria-live="polite" aria-atomic="false">
          {messages.length === 0 && !busy && (
            <div className="mx-auto mt-10 max-w-xl">
              <div className="text-[15px] font-medium text-gray-700">把你想研究的东西告诉研伴</div>
              <div className="mt-1 text-[12.5px] leading-5 text-gray-500">
                它会像一个带过很多学生的导师那样接话：先听懂你的处境，再一次只问最关键的一个问题，
                信息够了当场就给你论文。点下面任意一个开始，或者直接自己说。
              </div>
              <div className="mt-4 space-y-2">
                {EXAMPLES.map(ex => (
                  <button key={ex.title} onClick={() => { setInput(ex.title); }}
                    className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-left shadow-sm transition hover:border-primary-400 hover:bg-primary-50/40">
                    <div className="text-[13.5px] font-medium text-gray-800">{ex.title}</div>
                    <div className="mt-0.5 text-[11.5px] text-gray-500">{ex.sub}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              {m.role === "user" ? (
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-primary-600 px-4 py-2.5 text-[14px] leading-6 text-white">
                  {m.text}
                </div>
              ) : (
                <div className="min-w-0 max-w-[88%]" onClickCapture={e => onCiteClick(e, m.text)}>
                  {m.superseded && (
                    <details className="mb-1 rounded-lg bg-gray-100/70 px-2.5 py-1.5">
                      <summary className="cursor-pointer text-[11px] text-gray-500">
                        上一版{m.supersedeNote ? `（${m.supersedeNote}）` : ""}，点开对照
                      </summary>
                      <div className="mt-1 whitespace-pre-wrap text-[11.5px] leading-5 text-gray-500">
                        {m.superseded}
                      </div>
                    </details>
                  )}
                  <div className={`rounded-2xl px-4 py-2.5 text-[14px] leading-6 shadow-sm ${
                    m.error ? "bg-red-50 text-red-700" : "bg-white text-gray-800"}`}>
                    {m.text ? (
                      <Markdown text={linkifyCitations(m.text)} />
                    ) : (
                      <div className="flex items-center gap-2 text-gray-500">
                        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-primary-400" />
                        <span className="text-[13px]">研伴在想…</span>
                      </div>
                    )}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 px-1 text-[11px] text-gray-500">
                    {m.degraded && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">模型不可用，已降级回复</span>}
                    {m.searchNote && <span
                      data-testid={m.searchNote.declined ? "note-strip-declined" : "note-strip-failure"}
                      className={`rounded px-1.5 py-0.5 ${m.searchNote.declined ? "bg-gray-100 text-gray-600" : "bg-amber-50 text-amber-600"}`}
                      title={m.searchNote.text}>
                      {m.searchNote.declined ? "这轮你勾掉了检索，下面的话没有文献依据" : "这轮文献检索没成功，下面的话暂时没有文献依据"}
                    </span>}
                    {m.error && <button onClick={retry} className="rounded bg-primary-50 px-1.5 py-0.5 text-primary-700 hover:bg-primary-100">重试</button>}
                  </div>
                </div>
              )}
            </div>
          ))}
          {followupInline}
          {busy && steps.length > 0 && (
            <div data-testid="step-progress" className="ml-1 space-y-0.5 rounded-xl bg-gray-100/70 px-3 py-2">
              {steps.map(s => (
                <div key={s.id} className="flex items-center gap-1.5 text-[11.5px] text-gray-500">
                  <span className={s.status === "doing" ? "text-primary-600"
                    : s.status === "skipped" ? "text-gray-400"
                    : s.status === "pending" ? "text-gray-300" : "text-emerald-600"}>
                    {s.status === "done" ? "✓" : s.status === "skipped" ? "−"
                      : s.status === "pending" ? "○" : "◦"}</span>
                  <span className={`${s.status === "doing" ? "text-primary-700" : ""} ${s.status === "skipped" ? "text-gray-400 line-through" : ""}`}>{s.label}</span>
                  {typeof s.count === "number" && <span className="text-gray-500">{s.count} 篇</span>}
                </div>
              ))}
            </div>
          )}

          {usedMem.length > 0 && (
            <details className="ml-1 rounded-xl bg-gray-100/70 px-3 py-2">
              <summary className="cursor-pointer text-[11.5px] text-gray-500">
                研伴这轮参考了 {usedMem.length} 条记录（点开看是哪几条、各自来自哪里）
                {/* 原来写的是「你以前说过的话」：实测里面混着本轮状态机自己写的事件，
                    那不是学生以前说的，是这一轮刚产生的——说成记忆就是假记忆的味道。 */}
              </summary>
              <ul className="mt-1 space-y-0.5 text-[11px] text-gray-500">
                {usedMem.map((m, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="flex-1" data-testid={`used-memory-${i}`}>· [{m.tag}] {m.text}</span>
                    {typeof m.event_id === "number" && (
                      <button onClick={() => { setMemFocus(m.event_id ?? null); setTab("memory"); }}
                        data-testid={`used-memory-jump-${i}`}
                        data-event-id={m.event_id ?? undefined}
                        className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10.5px] text-gray-500 hover:bg-gray-50">
                        看这一条
                      </button>
                    )}
                    <button
                      onClick={() => {
                        if (!projectId) return;
                        const willBlock = !blocked.includes(m.text);
                        void fetch(`${API}/memory/${projectId}/${willBlock ? "block" : "unblock"}`, {
                          method: "POST", headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ text: m.text }),
                        }).then(async r => {
                          if (!r.ok) return;
                          const d = await r.json();
                          setBlocked(prev => willBlock
                            ? Array.from(new Set([...prev, m.text]))
                            : prev.filter(x => x !== m.text));
                          void d;
                        });
                      }}
                      className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10.5px] text-gray-500 hover:bg-gray-50">
                      {blocked.includes(m.text) ? "恢复引用" : "不再引用"}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {!busy && (chips.length > 0 || suggests.length > 0) && (
            <div className="space-y-2.5 pt-1">
              {chips.map(g => (
                <div key={g.field}>
                  <div className="text-[11px] text-gray-500">{g.question} · 点一下就行，也可以自己说</div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {g.options.map(o => (
                      <button key={o.label} onClick={() => answerWith(o.say)}
                        className="rounded-full border border-primary-200 bg-white px-3 py-1 text-[12px] text-primary-700 hover:bg-primary-50">
                        {o.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {fbEffects.length > 0 && (
                <div className="rounded-xl bg-emerald-50 px-3 py-2 text-[11.5px] leading-5 text-emerald-700">
                  <div className="mb-0.5 font-medium">这一轮因你的反馈做了调整</div>
                  {fbEffects.map((s, i) => <div key={i}>· {s}</div>)}
                </div>
              )}
              {suggests.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {suggests.map(o => (
                    <button key={o.label} onClick={() => answerWith(o.say)}
                      className="rounded-full bg-gray-100 px-3 py-1 text-[12px] text-gray-600 hover:bg-gray-200">
                      {o.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {offBottom && (
            <button type="button" data-testid="jump-latest" onClick={jumpLatest}
              className="sticky bottom-0 mx-auto block rounded-full border border-gray-200 bg-white/95 px-3 py-1 text-[11.5px] text-gray-600 shadow-sm">
              ↓ 最新回复
            </button>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="shrink-0 border-t border-gray-100 bg-white px-4 py-3 lg:px-6">
          {/* 检索意图卡（对标 undermind U3）：下一轮要用的检索式先摊开、允许改。
              打开卡与「记下这个范围」都只读写后端 intent，一次检索都不发；
              真检索仍由「发送」那一轮走既有 run_search —— 闸门一条没动。 */}
          {plan?.topic && (
            <div data-testid="search-plan" className="mb-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="shrink-0 text-[12px] font-medium text-gray-700">下一轮打算这样查</span>
                <code className="min-w-0 flex-1 truncate rounded bg-white px-1.5 py-0.5 text-[11.5px] text-slate-700"
                  title={plan.query}>{plan.query}</code>
                <button type="button" data-testid="search-plan-toggle" onClick={() => setPlanOpen(v => !v)}
                  className="shrink-0 rounded-lg border border-slate-200 bg-white px-2 py-0.5 text-[11.5px] text-gray-600 hover:bg-slate-100">
                  {planOpen ? "收起" : "调整范围"}
                </button>
              </div>
              {planOpen && (
                <div className="mt-2 space-y-2">
                  <label className="block text-[11.5px] text-gray-500">
                    研究主题（检索式唯一的来源，改它就等于改要查什么）
                    <input data-testid="plan-topic" value={draft.topic}
                      onChange={e => setDraft(d => ({ ...d, topic: e.target.value }))}
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-gray-800 outline-none focus:border-primary-500" />
                  </label>
                  <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
                    <label className="text-[11.5px] text-gray-500">
                      起始年份（留空＝不改；进相关度打分与年份过滤）
                      <input data-testid="plan-year" value={draft.year} inputMode="numeric" placeholder="如 2022"
                        onChange={e => setDraft(d => ({ ...d, year: e.target.value }))}
                        className="mt-1 h-9 w-28 rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-gray-800 outline-none focus:border-primary-500" />
                    </label>
                    <label data-testid="plan-oa-label" className="flex items-center gap-1.5 text-[12px] text-gray-600">
                      <input data-testid="plan-oa" type="checkbox" checked={draft.openAccess}
                        onChange={e => setDraft(d => ({ ...d, openAccess: e.target.checked }))} />
                      只要开放获取——只筛右栏那一列，不改检索式
                    </label>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" data-testid="plan-save" onClick={() => void savePlan()} disabled={planBusy}
                      className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-[12.5px] text-gray-700 hover:bg-slate-100 disabled:opacity-40">
                      记下这个范围（还没查）
                    </button>
                    <button type="button" data-testid="plan-run" onClick={() => void startSearch()}
                      disabled={planBusy || busy || !input.trim() || !yearValid(draft.year)}
                      className="h-9 rounded-lg bg-primary-600 px-3 text-[12.5px] font-medium text-white hover:bg-primary-700 disabled:opacity-40">
                      按这个范围检索
                    </button>
                  </div>
                  <div data-testid="plan-note" className="text-[11.5px] leading-4 text-gray-500">
                    {planNote || "先改范围再「记下」，这一步不会真的去查；「按这个范围检索」要等输入框有话、年份合法才可点。"}
                  </div>
                </div>
              )}
            </div>
          )}
          {toolSteps.length > 0 && (
            <div data-testid="composer-steps"
              className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-gray-500">
              <span className="text-gray-400">这条消息会做：</span>
              {toolSteps.map(s => (
                <span key={s.id} data-testid={`composer-step-${s.id}`} className="flex items-center gap-1">
                  <span>{s.label}</span>
                  {/* 工具名是一整串没有空格的模块路径：390 档不许把它裁掉（首跑就是被裁的），让它按字符折行 */}
                  <code className="rounded bg-gray-100 px-1 text-[10.5px] text-gray-500 break-all">{s.tool}</code>
                </span>
              ))}
              {toolSteps.filter(s => s.optional).map(s => (
                <label key={`dec-${s.id}`} data-testid={`step-decline-${s.id}`}
                  className="flex shrink-0 items-center gap-1 whitespace-nowrap text-gray-600">
                  <input type="checkbox" checked={noSearch} onChange={e => setNoSearch(e.target.checked)} />
                  {s.decline_label}
                </label>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !e.shiftKey && send()}
              placeholder="说说你想研究什么…"
              className="h-11 flex-1 rounded-xl border border-gray-200 px-4 text-[14px] outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
            />
            {busy ? (
              <button onClick={() => abortRef.current?.abort()}
                className="h-11 rounded-xl border border-gray-200 bg-white px-4 text-[13.5px] text-gray-600 hover:bg-gray-50">
                停止
              </button>
            ) : (
              <button onClick={send} disabled={!input.trim()}
                className="h-11 rounded-xl bg-primary-600 px-5 text-[14px] font-medium text-white hover:bg-primary-700 disabled:opacity-40">
                发送
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 右栏：Tab 化（论文 / 知识库 / 导图与工具） */}
      <div className={`${panelOpen ? "flex" : "hidden"} fixed inset-y-0 right-0 z-30 w-[min(460px,100vw)] shrink-0 flex-col overflow-hidden border-l border-gray-100 bg-gray-50 shadow-2xl lg:static lg:flex lg:bg-gray-50/60 lg:shadow-none`}>
        <div className="flex gap-1 border-b border-gray-100 px-3 py-2">
          {([
            ["papers", "论文", intent ? papers.length : null],
            ["knowledge", "知识库", projectId ? noteCount : null],
            ["memory", "记忆", projectId ? memCount : (usedMem.length || null)],
            ["map", "导图·工具", null],
          ] as const).map(([k, label, n]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`rounded-lg px-3 py-1 text-[12.5px] ${
                tab === k ? "bg-primary-600 text-white" : "text-gray-500 hover:bg-gray-100"}`}>
              {/* 计数只在真读到的时候出现：没数字＝还没开始/还没读到，0＝读到了确实是空的 */}
              {label}{n === null ? "" : ` ${n}`}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-3">
          {tab === "papers" && (
            <div className="space-y-2.5">
              {papers.length > 0 && (
                <div data-testid="filter-bar"
                  className="flex flex-wrap items-center gap-2 rounded-xl bg-white px-3 py-2 text-[11.5px] shadow-sm">
                  <span className="text-gray-500">筛选</span>
                  <select value={yearFrom} onChange={e => setYearFrom(Number(e.target.value))}
                    className="rounded-lg border border-gray-200 px-1.5 py-0.5 text-[11.5px] outline-none">
                    <option value={0}>不限年份</option>
                    <option value={2025}>2025 年起</option>
                    <option value={2022}>2022 年起</option>
                    <option value={2018}>2018 年起</option>
                  </select>
                  {/* label 原来把两颗 select 一起包住了（</label> 在 945 行才关）：
                      点「被引/排序」下拉会被转发成勾选那颗 OA 复选框。先只包它自己的控件。 */}
                  <label className="flex items-center gap-1 text-gray-600">
                    <input type="checkbox" checked={oaOnly} onChange={e => setOaOnly(e.target.checked)} />只要 OA
                  </label>
                  <select value={citMin} onChange={e => setCitMin(Number(e.target.value))}
                    className="h-7 rounded-lg border border-gray-200 px-1.5 text-[12px] text-gray-600">
                    <option value={0}>被引不限</option>
                    <option value={10}>被引 ≥10</option>
                    <option value={50}>被引 ≥50</option>
                    <option value={200}>被引 ≥200</option>
                  </select>
                  <select value={sortBy} onChange={e => setSortBy(e.target.value as typeof sortBy)}
                    className="h-7 rounded-lg border border-gray-200 px-1.5 text-[12px] text-gray-600">
                    <option value="relevance">按相关度</option>
                    <option value="citations">按被引</option>
                    <option value="year">按年份</option>
                  </select>
                  <span className="text-gray-500">{visiblePapers.length}/{papers.length} 篇</span>
                  <a className="ml-auto rounded-lg bg-primary-50 px-2 py-0.5 text-primary-700 hover:bg-primary-100"
                    href={exportUrl("csv")} title="按当前筛选导出表格" download>导出 CSV</a>
                  <a className="rounded-lg bg-primary-50 px-2 py-0.5 text-primary-700 hover:bg-primary-100"
                    href={exportUrl("ris")} title="按当前筛选导出引文" download>导出 RIS</a>
                  <a className="rounded-lg bg-primary-50 px-2 py-0.5 text-primary-700 hover:bg-primary-100"
                    href={exportUrl("bib")} title="按当前筛选导出引文" download>BibTeX</a>
                  <button onClick={() => { const next = !showMatrix; setShowMatrix(next); if (next && !matrix && !matrixBusy) loadMatrix(matrixCols); }}
                    className="rounded-lg bg-gray-50 px-2 py-0.5 text-gray-600 hover:bg-gray-100">
                    {showMatrix ? "收起对比表" : "跨论文对比表"}
                  </button>
                  <button onClick={openBrief} disabled={reportBusy}
                    className="rounded-lg bg-gray-50 px-2 py-0.5 text-gray-600 hover:bg-gray-100 disabled:opacity-50">
                    {reportBusy ? "写作中…" : "生成综述报告"}
                  </button>
                </div>
              )}
                {/* U14 读侧入口：数与名单都问后端要（excluded 来自 status=rejected 那一路），
                    不从这里包进 papers.length>0——本轮没检索过也要能捞回历史排除的那批。 */}
                {(papers.length > 0 || excluded.length > 0) && (
                  <div className="mx-3 mt-1 flex items-center gap-2 text-[11.5px] text-gray-500">
                    <button data-testid="trash-toggle" onClick={() => setShowExcluded(v => !v)}
                      className="rounded-lg bg-gray-50 px-2 py-0.5 text-gray-600 hover:bg-gray-100">
                      已排除 {excluded.length} 篇 · {showExcluded ? "收起" : "查看"}
                    </button>
                  </div>
                )}
                {showExcluded && (
                  <div data-testid="trash-list" className="mx-3 mt-2 rounded-lg border border-gray-200 px-2.5 py-2">
                    <div className="text-[11.5px] leading-5 text-gray-500">
                      这些是你点过「不感兴趣」的论文。恢复 = 把状态改回去，它会重新参与下一轮排序与推荐。
                    </div>
                    {excluded.map((p, k) => (
                      <div key={p.id || `x${k}`} className="mt-1.5 flex items-start gap-2 text-[12px]">
                        <span className="flex-1 leading-5 text-gray-600">
                          {p.title}
                          {p.user_feedback ? <span className="text-gray-400">（当时写的：{p.user_feedback}）</span> : null}
                        </span>
                        <button data-testid="trash-restore" onClick={() => feedback(p, "recommended")}
                          className="shrink-0 rounded-lg bg-primary-50 px-2 py-0.5 text-primary-700 hover:bg-primary-100">恢复</button>
                      </div>))}
                    {!excluded.length && (
                      <div className="mt-1 text-[12px] text-gray-500">还没有排除过任何论文。</div>)}
                  </div>
                )}
              {papers.length > 0 && briefOpen && brief && (
                  <div data-testid="report-brief"
                    className="mt-2 rounded-xl border border-primary-100 bg-primary-50/40 p-3 text-[11.5px]">
                    <div className="font-medium text-gray-700">写之前先定两件事（改的是各节的取舍，不是提示词）</div>
                    {briefErr && <div className="mt-1 text-red-600">{briefErr}</div>}
                    {brief.questions.map((q, qi) => (
                      <div key={q.key} className="mt-2">
                        <div className="text-gray-700">{qi + 1}. {q.text}</div>
                        <div className="mt-1 flex flex-wrap gap-2">
                          {q.options.map(o => (
                            <label key={o.value} title={o.effect}
                              className={`cursor-pointer rounded-lg border px-2 py-0.5 ${briefChoice[q.key] === o.value
                                ? "border-primary-300 bg-white text-primary-700" : "border-gray-200 bg-white text-gray-600"}`}>
                              <input type="radio" name={q.key} value={o.value} className="mr-1"
                                data-testid={`brief-opt-${q.key}-${o.value}`}
                                checked={briefChoice[q.key] === o.value}
                                onChange={() => setBriefChoice({ ...briefChoice, [q.key]: o.value })} />
                              {o.label}
                            </label>
                          ))}
                        </div>
                        {briefChoice[q.key] && (
                          <div data-testid="brief-effect" className="mt-1 text-gray-500">
                            ⇒ {q.options.find(o => o.value === briefChoice[q.key])?.effect}
                          </div>
                        )}
                      </div>
                    ))}
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button data-testid="brief-go" disabled={reportBusy || Object.keys(briefChoice).length < brief.questions.length}
                        onClick={() => sendBrief(false)}
                        className="rounded-lg bg-primary-600 px-2 py-0.5 text-white disabled:opacity-50">
                        按这两条写
                      </button>
                      <button data-testid="brief-skip" disabled={reportBusy}
                        onClick={() => sendBrief(true)}
                        className="rounded-lg bg-white px-2 py-0.5 text-gray-600 hover:bg-gray-50 disabled:opacity-50">
                        跳过，直接写
                      </button>
                      <button onClick={() => setBriefOpen(false)}
                        className="rounded-lg px-2 py-0.5 text-gray-500 hover:bg-white">先不写</button>
                    </div>
                    <div className="mt-1 text-gray-500">
                      {brief.answered ? "已记下这两条" : brief.skipped ? "上次你跳过了这两条" : "这两条还没记"}
                      {" · "}每颗选项后面那行「⇒」就是它会改动的地方，不点不生效
                    </div>
                  </div>
                )}
              {showMatrix && papers.length > 0 && (
                <div className="rounded-xl bg-white p-3 shadow-sm">
                  <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
                    <span className="font-medium text-gray-700">跨论文对比表</span>
                    <span className="text-gray-500">只从原文抽取，每格悬停可看逐字出处；找不到依据写「未知」</span>
                    {matrix?.cached ? <span className="text-gray-500">· 用的上次结果</span> : null}
                    {(matrix?.fulltext_rows ?? 0) > 0 ? (
                      <span className="text-gray-500">· {matrix!.fulltext_rows} 行是按 PDF 全文抽的</span>
                    ) : null}
                    <button onClick={() => loadMatrix(matrixCols)} disabled={matrixBusy}
                      className="ml-auto rounded-lg bg-primary-50 px-2 py-0.5 text-primary-700 disabled:opacity-50">
                      {matrixBusy ? "抽取中…" : "重新抽取"}
                    </button>
                  </div>
                  {matrixErr && <div className="mt-2 rounded-lg bg-amber-50 px-2 py-1 text-[11.5px] text-amber-700">{matrixErr}</div>}
                  {matrix?.note && <div className="mt-2 text-[11.5px] text-gray-500">{matrix.note}</div>}
                  {matrix && matrix.rows.length > 0 && (
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full border-collapse text-[11.5px] leading-5">
                        <thead>
                          <tr>
                            <th className="border-b border-gray-100 pr-2 pb-1 text-left font-medium text-gray-500">论文</th>
                            {matrix.columns.map(([key, label]) => (
                              <th key={key} className="border-b border-gray-100 px-2 pb-1 text-left font-medium text-gray-500">{label}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {matrix.rows.map(row => (
                            <tr key={row.id || row.title} className="align-top">
                              <td className="max-w-[190px] border-b border-gray-50 py-1.5 pr-2 text-gray-700">
                                {paperNo.has(row.id || row.title)
                                  ? <a href={`#paper-${paperNo.get(row.id || row.title)}`} className="hover:text-primary-700">{row.title}</a>
                                  : row.title}
                                {row.year ? <span className="ml-1 text-gray-500">{row.year}</span> : null}
                              </td>
                              {matrix.columns.map(([key]) => (
                                <td key={key} title={row.evidence?.[key] || "无逐字出处"}
                                  className="max-w-[220px] cursor-help border-b border-gray-50 px-2 py-1.5">
                                  {row.cells[key] === "未知"
                                    ? <span className="text-gray-500">未知</span>
                                    : <span className="text-gray-700">{row.cells[key]}</span>}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <div className="mt-2 flex items-center gap-1.5">
                    <input value={newCol} onChange={e => setNewCol(e.target.value)}
                      onKeyDown={e => { if (e.key === "Enter") addMatrixCol(); }}
                      placeholder="加一列自定义抽取，例如：有没有公开代码"
                      className="min-w-0 flex-1 rounded-lg border border-gray-200 px-2 py-1 text-[11.5px] outline-none" />
                    <button onClick={addMatrixCol} disabled={!newCol.trim() || matrixCols.length >= 3 || matrixBusy}
                      className="rounded-lg bg-primary-50 px-2.5 py-1 text-[11.5px] text-primary-700 disabled:opacity-40">加列</button>
                  </div>
                  {matrixCols.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1 text-[11px] text-gray-500">
                      {matrixCols.map((c, i) => <span key={i} className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600">{c.label}</span>)}
                      <span className="text-gray-500">（自定义列最多 3 个）</span>
                    </div>
                  )}
                </div>
              )}
              {(report || reportErr || reportBusy) && (
                <div className="rounded-xl bg-white p-3 shadow-sm">
                  <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
                    <span className="font-medium text-gray-700">{report?.title || (reportBusy ? "综述报告（生成中）" : "综述报告")}</span>
                    {report ? (
                      <span className="text-gray-500">
                        {report.words} 字 · 依据 {report.papers} 篇 · {report.used_matrix ? "用上了对比表" : "没用对比表"}
                      </span>
                    ) : null}
                    <button onClick={makeReport} disabled={reportBusy}
                      className="ml-auto rounded-lg bg-primary-50 px-2 py-0.5 text-primary-700 disabled:opacity-50">
                      {reportBusy ? "写作中…" : "重写"}
                    </button>
                  </div>
                  {reportErr && <div className="mt-2 rounded-lg bg-amber-50 px-2 py-1 text-[11.5px] text-amber-700">{reportErr}</div>}
                  {(report?.removed_titles.length ?? 0) > 0 && (
                    <div className="mt-2 rounded-lg bg-amber-50 px-2 py-1 text-[11.5px] text-amber-700">
                      已剔除 {report!.removed_titles.length} 处检索结果之外的论文标题：
                      {report!.removed_titles.join("、")}
                    </div>
                  )}
                  {reportBusy && !report && (
                    <div className="mt-2">
                      <div className="text-[11.5px] text-gray-500">{reportStep || "正在写…"}</div>
                      <div className="text-[10.5px] text-gray-500">草稿实时吐出；写完会按原文出处校验后再定稿</div>
                      <div className="report-prose mt-1"><Markdown text={reportDraft || "…"} /></div>
                    </div>
                  )}
                  {report && <ReportView report={report} onRewrite={rewriteSection} />}
                  {secMsg && <div className="mt-1 text-[11.5px] text-gray-500">{secMsg}</div>}
                </div>
              )}
              {lowRel.on && (
                <div className="rounded-lg bg-amber-50 px-3 py-1.5 text-[11.5px] leading-5 text-amber-700">
                  这轮{lowRel.dropped ? `滤掉了 ${lowRel.dropped} 篇与主题不相关的结果` : "没有与主题足够相关的结果"}，
                  所以宁可不推。换个更具体的说法，或告诉我想限定的方向，我再查一次。
                </div>
              )}
              {outage ? (
                /* 源不可用时绝不能报「英译没生效」：那是把基础设施故障算到学生的说法上。
                   截图取证实测到过这个错诊断（轮 99）。 */
                <div className="rounded-lg bg-amber-50 px-3 py-1.5 text-[11.5px] leading-5 text-amber-700">
                  这一轮文献检索源不可用（{outage}），所以下面的话没有文献依据。
                  这不是你的说法有问题，是外部库暂时连不上，稍后我再查一次。
                </div>
              ) : fallback && (
                <div className="rounded-lg bg-amber-50 px-3 py-1.5 text-[11.5px] leading-5 text-amber-700">
                  这次英译检索没有生效，是用中文原词直接查英文库的，相关性可能偏低——换个说法或让我再查一次会更好。
                </div>
              )}
              {visiblePapers.map(({ p, i }) => (
                <div key={p.id || p.title} id={`paper-${i + 1}`}
                  className="scroll-mt-4 rounded-xl bg-white p-3.5 shadow-sm">
                  <div className="text-[13.5px] font-medium leading-5 text-gray-800">{p.title}</div>
                  {citeEvidence && citeEvidence.i === i + 1 && (
                    <div data-testid={`cite-${citeEvidence.status}`}
                      className="mt-1.5 rounded-lg bg-primary-50/70 px-2.5 py-2 text-[12px] leading-5 text-gray-700">
                      {/* 三态各自的标题句：接地=给原文；挂错编号=点名真正撑住它的那篇；
                          真缺依据=直说没有，这一格里不摆任何看起来像"有依据"的徽章。
                          兜底支也不许是接地的标题句——后端给出未知状态时，这一格要说"不算依据"，
                          因为同处的 <mark> 只认 grounded，落到兜底就会出现"标题说有、正文拿不出"。 */}
                      <div className="text-[11px] text-primary-700">
                        {citeEvidence.status === "true_gap" ? "这句目前没有文献支撑"
                          : citeEvidence.status === "misnumbered" ? "这句的依据不在这一篇"
                          : citeEvidence.status === "grounded" ? "撑住上面那句话的原文"
                          : "程序没认出一个可比的判定状态——这一格不算依据"}
                      </div>
                      {citeEvidence.status === "grounded" && citeEvidence.spans.map((s, k) => (
                        <div key={k} className="mt-1">
                          <mark className="bg-yellow-100 px-0.5 text-gray-800">「{s.text}」</mark>
                          <span className="text-gray-600"> — 出自这篇的{s.source}</span>
                          {/* 页码只报后端量到的：定位不到就明说"没定位到页"，绝不退成"第一页"——
                              那会把一个不知道的数伪装成一个知道的数。 */}
                          {s.found
                            ? <span data-testid="cite-page" className="text-gray-600">
                                {" "}· 第 {s.page} 页{s.section ? ` · ${s.section}` : ""}
                                <button onClick={() => openPdf(p, s.page ?? null)}
                                  className="ml-1 text-primary-600 underline hover:text-primary-700">看这页</button>
                              </span>
                            : <span data-testid="cite-page-missing" className="text-gray-500">
                                {" "}· 没定位到页（PDF 未缓存或这段不在正文里），可全文检索
                              </span>}
                        </div>))}
                      {citeEvidence.status === "misnumbered" && citeEvidence.supporter && (
                        <div className="mt-1">
                          <mark className="bg-yellow-100 px-0.5 text-gray-800">「{citeEvidence.supporter.text}」</mark>
                          <span className="text-gray-600">
                            {" "}- 出自第 {citeEvidence.supporter.idx} 篇《{citeEvidence.supporter.title}》
                            · {citeEvidence.supporter.source}
                          </span>
                        </div>)}
                      {citeEvidence.status === "true_gap" && (
                        <div className="mt-1 text-gray-700">{citeEvidence.note}</div>)}
                      {citeEvidence.survey && citeEvidence.survey.checked > 0 && (
                        <div className="mt-1 text-[11px] text-gray-500">
                          库里 {citeEvidence.survey.checked} 篇里有 {citeEvidence.survey.support} 篇写到同一处
                          {citeEvidence.survey.against > 0
                            ? `，其中 ${citeEvidence.survey.against} 篇在同一处写了相反或局限的说法`
                            : ""}（关键词启发式，用来提示该核对哪几篇，不等于结论判定）
                        </div>
                      )}
                    </div>
                  )}
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="rounded bg-primary-50 px-1.5 py-0.5 text-primary-700">{p.venue || "未知来源"}</span>
                    {p.year ? <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600">{p.year}</span> : null}
                    {p.citation_count != null ? <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600">被引 {p.citation_count}</span> : null}
                    {p.open_access ? <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">OA 可下载</span> : null}
                    {p.status && p.status !== "recommended" ? <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">{p.status}</span> : null}
                  </div>
                  {p.tldr && <div className="mt-1.5 text-[12px] leading-5 text-gray-600">{p.tldr}</div>}
                  {(p.recommendation_reason?.length ?? 0) > 0 && (
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-[11.5px] text-primary-700">为什么推荐给你</summary>
                      <ul className="mt-1 space-y-0.5 pl-1 text-[11.5px] text-gray-500">
                        {p.recommendation_reason!.map((r, j) => <li key={j}>✓ {r}</li>)}
                      </ul>
                    </details>
                  )}
                  <div className="mt-2 flex gap-1.5">
                    <button onClick={() => openPdf(p)}
                      className="rounded-lg bg-primary-50 px-2.5 py-1 text-[11.5px] text-primary-700 hover:bg-primary-100">阅读 PDF</button>
                    <button onClick={() => feedback(p, "favorite")}
                      className="rounded-lg bg-gray-50 px-2.5 py-1 text-[11.5px] text-gray-600 hover:bg-gray-100">收藏</button>
                    {p.status === "rejected" ? (
                      <button data-testid="card-restore" onClick={() => feedback(p, "recommended")}
                        className="rounded-lg bg-primary-50 px-2.5 py-1 text-[11.5px] text-primary-700 hover:bg-primary-100">恢复</button>
                    ) : (
                      <button onClick={() => feedback(p, "rejected")}
                        className="rounded-lg bg-gray-50 px-2.5 py-1 text-[11.5px] text-gray-600 hover:bg-gray-100">不感兴趣</button>
                    )}
                  </div>
                </div>
              ))}
              {papers.length === 0 && (
                <>
                  {progress}{followup}
                  <div className="px-1 text-[12px] text-gray-500">
                    聊到信息足够时，研伴会当场把论文放进这里，并在回复里用 [1] [2] 引给你。
                  </div>
                </>
              )}
            </div>
          )}

          {tab === "knowledge" && projectId && <KnowledgePanel projectId={projectId} onCount={setNoteCount} />}
          {tab === "knowledge" && !projectId && (
            <div className="space-y-2.5">
              {progress}{followup}
              <div className="px-1 text-[12px] text-gray-500">开始对话后，研伴会自动为论文和你的决策生成笔记。</div>
            </div>
          )}

          {tab === "memory" && projectId && <MemoryPanel projectId={projectId} onCount={setMemCount} focusId={memFocus} />}
          {tab === "memory" && !projectId && (
            <div className="space-y-2.5">
              {progress}{followup}
              {/* 本轮真正参考过的记忆在第一条回复就有（不依赖 projectId），过去被一刀切闸门挡在灰字后面 */}
              {usedMem.length > 0 && (
                <div className="rounded-xl bg-white p-3 shadow-sm">
                  <div className="label-caps text-[11px] text-gray-500">刚才这句话参考了 {usedMem.length} 条</div>
                  <ul className="mt-1 space-y-1">
                    {usedMem.map((m, i) => (
                      <li key={i} className="text-[11.5px] leading-5 text-gray-600">
                        <span className="text-gray-500" data-testid={`used-memory-${i}`}>{m.tag}：</span>{m.text}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="px-1 text-[12px] text-gray-500">
                聊几句之后，这里会列出研伴记得关于你的每件事，可以逐条屏蔽或删掉自己写的笔记。
              </div>
            </div>
          )}
          {tab === "map" && !projectId && (
            <div className="space-y-2.5">
              {progress}{followup}
              {/* 翻译工具本来就不需要项目，过去跟着整个 tab 一起被挡掉了 */}
              <ToolsPanel projectId={null} />
            </div>
          )}
          {tab === "map" && projectId && (
            <div onClickCapture={e => {
              const label = (e.target as Element).textContent?.trim() || "";
              const n = papers.findIndex(p => label.length > 4 && (p.title.includes(label) || label.includes(p.title.slice(0, 24))));
              if (n >= 0) locatePaper(n + 1);
            }}>
              <a href={`${API}/knowledge/${projectId}/export`} download
                className="mb-2 inline-block rounded-lg bg-white px-3 py-1.5 text-[12px] text-primary-700 shadow-sm hover:bg-primary-50">
                导出知识库（.zip，Obsidian 可用）
              </a>
              <a href={`${API}/artifact/${projectId}`} target="_blank" rel="noreferrer"
                className="mb-2 ml-2 inline-block rounded-lg bg-white px-3 py-1.5 text-[12px] text-primary-700 shadow-sm hover:bg-primary-50">
                打开单文件报告（.html，断网可看、角标可点）
              </a>
              <ToolsPanel projectId={projectId} />
              <div className="mt-2 text-[11px] text-gray-500">点导图上的论文节点可直接跳到右栏对应卡片。</div>
            </div>
          )}
        </div>
      </div>

      {/* PDF Drawer */}
      {drawerPaper && (
        <div className="absolute inset-y-0 right-0 z-20 flex w-[55%] flex-col border-l border-gray-200 bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5">
            <div className="truncate text-[13px] font-medium text-gray-700">{drawerPaper.title}</div>
            <button onClick={() => setDrawerPaper(null)} className="text-[13px] text-gray-500 hover:text-gray-600">关闭 ✕</button>
          </div>
          {pdfUrl === "__none__" ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 text-[13px] text-gray-500">
              <div>未找到合法 OA 全文（该论文仅题录可见）。</div>
              {drawerPaper.url && <a className="text-primary-600 underline" href={drawerPaper.url} target="_blank" rel="noreferrer">打开出版方页面</a>}
            </div>
          ) : pdfUrl ? (
            <div className="flex flex-1 flex-col overflow-hidden">
              <div className="flex items-center gap-2 border-b border-gray-100 px-3 py-2">
                <input value={drawerQa} onChange={e => setDrawerQa(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && drawerQaAsk()}
                  placeholder="问研伴：结合这篇论文提问…"
                  className="h-9 flex-1 rounded-lg border border-gray-200 px-3 text-[13px] outline-none focus:border-primary-400" />
                <button onClick={drawerQaAsk}
                  className="h-9 rounded-lg bg-primary-600 px-3 text-[12.5px] text-white hover:bg-primary-700">问研伴</button>
              </div>
              {sections.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 border-b border-gray-100 px-3 py-1.5">
                  <span className="text-[11px] text-gray-500">只问某一节</span>
                  <button onClick={() => setActiveSection("")}
                    className={`rounded-full px-2 py-0.5 text-[11.5px] ${!activeSection ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                    全文
                  </button>
                  {sections.map(s => (
                    <button key={s.name} onClick={() => setActiveSection(s.name)}
                      title={`${s.chars} 字`}
                      className={`rounded-full px-2 py-0.5 text-[11.5px] ${activeSection === s.name ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                      {s.name}
                    </button>
                  ))}
                </div>
              )}
              {/* 页码跳转交给浏览器自带的 PDF 视图（#page=N）：我们不自己渲染 PDF，
                  所以这里只有"定位到第几页"，没有整页渲染/缩放/书签（那才是阅读器，本轮不做）。 */}
              <iframe title="pdf" data-testid="pdf-frame"
                src={drawerPage ? `${pdfUrl}#page=${drawerPage}` : pdfUrl} className="flex-1" />
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center text-[13px] text-gray-500">全文加载中…</div>
          )}
        </div>
      )}
    </div>
  );
}

function KnowledgePanel({ projectId, onCount }: { projectId: string; onCount?: (n: number | null) => void }) {
  const [notes, setNotes] = useState<NoteMeta[]>([]);
  const [loadErr, setLoadErr] = useState(false);
  const [open, setOpen] = useState<{ title: string; body: string; backlinks: string[] } | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    void fetch(`${API}/knowledge/${projectId}`).then(r => r.json())
      .then(d => { const ns: NoteMeta[] = d.notes || []; setNotes(ns); setLoadErr(false); onCount?.(ns.length); })
      // 读不到不等于"没有笔记"：计数报 null，空态文案也得换，否则一次网络抖动就长成"还没开始"的样子
      .catch(() => { setNotes([]); setLoadErr(true); onCount?.(null); });
  }, [projectId, open?.title, onCount]);

  const openNote = useCallback((title: string) => {
    void fetch(`${API}/knowledge/${projectId}/note?title=${encodeURIComponent(title)}`)
      .then(r => (r.ok ? r.json() : null)).then(d => d && setOpen(d));
  }, [projectId]);

  // 双链 [[笔记名]] → 可点：捕获层拦截 #wiki: 锚点
  const onBodyClick = useCallback((e: React.MouseEvent) => {
    const a = (e.target as Element).closest?.("a[href^='#wiki:']");
    const href = a?.getAttribute("href");
    if (!href) return;
    e.preventDefault();
    openNote(decodeURIComponent(href.slice(6)));
  }, [openNote]);

  const body = open ? open.body.replace(/\[\[([^\]]+)\]\]/g, "[[$1]](#wiki:$1)") : "";

  return (
    <div className="space-y-2.5">
      <div className="flex gap-1.5">
        <input value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === "Enter" && q.trim() && void fetch(
            `${API}/knowledge/${projectId}/search?q=${encodeURIComponent(q.trim())}`)
            .then(r => r.json()).then(d => setNotes((d.hits || []).map((h: { note: string; context: string }) =>
              ({ note: h.note, bytes: 0, links: [], head: h.context })))).catch(() => setNotes([]))}
          placeholder="搜笔记全文…"
          className="h-8 flex-1 rounded-lg border border-gray-200 px-2.5 text-[12.5px] outline-none focus:border-primary-400" />
        <button onClick={() => setOpen(null)} className="h-8 rounded-lg bg-white px-2.5 text-[12px] text-gray-500 shadow-sm hover:bg-gray-50">
          看列表
        </button>
      </div>

      {open ? (
        <div onClickCapture={onBodyClick}>
          <div className="text-[13.5px] font-medium text-gray-800">{open.title}</div>
          <div className="mt-1 rounded-xl bg-white p-3 shadow-sm">
            <Markdown text={body} />
          </div>
          {open.backlinks.length > 0 && (
            <div className="mt-2 rounded-xl bg-white p-3 shadow-sm">
              <div className="text-[11px] label-caps text-gray-500">反向链接（{open.backlinks.length}）</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {open.backlinks.map(b => (
                  <button key={b} onClick={() => openNote(b)}
                    className="rounded-lg bg-primary-50 px-2 py-0.5 text-[11.5px] text-primary-700 hover:bg-primary-100">{b}</button>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {notes.map(n => (
            <button key={n.note} onClick={() => openNote(n.note)}
              className="w-full rounded-xl bg-white p-3 text-left shadow-sm hover:bg-primary-50/40">
              <div className="text-[13px] font-medium text-gray-800">{n.note}</div>
              <div className="mt-0.5 line-clamp-2 text-[11.5px] text-gray-500">{n.head}</div>
              {n.links.length > 0 && <div className="mt-1 text-[11px] text-gray-500">关联：{n.links.slice(0, 3).join("、")}</div>}
            </button>
          ))}
          {notes.length === 0 && (
            <div className="px-1 text-[12px] text-gray-500">
              {loadErr ? "笔记清单没读到（刷新或再问一句会重试）" : "还没有笔记。收藏或排除论文时，研伴会自动写下第一条。"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
