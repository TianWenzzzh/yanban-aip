"""这块矩形里到底有什么颜色？给占比，不靠眼睛判断「有没有画出来」。

为什么要有它（轮 137）：数据概览页六行横条，第一行是实心蓝条，其余五行看着像空轨道。
「看着像」不能当结论——上一轮我差点把一张空白截图当成 P1 缺陷。这里把区域里的颜色分布数出来：
非背景像素占比 = 这一行到底画没画。

用法：python png_colors.py <png> x0,y0,x1,y1 [x0,y0,x1,y1 ...]
退出码：0=都数出来了｜2=解不开/参数非法
"""
from __future__ import annotations

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from png_right_edge import decode


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src = pathlib.Path(sys.argv[1])
    try:
        w, h, bpp, px = decode(src)
    except Exception as exc:                       # noqa: BLE001
        print(f"{src.name}：解不开（{exc}）⇒ 不出数（RC=2）")
        return 2
    print(f"{src.name}：{w}x{h}")
    for spec in sys.argv[2:]:
        try:
            x0, y0, x1, y1 = (int(v) for v in spec.split(","))
        except ValueError:
            print(f"区域参数非法：{spec}")
            return 2
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            print(f"{spec}：落在图外 ⇒ 无数")
            continue
        cnt = collections.Counter()
        n = 0
        for y in range(y0, y1):
            row = y * w
            for x in range(x0, x1):
                o = (row + x) * bpp
                cnt[(px[o], px[o + 1], px[o + 2])] += 1
                n += 1
        top = cnt.most_common(3)
        bg, bg_n = top[0]
        parts = "、".join(f"#{r:02x}{g:02x}{b:02x} {c / n:.1%}" for (r, g, b), c in top)
        print(f"  区域 {spec}：{n} 个像素｜背景#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x} 占 {bg_n / n:.1%}"
              f"｜非背景 {1 - bg_n / n:.1%}｜{parts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
