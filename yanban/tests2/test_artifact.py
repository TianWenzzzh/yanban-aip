"""自包含 HTML 产物的测试：安全属性优先（转义 / 协议白名单 / 无 JS 无外链 / 不留死锚点）。

为什么这些是主判据而不是排版：产物是要**双击在浏览器里打开**的，
里面的标题、摘要、链接全部来自外部文献库与模型输出 —— 一处不转义就是 XSS，
一条 `javascript:` 链接就是一个可点击的载荷。排版坏了看得见，安全坏了看不见。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import companion.artifact as A
import companion.db as db_module
import companion.knowledge as knowledge
import companion.literature.evidence as evidence
from companion.memory import paper_memory

PAPERS = [
    {"title": "钓鱼检测的多模态方法", "authors": ["张三", "李四"], "year": 2024,
     "venue": "ACSAC", "source": "crossref", "oa_url": "https://example.org/a.pdf"},
    {"title": "Contrastive learning for phish", "authors": ["Doe"], "year": 2023,
     "venue": "", "source": "openalex", "doi_url": "https://doi.org/10.1/x"},
]
NOTES = [{"note": "综述", "body": "# 相关工作\n\n第一条结论 [1]，第二条 [2]。\n\n- 要点甲\n- 要点乙\n"}]


def render(papers=None, notes=None, events=None):
    return A.render_artifact("钓鱼检测研究", notes if notes is not None else NOTES,
                             papers if papers is not None else PAPERS, generated_at="2026-09-24T07:00:00Z",
                             events=events)


# ── 安全 ──────────────────────────────────────────────────────────────
def test_external_text_is_escaped_not_executed():
    evil = [{"note": "n", "body": '标题 <script>alert("x")</script> 与 <img src=x onerror=alert(1)> [1]'}]
    out = render(notes=evil)
    # 判据是"不再生成标签"，不是"文字消失"：正文里那串东西应当以文本形式留着。
    assert "<script" not in out and "<img" not in out
    assert "&lt;script&gt;" in out and "&lt;img" in out


def test_hostile_paper_title_escaped():
    out = render(papers=[{"title": '<b>粗</b>"引"', "authors": ['<i>x</i>']}])
    assert "<b>粗</b>" not in out
    assert "&lt;b&gt;" in out and "&lt;i&gt;" in out


def test_url_scheme_allowlist():
    papers = [{"title": "T", "oa_url": "javascript:alert(1)", "doi_url": "data:text/html;base64,AA",
               "doi": "javascript:void(0)"},
              {"title": "U", "oa_url": "https://ok.example/x"}]
    out = render(papers=papers)
    assert 'href="javascript' not in out and 'href="data' not in out
    assert 'href="https://ok.example/x"' in out
    assert "javascript:alert(1)" not in out   # 被整体丢掉，不伪装成 DOI 文本


def test_no_javascript_and_no_external_resources():
    out = render()
    for banned in ("<script", "</script", "javascript:", " src=", "<link", "@import", "url("):
        assert banned not in out, banned
    assert "<style>" in out                   # 样式内联，断网可开


# ── 锚点完整性 ────────────────────────────────────────────────────────
def test_citations_become_in_page_anchors():
    out = render()
    assert 'href="#ref-1"' in out and 'href="#ref-2"' in out
    assert 'id="ref-1"' in out and 'id="ref-2"' in out
    assert re.search(r'<li id="ref-1">.*钓鱼检测的多模态方法', out, re.S)


def test_out_of_range_citation_is_downgraded_not_dead_link():
    out = render(papers=PAPERS[:1],
                 notes=[{"note": "n", "body": "在架的 [1] 与越界的 [7]，还有零号的 [0]。"}])
    assert 'href="#ref-1"' in out
    assert 'href="#ref-7"' not in out and 'href="#ref-0"' not in out
    assert "cite dead\">[7]</span>" in out
    assert "[7]" in out                       # 文本还在，只是不再假装能点开


def test_every_anchor_target_exists():
    out = render()
    hrefs = set(re.findall(r'href="#(ref-\d+)"', out))
    ids = set(re.findall(r'id="(ref-\d+)"', out))
    assert hrefs and hrefs <= ids, hrefs - ids


# ── 内容与空态 ────────────────────────────────────────────────────────
def test_markdown_subset_renders():
    out = render(notes=[{"note": "n", "body": "## 二级\n\n- 甲\n- 乙\n\n1. 一\n2. 二\n\n**粗** 与 [[另一篇]]\n"}])
    assert "<h3>二级</h3>" in out
    assert "<li>甲</li>" in out and "<li>一</li>" in out
    assert "<ol>" in out and "<ul>" in out
    assert "<strong>粗</strong>" in out
    assert 'class="wikilink">另一篇</span>' in out   # 双链留成文本标记，不造死链


def test_empty_project_renders_honest_notice():
    out = render(papers=[], notes=[])
    assert "还没有笔记" in out and "还没有入库论文" in out
    assert 'href="#ref-' not in out
    assert "参考文献（0）" in out              # 计数与实物一致，不写个"参考文献"标题空着


def test_paper_without_status_has_no_placeholder_badge():
    """没有 status 的论文不该出现徽章，更不该把 None 印给用户（第一次跑就当场露馅过）。"""
    out = render(papers=[{"title": "无状态"}])
    assert 'class="badge"' not in out      # 注意别断言 "badge"：CSS 里就有 .badge{}
    assert ">None<" not in out


def test_citation_numbering_stops_where_the_report_stops():
    """报告只能引用 papers_block 给到的前 N 篇 ⇒ 产物也不许把第 N+1 篇编成 ref-N+1，
    否则等于宣称它被引用过。N 从 mentor 派生，测试里也不钉字面量。"""
    from companion.conversation.mentor import MAX_PAPER_CANDIDATES as N
    many = [{"title": f"论文{i + 1}", "authors": ["x"], "year": 2024} for i in range(N + 2)]
    out = render(papers=many, notes=[{"note": "n", "body": f"引到最后一篇 [{N}]，再引一篇 [{N + 1}]。"}])
    ids = set(re.findall(r'id="ref-(\d+)"', out))
    assert ids == {str(i) for i in range(1, N + 1)}, ids     # 正则捕获的是数字本身
    assert f'href="#ref-{N + 1}"' not in out          # 越界 ⇒ 不可点
    assert f"论文{N + 1}" in out                        # 但信息不丢：列在不参与编号那一节
    assert f"不参与编号" in out and f"另有 {2} 篇" in out


def test_static_snapshot_line_present():
    assert "静态快照" in render()


# ── 编号与报告真同源（轮 126）───────────────────────────────────────────
def titled(n):
    return [{"title": f"ZetaPaper {i}", "authors": ["x"], "year": 2024,
             "abstract": f"zeta{i} studies nothing else"} for i in range(n)]


def test_citable_papers_is_the_single_selection_expression():
    """报告用的清单 = 先剔无标题、再截 MAX_PAPERS；这个表达式只许有一份。"""
    from companion.conversation import report
    papers = [{"title": ""}] + [{"title": f"T{i}"} for i in range(report.MAX_PAPERS + 3)]
    got = report.citable_papers(papers)
    assert [p["title"] for p in got] == [f"T{i}" for i in range(report.MAX_PAPERS)]


def test_untitled_records_do_not_shift_the_numbering():
    """库里只要有一条没标题，产物若不过滤就会让 ref-3 指的不是报告里的 [3]。

    判据不是「看起来对」：拿产品自己的编号函数 mentor.papers_block 逐号对账。
    """
    from companion.conversation import mentor, report
    papers = [{"title": "", "abstract": "ghost record body"}] + titled(3)
    out = render(papers=papers, notes=[{"note": "n", "body": "引用 [1] 与 [2]。"}])
    got = re.findall(r'<li id="ref-\d+"><strong>(.*?)</strong>', out)
    # papers_block 每条论文占多行（摘要要点另起一行），只认「数字. 标题（」这一行
    want = re.findall(r"(?m)^\d+\. (.*?)（", mentor.papers_block(report.citable_papers(papers)))
    assert want and got[:len(want)] == want, (got, want)
    assert "ghost record body" not in out       # 不进参考文献，也不占编号


# ── 可核对性块（轮 126）────────────────────────────────────────────────
GROUND = [
    {"title": "ZephyrNet edge inference",
     "abstract": "ZephyrNet reduces median latency by 14 percent on Jetson hardware."},
    {"title": "GalloMerge batching",
     "abstract": "GalloMerge studies gradient batch tradeoffs in federated settings."},
    {"title": "PrismDraft decoding",
     "abstract": "PrismDraft examines speculative decoding draft acceptance rates."},
]
# 第一句的 ZephyrNet + 14 能在被引那篇摘要里找到；第二句只有模型名重合，词面不够两个词。
REPORT_BODY = ("# 综述\n\nZephyrNet 把中位延迟降低 14 个百分点 [1]。\n\n"
               "GalloMerge 的收敛性与批次大小无关 [2]。\n")


def test_checkability_block_counts_grounded_and_ungrounded_separately():
    out = render(papers=GROUND, notes=[{"note": "综述", "body": REPORT_BODY, "tags": ["report"]}])
    assert "可核对性" in out
    assert "1/2" in out                                   # 接得住 / 带角标的句子
    assert "GalloMerge 的收敛性与批次大小无关" in out      # 查无依据的那句原样列出，让人自己去核


def test_checkability_block_absent_without_a_report_note():
    """只有论文笔记时不出现这块：没有「报告」就没有可核对的对象，不许拿论文笔记凑数。"""
    out = render()
    assert "可核对性" not in out and 'class="check"' not in out


def test_checkability_denominator_matches_its_own_label():
    """量的到底是啥就得写啥：grounding_report 的分母含《篇名》句，不止 [n] 角标句。
    线上第一版读数就是靠这条抓出来的（角标 6 处 / 分母 21 句）。
    """
    body = "# 综述\n\n《PrismDraft decoding》提出了新的草稿接受率评估方法。\n"
    out = render(papers=GROUND, notes=[{"note": "综述", "body": body, "tags": ["report"]}])
    assert "带出处标记（[n] 角标或《篇名》）的句子 <strong>1</strong>" in out


def test_zero_cited_sentences_says_unmeasured_not_a_rate():
    body = "# 综述\n\n这一节一个角标都没有挂。\n"
    out = render(papers=GROUND, notes=[{"note": "综述", "body": body, "tags": ["report"]}])
    assert "未测" in out


# ── 可核对性的第二维：这篇该不该被引（轮 134，ALCE 的 recall/precision 之分）──
def _report_out(body):
    return render(papers=GROUND, notes=[{"note": "综述", "body": body, "tags": ["report"]}])


def test_supports_tally_is_derived_from_the_same_rows():
    """恒等式 `sum(supports) == grounded + misnumbered`：两边各算一遍就会印出对不上的来源数。"""
    g = evidence.grounding_report(REPORT_BODY, GROUND)
    assert len(g["supports"]) == len(GROUND)
    assert sum(g["supports"]) == g["grounded"] + g["misnumbered"]


def test_headline_names_cited_but_unsupported_papers():
    """[1] 撑住了、[2] 挂了角标却整篇没撑住、[3] 一次都没被挂 —— 三个数必须分开说。"""
    out = _report_out(REPORT_BODY)
    assert "<strong>2</strong> 篇被挂过角标" in out
    assert "其中 <strong>1</strong> 篇整篇没撑住任何一句" in out
    assert "另有 1 篇列在来源里但一次都没被挂过" in out


def test_each_reference_carries_its_own_support_label():
    out = _report_out(REPORT_BODY)
    assert "挂 1 次 · 撑住 1 句" in out                       # ref-1
    assert "挂 1 次 · 撑住 0 句" in out                       # ref-2（假引用）
    assert "挂 0 次" in out                                    # ref-3
    assert out.count('class="sup zero"') == 1                 # 只标真没撑住的那一篇
    # 三种形态同一句式（看图抓的：两长一短，长句在 white-space:nowrap 下会顶出边界）。
    labels = re.findall(r'<span class="sup[^"]*">([^<]*)</span>', out)
    assert len(labels) == 3
    for lb in labels:
        assert re.fullmatch(r"挂 \d+ 次( · 撑住 \d+ 句)?", lb), lb


def test_more_supported_than_cited_gets_explained_not_left_contradictory():
    """「撑住 > 挂次」是可达的构造（挂错编号的那几句把依据记到别篇头上）——但线上 12 个样本里 over=0，
    一例都没出现 ⇒ 这条是防御性解释，测试只主张「该有／该没有」一对，不冒充观察结果。
    """
    def block(supports, cites):
        g = {"cited_sentences": 4, "grounded": 2, "misnumbered": 2, "true_gap": 0,
             "supports": supports, "rows": [], "coverage": 0.5, "ungrounded_samples": []}
        return A._checkability(g, cites, [{"title": "x"} for _ in cites], len(cites))
    over = block([3, 1], [1, 2])
    assert "1 篇撑住的句数多于它被挂的角标数" in over
    assert "挂错编号" in over                                  # 指回同一件事的另一面
    assert "撑住的句数多于" not in block([1, 1], [1, 2])


def test_precision_untested_when_report_only_uses_book_titles():
    """整篇没有 [n] 角标 ⇒ 这一维没有分母，只能说明说「未测」，不许拿 0 篇冒充「全都撑住了」。"""
    out = _report_out("# 综述\n\n《PrismDraft decoding》提出了新的草稿接受率评估方法。\n")
    assert "「这篇该不该被引」<strong>未测</strong>" in out
    assert "篇被挂过角标" not in out


def test_all_cited_papers_unsupported_are_flagged_not_hidden():
    """最坏情况（默认夹具：论文卡只有标题、句子词面接不住）也要照说：两篇挂了角标、两篇都没撑住。"""
    out = render(notes=[{"note": "综述", "body": "# 综述\n\n第一条结论 [1]。\n\n第二条 [2]。\n",
                         "tags": ["report"]}])
    assert "其中 <strong>2</strong> 篇整篇没撑住任何一句" in out
    assert out.count('class="sup zero"') == 2


def test_support_labels_absent_without_a_report_note():
    """没有报告就没有判定 ⇒ 参考文献也不许冒出「撑住 0 句」这种读数的样子。"""
    out = render()
    assert 'class="sup' not in out
    assert re.search(r"\d+%", out) is None, re.findall(r"\d+%", out)


def test_ungrounded_sample_is_escaped_too():
    body = "# 综述\n\nKleptoScribe 的收敛性无关 [2]。\n\n<script>alert(1)</script> 这句没出处。\n"
    out = render(papers=GROUND, notes=[{"note": "综述", "body": body, "tags": ["report"]}])
    assert "<script" not in out and "&lt;script&gt;" in out


def test_sample_markup_renders_and_keeps_no_dead_anchor():
    """摘录要和正文走同一套内联渲染（线上截图里 `**粗体**` 以字面量露出来了），
    但同一套死锚点规则也必须管到块里 —— 转成链接却指向不存在的位置更糟。"""
    body = ("# 综述\n\n**GalloMerge 的结论** 在批次上无关 [9]。\n\n"
            "ZephyrNet 把中位延迟降低 14 个百分点 [1]。\n")
    out = render(papers=GROUND, notes=[{"note": "综述", "body": body, "tags": ["report"]}])
    assert "<strong>GalloMerge 的结论</strong>" in out and "**" not in out
    assert 'href="#ref-9"' not in out
    assert 'class="cite dead">[9]</span>' in out


# ── 逐句依据台账（轮 130 · R4 逐句证据绑定）─────────────────────────────
def _report_note(body: str) -> dict:
    return {"note": "综述", "body": body, "tags": ["report"]}


def test_trail_quotes_the_supporting_passage_verbatim():
    out = render(papers=GROUND, notes=[_report_note(REPORT_BODY)])
    assert "ZephyrNet reduces median latency by 14 percent on Jetson hardware." in out
    assert "依据：《ZephyrNet edge inference》（ref-1）" in out
    assert "出自该篇的「摘要」" in out


def test_trail_rows_equal_the_headline_counts():
    """台账与汇总同源：headline 说接地几句，正文里就得恰好有几行带「依据：」。
    两条各自实现一遍的话，迟早一个说 18 句、另一个只列得出 15 句还各自绿。"""
    g = evidence.grounding_report(REPORT_BODY, GROUND)
    assert (g["cited_sentences"], g["grounded"]) == (2, 1)
    out = render(papers=GROUND, notes=[_report_note(REPORT_BODY)])
    assert out.count("依据：《") == g["grounded"]
    assert "整池查无依据" in out                 # 没接住的那句明说，不靠读者自己数


def test_misnumbered_row_points_at_where_the_evidence_actually_is():
    """挂错编号那句：不许替它把编号圆回去，要写「依据其实在」并指到真正撑住它的那篇。"""
    body = "# 综述\n\nGalloMerge studies gradient batch tradeoffs in federated settings [1]。\n"
    out = render(papers=GROUND, notes=[_report_note(body)])
    assert "这句挂的编号没接住，依据其实在《GalloMerge batching》（ref-2）" in out


def test_true_gap_row_prints_no_fake_evidence_line():
    body = "# 综述\n\nKleptoScribe 的收敛性与批次大小无关 [1]。\n"
    out = render(papers=GROUND, notes=[_report_note(body)])
    assert "整池查无依据" in out and "依据：《" not in out


def test_trail_is_capped_and_says_what_it_left_out():
    body = "# 综述\n\n" + ("ZephyrNet 把中位延迟降低 14 个百分点 [1]。\n\n" * 13)
    out = render(papers=GROUND, notes=[_report_note(body)])
    assert out.count("依据：《ZephyrNet edge inference》") == A._TRAIL_MAX
    assert f"（另有 {13 - A._TRAIL_MAX} 句未列出）" in out


def test_trail_details_carry_no_js_and_survive_escaping():
    hostile = [{"title": 'X "y"', "abstract": "ZephyrNet 14 percent <script>alert(1)</script>"}]
    body = "# 综述\n\nZephyrNet 把中位延迟降低 14 个百分点 [1]。\n"
    out = render(papers=hostile + GROUND, notes=[_report_note(body)])
    assert "<script" not in out and "&lt;script&gt;" in out
    assert "<onerror=" not in out and "onclick=" not in out


def test_trail_is_collapsed_by_default_so_the_report_stays_readable():
    """线上真产物截图实测出来的：12 行台账摊开会把综述正文推到两三屏之后，
    「可核对」反而挡住了要读的东西 ⇒ 默认折叠，点开就在。
    这条测的是**真实状态**：外层不得带 open，开合标签还得配平（配不平就是坏 HTML）。"""
    out = render(papers=GROUND, notes=[_report_note(REPORT_BODY)])
    assert '<details class="trail"><summary>' in out
    assert out.count("<details") == out.count("</details>") == 3     # 外层 1 + 两句各 1


def test_report_note_renders_before_other_notes():
    """综述提到最前；其余笔记保持进来的相对顺序（稳定排序，不按标题重排）。"""
    notes = [{"note": "论文：乙", "body": "论文段。\n", "tags": ["paper"]},
             {"note": "决策：丙", "body": "决策段。\n", "tags": []},
             {"note": "综述：丁", "body": "报告段。\n", "tags": ["report"]}]
    out = render(papers=titled(1), notes=notes)
    assert out.index("报告段") < out.index("论文段") < out.index("决策段")


# ── 封面标题（轮 127：线上截图里 h1 是用户那句被截断的开场白）──────────────
def test_cover_title_prefers_the_report_note():
    notes = [{"note": "综述：多智能体协作", "body": "报告段。\n", "tags": ["report"]},
             {"note": "论文：乙", "body": "论文段。\n", "tags": ["paper"]}]
    out = render(papers=titled(1), notes=notes)
    assert "<h1>综述：多智能体协作</h1>" in out
    assert "项目：钓鱼检测研究" in out       # 项目名不丢，只是退到副行


def test_cover_title_falls_back_to_project_name_without_a_report():
    out = render()                          # NOTES 里没有 report 标签
    assert "<h1>钓鱼检测研究</h1>" in out
    assert "项目：" not in out              # 与标题同名时不重复印一遍


def test_hostile_report_title_cannot_escape_into_a_tag():
    notes = [{"note": "综述：<script>alert(1)</script>", "body": "报告段。\n", "tags": ["report"]}]
    out = render(papers=titled(1), notes=notes)
    assert "<script" not in out and "&lt;script&gt;" in out


# ── 字段形状（轮 128：线上产物把 Python 的 list repr 印给了用户）───────────
def test_source_list_renders_as_words_not_python_repr():
    """`engine.py:129` 存的是列表 ["crossref"]，`str()` 出来就是 ['crossref']。
    线上 a3e3f3dbe496 的 ref-1 就是这么印的（截图里能看到方括号和 &#x27;）。"""
    papers = [{"title": "Agent Network", "year": 2026, "venue": "Tsinghua Science and Technology",
               "source": ["crossref", "openalex"]}]
    out = render(papers=papers, notes=[])
    assert "crossref·openalex" in out
    assert "['" not in out and "&#x27;" not in out     # 不许有 repr 的引号与方括号


