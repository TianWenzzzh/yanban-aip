"""轮 183 B2：点角标之后，右栏必须说清"这句到底有没有原文撑住"，三种结局各有各的文案。

对标 undermind 的 U5（"Trace any statement by following in-line citations back to the source paper"），
但**不抄它的呈现**：它给一个 100% 相关度徽章，我们给"程序知不知道是哪篇的哪段"。
关键红线是 true_gap 那一格——找不到依据时必须直说"这句目前没有文献支撑"，
不许用一个看起来像依据的徽章把空位糊上（那是本项目防幻觉闸门一直在挡的事）。

status 由后端一处判（router.cite_context），前端只按 status 选文案；
判据因此分两层：后端三态各测一次，前端钉"不许自己推 status、三态各有文案、原文要 mark 出来"。
"""
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.memory import paper_memory

A = {"id": "pa", "title": "Contrastive URL classification",
     "abstract": "We contrast normal and abnormal subgraphs and report state-of-the-art "
                 "recall on four public graph anomaly benchmarks with unified protocols."}
B = {"id": "pb", "title": "A survey of agent evaluation",
     "abstract": "This survey reviews 120 papers on multi-agent LLM systems and the "
                 "evaluation protocols they use to compare baselines."}
# 逐字取自 A 的摘要：接地与否必须只看字面重合，不靠模型再判一遍
CLAIM_A = "report state-of-the-art recall on four public graph anomaly benchmarks"
# 整池一个词都没有的句子才配当 true_gap 的样本。第一版这里写的是"厨房配方要三个鸡蛋"，
# 结果被判成 misnumbered：两篇论文的池子上 rare_tokens 几乎不过滤，the/a/and 这类通用词
# 就能凑够 MIN_TOKENS。这是词面匹配在小池子上的真实边界（已写进本轮文档），不是这条用例要钉的东西。
CLAIM_GAP = "xylophone quokka marmalade trombone flibbertigibbet wisteria"


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    from leader import app
    client = TestClient(app)
    from companion.memory import project_memory
    pid = project_memory.create_project(user_id="default", name="三态")
    paper_memory.save_many(pid, [A, B])
    yield client, pid
    db_module._CONN = None


def _post(client, pid, idx, claim):
    r = client.post("/companion/cite/context",
                    json={"project_id": pid, "paper_index": idx, "claim": claim})
    assert r.status_code == 200, r.text[:200]
    return r.json()


@pytest.mark.parametrize("idx,claim,want", [
    (1, CLAIM_A, "grounded"),        # 被引那篇自己就有原文
    (2, CLAIM_A, "misnumbered"),     # 被引那篇没有、别篇有 ⇒ 编号挂错
    (1, CLAIM_GAP, "true_gap"),      # 整池都没有 ⇒ 直说没有
])
def test_cite_context_returns_the_three_statuses(seeded, idx, claim, want):
    client, pid = seeded
    d = _post(client, pid, idx, claim)
    assert d["status"] == want, f"期望 {want}，读到 {d['status']}：{d}"
    assert d["found"] is (want == "grounded")
    if want == "grounded":
        assert d["spans"] and d["spans"][0]["text"] in A["abstract"], "片段必须逐字来自原文"
        assert d["supporter"] is None
    if want == "misnumbered":
        assert d["spans"] == [] and d["supporter"], "挂错编号要点名真正撑住它的那篇"
        assert d["supporter"]["idx"] == 1 and d["supporter"]["title"] == A["title"]
        assert d["supporter"]["text"] in A["abstract"]
    if want == "true_gap":
        assert d["spans"] == [] and d["supporter"] is None
        assert "这句目前没有文献支撑" in d["note"], d["note"]


def test_the_gap_note_stops_calling_it_a_verification_hint(seeded):
    """旧文案是"请你自己核对原文再引用"——把"程序没找到依据"推给学生；现在直说没有。"""
    client, pid = seeded
    d = _post(client, pid, 1, CLAIM_GAP)
    assert "这句目前没有文献支撑" in d["note"]
    assert "请你自己核对原文" not in d["note"]


