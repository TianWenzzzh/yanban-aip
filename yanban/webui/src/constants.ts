// 前端常量：引导语、示例、阶段命名——纯静态文案，不新增任何 LLM 调用（P2 要求）

export const HERO_TITLE = "说说你要研究什么，剩下的交给我们";
export const HERO_SUB =
  "研伴会把你的想法拆解成检索、实验、写作、审稿等专业步骤，由多个科研智能体协作完成，全程可见。";

export const WIZARD_STEPS = [
  {
    title: "研究主题",
    hint: "用一句自然的话描述你想研究或了解的问题就好，不需要专业格式。",
    placeholder: "例如：调研大模型多智能体协作的研究现状，给我摘要与方向建议",
  },
  {
    title: "范围与约束",
    hint: "可选。告诉研伴文献数量、数据、侧重点等要求；没有就跳过。",
    placeholder: "例如：限 2023 年以来文献 3 篇；侧重协议标准化方向；给出对比表格",
  },
  {
    title: "确认提交",
    hint: "确认这段话会发给研伴。提交后多个智能体会立即开工。",
    placeholder: "",
  },
];

export const EXAMPLE_CHIPS = [
  "检索 multi-agent 文献 2篇，并写一段相关工作",
  "对比 RAG 与微调在医疗问答的准确率，设计实验方案",
  "论文全流程：主题：多智能体协作提升科研效率",
  "分析这批实验结果的相关性（粘贴 CSV 即可）",
];

// Partner → 中文阶段名（P3 流水线卡片用；与 partners.toml 注册表对应）
export const PARTNER_LABELS: Record<string, string> = {
  literature: "文献",
  mcp_gateway: "工具检索",
  experiment: "实验",
  analysis: "数据分析",
  writing: "写作",
  review: "审稿",
};

// 状态徽章语义（对齐旧 ui.html badge 配色）
export function stateKind(state: string): "pending" | "running" | "done" | "failed" | "awaiting" {
  const s = state.replace("TaskState.", "");
  if (!s || s === "pending") return "pending";
  if (s === "Completed") return "done";
  if (s === "Failed" || s === "Rejected" || s === "timeout" || s === "skipped") return "failed";
  if (s === "AwaitingInput") return "awaiting";
  return "running"; // Working / Accepted / AwaitingCompletion 等
}

export const STATE_LABELS: Record<string, string> = {
  pending: "待执行",
  Working: "执行中",
  Accepted: "已受理",
  Completed: "完成",
  AwaitingInput: "等待补充",
  AwaitingCompletion: "待交付",
  Failed: "失败",
  Rejected: "被拒",
  timeout: "超时",
  skipped: "上游失败跳过",
};

export const VERDICT_LABELS: Record<string, string> = {
  accept: "接收",
  minor: "小修",
  major: "大修",
  reject: "拒稿",
};

// 全站诚实页脚（R2：指标出处）
export const FOOTER_TEXT =
  "AI 辅助科研编排，非学术发表保证；指标为实测值与公式分。来源：bench-results-20260917.md / docs/quality_experiment_v2.md";
