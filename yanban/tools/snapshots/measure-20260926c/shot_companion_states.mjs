// P0 视觉验收仪器：把右栏进度卡在各状态下渲成图，并同时给出文本层读数。
//
// 为什么不是 `msedge --screenshot`：那种一次性进程在别的 Edge 实例在跑时会「RC=0、不出文件」，
// 而且没法驱动「发一条消息」这个状态迁移。这里用 CDP 常驻一个 headless Edge，
// 逐场景 navigate → 输入 → 发送 → 等进度卡出现 → 截图，并把卡片文案与刻度几何一起印出来，
// 免得「图上看着在 57%」这种事只靠眼睛（轮 135 的教训：从截图读的数字必须回文本层复核）。
//
// 用法：node 输出/scripts/shot_companion_states.mjs --base http://127.0.0.1:8090 \
//          --outdir code/yanban/docs/evidence --prefix g153 [--scenarios A_before,B_clarify,...] [--port 9412]
// 退出码：0 = 全部出图且各图字节互不相同；2 = 有场景没出图/没读到进度卡（读数作废，不报喜）
import { mkdirSync, readFileSync, writeFileSync, rmSync, mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const argv = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith("--")) argv[a.slice(2)] = process.argv[++i];
}
const base = argv.base || "http://127.0.0.1:8090";
const outdir = argv.outdir || "docs/evidence";
const prefix = argv.prefix || "p0";
const cdpPort = argv.port || "9412";
const only = argv.scenarios ? argv.scenarios.split(",") : null;

const EDGE = ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"].find(p => existsSync(p));

// 每个场景：宽度（决定 lg 断点 1024 左右）、深色、要不要发消息、发哪句
// expect: "visible" = 进度卡该在画面里；"hidden" = 窄屏右栏收起，卡只存在于抽屉里
const VAGUE = "我想做多模态钓鱼网站检测，但不知道从哪开始。";
const RICH = "我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点，老师要求三个月内出一篇 SCI；"
  + "我只有轻薄本没有 GPU，数据可以自己爬。文献想看近五年的，尽量能免费下载。";
const WEIRD = "我第一次做科研，想做一套基于量子占星术的推荐系统，深度学习只学过一点，老师要求三个月内出一篇 SCI；"
  + "文献想看近五年的，尽量能免费下载，我只有轻薄本没有 GPU。";
const SCENARIOS = [
  { name: "A_before_light", w: 1280, h: 1500, dark: false, say: "" },
  { name: "A_before_dark", w: 1280, h: 1500, dark: true, say: "" },
  { name: "B_clarify_light", w: 1280, h: 1500, dark: false, say: VAGUE },
  { name: "B_clarify_dark", w: 1280, h: 1500, dark: true, say: VAGUE },
  { name: "C_rich_light", w: 1280, h: 1500, dark: false, say: RICH },
  // 已过线**且论文为空**的那一态：C 场一旦检索到论文，四个面板都不再垫卡，看不到新文案
  { name: "F_pastline_light", w: 1280, h: 1500, dark: false, say: WEIRD },
  { name: "D_narrow_closed", w: 780, h: 1200, dark: false, say: VAGUE, open: false, expect: "hidden" },
  { name: "E_narrow_open", w: 780, h: 1200, dark: false, say: VAGUE, open: true },
  // P7 发现性：产物出口在**表头**，所以窄屏右栏收起时也必须看得见
  { name: "G_artifact_narrow", w: 780, h: 1200, dark: false, say: RICH, open: false, expect: "any" },
  { name: "H_artifact_wide", w: 1280, h: 1500, dark: false, say: RICH, expect: "any" },
];

const sleep = ms => new Promise(r => setTimeout(r, ms));
const profile = mkdtempSync(path.join(tmpdir(), "p0profile-"));
const { spawn } = await import("node:child_process");
const edge = spawn(EDGE, ["--headless=new", `--remote-debugging-port=${cdpPort}`,
  `--user-data-dir=${profile}`, "--disable-gpu", "--hide-scrollbars",
  "--no-first-run", "--window-size=1280,1500", "about:blank"], { stdio: "ignore" });

