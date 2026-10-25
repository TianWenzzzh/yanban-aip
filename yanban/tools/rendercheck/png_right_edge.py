"""看图也要有数值判据：这张截图里，内容有没有被右边缘裁掉（横向溢出）。

为什么要有它（轮 135）：我这一轮已经犯过一次「把图上的 4 看成 1」。窄视口下「有没有溢出」
本来就该是一个像素坐标，而不是我盯着图说"看起来被切了"。
做法：纯标准库解 PNG（zlib + 五种 filter 还原），按页背景色找出「最右一列还有内容的 x」。
🔴 **可靠下限 500px**（实测，轮 135）：headless Edge 有最小窗口宽度——同一份页面在
`--window-size=390` 下 PNG 宽 390 但**布局视口仍是 ~500**，于是内容被裁、这台仪器把没溢出的页面
判成「贴边」。自证方法：放一条 `float:right` 的探针带，若它在截图里**整条都不见**，那就是布局视口
比截图宽（仪器失真），不是内容溢出。低于下限的图一律标「未判」，不计入结论。
用法：python png_right_edge.py <a.png> [<b.png> ...]
退出码：0=所有可靠读数都没贴边｜1=有可靠读数贴边（真溢出）｜2=解不开/不是 RGB PNG
"""
from __future__ import annotations

import pathlib
import struct
import sys
import zlib

MIN_RELIABLE_W = 500


def decode(path: pathlib.Path):
    b = path.read_bytes()
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos, w, h, depth, ctype, data = 8, 0, 0, 8, 2, bytearray()
    while pos < len(b):
        ln, tag = struct.unpack(">I4s", b[pos:pos + 8])
        chunk = b[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", chunk[:10])
            bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ctype] * (depth // 8)
        elif tag == b"IDAT":
            data += chunk
        elif tag == b"IEND":
            break
        pos += 12 + ln
    if depth != 8 or ctype not in (2, 6):
        raise ValueError(f"只支持 8bit RGB/RGBA，这是 depth={depth} ctype={ctype}")
    raw = zlib.decompress(bytes(data))
    stride = w * bpp
    out, prev = bytearray(stride * h), bytearray(stride)
    i = 0
    for y in range(h):
        ft = raw[i]; i += 1
        line = bytearray(raw[i:i + stride]); i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            bb = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ft == 1:
                line[x] = (line[x] + a) & 255
            elif ft == 2:
                line[x] = (line[x] + bb) & 255
            elif ft == 3:
                line[x] = (line[x] + ((a + bb) >> 1)) & 255
            elif ft == 4:
                p = a + bb - c
                pa, pb, pc = abs(p - a), abs(p - bb), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (bb if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out


def main() -> int:
    bad = []
    skipped: list[str] = []
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        try:
            w, h, bpp, px = decode(p)
        except Exception as exc:                       # noqa: BLE001
            print(f"{p.name}：解不开（{exc}）⇒ 不出数（RC=2）")
            return 2
        # 背景取左上角第一像素；"有内容" = 与该角色差超过阈值
        bg = px[:bpp]
        right = 0
        for x in range(w - 1, -1, -1):
            col_hit = False
            for y in range(0, h, 3):                   # 隔行采样，够用且快
                o = (y * w + x) * bpp
                if abs(px[o] - bg[0]) + abs(px[o + 1] - bg[1]) + abs(px[o + 2] - bg[2]) > 60:
                    col_hit = True
                    break
            if col_hit:
                right = x
                break
        pad = w - 1 - right
        if w < MIN_RELIABLE_W:
            print(f"{p.name}：宽 {w}px｜内容最右 x={right} ⇒ **未判**（低于可靠下限 "
                  f"{MIN_RELIABLE_W}px：headless Edge 的最小窗口宽度会让布局比截图宽，"
                  "没溢出的页面也会被判成贴边——本轮实测过）")
            skipped.append(p.name)
            continue
        print(f"{p.name}：宽 {w}px｜内容最右 x={right}｜距右边缘 {pad}px"
              + ("  ⇒ 🔴 贴边，真溢出" if pad <= 1 else "  ⇒ 未贴边"))
        if pad <= 1:
            bad.append(p.name)
    if bad:
        print(f"判读：{len(bad)} 张图的内容贴到最后一列（{', '.join(bad)}）⇒ 按"
              "「横向溢出」处理；这张图就是证据，别只写结论")
        return 1
    print(f"判读：{len(sys.argv) - 1 - len(skipped)} 张可靠读数都没贴边"
          + (f"；另有 {len(skipped)} 张因宽度低于下限未判（{', '.join(skipped)}）" if skipped else "")
          + " ⇒ 没测到横向溢出（这不等于『任意宽度都不会溢出』——低于下限的区间本仪器看不见）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
