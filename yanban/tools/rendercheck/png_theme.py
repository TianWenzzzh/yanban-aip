"""这张截图到底是浅色还是深色？给个像素数，别靠眼看。

为什么要有它（轮 136）：产物 CSS 是「基础=浅色 + @media(prefers-color-scheme:dark) 覆盖」，
而我此前每一张截图都是深色底 —— 意味着**浅色分支一次都没被看过**。想用
`--blink-settings=preferredColorScheme=0` 切浅色，结果截图左上角仍是 (28,28,30)：旗子没生效，
如果我只看图就会把"和上一张一样"当成"浅色模式长这样"。
Blink 该枚举是 0=跟随系统 / 1=深色 / 2=浅色，传 0 等于没传。

用法：python png_theme.py <a.png> [<b.png> ...]
退出码：0=全部能判定｜2=有图解不开或判不出（占少数时也报）
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from png_right_edge import decode  # 同一套纯标准库解码，不引入第二把尺子

LIGHT_LUM = 200
DARK_LUM = 60
MIN_SHARE = 0.8            # 低于这个占比就不下结论：可能是截图根本没渲染完


def main() -> int:
    rc = 0
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        try:
            w, h, bpp, px = decode(p)
        except Exception as exc:                       # noqa: BLE001
            print(f"{p.name}：解不开（{exc}）⇒ 不出数（RC=2）")
            return 2
        step = max(1, (w * h) // 200000)               # 大图等距采样，够稳且快
        light = dark = n = 0
        for i in range(0, w * h, step):
            o = i * bpp
            lum = (px[o] * 30 + px[o + 1] * 59 + px[o + 2] * 11) // 100
            n += 1
            light += lum >= LIGHT_LUM
            dark += lum <= DARK_LUM
        corner = tuple(px[:3])
        if light / n >= MIN_SHARE:
            tag = "浅色"
        elif dark / n >= MIN_SHARE:
            tag = "深色"
        else:
            tag = "🔴 判不出（浅色/深色占比都不到 80%，可能没渲染完或被弹窗挡住）"
            rc = 2
        print(f"{p.name}：{w}x{h}｜左上 RGB {corner}｜浅色占 {light / n:.1%}｜深色占 {dark / n:.1%}"
              f" ⇒ {tag}")
    print(f"判读：{len(sys.argv) - 1} 张图" + ("" if rc == 0 else " ⇒ 有图判不出主题，别按「它长这样」下结论"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
