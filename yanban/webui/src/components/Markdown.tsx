import { useMemo } from "react";
import { marked } from "marked";

// Markdown 报告渲染（复刻旧 ui.html 的 marked.parse 能力；同步渲染 + sanitize 关闭
// ——报告内容由本系统 Partner 产出，非任意用户输入，与旧 UI 威胁模型一致）
marked.setOptions({ async: false, gfm: true, breaks: false });

// 模型有时把编号写进正文（"1. 1. 标题"），marked 又把它渲染成有序列表、由 CSS ::marker 再补一个号
// ⇒ 屏上出现两个号（轮 219 看图抓到）。这里只折叠「列表项行首紧跟第二个标记」这一种形状：
// 第二个标记必须后跟空白，所以 "3.14" 这类小数不会被吃掉；行中没有列表起手的正文也不受影响。
export const DUP_LIST_MARKER = /^([ \t]*(?:[-*+]|\d+[.)])[ \t]+)((?:\d+[.)]|[-*+·])[ \t]+)/gm;

export function dedupeListMarkers(text: string): string {
  return (text || "").replace(DUP_LIST_MARKER, "$1");
}

export default function Markdown({ text }: { text: string }) {
  const html = useMemo(() => marked.parse(dedupeListMarkers(text)) as string, [text]);
  return (
    <div
      className="report-prose text-[14.5px] leading-7 text-gray-800"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
