"""把一张长截图按行裁开：看局部细节不用重新渲染一遍。

为什么要有它（轮 136）：上一轮为了看窄视口的不同部分，我用 `--window-size` 重渲染了 5 次
（w500/w600/w760/top/full…）——那是在拿渲染器当裁剪器用，每次几秒且换不来任意区间。
纯标准库（复用 png_right_edge 的解码 + 无 filter 重新编码），因此不需要 PIL。

用法：python png_crop.py <in.png> <out_prefix> <y0> <y1> [step]
      从 y0 起，每 step 行切一张高 (y1-y0) 的图，直到超出图像高度；输出 out_prefix_00.png…
退出码：0=裁了至少一张｜2=解不开/参数非法
"""
from __future__ import annotations

import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from png_right_edge import decode  # 同一把尺子，不重复实现解码


def encode(path: pathlib.Path, w: int, h: int, rgb: bytearray) -> None:
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__)
        return 2
    src, prefix = pathlib.Path(sys.argv[1]), sys.argv[2]
    h0, h1 = int(sys.argv[3]), int(sys.argv[4])
    step = int(sys.argv[5]) if len(sys.argv) > 5 else h1 - h0
    if h0 < 0 or h1 <= h0 or step <= 0:
        print("参数非法：要 0 <= y0 < y1，step > 0")
        return 2
    try:
        w, h, bpp, px = decode(src)
    except Exception as exc:                       # noqa: BLE001
        print(f"{src.name}：解不开（{exc}）⇒ 不出数（RC=2）")
        return 2
    n = 0
    for y in range(h0, min(h, h0 + step * 100), step):
        bh = min(h1 - h0, h - y)
        out = bytearray(3 * w * bh)
        for r in range(bh):
            s = ((y + r) * w) * bpp
            row = px[s:s + w * bpp]
            if bpp == 4:
                del row[3::4]                          # RGBA→RGB，整行切比逐像素快两个量级
            elif bpp != 3:
                row = bytearray(b"".join(px[s + c * bpp:s + c * bpp + 3] for c in range(w)))
            out[r * w * 3:(r + 1) * w * 3] = row
        p = pathlib.Path(f"{prefix}_{n:02d}.png")
        encode(p, w, bh, out)
        print(f"{p.name}：{w}x{bh}（原图 y={y}..{y + bh}）")
        n += 1
        if y + bh >= h:
            break
    if not n:
        print(f"原图高 {h}，没落到 y0={h0} 的区间 ⇒ 没裁出任何图（RC=2）")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
