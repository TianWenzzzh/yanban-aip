import { useEffect, useState } from "react";
import Sidebar, { type PageKey } from "./components/Sidebar";
import Companion from "./pages/Companion";
import Workbench from "./pages/Workbench";
import TaskHistory from "./pages/TaskHistory";
import DataOverview from "./pages/DataOverview";
import ProtocolDemo from "./pages/ProtocolDemo";
import Settings from "./pages/Settings";

const PAGES: PageKey[] = ["companion", "workbench", "history", "data", "protocol", "settings"];

/** 主题与落地页提到 App 层：暗色要覆盖全部页面，深链要能直达任意一页。 */
function readQuery(key: string): string {
  return new URLSearchParams(window.location.search).get(key) || "";
}

export default function App() {
  const defaultPage = (import.meta.env.VITE_DEFAULT_UI as PageKey) || "workbench";
  const [page, setPage] = useState<PageKey>(() => {
    const fromUrl = readQuery("page");
    return PAGES.includes(fromUrl as PageKey) ? (fromUrl as PageKey) : defaultPage;
  });
  // 深链显式给值时以 URL 为准（与上面 `page` 同一套语义）：`?dark=0` 压不过已存的偏好，
  // 分享"浅色"链接的人自己会先掉进暗色；只有 URL 没给值才回落到 localStorage。
  const [dark, setDark] = useState(() => {
    const fromUrl = readQuery("dark");
    if (fromUrl === "1" || fromUrl === "0") return fromUrl === "1";
    return localStorage.getItem("yanban-dark") === "1";
  });
  const [reopenTaskId, setReopenTaskId] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("yanban-dark", dark ? "1" : "0");
  }, [dark]);

  // 侧栏换页时 URL 原本一动不动：屏幕上已经是「数据概览」，地址栏还写着 ?page=companion
  // ⇒ 刷新、分享、发深链都会跳回进入时那一页。主题既然承诺了"可用 ?dark=1 深链"，页面也该同样可分享。
  useEffect(() => {
    const u = new URL(window.location.href);
    u.searchParams.set("page", page);
    window.history.replaceState({}, "", u);
  }, [page]);

  const openTask = (taskId: string) => {
    setReopenTaskId(taskId);
    setPage("workbench");
  };

  const navigate = (p: PageKey) => {
    if (p !== "workbench") setReopenTaskId(null);
    setPage(p);
  };

  return (
    <div className="flex min-h-screen bg-gray-50 text-gray-900">
      <Sidebar page={page} onNavigate={navigate} dark={dark} onToggleDark={() => setDark(v => !v)} />
      <main className="flex min-w-0 flex-1 flex-col">
        {page === "companion" && <Companion />}
        {page === "workbench" && <Workbench reopenTaskId={reopenTaskId} />}
        {page === "history" && <TaskHistory onOpen={openTask} />}
        {page === "data" && <DataOverview />}
        {page === "protocol" && <ProtocolDemo />}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}
