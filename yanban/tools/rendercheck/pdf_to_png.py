"""把 PDF 摊开给人看：逐页渲染成 PNG，并顺手量「有没有文字被裁到页面外」。

为什么要有它（轮 136）：产物 CSS 里有 `@media print`，而打印/PDF 是它第三个渲染面
（前两个是屏幕浅色、深色）。这一轮之前从没看过 —— 只因为 Read 工具缺 poppler 就放弃，
等于把「凡是渲染给人看的东西必须亲眼看图」这条判据悄悄缩小了范围。
本机没有 pdftoppm/pdftotext，所以走 PyMuPDF（`uv run --with pymupdf`）。

用法：python pdf_to_png.py <in.pdf> <out_prefix> [zoom]
退出码：0=渲染完且没测到越界文字｜1=有文字块超出页面边界｜2=打不开
"""
from __future__ import annotations

import sys

try:
    import fitz
except ImportError:                                    # pragma: no cover
    print("缺 pymupdf ⇒ 用 `uv run --no-project --python 3.12 --with pymupdf` 跑")
    return_code = 2
    sys.exit(return_code)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, prefix = sys.argv[1], sys.argv[2]
    zoom = float(sys.argv[3]) if len(sys.argv) > 3 else 1.6
    try:
        doc = fitz.open(src)
    except Exception as exc:                           # noqa: BLE001
        print(f"{src}：打不开（{exc}）⇒ 不出数（RC=2）")
        return 2
    over = 0
    thin: list[tuple[int, float]] = []
    LOW_FILL = 0.5
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        out = f"{prefix}_{i + 1:02d}.png"
        pix.save(out)
        words = page.get_text("words")
        rect = page.rect
        clipped = [w for w in words
                   if w[0] < rect.x0 - 1 or w[1] < rect.y0 - 1
                   or w[2] > rect.x1 + 1 or w[3] > rect.y1 + 1]
        over += len(clipped)
        # 版面占用率 = 最低一行文字的下沿 / 页高。大面积留白只有在这里才看得见：
        # 光数页数会漏掉「第 1 页只有三行、正文整块被 page-break 推到第 2 页」这种缺陷。
        fill = (max((w[3] for w in words), default=rect.y0) - rect.y0) / (rect.y1 - rect.y0)
        if fill < LOW_FILL and i + 1 < doc.page_count:
            thin.append((i + 1, fill))
        first = " ".join(w[4] for w in words[:8])
        print(f"第 {i + 1} 页：{pix.width}x{pix.height}｜文字块 {len(words)} 个｜越界 {len(clipped)} 个｜"
              f"版面占用 {fill:.0%}｜开头「{first[:38]}」")
    tail_blank = sum(1 for i, page in enumerate(doc) if not page.get_text("words"))
    print(f"判读：共 {doc.page_count} 页，空白页 {tail_blank} 页，越界文字块 {over} 个")
    if thin:
        print("⇒ 🔴 有非末页大面积留白：" + "、".join(f"第{p} 页只占 {f:.0%}" for p, f in thin)
              + "（多半是某条 page-break 规则把整块内容推走了）")
    if over:
        print("⇒ 🔴 有文字被裁到页面外（打印会丢内容）")
        return 1
    print("⇒ 没测到越界；这不等于排版好看，图还是要自己看")
    return 0


if __name__ == "__main__":
    sys.exit(main())