let version = null;
for (let i = 0; i < 40; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${cdpPort}/json/version`);
    version = await r.json(); break;
  } catch { await sleep(500); }
}
if (!version) {
  console.log(`判读：连不上 CDP :${cdpPort} ⇒ 全部读数作废（RC=2）`);
  edge.kill(); process.exitCode = 2;
} else {
  await run();
}

async function run() {
  const list = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
  const page = list.find(t => t.type === "page");
  if (!page) { console.log("判读：没有可连接的页面 ⇒ RC=2"); process.exitCode = 2; return; }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  await new Promise(r => ws.onopen = r);
  const ev = async expr => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 200));
    return r.result.value;
  };

  mkdirSync(outdir, { recursive: true });
  const produced = [];
  let failed = [];

  for (const sc of (only ? SCENARIOS.filter(s => only.includes(s.name)) : SCENARIOS)) {
    const url = `${base}/?page=companion${sc.dark ? "&dark=1" : ""}`;
    await send("Emulation.setDeviceMetricsOverride",
      { width: sc.w, height: sc.h, deviceScaleFactor: 1, mobile: false });
    // 主题写在 localStorage，同 profile 会串到下一张（轮 142 的 shot_pages 教训）：
    // 必须在**同源**下清空，about:blank 上的 clear 清的是另一个 origin。
    await send("Page.navigate", { url: `${base}/?page=companion` });
    await sleep(1500);
    await ev(`(() => { try { localStorage.clear(); return 1; } catch (e) { return 0; } })()`);
    await send("Page.navigate", { url });
    // 首屏文案：等应用挂载（轮 153 的坑④：首次 navigate 常未挂载）
    let mounted = false;
    for (let i = 0; i < 20; i++) {
      await sleep(800);
      mounted = await ev(`!!Array.from(document.querySelectorAll("div,span")).find(d => d.textContent === "研伴 · 认知层")`);
      if (mounted) break;
    }
    if (!mounted) { await send("Page.reload", {}); await sleep(4000); }

    let replyState = "无需回复";
    if (sc.say) {
      const typed = await ev(`(() => {
        const ta = document.querySelector("textarea") || document.querySelector("input");
        if (!ta) return "no-input";
        const proto = ta.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(ta, ${JSON.stringify(sc.say)});
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        return ta.value.length; })()`);
      await sleep(300);
      const clicked = await ev(`(() => {
        const b = Array.from(document.querySelectorAll("button")).find(x => x.textContent.trim() === "发送");
        if (!b) return "no-button"; b.click(); return "ok"; })()`);
      if (typed === "no-input" || clicked === "no-button") {
        failed.push(`${sc.name}: 输入=${typed} 按钮=${clicked}`);
      }
      // 等的是**这一轮真的落地**（卡上出现「理解度」＝intent 回来了），不是「卡存在」——
      // 卡存在是常态，拿它当等待条件会把「回复还没到」读成已就绪（首跑 C 场就是这么假的）
      replyState = "超时未见理解度";
      for (let i = 0; i < 60; i++) {
        await sleep(1500);
        const ready = await ev(`(() => { const t = document.body.innerText;
          return t.includes("理解度") || t.includes("信息够了") ? 1 : 0; })()`);
        if (ready) { replyState = "理解度已出现"; break; }
      }
      await sleep(3000);
    }
    if (sc.open) {
      await ev(`(() => { const b = Array.from(document.querySelectorAll("button")).find(x => x.textContent.trim() === "论文·知识库"); if (b) b.click(); return !!b; })()`);
      await sleep(1200);
    }
    // 进度卡是「面板空了才垫场」的：论文一多，论文 tab 里就没它了（本轮 C 场实测）。
    // 所以找不到卡不等于卡坏了，得逐个 tab 找一遍，并把「这次是在哪个 tab 看到的」记下来。
    let cardTab = "";
    const CARD = `!!Array.from(document.querySelectorAll("div")).find(d => /rounded-xl/.test(String(d.className))
        && /p-3/.test(String(d.className)) && (d.innerText || "").startsWith("研究进度"))`;
    for (const t of ["论文", "导图·工具", "记忆", "知识库"]) {
      if (await ev(CARD)) { cardTab = t; break; }
      // 🔴 必须按 tab 标签的**完整**形状匹配：窄屏那个抽屉按钮文案是「论文·知识库」，
      // 用 startsWith("论文") 会把它点开，于是"卡不可见"其实是仪器自己把面板打开了（本轮踩过）。
      await ev(`(() => { const re = new RegExp("^" + ${JSON.stringify(t)} + "( \\\\d+)?$");
        const b = Array.from(document.querySelectorAll("button")).find(x => re.test(x.textContent.trim()));
        if (b) b.click(); return !!b; })()`);
      await sleep(900);
    }

    const reading = await ev(`(() => {
      const card = Array.from(document.querySelectorAll("div")).find(d => /rounded-xl/.test(String(d.className))
        && /p-3/.test(String(d.className)) && (d.innerText || "").startsWith("研究进度"));
      const r = e => { const b = e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; };
      const out = { card: card ? (card.innerText || "").replace(/\\s+/g, " ") : "", rect: card ? r(card) : null,
                    viewport: {w: innerWidth, h: innerHeight},
                    panelVisible: !!card && card.getBoundingClientRect().width > 0,
                    theme: document.documentElement.classList.contains("dark") ? "dark" : "light" };
      if (card) {
        const track = Array.from(card.querySelectorAll("div")).find(d => /h-2/.test(d.className) && /rounded-full/.test(d.className) && d.children.length);
        if (track) {
          const tr = r(track);
          const fill = track.firstElementChild ? r(track.firstElementChild) : null;
          const tick = Array.from(track.querySelectorAll("div")).map(r).find(x => x.w <= 4 && x.h >= 8);
          out.track = {w: +tr.w.toFixed(1), fillPct: fill ? +(100 * fill.w / tr.w).toFixed(1) : null,
                       tickPct: tick ? +(100 * (tick.x - tr.x) / tr.w).toFixed(1) : null,
                       tickInside: tick ? (tick.x >= tr.x - 1 && tick.x + tick.w <= tr.x + tr.w + 1) : null};
        }
        out.gapItems = card.querySelectorAll("ul li").length;
        out.overflow = card.scrollWidth > card.clientWidth + 1;
      }
      const art = Array.from(document.querySelectorAll("a")).filter(a => (a.getAttribute("href") || "").includes("/artifact/"));
      // 「看得见」必须是**画面最前面**那个：本轮差点把「被抽屉盖住的表头链接」读成可见，
      // 所以判定用 elementFromPoint 命中测试，而不是 boundingRect.width > 0。
      const topmost = a => {
        const b = a.getBoundingClientRect();
        if (b.width <= 0 || b.height <= 0) return false;
        const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
        return !!hit && (hit === a || a.contains(hit) || hit.closest("a") === a);
      };
      out.artifact = { n: art.length,
                       inHeader: art.some(a => !!a.closest("header")),
                       visible: art.some(topmost),
                       hasBox: art.some(a => a.getBoundingClientRect().width > 0),
                       text: art.length ? art[0].textContent.trim() : "" };
      return JSON.stringify(out); })()`);
    const rd = JSON.parse(reading);
    const want = sc.dark ? "dark" : "light";
    const expectHidden = sc.expect === "hidden";
    const file = path.join(outdir, `${prefix}_progress_${sc.name}.png`);
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(file, Buffer.from(shot.data, "base64"));
    produced.push([sc.name, rd.panelVisible ? "卡可见" : (expectHidden ? "卡收起(预期)" : "卡不可见"),
      rd.theme === want ? want : `主题串了→${rd.theme}`,
      rd.overflow ? "横向溢出" : "无溢出",
      rd.track ? `填充${rd.track.fillPct}% 刻度${rd.track.tickPct}% ${rd.track.tickInside ? "在线内" : "出线"}` : "无进度条",
      `${replyState}｜卡所在 tab=${cardTab || "没找到"}`]);
    if (rd.theme !== want) failed.push(`${sc.name}: 期望 ${want} 实为 ${rd.theme}（主题串场＝这张图不算该主题的读数）`);
    if (!expectHidden && sc.expect !== "any" && !rd.panelVisible) failed.push(`${sc.name}: 进度卡不可见`);
    if (expectHidden && rd.panelVisible) failed.push(`${sc.name}: 窄屏收起态里进度卡竟然可见`);
    if (sc.expect === "any") {
      if (!rd.artifact.inHeader || !rd.artifact.visible) {
        failed.push(`${sc.name}: 表头没有看得见的产物出口（inHeader=${rd.artifact.inHeader} visible=${rd.artifact.visible} 共 ${rd.artifact.n} 个链接）`);
      }
    }
    console.log(`  产物出口：${rd.artifact.n} 个链接｜在表头=${rd.artifact.inHeader}｜看得见=${rd.artifact.visible}｜有盒子=${rd.artifact.hasBox}｜文案「${rd.artifact.text}」`);
    if (rd.card) console.log(`\n[${sc.name}] ${sc.w}x${sc.h} ${rd.theme}｜${replyState}\n  文案: ${rd.card.slice(0, 300)}`);
    else console.log(`\n[${sc.name}] ${sc.w}x${sc.h} ${rd.theme}｜${replyState}\n  文案: （没读到进度卡）`);
    if (rd.gapItems !== undefined) console.log(`  缺项条数=${rd.gapItems}｜刻度读数=${JSON.stringify(rd.track)}`);

    if (rd.rect && rd.rect.w > 0 && rd.panelVisible) {   // 卡片裁切放大图，便于逐字看排版
      const c = rd.rect, pad = 8;
      const crop = await send("Page.captureScreenshot", {
        format: "png",
        clip: { x: Math.max(0, c.x - pad), y: Math.max(0, c.y - pad),
                width: Math.min(c.w + 2 * pad, sc.w), height: Math.min(c.h + 2 * pad, sc.h - c.y), scale: 2 }
      });
      writeFileSync(file.replace(/\.png$/, "_crop.png"), Buffer.from(crop.data, "base64"));
      produced.push([`${sc.name}_crop`, "裁切", "", "", "", ""]);
    }
  }

  ws.close();
  edge.kill();
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 400 }); }
  catch (e) { console.log(`（临时 profile 没删掉：${String(e.message).slice(0, 60)}，不影响读数）`); }

  console.log("\n场景｜卡｜横向溢出｜刻度");
  for (const p of produced) console.log(`  ${p.join(" | ")}`);
  const sizes = new Map();
  for (const [n] of produced) {
    const f = path.join(outdir, `${prefix}_progress_${n}.png`);
    try { const b = readFileSync(f).length; sizes.set(b, (sizes.get(b) || []).concat(n)); } catch { }
  }
  const dup = [...sizes.values()].filter(v => v.length > 1);
  const expected = (only ? SCENARIOS.filter(s => only.includes(s.name)) : SCENARIOS).length;
  const onDisk = produced.filter(p => !p[0].endsWith("_crop")).length;
  console.log(`\n出图 ${onDisk}/${expected} 场｜重复字节组 ${dup.length ? dup.map(d => d.join(",")).join(" ; ") : "0"}`);
  if (failed.length) { console.log("不合格场景：" + failed.join(" ; ")); }
  const ok = onDisk === expected && !failed.length && !dup.length;
  console.log(`判读：${ok ? "P0 出图完成（像素目视仍需人看图，本行只证明「图存在且互不相同」）" : "P0 未达成 ⇒ 读数作废（RC=2）"}`);
  process.exitCode = ok ? 0 : 2;
}