def test_source_string_still_renders_once():
    out = render(papers=[{"title": "T", "source": "crossref"}], notes=[])
    assert out.count("crossref") == 1     # 纯字符串不重复、也不被当成列表拆开


def test_real_field_shapes_never_leak_python_repr():
    """按真实库里的字段形状来一份（普查 data/yanban.db 118 条：列表形状只有
    authors / source / recommendation_reason，其余是 str/int）——
    渲染层任何一格把它们 `str()` 成 repr 都该红，而不是等人看图发现。
    """
    papers = [{"title": "多模态钓鱼检测", "authors": ["张三", "李四"], "year": 2024,
               "venue": "ACSAC", "source": ["crossref", "openalex"],
               "recommendation_reason": ["与主题语义相关", "近一年"],
               "abstract": "A multimodal pipeline.", "oa_url": "https://example.org/a.pdf"}]
    out = render(papers=papers, notes=[])
    assert "张三, 李四" in out and "crossref·openalex" in out
    assert "['" not in out and "&#x27;" not in out and '["' not in out


# ── 端点（走入口，不只测纯函数）────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    from fastapi.testclient import TestClient
    from leader import app
    return TestClient(app)


def _seed(client, pid="p-art"):
    conn = db_module.get_conn()
    conn.execute("INSERT INTO projects (id,user_id,name,state,created_at,updated_at) "
                 "VALUES (?, 'u', '钓鱼检测研究', 'DISCOVER', 'now', 'now')", (pid,))
    conn.commit()
    paper_memory.save_many(pid, PAPERS)
    knowledge.upsert_note(pid, "综述", NOTES[0]["body"])
    return pid


