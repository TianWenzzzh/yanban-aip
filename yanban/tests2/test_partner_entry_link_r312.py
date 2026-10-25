# -*- coding: utf-8 -*-
r"""轮 312 第十三段·常驻门：每条条目行**要么带一条点得开的链接，要么明写没有**，且链接与标识同源。

为什么要这一层（现读实测，不是设想）。探针 `输出/_scratch/r312_p349_probe_ident_shapes.py`
把七种标识形状喂进**现役** `sources._fmt`，读数落在 `输出/_logs/r312_p349_probe_ident_shapes.log`：
  ① 空标识那两格渲染成 `… ｜ 无 DOI ｜ 源:…`——**整行没有任何链接，也没有一句"这条点不开"**。
     这不是边角：Crossref 侧 `"doi": it.get("DOI", "")`（`sources.py:58`），没 DOI 的条目是常态；
     arXiv 侧 `findtext` 取不到 `<id>` 也给空串（`sources.py:131`）。
  ② 认不出的形状（裸 arXiv 号 `2403.01234v1`、任意串 `not-an-identifier`）被 `_source_link` 末尾那行
     无条件拼成 `https://doi.org/` + 原串 ⇒ 渲染出一条**看着能点开、实际点不开**的链接。
     今天两个解析口都送不进这个形状（＝latent），但"拿不认识的字符串去拼 DOI 解析器"这件事
     正是队长 §4.1「不许空口宣称」的反面：链接点不开却写着它能点开，比不写更坏。

合同的五档（`EXPECT` 逐格写死，判据只认这一张表）：
  `arXiv:<号>` → `https://arxiv.org/abs/<号>`；裸 `<号>`（同样形状）→ 同一个 abs 页；
  `10.x/…` → `https://doi.org/<同一 DOI>`；绝对 URL → 逐字就是它自己；
  空串或认不出的串 → **不拼任何链接**，并且那一行必须明写 `NO_LINK`。
K4 是分母钉：形状表是从那两个解析口派生的，那两行一改，本表就过期，必须重量（不许沿用）。
K5 是负控：五枚植入各自喂给判据函数，必须都判得出问题——没有这条，K1/K2/K3 可能恒真。
"""
import re
import sys

sys.path.insert(0, "..")

import sources  # noqa: E402

