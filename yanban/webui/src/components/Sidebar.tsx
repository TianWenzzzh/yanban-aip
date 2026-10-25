import Owl from "./Owl";
import { FOOTER_TEXT } from "../constants";

export type PageKey = "companion" | "workbench" | "history" | "data" | "protocol" | "settings";

const NAV: { key: PageKey; label: string; icon: JSX.Element }[] = [
  {
    key: "companion",
    label: "研伴 2.0",
    icon: (
      <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="10" cy="10" r="7" />
        <path d="M6.5 8.5 h.01 M13.5 8.5 h.01 M7 12.5 q3 2 6 0" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "workbench",
    label: "新任务",
    icon: (
      <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M11 3 L5 11.5 H9.5 L8.5 17 L15 8.5 H10.5 Z" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    key: "history",
    label: "任务记录",
    icon: (
      <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="10" cy="10" r="7" />
        <path d="M10 6 v4.2 l3 1.8" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "data",
    label: "数据概览",
    icon: (
      <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M3.5 16.5 v-6 M8.2 16.5 v-10 M12.9 16.5 v-4 M17 16.5 v-8" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "protocol",
    label: "协议演示",
    icon: (
      <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7">
        <rect x="2.5" y="4" width="6" height="5" rx="1.2" />
        <rect x="11.5" y="11" width="6" height="5" rx="1.2" />
        <path d="M5.5 9 v3.5 a1.5 1.5 0 0 0 1.5 1.5 h4.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "settings",
    label: "设置",
    icon: (
      <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="10" cy="10" r="2.6" />
        <path d="M10 3 v2 M10 15 v2 M3 10 h2 M15 10 h2 M5.2 5.2 l1.4 1.4 M13.4 13.4 l1.4 1.4 M14.8 5.2 l-1.4 1.4 M6.6 13.4 l-1.4 1.4" strokeLinecap="round" />
      </svg>
    ),
  },
];

export default function Sidebar({ page, onNavigate, dark, onToggleDark }: {
  page: PageKey;
  onNavigate: (p: PageKey) => void;
  dark: boolean;
  onToggleDark: () => void;
}) {
  return (
    <aside className="flex h-screen w-16 shrink-0 flex-col border-r border-gray-100 bg-white md:w-56">
      <div className="flex items-center gap-2.5 px-3 pb-5 pt-6 md:px-5">
        <Owl size={38} />
        <div className="hidden md:block">
          <div className="text-[16.5px] font-bold leading-tight tracking-[0.01em] text-gray-900">研伴工作台</div>
          <div className="label-caps mt-0.5 text-gray-500">AIP GB/Z 185</div>
        </div>
      </div>
      <div className="mx-3 mb-2 h-px bg-gray-100 md:mx-5" />
      <nav className="flex-1 space-y-0.5 px-2 pt-1 md:px-3">
        {NAV.map((n) => {
          const active = page === n.key;
          return (
            <button
              key={n.key}
              title={n.label}
              onClick={() => onNavigate(n.key)}
              className={`relative flex w-full items-center justify-center gap-2.5 rounded-lg px-2 py-[7px] text-[13.5px] transition-colors md:justify-start md:px-3 ${
                active ? "bg-primary-50 font-semibold text-primary-700" : "font-normal text-gray-600 hover:bg-gray-50"
              }`}
            >
              {active && <span className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-primary-600" />}
              <span className={active ? "text-primary-600" : "text-gray-500"}>{n.icon}</span>
              <span className="hidden md:inline">{n.label}</span>
            </button>
          );
        })}
      </nav>
      <footer className="border-t border-gray-100 px-2 py-4 text-[11px] leading-[1.7] text-gray-500 md:px-5">
        <button onClick={onToggleDark} title="切换暗色（偏好会记住，也可用 ?dark=1 深链）"
          className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg border border-gray-200 px-2 py-[7px] text-[12.5px] text-gray-600 transition-colors hover:bg-gray-50 md:justify-start md:px-3">
          <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7">
            {dark
              ? <><circle cx="10" cy="10" r="4" /><path d="M10 2.5 v2 M10 15.5 v2 M2.5 10 h2 M15.5 10 h2 M4.7 4.7 l1.4 1.4 M13.9 13.9 l1.4 1.4 M15.3 4.7 l-1.4 1.4 M6.1 13.9 l-1.4 1.4" strokeLinecap="round" /></>
              : <path d="M16 12.5 A7 7 0 1 1 7.5 4 A5.5 5.5 0 0 0 16 12.5 Z" strokeLinejoin="round" />}
          </svg>
          <span className="hidden md:inline">{dark ? "浅色模式" : "暗色模式"}</span>
        </button>
        <div className="hidden md:block">{FOOTER_TEXT}</div>
      </footer>
    </aside>
  );
}