def test_endpoint_returns_html(client):
    pid = _seed(client)
    r = client.get(f"/companion/artifact/{pid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "inline" in r.headers["content-disposition"]
    body = r.text
    assert 'href="#ref-1"' in body and "&lt;script" not in body
    assert "钓鱼检测的多模态方法" in body        # 论文真的进了参考文献，不是空壳


def test_endpoint_download_disposition(client):
    pid = _seed(client, "p-dl")
    r = client.get(f"/companion/artifact/{pid}?download=1")
    assert "attachment" in r.headers["content-disposition"]


def test_endpoint_404_for_unknown_project(client):
    assert client.get("/companion/artifact/does-not-exist").status_code == 404


def test_endpoint_carries_tags_through_to_the_block(client):
    """tags 是「哪篇是报告」的唯一依据，而它由 list_notes 现算——端点漏传就整块静默消失。

    所以这条必须走产品入口（HTTP），不能只测纯函数。
    """
    conn = db_module.get_conn()
    conn.execute("INSERT INTO projects (id,user_id,name,state,created_at,updated_at) "
                 "VALUES ('p-tag', 'u', '边缘推理', 'DISCOVER', 'now', 'now')")
    conn.commit()
    paper_memory.save_many("p-tag", GROUND)
    knowledge.upsert_note("p-tag", "综述：边缘推理", REPORT_BODY, tags=["report"])
    body = client.get("/companion/artifact/p-tag").text
    assert "可核对性" in body and "1/2" in body


# ── 执行轨迹（对标 WorkBuddy 差距①：交付物要看得见做了哪几步）───────────────
STEPS = [{"kind": "project_created", "summary": "创建研究项目 钓鱼检测",
          "created_at": "2026-09-24T07:00:00+00:00"},
         {"kind": "decision", "summary": "选定对比维度：数据集/指标",
          "created_at": "2026-09-24T07:01:00+00:00"}]


def test_steps_block_absent_without_events():
    """没有事件 ⇒ 整块不出现：印一个「0 步」是拿空分母冒充交代。"""
    assert "执行轨迹" not in render()


def test_steps_list_every_event_in_order_with_time():
    out = render(events=STEPS)
    assert "执行轨迹 <strong>2</strong> 步" in out
    i, j = out.find("创建研究项目 钓鱼检测"), out.find("选定对比维度：数据集/指标")
    assert 0 < i < j
    assert "2026-09-24 07:00" in out and "UTC" in out
    assert "+00:00" not in out        # 整串 ISO 会把一行撑到换行、日期从中间折断（看图看到的）


def test_steps_time_falls_back_to_raw_when_unparseable():
    """认不出的时间格式原样印，不猜一个钟点出来（猜错就是给读者一个假时间）。"""
    odd = [{"kind": "note", "summary": "旧数据没有时间", "created_at": "昨天下午"}]
    out = render(events=odd)
    assert "昨天下午" in out and "2026-" not in out.split("执行轨迹")[1][:400]


def test_steps_header_count_and_list_share_one_source():
    """总数与列出的条目同取一个 rows —— 各算一遍会印「共 15 步」却只列得出 12 步，两边都不红。"""
    many = [{"kind": "user_note", "summary": f"第 {n} 步", "created_at": ""}
            for n in range(A._STEPS_MAX + 3)]
    out = render(events=many)
    assert f"执行轨迹 <strong>{A._STEPS_MAX + 3}</strong> 步" in out
    assert out.count("<li>第 ") == A._STEPS_MAX
    assert "另有 3 步未列出" in out
    for n in range(A._STEPS_MAX, A._STEPS_MAX + 3):
        assert f"第 {n} 步" not in out          # 被封顶掐掉的确实没进来


def test_steps_collapsed_by_default_and_tags_balanced():
    """默认折叠：轨迹摊开会把综述正文推下去（轮 130 看图学到的同一条教训）。"""
    out = render(events=STEPS)
    assert '<details class="steps"><summary>' in out
    assert '<details class="steps" open' not in out
    assert out.count("<details") == out.count("</details>")


def test_steps_summary_is_escaped_not_executed():
    evil = [{"kind": "note", "summary": '<script>alert(1)</script> 与 <b>粗</b>', "created_at": ""}]
    out = render(events=evil)
    assert "<script>alert" not in out and "&lt;script&gt;alert" in out
    assert "<li><b>" not in out                 # 不得逃成标签


def test_endpoint_emits_steps_from_written_events(client):
    """走 HTTP 入口的活性测试：端点漏传 events 时，纯函数测试全绿而真产物里根本没有这块。"""
    pid = _seed(client, "p-steps")
    db_module.write_event(db_module.get_conn(), pid, "decision", "生成综述报告并挂上引用")
    body = client.get(f"/companion/artifact/{pid}").text
    assert "执行轨迹" in body and "生成综述报告并挂上引用" in body


def test_print_css_keeps_headings_paired_but_not_the_whole_review():
    """打印面（轮 136 实测）：`.note{page-break-inside:avoid}` 会把整份综述推到下一页。

    线上真产物打印出来第 1 页只剩标题与可核对性块（版面占用 33%、共 4 页），去掉这条后 3 页、
    第 1 页占 95%（读数与图在 docs/evidence/g125_*）。pytest 量不到浏览器排版，它钉的是
    "别被顺手加回来"；`h2{page-break-after:avoid}` 是该留的那半（标题不许孤零零掉在页尾）。
    注释里写着这条规则的名字，所以判据先剥掉 CSS 注释再比。
    """
    css = re.sub(r"/\*.*?\*/", "", A._CSS, flags=re.S)
    assert "page-break-inside" not in css
    assert "h2{page-break-after:avoid}" in css
