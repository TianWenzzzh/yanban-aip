"""B4 结果表升级（对标 undermind U6）：导出必须就是表里那批料，排序不许抖，未知参数必须响。

三条不是装饰的判据：
① **导出与表内逐行一致**：CSV 的行序与内容必须等于 `paper_memory.visible_papers`（同一套筛选/排序），
   且 CSV / RIS / BibTeX 三种格式对同一批参数给出的**篇数一致**（各写一遍筛选迟早一个说 3 篇一个说 8 篇）。
② **同值必须点名二次键**：不写二次键时 sort 只保证"相等项维持输入顺序"，而输入顺序随入库时间变
   ⇒ 同一份料两次打开顺序会抖。这里用"换插入顺序、导出顺序必须相同"来钉。
③ **前端筛选与导出 URL 双向对账**：判据从**筛选谓词里自己发现**状态名，再要求导出 URL 的查询串逐条带上。
   加第四颗筛选器却忘了改导出 ⇒ 这条红（而不是留下一对各自绿的实现）。

外加两处本轮读码/CDP 抓到的真缺陷：`<label>` 把两颗 select 一起包住（点下拉会误勾选 OA）、
`format=csv` 此前根本不存在（`cite.py` 文件头写着"表格 CSV"却只有 RIS/BIB ⇒ 空头承诺，
按"说做了没做优先改代码而不是改口径"补上）。

🔴 写夹具时量到的两件事，别当成断言失败：
 · `papers.id` 是**全局 UNIQUE**（不按 project 分），所以两个项目喂同一批 id 会 IntegrityError；
   本文件按项目加前缀绕开。这条产品事实记在这里，谁要做"同一篇跨项目复用"时会先撞上它。
 · 空标题的论文**根本进不了库**：`save_many` 的去重是 `paper_json LIKE '%title[:60]%'`，
   空串退化成 `LIKE '%%'` ⇒ 匹配任意一行，被当成"已存在"跳过。所以"无标题不留空壳行"在
   存储层就成立，导出层的红线只是第二道（下面用纯函数单独钉）。
"""
import csv
import io
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.literature import cite
from companion.memory import paper_memory

TSX = pathlib.Path(__file__).resolve().parents[1] / "webui/src/pages/Companion.tsx"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def _paper(i: int, year: int, cites: int, oa: bool = False) -> dict:
    return {"id": f"p{i}", "title": f"Paper {i}", "year": year, "citation_count": cites,
            "open_access": oa, "venue": f"V{i}", "doi": f"10.1/{i}", "url": f"http://x/{i}",
            "authors": [f"A{i}"], "type": "journal-article"}


# 四颗：2019/50、2023/5(OA)、2024/50、2018/5 —— 每颗都至少被一颗筛选器单独筛掉一次
LIB = [_paper(1, 2019, 50), _paper(2, 2023, 5, oa=True), _paper(3, 2024, 50), _paper(4, 2018, 5)]


def _seed(client_pid: str, papers=LIB) -> None:
    """按项目给 id 加前缀：papers.id 全局 UNIQUE，两个项目复用同一批 id 会撞约束。"""
    paper_memory.save_many(client_pid, [dict(p, id=f"{client_pid}-{p['id']}") for p in papers],
                           status="recommended")


