import { useState } from "react";
import { StatusBadge } from "../components/Badge";
import { loadHistory, removeRecord, type TaskRecord } from "../lib/storage";

// 任务记录页（P4）：本轮任务列表，localStorage 持久化，点击回看报告
export default function TaskHistory({ onOpen }: { onOpen: (taskId: string) => void }) {
  const [list, setList] = useState<TaskRecord[]>(loadHistory);

  return (
    <div className="h-screen flex-1 overflow-y-auto px-8 py-8">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-[20px] font-bold text-gray-900">任务记录</h1>
            <p className="mt-1 text-[13px] text-gray-500">
              保存在本机浏览器（localStorage），共 {list.length} 条；点击任意一条回看报告。
            </p>
          </div>
          {list.length > 0 && (
            <button
              onClick={() => { list.forEach((r) => removeRecord(r.task_id)); setList([]); }}
              className="btn-secondary !h-[32px] !px-3 !text-[12.5px] text-gray-500"
            >
              清空记录
            </button>
          )}
        </div>
        {list.length === 0 ? (
          <div className="mt-10 rounded-2xl border border-dashed border-gray-200 p-12 text-center text-[14px] text-gray-500">
            还没有任务记录。去「新任务」页提交第一个研究问题吧。
          </div>
        ) : (
          <div className="mt-6 space-y-3">
            {list.map((r) => (
              <button
                key={r.task_id}
                onClick={() => onOpen(r.task_id)}
                className="block w-full rounded-2xl border border-gray-100 bg-white p-5 text-left shadow-sm transition-colors hover:border-primary-200 hover:bg-primary-50/20"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11.5px] tracking-tight text-gray-500">{r.task_id}</span>
                  <StatusBadge status={r.status} />
                </div>
                <p className="mt-2 line-clamp-2 text-[14px] leading-6 text-gray-800">{r.query}</p>
                <div className="tnum mt-2 text-[12px] text-gray-500">
                  {new Date(r.submitted_at).toLocaleString("zh-CN")} · 端到端 {(r.elapsed_ms / 1000).toFixed(1)}s · RPC {r.rpc_total} 次
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