# ── 前端：status 只能来自后端，三态各有文案 ─────────────────────────────
TSX = pathlib.Path(__file__).resolve().parents[1] / "webui" / "src" / "pages" / "Companion.tsx"

STATUS_FROM_BACKEND = re.compile(r"status:\s*d\.status\s|\|\|\s*\"true_gap\"")
MARKS_SPAN = re.compile(r"<mark[^>]*>「\{s\.text\}」")
# 轮 198 完成审计抓到的缺口：旧判据只查"这句目前没有文献支撑"这串字符在不在，
# 不查它绑在哪个 status 上——把条件换成 misnumbered 之后文案仍在场、true_gap 却会显示成"有依据"。
TRUE_GAP_TEXT_BOUND = re.compile(r'citeEvidence\.status === "true_gap" \? "这句目前没有文献支撑"')
# 轮 200：同一族缺陷的镜像面。轮 198 只把 true_gap 那句绑死了，misnumbered 的文案仍只查"在场"——
# 把「这句的依据不在这一篇」挂到别的 status 上，旧判据一个字都不会红。
MISNUMBERED_TEXT_BOUND = re.compile(r'citeEvidence\.status === "misnumbered" \? "这句的依据不在这一篇"')
# 轮 201：前两条管的是"文案挂错了 status"，这一条管剩下的那只手——三元的**兜底支**。
# 改前 else 直接给「撑住上面那句话的原文」，于是后端一旦给出第四个（未知）status，
# 那一格就自己宣布"有原文依据"，而同处的 <mark> 只认 grounded ⇒ 标题说有、正文拿不出＝最难发现的那种假象。
GROUNDED_TEXT_BOUND = re.compile(r'citeEvidence\.status === "grounded" \? "撑住上面那句话的原文"')
# 旧写法：在整个 msg.text 里找第一条含 [n] 的句子。同一个编号用两次时必拿错句子。
CLAIM_FROM_FIRST_MATCH = re.compile(r"text\.split\([^\)]*\)\.find\([^)]*\[[^)]*\]")
# 轮 202：CDP 想把 misnumbered 那一态驱动出来才发现的缺陷——相邻两颗角标（"…这组数据 [1][2]。"）
# 之间**没有文本节点**，旧写法只读紧邻的那一个兄弟节点，第二颗的 claim 就退化成「2。」。
# 实测（输出/_logs/r202_cite_runA_before_fix.log 的 C10/C11）：那一格因此把**真有依据**的句子
# 报成「这句目前没有文献支撑」——与轮 198 那条相反的假诚实方向。
CLAIM_SIBLING_WALK = re.compile(r"a\.previousSibling[\s\S]{0,120}?SENT\.test\(head\)")
# 轮 202 看图抓到的第二处：markdown 的链接文字会吃掉方括号，`[1](#paper-1)` 在屏上只剩「1」，
# 于是一句末尾的 [1][2] 连读成「12」。链接文字必须自己把括号带回来（`\[n\]`）。
BRACKETLESS_MARKER = re.compile(r'"\[\$1\]\(#paper-\$1\)"')