def _csv(client, pid, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = client.get(f"/companion/paper/{pid}/export?format=csv&{qs}")
    assert r.status_code == 200, r.text
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    return rows[0], rows[1:]


def test_csv_rows_equal_the_filtered_selection(client):
    """① 导出行数/顺序/标题 == visible_papers 同参数结果（不是"整库倒出来"）。"""
    _seed("b4a", LIB)
    header, rows = _csv(client, "b4a", year_from=2023, min_citations=10, sort="year")
    want = paper_memory.visible_papers("b4a", year_from=2023, min_citations=10, sort="year")
    assert header == [label for label, _ in cite.CSV_COLUMNS]
    assert [r[0] for r in rows] == [p["title"] for p in want]
    assert [r[0] for r in rows] == ["Paper 3"], f"2023 起 + 被引≥10 只剩 p3：{rows}"
    # 同一颗筛选器单独收紧时，条数必须真的跟着变（否则上面那句是恒真）
    _, all_rows = _csv(client, "b4a")
    assert len(all_rows) == 4, all_rows
    _, oa_rows = _csv(client, "b4a", open_access=1)
    assert [r[0] for r in oa_rows] == ["Paper 2"], oa_rows


def test_three_formats_agree_on_count_for_same_filters(client):
    """② 同一批参数下，CSV/RIS/BibTeX 的篇数必须一致（各算一遍就会各说一套）。"""
    _seed("b4b", LIB)
    n_csv = len(_csv(client, "b4b", year_from=2023, open_access=1)[1])
    q = "year_from=2023&open_access=1"
    ris = client.get(f"/companion/paper/b4b/export?format=ris&{q}").text
    bib = client.get(f"/companion/paper/b4b/export?format=bib&{q}").text
    assert n_csv == 1, "这批参数只该剩 p2"
    assert n_csv == ris.count("TY  -"), ris
    assert n_csv == len(re.findall(r"^@", bib, re.M)), bib


def test_equal_values_export_in_id_order_not_insertion_order(client):
    """③ 防抖：p1/p3 同为 50 被引，换一种入库顺序导出顺序必须完全相同。"""
    _seed("b4c1", [_paper(1, 2019, 50), _paper(3, 2024, 50)])
    _seed("b4c2", [_paper(3, 2024, 50), _paper(1, 2019, 50)])
    a = [r[0] for r in _csv(client, "b4c1", sort="citations")[1]]
    b = [r[0] for r in _csv(client, "b4c2", sort="citations")[1]]
    assert a == b, (a, b)
    # 正向对照：二次键真的在起作用（前缀不同才会让两臂都指向同一颗排在前面）
    assert a == ["Paper 1", "Paper 3"], a


def test_unknown_sort_is_rejected_loudly(client):
    """静默退回默认序＝用户以为排过序了。未知值必须 400 并点名可选值。"""
    _seed("b4d", LIB)
    r = client.get("/companion/paper/b4d/export?format=csv&sort=bogus")
    assert r.status_code == 400, r.text
    assert "relevance" in r.text and "citations" in r.text


def test_unknown_format_is_rejected_and_names_what_works(client):
    _seed("b4e", LIB)
    r = client.get("/companion/paper/b4e/export?format=xlsx")
    assert r.status_code == 400, r.text
    assert "csv" in r.text, "报错得把支持的格式说出来（旧文案只写 ris 或 bib，会永远漏掉新加的那个）"


def test_titleless_paper_never_becomes_an_empty_row():
    """纯函数层的第二道红线：没标题就不给那一行（宁少一行，不给空壳）。"""
    body = cite.to_csv([{"title": "", "year": 2024, "citation_count": 900}, {"title": "Real", "year": 2024}])
    # 期望串是**跑出来的真值**（空字段不带引号、九个字段八个逗号），不是照着格式猜的
    assert body.strip().splitlines()[1:] == ["Real,2024,,,,,,,"], body
    assert cite.to_ris([{"title": "", "year": 2024}]) == ""


def test_csv_quotes_commas_and_keeps_crlf_out(client):
    """标题里带逗号必须被引用；行尾必须 \\n（默认 \\r\\n 会让"数行数"的判据在 Windows 上骗人）。"""
    _seed("b4f", [{"id": "c1", "title": "Heat, and salt: a study", "year": 2022,
                   "citation_count": 1, "authors": []}])
    rows = client.get("/companion/paper/b4f/export?format=csv").text
    assert '"Heat, and salt: a study"' in rows
    assert "\r\n" not in rows, "CSV 里混进 CRLF：Excel 与判据看到的行数会不一样"


# ── 前端判据：筛选与导出必须一起长，label 不许包住 select ─────────────────
def _regions(text: str) -> tuple[str, str]:
    """谓词区＝.filter( 到导出构造器之前；URL 区＝exportUrl 里的**查询串**（路径上的 projectId 不算筛选）。"""
    at = text.index(".filter(", text.index("const visiblePapers"))
    bt = text.index("const exportUrl")
    ct = text.index(";", text.index("&sort=", bt))
    query = text[text.index("?", bt):ct]
    return text[at:bt], query


def test_export_url_carries_every_filter_the_table_uses():
    """判据从谓词里**自己发现**状态名，再要求导出逐条带上（加筛选调而不改导出会红）。"""
    text = TSX.read_text(encoding="utf-8")
    declared = set(re.findall(r"const \[(\w+), set\w+\] = useState", text))
    pred, query = _regions(text)
    used = {n for n in declared if re.search(r"\b" + re.escape(n) + r"\b", pred)}
    in_url = {n for n in declared if re.search(r"\$\{" + re.escape(n) + r"\b", query)}
    assert used == in_url, (f"表内筛了但导出没带：{sorted(used - in_url)}；"
                            f"导出带了但表内没筛：{sorted(in_url - used)}")
    assert used >= {"yearFrom", "oaOnly", "citMin", "sortBy"}, f"筛选器比预期少了：{sorted(used)}"


def test_oa_label_does_not_wrap_the_two_selects():
    """`<label>` 里套着 select ⇒ 点"被引/排序"下拉会顺带勾选那颗 OA 复选框。"""
    text = TSX.read_text(encoding="utf-8")
    at = text.index("只要 OA")
    between = text[text.rindex("<label", 0, at):text.index("</label>", at)]
    assert "<select" not in between, between
    # 反向对照：这一屏里三颗导出键确实都在这段筛选条上（不是一段永不显示的死 DOM）
    tail = text[at:at + 1800]
    assert tail.count("exportUrl(") == 3, "三个导出键应全部改走同一把 URL 构造器"
