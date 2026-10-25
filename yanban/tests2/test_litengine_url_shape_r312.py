# -*- coding: utf-8 -*-
r"""轮 312 第十四段·常驻门：`companion/literature/engine.py::_normalize` 造出来的 `url` 必须有形状门。

为什么要这一层（现读实测，不是设想）。第十三段在 `sources.py` 根掉"认不出的标识被无条件拼成 doi.org"之后，
按"修一类缺陷要把同类全部调用点列一遍"的规矩全库 grep `doi.org|arXiv`，剩下的**唯一另一处构造点**就是这一行：
    "url": raw.get("url") or (f"https://doi.org/{doi}" if doi else "")      # 改之前的形状
而只读探针（`输出/_scratch/r312_p393_measure_engine_url_shapes.py` ⇒ `输出/_logs/r312_p393_measure_engine_url_shapes.log`）量到：
  · 四个现役 provider 里 **`crossref` 6/6 条、`semantic_scholar` 全靠这一行**（它们的输出字典里根本没有 `url` 键），
    `arxiv` 2/2 与 `openalex` 6/6 自带 `url`；真条目 14 条走真 `_normalize` 当天坏形状 **0 条**＝**latent，不是"没缺陷"**；
  · 两枚合成对照当场造出坏形状：`doi="arXiv:2406.09876v1"` → `https://doi.org/arXiv:2406.09876v1`、
    `doi="https://dx.doi.org/10.9999/x"` → `https://doi.org/https://dx.doi.org/10.9999/x`（OpenAlex 那行是**字面**
    `.replace("https://doi.org/", "")`，前缀一换就剥不掉 ⇒ 套娃这一支真能走到）。
一条点不开的假链接比一句"没有链接"更坏：卡片与网页都只认 `url` 有值就画成可点。

形状合同只有一份实现（`sources._source_link`，第十三段立的），本文件不抄第二份：
N1/N2 钉 `_normalize` 的行为，N3 钉"两条路径逐值同源"，N4 钉"实现没有被抄第二遍"，
N6 钉"入库侧剥前缀用的那条字面串与合同前缀逐字相同"（普查在产品代码里读到三处该字面串，只有这一对是真耦合）。
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sources                                               # noqa: E402
from companion.literature.engine import _normalize          # noqa: E402

ENGINE = ROOT / "companion" / "literature" / "engine.py"
SRC = ROOT / "sources.py"
OPENALEX = ROOT / "companion" / "literature" / "providers" / "openalex.py"

# 六格：标识形状 → 该当什么。派生自两个解析口的真实产出（`sources.py` 的 arXiv 前缀替换、
# Crossref 的 `it.get("DOI", "")`）＋第十三段那七形状里 provider 侧能送进来的那几种。
#   ① `arXiv:<号>`——ArxivProvider 的 `<id>` 换前缀之后
#   ② 裸 `<号>`——`<id>` 换了个前缀没剥掉的形状（形状门不许替它编 doi.org）
#   ③ `10.x/…`——Crossref / SemanticScholar 的真 DOI
#   ④ 绝对 URL（含 `dx.doi.org` 这种 OpenAlex 剥不掉的）——原样，不套娃
#   ⑤ 任意串——不许拼
#   ⑥ 空串——没有可点开的链接
SHAPES = [
    ("arXiv:2406.09876v2", "https://arxiv.org/abs/2406.09876v2"),
    ("2403.01234v1", "https://arxiv.org/abs/2403.01234v1"),
    ("10.1109/ACCESS.2023.000001", "https://doi.org/10.1109/ACCESS.2023.000001"),
    ("10.1/x", ""),        # `10.` 后位数不对＝不是已注册 DOI 形状，宁可没链接也不拼一条点不开的
    ("https://dx.doi.org/10.9999/x", "https://dx.doi.org/10.9999/x"),
    ("not-an-identifier", ""),
    ("", ""),
]


def raw_of(ident: str, url=None) -> dict:
    """按 **`crossref`／`semantic_scholar` 两路的真实形状**造 raw：默认**不带** `url` 键（探针实测它们就没有）。"""
    p = {"title": "T", "doi": ident, "authors": [], "year": "2024", "venue": "V", "citations": None}
    if url is not None:
        p["url"] = url
    return p


def code_lines(text: str) -> list[str]:
    """剥注释后的代码行——注释里转述旧形状（`https://doi.org/arXiv:…`）不该被算成"第二处构造"。"""
    return [l for l in text.split("\n") if not l.lstrip().startswith("#")]


def test_N1_engine_url_follows_the_same_shape_contract():
    for ident, want in SHAPES:
        got = _normalize(raw_of(ident), "crossref")["url"]
        assert got == want, f"标识 {ident!r} 的 url 是 {got!r}，合同要求 {want!r}"


def test_N2_provider_supplied_url_is_never_rewritten():
    """provider 自己给了落地页就照用——哪怕它与 doi 不同源（那是上游给的权威链接，不是我该改写的）。"""
    got = _normalize(raw_of("10.1109/ACCESS.2023.000001", "https://publisher.example/a"), "crossref")["url"]
    assert got == "https://publisher.example/a", f"provider 给的 url 被改写了：{got!r}"
    # provider 只给了个**空串**（OpenAlex 的 `landing_page_url` 缺 `primary_location` 时就是空）⇒ 必须回到形状门，
    # 不许因为"键在"就当它没有，也不许留空把一条本来点得开的 DOI 报成没链接。
    assert _normalize(raw_of("arXiv:1706.03762", ""), "arxiv")["url"] == "https://arxiv.org/abs/1706.03762", \
        "provider 给了空 url 时没回到形状门"
    assert _normalize(raw_of("10.1109/ACCESS.2023.000001", ""), "crossref")["url"] == \
        "https://doi.org/10.1109/ACCESS.2023.000001"


def test_N3_both_paths_agree_value_by_value():
    """同源腿：网页/卡片侧（`_normalize`）与伙伴侧条目行（`sources._source_link`）逐值相等。

    这两处一旦各写一份政策，就会"修了一处、另一处照旧说谎"——第十三段修的正是其中一处。
    """
    for ident, _ in SHAPES:
        assert _normalize(raw_of(ident), "arxiv")["url"] == sources._source_link(ident), \
            f"{ident!r}：两条路径给出的链接不一样＝形状合同被抄成了第二份"


def test_N4_the_shape_gate_exists_exactly_once():
    """反抄本腿：构造 doi.org 的代码只许有一处（在 `sources.py`），`engine.py` 只许引用不许重写。"""
    eng, src = ENGINE.read_text(encoding="utf-8"), SRC.read_text(encoding="utf-8")
    eng_build = [l for l in code_lines(eng) if "https://doi.org" in l]
    assert not eng_build, f"engine 里出现了第二处 doi.org 构造＝政策被抄第二遍：{eng_build[:2]}"
    url_lines = [l for l in code_lines(eng) if '"url":' in l and "raw.get" in l]
    assert len(url_lines) == 1, f"`_normalize` 里 url 那一行读到 {len(url_lines)} 条，判据落点不明"
    assert "_source_link" in url_lines[0], f"url 没走那份唯一实现：{url_lines[0].strip()[:90]}"
    assert "doi.org" not in url_lines[0], f"url 那一行自己拼了 doi.org＝绕过了形状门"
    build = [l for l in code_lines(src) if re.search(r'"https://doi\.org/" \+', l)]
    assert len(build) == 1, f"唯一实现应在 `sources.py` 里恰好 1 处，读到 {len(build)} 处"


def test_N4b_engine_delegates_with_a_function_level_import():
    """`engine` 反向 import `sources` 只能在函数内（顶层会成环——`sources` 顶层 import 本模块）。"""
    eng = ENGINE.read_text(encoding="utf-8")
    top = [l for l in code_lines(eng) if re.match(r"^(from|import)\s+sources\b", l)]
    assert not top, f"engine 顶层 import 了 sources，会与 sources 顶层 import engine 撞成环：{top[:2]}"
    inside = re.search(r"def _normalize\(.*?\n(.*?)\n    title", eng, re.S)
    assert inside and "from sources import _source_link" in inside.group(1), \
        "形状门的接线没找到：`_normalize` 里没有那句函数内 import（这条腿是装饰的话，删掉 import 也不会红）"


def prefixes_agree(src: str, oa: str) -> tuple[bool, str]:
    """`sources` 拼链接用的前缀 与 `openalex` 剥上游 doi 用的前缀 必须**逐字相同**。

    为什么要机器盯（本轮普查实测，不是设想）：产品代码里含 `https://doi.org` 字面串的有三处——
    `sources.py`（构造）、`providers/openalex.py:38`（`.replace("https://doi.org/", "")` 剥前缀）、
    `engine.py` 的注释。合同侧一改（去尾斜杠、换域名），剥前缀那行会**静默失效**：`doi` 字段变成一整条 URL，
    形状门把它当"绝对 URL 原样放行"，链接仍然点得开，但标识列与链接列从此重复显示同一条串，
    逐条同源那类判据再也读不到稳定标识——缺陷不红，只是悄悄换了一种坏法。
    """
    a = re.search(r'"(https?://[^"]*doi\.org/[^"]*)" \+ ident', src)
    b = re.search(r'\.replace\("(https?://[^"]*doi\.org/[^"]*)", ""\)', oa)
    if not (a and b):
        return False, f"合同侧 {bool(a)}／剥前缀侧 {bool(b)}——有一处读不到，这条腿就是装饰"
    return a.group(1) == b.group(1), f"合同={a.group(1)!r} 剥前缀={b.group(1)!r}"


def test_N6_the_inbound_strip_prefix_is_the_same_string_as_the_contract():
    src = SRC.read_text(encoding="utf-8")
    oa = OPENALEX.read_text(encoding="utf-8")
    agree, why = prefixes_agree(src, oa)
    assert agree, "两条 doi.org 字面串漂了：" + why
    # 分辨力自证：对照必须真的改动字节，且改动后判决必须翻成"不一致"
    mutated = oa.replace('replace("https://doi.org/", "")', 'replace("https://doi.org", "")')
    assert mutated != oa, "这枚对照没改动任何字节＝空对照（多半是 openalex 那行的形状变了，去看它）"
    assert prefixes_agree(src, mutated)[0] is False, "剥前缀漂了却仍判一致＝这条腿没有分辨力"
    assert prefixes_agree(src, oa)[0] is True, "现役两份本该一致"


def test_N5_plants_prove_these_legs_fire():
    """合成植入（字符串层）：证明"摘掉形状门"确实会被抓住，同时给出旧形状**会长什么样**的逐字凭据。

    真文件级的红绿对拆在 `输出/_scratch/r312_p394_redgreen_unfix14.py`（改现役 engine.py 再按字节还原）。
    这里只做两件事：① 旧表达式产出的就是那两条坏链接（＝缺陷原形，不是我想象的）；② 把合同表里任一格的
    期望改成旧形状，判据必须不成立——防止有人把 N1 写成"等于什么都算对"。
    """
    old = lambda doi: f"https://doi.org/{doi}" if doi else ""      # noqa: E731 改之前的形状，逐字来自 git 那一行
    assert old("arXiv:2406.09876v2") == "https://doi.org/arXiv:2406.09876v2"
    assert old("https://dx.doi.org/10.9999/x") == "https://doi.org/https://dx.doi.org/10.9999/x"
    assert old("not-an-identifier").startswith("https://doi.org/not-an")
    now = {i: _normalize(raw_of(i), "crossref")["url"] for i, _ in SHAPES}
    differ = [i for i, _ in SHAPES if now[i] != old(i)]
    assert len(differ) >= 4, f"这一版与旧形状只有 {len(differ)} 格不同＝形状门约等于没在管事：{differ}"
    for ident, want in SHAPES:
        assert now[ident] == want, f"{ident!r}：{now[ident]!r} != 合同 {want!r}"