def problems(text: str) -> list[str]:
    out: list[str] = []
    if "status:" not in text or not STATUS_FROM_BACKEND.search(text):
        out.append("citeEvidence.status 不是从 /cite/context 的响应里取的 ⇒ 前端在自己判三态")
    if "这句目前没有文献支撑" in text and not TRUE_GAP_TEXT_BOUND.search(text):
        out.append("true_gap 的「无支撑」文案没有绑在 status === \"true_gap\" 上 ⇒ "
                   "后端判成真缺依据时，那一格可能仍被画成看起来像依据的东西（§4 B2 验收③）")
    if "这句的依据不在这一篇" in text and not MISNUMBERED_TEXT_BOUND.search(text):
        out.append("misnumbered 的「依据不在这一篇」没有锁在 status === \"misnumbered\" 上 ⇒ "
                   "挂错编号那句可能被画成接地（与 true_gap 那条同族，轮 198 只堵了一半）")
    # 判据之间的措辞不许共享子串（轮 200 的教训：共享会让植入红在不该红的那条上、失去归因），
    # 所以这条用的令牌是「兜底支」，与上面两条的「没有绑在」「没有锁在」都不同形。
    if "撑住上面那句话的原文" in text and not GROUNDED_TEXT_BOUND.search(text):
        out.append("接地的标题句坐在 else 兜底支上 ⇒ 后端给出未知/第四个 status 时那一格会自己宣布"
                   "「有原文依据」，而同处的 <mark> 只认 grounded＝标题说有、正文拿不出（§4 B2 验收③ 剩的那一半）")

    if re.search(r"status:\s*\w+\.spans\.length", text):
        out.append("status 由 spans.length 推出来：那是把「没找到」与「没查」合并的老毛病")
    if CLAIM_FROM_FIRST_MATCH.search(text):
        out.append("claim 取的是「正文里第一条含 [n] 的句子」而不是被点的那颗角标所在句 ⇒ "
                   "同一个编号出现两次时（一句引对、一句其实没依据），第二次的依据会被错算到第一次头上")
    if "sentenceOfAnchor" in text and not CLAIM_SIBLING_WALK.search(text):
        out.append("claim 只认紧邻的那一颗兄弟节点 ⇒ 相邻两颗角标（…[1][2]。）之间没有文本节点，"
                   "第二颗的 claim 会退化成「2。」——拿一句空话问后端，那一格就把**真有依据**的句子说成没依据")
    if "linkifyCitations" in text and BRACKETLESS_MARKER.search(text):
        out.append("角标的链接文字没把方括号带回来 ⇒ markdown 吃掉 [ ] 之后，一句末尾的两颗角标"
                   "在屏上连读成一个数（「这组数据 12。」），读者数不清这句引了几篇，也点不准是哪一颗")
    for need, why in [
        ('data-testid={`cite-${citeEvidence.status}`}', "三态没有各自可定位的容器（判据只能靠文案猜）"),
        ("这句目前没有文献支撑", "true_gap 的文案没了：空位会被看起来像依据的东西糊上"),
        ("这句的依据不在这一篇", "misnumbered 的文案没了：挂错编号会被读成「这篇有依据」"),
        ("撑住上面那句话的原文", "grounded 的标题句没了"),
        ("citeEvidence.supporter.idx", "misnumbered 没点名真正撑住它的是第几篇"),
    ]:
        if need not in text:
            out.append(f"{why}（缺 {need}）")
    if not MARKS_SPAN.search(text):
        out.append("接地片段没有 <mark> 高亮：一屏论文里读不出是哪一句撑住它")
    if not re.search(r"if \(window\.innerWidth < 1024\) setPanelOpen\(true\);", text):
        out.append("窄屏点角标没把右栏抽屉拉开 ⇒ 跳到的是一个看不见的面板"
                   "（1024 必须与 Tailwind 的 lg 断点同值，本轮 CDP 在 390 实测到 offsetParent=null）")
    return out


# 改前的形状（轮 183 之前的 Companion.tsx 那一段），负对照：证明判据会咬人
OLD_SHAPE = '''
                    <div className="mt-1.5 rounded-lg bg-primary-50/70 px-2.5 py-2">
                      <div className="text-[11px] text-primary-700">上面那句话的原文依据</div>
                      {citeEvidence.spans.length
                        ? citeEvidence.spans.map((s, k) => (
                          <div key={k} className="mt-1">「{s.text}」
                            <span className="text-gray-500"> — {s.source}</span></div>))
                        : <div className="mt-1 text-gray-500">{citeEvidence.note}</div>}
                    </div>
'''


def test_frontend_renders_the_three_states_the_backend_declared():
    found = problems(TSX.read_text(encoding="utf-8"))
    assert not found, "；".join(found)


def test_the_pre_change_shape_would_have_been_caught():
    """负对照：改前那份必须同时被"没接线""没三态容器""没高亮"三条抓住。"""
    found = problems(OLD_SHAPE)
    assert any("不是从 /cite/context" in p for p in found), f"接线那条没开火：{found}"
    assert any("三态没有各自可定位的容器" in p for p in found), f"容器那条没开火：{found}"
    assert any("<mark> 高亮" in p for p in found), f"高亮那条没开火：{found}"


