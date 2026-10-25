import { useCallback, useEffect, useRef, useState } from "react";

const API = "/companion";

type Mem = { id: number; kind: string; tag: string; text: string; created_at: string; blocked: boolean; deletable: boolean; source: string };

/** 记忆抽屉：系统记得什么、哪些被「不再引用」、显式补写一条、删掉自己写的笔记。 */
export default function MemoryPanel({ projectId, onCount, focusId }: {
  projectId: string; onCount?: (n: number | null) => void; focusId?: number | null;
}) {
  const [items, setItems] = useState<Mem[]>([]);
  const focusRef = useRef<HTMLDivElement | null>(null);
  // 身份只认库里那行 id：文本相同是巧合，不是同一条（「不再引用」就是按文本挡的）
  const focusHit = typeof focusId === "number" && items.some(x => x.id === focusId);
  const [blocked, setBlocked] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  // 稳定画像：键名与中文标签都由后端给（PROFILE_KEYS / PROFILE_LABELS 只有一份出处）
  const [profile, setProfile] = useState<Record<string, string>>({});
  const [profileRecords, setProfileRecords] = useState<Record<string, { project?: string }>>({});
  const [profileLabels, setProfileLabels] = useState<Record<string, string>>({});
  const [userId, setUserId] = useState("");

  const load = useCallback(async () => {
    const r = await fetch(`${API}/memory/${projectId}/all`);
    if (r.ok) {
      const d = await r.json();
      const ms: Mem[] = d.memories || [];
      setItems(ms); setBlocked(d.blocked || []);
      onCount?.(ms.length);
    } else { setMsg("记忆清单没读到"); onCount?.(null); }   // 读不到 ≠ 没记忆，计数不许报 0
  }, [projectId, onCount]);
  const loadProfile = useCallback(async (uid: string) => {
    const r = await fetch(`${API}/users/${uid}/profile`);
    if (!r.ok) { setProfile({}); return; }        // 读不到就说"没读到"，不摆一张空卡冒充"没有画像"
    const d = await r.json();
    setProfile(d.profile || {}); setProfileRecords(d.records || {}); setProfileLabels(d.labels || {});
  }, []);
  useEffect(() => {
    void (async () => {
      const r = await fetch(`${API}/project/${projectId}`);
      if (!r.ok) return;
      const uid = (await r.json()).user_id || "";
      setUserId(uid);
      if (uid) await loadProfile(uid);
    })();
  }, [projectId, loadProfile]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (focusHit) focusRef.current?.scrollIntoView({ block: "center" }); }, [focusHit, items]);

  const run = async (fn: () => Promise<void>, ok: string) => {
    setBusy(true); setMsg("");
    try { await fn(); if (ok) setMsg(ok); } catch (e) { setMsg(`操作失败：${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  const add = () => run(async () => {
    const t = text.trim();
    if (!t) return;
    const r = await fetch(`${API}/memory/${projectId}/note`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) });
    if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
    setText(""); await load();
  }, "已记住，之后会优先想起来");

  const toggleBlock = (m: Mem) => run(async () => {
    const verb = m.blocked ? "unblock" : "block";
    const r = await fetch(`${API}/memory/${projectId}/${verb}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: m.text }) });
    if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
    await load();
  }, "");

  const forget = (m: Mem) => run(async () => {
    const r = await fetch(`${API}/memory/${projectId}/note/${m.id}`, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
    await load();
  }, "这条笔记已删除");

  const forgetProfile = (key: string) => run(async () => {
    const r = await fetch(`${API}/users/${userId}/profile/forget`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
    await loadProfile(userId);
  }, "这条画像已经不记了");

  const profileKeys = Object.keys(profile);

  return (
    <div className="space-y-2">
      {profileKeys.length > 0 && (
        <div className="rounded-xl bg-white p-3 shadow-sm" data-testid="profile-card">
          <div className="text-[12.5px] font-medium text-gray-700">它记住的关于你的稳定事实</div>
          <div className="mt-0.5 text-[11px] leading-4 text-gray-500">
            换题目时这些会一起带上；与本轮说法冲突时以本轮为准。每条都写着它是从哪个项目学来的。
          </div>
          <ul className="mt-1.5 space-y-1">
            {profileKeys.map(k => (
              <li key={k} className="flex items-start gap-1.5 text-[11.5px] leading-5 text-gray-700">
                <span className="shrink-0 rounded bg-primary-50 px-1.5 py-0.5 text-[10.5px] text-primary-700">
                  {profileLabels[k] || k}
                </span>
                <span className="flex-1">{profile[k]}</span>
                <button onClick={() => void forgetProfile(k)} disabled={busy}
                  className="shrink-0 rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-100 disabled:opacity-50">
                  别记了
                </button>
                {profileRecords[k]?.project && (
                  <span className="shrink-0 text-[10.5px] text-gray-500">
                    {profileRecords[k].project === projectId ? "本项目里学的" : `来自 ${profileRecords[k].project}`}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="rounded-xl bg-white p-3 shadow-sm">
        <div className="text-[12.5px] font-medium text-gray-700">告诉研伴一件要记住的事</div>
        <div className="mt-0.5 text-[11px] leading-4 text-gray-500">
          显式记下的内容会在之后每轮优先被想起来；只有这类笔记能直接删除，对话记录只能「不再引用」。
        </div>
        <div className="mt-2 flex gap-1.5">
          <input value={text} onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && text.trim() && !busy) void add(); }}
            placeholder="例如：我导师只认中文核心期刊"
            className="min-w-0 flex-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-[12px] outline-none focus:border-primary-400" />
          <button onClick={() => void add()} disabled={!text.trim() || busy}
            className="rounded-lg bg-primary-600 px-3 text-[12px] text-white hover:bg-primary-700 disabled:opacity-40">
            记住
          </button>
        </div>
        {msg && <div className="mt-1.5 text-[11.5px] text-gray-500">{msg}</div>}
      </div>

      <div className="rounded-xl bg-white p-3 shadow-sm">
        <div className="mb-1.5 flex items-center gap-2 text-[11.5px]">
          <span className="font-medium text-gray-700">系统记得的 {items.length} 条</span>
          {blocked.length > 0 && <span className="text-gray-500">其中 {blocked.length} 条已屏蔽引用</span>}
          <button onClick={() => void load()} disabled={busy}
            className="ml-auto rounded-lg bg-gray-50 px-2 py-0.5 text-gray-600 hover:bg-gray-100 disabled:opacity-50">刷新</button>
        </div>
        {typeof focusId === "number" && !focusHit && (
          <div className="text-[11px] text-gray-500" data-testid="memory-focus-missing">
            这条不在当前清单里——可能是本轮刚产生、还没进这份清单，也可能已经不在最近 40 条之内。
          </div>
        )}
        {items.length === 0 && <div className="text-[11.5px] text-gray-500">还没有记忆。聊几句或写一条，这里就会长出来。</div>}
        {/* 平时给清单打 46vh 的帽子，是为了上方的卡与输入框不被推走；但「看这一条」跳进来时
            这个盒子自己就在视口外半截，scrollIntoView 只能把行摆进盒子里面 ⇒ 排在靠后的那一行
            仍然看不见（轮 205 看 390 档的图抓到）。命中这一趟不打帽，改由外层面板体来滚。 */}
        <div className={`${focusHit ? "" : "max-h-[46vh] "}space-y-1.5 overflow-y-auto`}>
          {items.map(m => (
            <div key={m.id} ref={m.id === focusId ? focusRef : null}
              data-testid={m.id === focusId ? "memory-focus" : undefined}
              className={`rounded-lg border px-2.5 py-1.5 text-[11.5px] leading-5 ${
              m.id === focusId ? "ring-2 ring-primary-300 " : ""}${
              m.blocked ? "border-gray-100 bg-gray-50 text-gray-500" : "border-gray-100 bg-white text-gray-700"}`}>
              <div className="flex items-start gap-1.5">
                <span className="shrink-0 rounded bg-primary-50 px-1.5 py-0.5 text-[10.5px] text-primary-700">
                  {m.tag}
                </span>
                <span className={m.blocked ? "line-through" : ""}>{m.text}</span>
              </div>
              <div className="mt-0.5 text-[10.5px] leading-4 text-gray-500" data-testid={`mem-source-${m.id}`}>
                出处：{m.source || "无出处记录"}
              </div>
              <div className="mt-1 flex gap-1.5 text-[11px]">
                <button onClick={() => void toggleBlock(m)} disabled={busy}
                  className="rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-100 disabled:opacity-50">
                  {m.blocked ? "恢复引用" : "不再引用"}
                </button>
                {m.deletable && (
                  <button onClick={() => void forget(m)} disabled={busy}
                    className="rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-100 disabled:opacity-50">删除</button>
                )}
                <span className="ml-auto text-[10.5px] text-gray-500">{(m.created_at || "").slice(5, 16).replace("T", " ")}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
