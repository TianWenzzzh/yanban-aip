import { useEffect, useState } from "react";

// 设置页（P5，只读）：identity mode 当前态 + partners.toml 端点表
// 数据来自 Leader 只读端点 /identity，本页不提供任何修改能力
interface IdentityInfo {
  identity_mode: string;
  binding_enabled: boolean;
  leader_aic: string;
  partners: { name: string; endpoint: string; compose_endpoint?: string }[];
  source: string;
}

export default function Settings() {
  const [info, setInfo] = useState<IdentityInfo | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/identity")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setInfo)
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
  }, []);

  return (
    <div className="h-screen flex-1 overflow-y-auto px-8 py-8">
      <div className="mx-auto max-w-3xl">
        <h1 className="text-[20px] font-bold text-gray-900">设置</h1>
        <p className="mt-1 text-[13px] text-gray-500">本页只读：展示系统当前身份绑定模式与 Partner 注册表。</p>
        {error && <div className="mt-4 rounded-xl bg-red-50 p-4 text-[13px] text-red-600">加载失败：{error}</div>}
        {!info && !error && <div className="mt-6 text-[14px] text-gray-500">加载中…</div>}
        {info && (
          <>
            <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
              <div className="text-[14px] font-semibold text-gray-800">身份绑定模式（identity_mode.py）</div>
              <div className="mt-3 flex items-center gap-3">
                <span
                  className={`rounded-full px-3 py-1 text-[13px] font-medium ${
                    info.identity_mode === "trusted" ? "bg-purple-100 text-purple-700" : "bg-green-100 text-green-700"
                  }`}
                >
                  {info.identity_mode}
                </span>
                <span className="text-[13px] text-gray-500">
                  身份绑定{info.binding_enabled ? "已开启" : "未开启"}（demo 模式：外部 Leader 未持证书也可调用；trusted 模式：peer AIC 来自 mTLS 客户端证书，senderId 不匹配即 -32009 拒绝）
                </span>
              </div>
              <div className="mt-3 font-mono text-[11.5px] text-gray-500">Leader AIC：{info.leader_aic}</div>
              <div className="mt-1 text-[11px] text-gray-500">来源：{info.source}</div>
            </div>
            <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
              <div className="text-[14px] font-semibold text-gray-800">Partner 端点表（partners.toml）</div>
              <table className="mt-3 w-full text-[12.5px]">
                <thead>
                  <tr className="text-left text-[11px] font-medium tracking-[0.02em] text-gray-500">
                    <th className="pb-2 font-medium">名称</th>
                    <th className="pb-2 font-medium">宿主机端点</th>
                    <th className="pb-2 font-medium">容器网络端点</th>
                  </tr>
                </thead>
                <tbody>
                  {info.partners.map((p) => (
                    <tr key={p.name} className="border-t border-gray-50">
                      <td className="py-1.5 font-medium text-gray-700">{p.name}</td>
                      <td className="tnum py-1.5 font-mono text-[12px] text-gray-500">{p.endpoint}</td>
                      <td className="tnum py-1.5 font-mono text-[12px] text-gray-500">{p.compose_endpoint ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