# 合成形状：文案一个字没少，只是绑到了别的 status 上——旧判据（只查字符串在场）对它完全没反应。
WRONG_BINDING_SHAPE = '''
  const [citeEvidence, setCiteEvidence] = useState<{ status: string; spans: any[] }>();
  setCiteEvidence({ status: d.status || "true_gap", spans: d.spans || [] });
  {citeEvidence.status === "misnumbered" ? "这句目前没有文献支撑" : "撑住上面那句话的原文"}
'''


def test_a_wrongly_bound_true_gap_text_is_caught_though_the_words_are_all_there():
    """§4 B2 验收③ 的可证伪形式：后端判成 true_gap 时那一格不许画成"有依据"。
    先正向对照（现役这份必须闭嘴），再喂合成串——没有正向对照，判据写错成恒红也会"看起来在咬人"。"""
    live = TSX.read_text(encoding="utf-8")
    assert not any("没有绑在" in p for p in problems(live)), "现役代码被自己的新判据咬了 ⇒ 判据形状与代码不一致"
    found = problems(WRONG_BINDING_SHAPE)
    assert any("没有绑在" in p for p in found), f"文案在场但绑错 status 都没抓到 ⇒ 这条判据是装饰：{found}"
    assert "这句目前没有文献支撑" in WRONG_BINDING_SHAPE, "合成串少了那句文案，测的就不是绑定而是存在性"


# 轮 200 的合成形状：misnumbered 的文案一个字没少，只是挂到了 true_gap 那一支上。
WRONG_BINDING_MISNUMBERED_SHAPE = '''
  const [citeEvidence, setCiteEvidence] = useState<{ status: string; spans: any[] }>();
  setCiteEvidence({ status: d.status || "true_gap", spans: d.spans || [] });
  {citeEvidence.status === "true_gap" ? "这句的依据不在这一篇" : "撑住上面那句话的原文"}
'''


def test_a_wrongly_bound_misnumbered_text_is_caught_though_the_words_are_all_there():
    """同族镜像：先正向对照（现役这份必须闭嘴），再喂合成串。

    没有正向对照就可能写成一条恒红的判据——那和恒绿一样是装饰（轮 198 同一课，这次是另一侧）。
    """
    live = TSX.read_text(encoding="utf-8")
    assert not any("misnumbered 的「依据不在这一篇」没有锁在" in p for p in problems(live)), \
        "现役代码被自己的新判据咬了 ⇒ 判据形状与代码不一致"
    found = problems(WRONG_BINDING_MISNUMBERED_SHAPE)
    assert any("misnumbered 的「依据不在这一篇」没有锁在" in p for p in found), \
        f"文案在场但挂错 status 都没抓到 ⇒ 这条判据是装饰：{found}"
    assert "这句的依据不在这一篇" in WRONG_BINDING_MISNUMBERED_SHAPE, \
        "合成串少了那句文案，测的就不是绑定而是存在性"



NEW_CLAIM = "const claim = sentenceOfAnchor(a, text);"
OLD_CLAIM = "const claim = (text.split(/[。！？]/).find(s => s.includes(`[${n}]`)) || text).slice(0, 300);"


def test_claim_taken_from_the_clicked_marker_not_the_first_match():
    """同一编号在一行里出现两次是常态（引对一句、编一句）。

    负对照用真文件：把这一行还原成改前写法，判据必须咬住——不是凭空编一个旧形状。
    """
    live = TSX.read_text(encoding="utf-8")
    assert live.count(NEW_CLAIM) == 1, f"claim 取值那行的形状变了，判据要跟着改（命中 {live.count(NEW_CLAIM)} 次）"
    assert any("被点的那颗角标" in p for p in problems(live.replace(NEW_CLAIM, OLD_CLAIM))), \
        "把 claim 还原成「第一条含 [n] 的句子」时判据没开火 ⇒ 这条抓不住回归"