NO_LINK = "无可点开的链接（源未给可辨认的 DOI／arXiv 标识）"
DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
ARXIV_BARE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def expected_link(ident):
    """这张表就是合同本身：产品渲染出的链接必须逐字等于这里的值（空串＝该条不拼链接）。"""
    s = (ident or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low.startswith("arxiv:"):
        return "https://arxiv.org/abs/" + s.split(":", 1)[1]
    if ARXIV_BARE.match(s):
        return "https://arxiv.org/abs/" + s
    if low.startswith(("http://", "https://")):
        return s
    if DOI_SHAPE.match(s):
        return "https://doi.org/" + s
    return ""


# (标识形状, 来源, 这一格的出处——两条现役解析口 + 三格形状门)
SHAPES = [
    ("arXiv:2403.01234v1", "arXiv", "sources.py:131 的 replace 产物"),
    ("https://arxiv.org/abs/2406.09876v2", "arXiv", "sources.py:131 只认 http://，https 那支原样留下"),
    ("10.1109/ACCESS.2023.000001", "Crossref", "sources.py:58 `it.get(\"DOI\", \"\")` 给得出"),
    ("arXiv:2403.01234", "arXiv", "providers/crossref_arxiv.py:78 同形状"),
    ("", "Crossref", "sources.py:58 缺 DOI＝空串（常态，不是异常）"),
    ("", "arXiv", "sources.py:131 `findtext` 取不到 `<id>`"),
    ("2403.01234v1", "arXiv", "形状门：裸号（解析口今天送不进来＝latent，但函数不许替它编 doi.org）"),
    ("not-an-identifier", "Crossref", "形状门：认不出的串一律不拼链接"),
    ("10.1109/", "Crossref", "形状门：DOI 形状不合法（缺后缀）"),
]

LINK_RE = re.compile(r"https?://\S+")


def render(ident, venue="IEEE"):
    """走**现役产品函数**渲染一条，取回那一行条目文本（不在测试里自己拼字符串）。"""
    p = {"title": " phishing website detection 的一条", "authors": ["张三"], "year": "2024",
         "venue": venue, "doi": ident, "citations": None}
    body = [l for l in sources._fmt([p], venue, tokens=None) if l.startswith("   [")]
    assert len(body) == 1, f"渲染不出条目行（{body!r}）＝仪器坏了，别看判据"
    return body[0]


def links_in(line):
    return LINK_RE.findall(line)


def contract_ok(line, ident):
    """一行合格＝它带的链接**只有合同那一条**（URL 型标识本身也带一条同样的串，那是同一条不是第二条），
    或者干脆没有链接且明写了 NO_LINK。"""
    want, got = expected_link(ident), links_in(line)
    if want:
        return set(got) == {want}
    return got == [] and NO_LINK in line


def test_K1_link_is_exactly_the_one_that_shape_deserves():
    bad = []
    for ident, venue, _why in SHAPES:
        line = render(ident, venue)
        if not contract_ok(line, ident):
            bad.append((ident, venue, expected_link(ident) or "（该条不许拼链接）", links_in(line), line))
    assert not bad, f"{len(bad)} 格的链接与合同不符（前两格）：{[(b[0], b[3]) for b in bad[:2]]}"


def test_K2_line_without_link_says_so():
    """`无 DOI` 只说了 DOI 不在，没说"这一条点不开"——K2 要的是后者。"""
    for ident, venue, why in SHAPES:
        if expected_link(ident):
            continue
        line = render(ident, venue)
        assert NO_LINK in line, f"{why}：没链接也没写实话（{line!r}）"


def test_K3_mixed_batch_every_line_lands_in_one_of_two_buckets():
    papers = [{"title": f"条目 {i}", "authors": ["甲"], "year": "2024",
               "venue": v, "doi": d, "citations": None}
              for i, (d, v, _w) in enumerate(SHAPES, 1)]
    body = [l for l in sources._fmt(papers, "Mixed", tokens=None) if l.startswith("   [")]
    assert len(body) == len(SHAPES), f"批级渲染掉了行：{len(body)}/{len(SHAPES)}"
    naked = [(SHAPES[i][0], l) for i, l in enumerate(body)
             if not links_in(l) and NO_LINK not in l]
    assert not naked, f"{len(naked)} 行既没有链接也没有实话：{naked[:2]}"


def test_K4_shape_denominator_is_still_those_two_parse_sites():
    """分母钉：本表是从那两行派生的；那两行一改，形状集必须重新量，不许沿用手写表。"""
    src = open("sources.py", encoding="utf-8").read()
    prov = open("companion/literature/providers/crossref_arxiv.py", encoding="utf-8").read()
    for hay, needle, label in ((src, 'it.get("DOI", "")', "Crossref 取 DOI 那一行"),
                               (src, '.replace("http://arxiv.org/abs/", "arXiv:")', "sources 的 arXiv 前缀替换"),
                               (prov, '.replace("http://arxiv.org/abs/", "arXiv:")', "providers 的 arXiv 前缀替换")):
        assert needle in hay, f"{label} 不在了（{needle}）⇒ 形状集要重新派生"


def test_implants_are_caught():
    """负控：五枚植入喂给 K1/K2/K3 用的那台判据函数，必须都判得出问题。"""
    # 正对照：真渲染出来的行必须过合同
    for ident, venue, _w in SHAPES:
        line = render(ident, venue)
        assert contract_ok(line, ident), \
            f"正对照自己没过（{ident!r} → {line!r}）＝判据函数坏了"
    # 植入 1：把 DOI 行的链接尾巴挪走（不同源）
    good = render("10.1109/ACCESS.2023.000001")
    swapped = good.replace("10.1109/ACCESS.2023.000001", "10.9999/OTHER", 2)
    assert not contract_ok(swapped, "10.1109/ACCESS.2023.000001"), "换掉尾巴还判合格＝K1 是装饰"
    # 植入 2：删掉那句实话 ⇒ K2 的形状必须变成"既没链接也没实话"
    empty = render("")
    assert contract_ok(empty, "") and not contract_ok(empty.replace(NO_LINK, "", 1), ""), \
        "删掉实话还判通过＝K2 是装饰"
    # 植入 3：给认不出的串编一条 doi.org（＝本轮修掉的旧缺陷形状）
    fake = "   [2024] 甲 ｜ arXiv ｜ not-an-identifier ｜ https://doi.org/not-an-identifier ｜ 源:Mixed"
    assert links_in(fake) and not contract_ok(fake, "not-an-identifier"), "编造的链接 K1 认不出＝挡不住假链接"
    # 植入 4：绝对 URL 那格的链接被换成另一个地址
    url_id = "https://arxiv.org/abs/2406.09876v2"
    assert contract_ok(render(url_id, "arXiv"), url_id), "URL 型正对照没过＝判据函数坏了"
    assert not contract_ok(render(url_id, "arXiv").replace("2406.09876v2", "2406.00000v9"), url_id), \
        "K1 认不出被改走的号"
    # 植入 5：把「该有链接」那一格的链接整段抹掉——合同必须认出这一格现在既没链接也没实话
    stripped = good.replace(" ｜ https://doi.org/10.1109/ACCESS.2023.000001", "")
    assert not contract_ok(stripped, "10.1109/ACCESS.2023.000001"), "把链接抹掉后合同还判通过＝K1 认不出「没链接」"
    assert NO_LINK not in stripped, "这枚植入没造出「既没链接也没实话」的形状＝负控是假的"
