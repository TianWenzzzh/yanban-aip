import { STATE_LABELS, stateKind } from "../constants";

// 状态徽章：配色语义对齐旧 ui.html——completed 绿 / running 橙 / failed 红 / awaiting 紫
const KIND_CLASS: Record<string, string> = {
  done: "bg-green-50 text-green-700 ring-1 ring-inset ring-green-200",
  running: "bg-orange-50 text-orange-700 ring-1 ring-inset ring-orange-200",
  failed: "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  awaiting: "bg-purple-50 text-purple-700 ring-1 ring-inset ring-purple-200",
  pending: "bg-gray-50 text-gray-500 ring-1 ring-inset ring-gray-200",
};

const KIND_DOT: Record<string, string> = {
  done: "bg-green-500",
  running: "bg-orange-500 animate-pulse",
  failed: "bg-red-500",
  awaiting: "bg-purple-500",
  pending: "bg-gray-300",
};

export function Badge({ state }: { state: string }) {
  const kind = stateKind(state);
  const label = STATE_LABELS[state.replace("TaskState.", "")] ?? state.replace("TaskState.", "");
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[11.5px] font-medium leading-none ${KIND_CLASS[kind]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${KIND_DOT[kind]}`} />
      {label}
    </span>
  );
}

export function StatusBadge({ status }: { status: "running" | "completed" | "failed" }) {
  const map = {
    running: { cls: "bg-orange-50 text-orange-700 ring-1 ring-inset ring-orange-200", text: "编排中" },
    completed: { cls: "bg-green-50 text-green-700 ring-1 ring-inset ring-green-200", text: "已完成" },
    failed: { cls: "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200", text: "失败" },
  } as const;
  const m = map[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[11.5px] font-medium leading-none ${m.cls}`}>
      {m.text}
    </span>
  );
}