# 轮 201 之前的现役形状：三元的兜底支直接给接地标题句（未知 status 就落在这里）
ELSE_IS_GROUNDED_SHAPE = '''
  {citeEvidence.status === "true_gap" ? "这句目前没有文献支撑"
    : citeEvidence.status === "misnumbered" ? "这句的依据不在这一篇"
    : "撑住上面那句话的原文"}
'''


def test_a_status_the_backend_never_emits_does_not_claim_to_have_evidence():
    """兜底支决定"第四个值长什么样"。今天只出三态，但改口那天它不该自己宣布有依据。

    先正向对照（现役那份必须对这条闭嘴——判据写错成恒红也会"看起来在咬人"），
    再喂改前那个形状，它必须咬。
    """
    live = TSX.read_text(encoding="utf-8")
    found = problems(live)
    assert not any("兜底支" in p for p in found), f"接地的标题句还坐在 else 兜底支上：{found}"
    bitten = problems(ELSE_IS_GROUNDED_SHAPE)
    assert any("兜底支" in p for p in bitten), f"改前那个形状都没被抓到 ⇒ 这条判据是装饰：{bitten}"


# 轮 202 改前的取句形状（逐字从提交里抄回来，不是我编的"旧写法"）：
# 只读紧邻的那一个兄弟节点，且只认文本节点。
SINGLE_SIBLING_SHAPE = '''
const sentenceOfAnchor = (a: Element, fallback: string): string => {
  const tv = (n: Node | null) => (n?.nodeType === 3 ? n.textContent || "" : "");
  const head = tv(a.previousSibling);
  const joined = head + (a.textContent || "") + tv(a.nextSibling);
  return fallback.slice(0, 300);
};
'''


def test_adjacent_markers_do_not_collapse_the_claim_to_a_digit():
    """多头引用（一句末尾连着写 [1][2]）里两颗角标之间没有文本节点。

    这条不是"为了让 misnumbered 出图"顺手加的：CDP 把它驱动出来时实测到 claim 只剩「2。」，
    后端对这种空 claim 只能判 true_gap ⇒ 屏上把**真有依据**的句子报成「没有文献支撑」。
    先正向对照（现役那份必须对这条闭嘴），再喂改前那份，它必须咬。
    """
    live = TSX.read_text(encoding="utf-8")
    assert "sentenceOfAnchor" in live, "取句函数没了 ⇒ 这条判据失去被测对象"
    assert not any("只认紧邻的那一颗" in p for p in problems(live)), \
        "现役代码被自己的新判据咬了 ⇒ 判据形状与代码不一致"
    found = problems(SINGLE_SIBLING_SHAPE)
    assert any("只认紧邻的那一颗" in p for p in found), \
        f"还原成「只读紧邻那一个节点」都没抓到 ⇒ 这条判据是装饰：{found}"
    assert "sentenceOfAnchor" in SINGLE_SIBLING_SHAPE, "合成串少了取句函数，测的就不是 claim 而是别的"


# 轮 202 改前的角标链接（逐字）：markdown 的链接文字吃掉方括号，屏上只剩裸数字。
BRACKETLESS_SHAPE = 'const linkifyCitations = (t: string) => t.replace(/\\[(\\d{1,2})\\]/g, "[$1](#paper-$1)");'


def test_marker_link_text_keeps_its_brackets_in_the_source():
    """两颗角标连着写时，裸数字会连读成一个数（「这组数据 12。」）。

    这条只钉**源码形状**（不执行函数、不渲染）；屏上那一面由 CDP 仪器的 C12 与渲染层植入 R2 负责。
    先正向对照（现役那份必须闭嘴），再喂改前那一行，它必须咬。
    """
    live = TSX.read_text(encoding="utf-8")
    assert not any("连读成一个数" in p for p in problems(live)), \
        "现役代码被自己的新判据咬了 ⇒ 链接文字又不带括号了"
    found = problems(BRACKETLESS_SHAPE)
    assert any("连读成一个数" in p for p in found), \
        f"还原成不带方括号的链接文字都没抓到 ⇒ 这条判据是装饰：{found}"
