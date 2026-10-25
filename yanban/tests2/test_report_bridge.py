"""报告侧与跨语言桥的关系：**只读的地方开，改写的地方不开**（轮 174 开、轮 178 按 g183 撤）。

这组用例钉的是"哪一侧用哪把尺"，不是"桥能不能用"——桥本身在 `evidence.py` 里，
`/cite/context`（只展示依据片段）与所有只读读数都还在用它。

撤回的依据（g183，8 份线上中文报告，只读）：拿**另一个项目的论文集**当依据池，
字面尺改指 6 处、开桥改指 12 处，3/8 份变多（最坏一份 3→7）。多出来的"依据"只由翻出来的
通用研究词撑住——那就是把角标改到一篇没说过这句话的论文上。改指是**改写学生的出处**，
判错的方向不可接受，所以写路径一律回到字面尺。
P6 那次"池序轮转覆盖率塌陷（0.743→0.128）"管不住这条：改指动的正是"被引那篇得分 0"的句子，
那时桥找的是"池里任何一篇"。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

os.environ["LLM_MOCK"] = "1"
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm                                        # noqa: E402
from companion.conversation import report         # noqa: E402
from companion.literature import evidence         # noqa: E402

SRC = pathlib.Path(report.__file__).read_text(encoding="utf-8")

# 纯中文的一句论断：字面尺两侧都不重合，只有经桥才看得见依据在 [1]。
ZH_CLAIM = "这篇用图神经网络把网址字符串编码后训练分类器，钓鱼检测的准确率做到九成二 [2]。"
P_GRAPH = {"id": "a", "title": "Graph features for phishing detection", "year": 2023,
           "abstract": "We reach 92% accuracy on phishing detection by training a graph neural "
                       "network over URL string embeddings with a contrastive objective."}
P_CORAL = {"id": "b", "title": "Coral reef classification with remote sensing", "year": 2021,
           "abstract": "Sea surface temperature drives coral bleaching rates across the Great "
                       "Barrier Reef, measured with satellite remote sensing since 2019."}
POOL = [P_GRAPH, P_CORAL]
GLOSS = "graph neural network url string embedding accuracy"


def stub_gloss(monkeypatch, words: str = GLOSS):
    """桩化那条翻译调用：形状用 g177 从真实通道采到的那一种（i 是字符串、k 是数组）。"""
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake_chat_json(prompt, **kw):
        n = len([l for l in prompt.splitlines() if re.match(r"^\d+\. ", l)])
        return {"items": [{"i": str(i), "k": words.split()} for i in range(n)]}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)


def _func_body(head: str, nxt: str) -> str:
    i, j = SRC.index(f"def {head}("), SRC.index(f"def {nxt}(")
    return SRC[i:j]


def code_only(text: str) -> str:
    """剥掉整行注释与文档串再扫：判据管的是"代码里开了桥"，注释里写"曾经开过、为何撤"不是开桥。
    （轮 173 就红过一次同样的形状——正解是语义归一化，不是把注释改掉。）"""
    text = re.sub(r'""".*?"""', "", text, flags=re.S)
    return re.sub(r"^\s*#.*$", "", text, flags=re.M)


CODE = code_only(SRC)


# ── 一、写路径必须是字面尺（g183 撤回之后，这条是防它被"顺手再开"） ─────
def test_clean_report_does_not_open_the_bridge(monkeypatch):
    seen = {}

    def spy(text, papers, **kw):
        seen.update(kw)
        return text, 0

    monkeypatch.setattr(evidence, "renumber_citations", spy)
    report.clean_report_with_ledger(ZH_CLAIM, POOL)
    assert not seen.get("bilingual"), f"改指又开了桥（g183 判它不安全）：{seen}"


def test_no_write_path_in_report_passes_a_gloss_map():
    """报告侧所有 `grounding_report` 调用都不许带桥：这几处的读数决定的是
    要不要重写某一节、要不要把一句新话插进节首——都是**往报告里写东西**。"""
    assert "gloss_map=" not in CODE, "report.py 的代码里又出现共用桥词的判定"
    assert "bilingual=True" not in CODE, "report.py 的代码里又有人开桥"
    assert CODE.count("grounding_report(") >= 4        # 前提：确实有若干处在判 coverage


def test_counter_evidence_compares_both_sides_with_the_same_literal_ruler():
    body = code_only(_func_body("_counter_evidence", "rewrite_section"))
    assert body.count("evidence.grounding_report(") == 3
    assert "english_gloss(" not in body
    assert "evidence.survey(sentence, papers)" in body


# ── 二、桥本身还能用（只读侧与仪器要靠它），机制不许被撤成"没有这个能力" ──
def test_bridged_renumber_still_moves_the_marker_when_asked(monkeypatch):
    """`bilingual=True` 这个开关留着是给**测量**用的（g183 就是靠两侧对比才判出来的）。
    撤的是产品里的调用点，不是能力——能力没了，下次想复测就得重写一份尺子。"""
    stub_gloss(monkeypatch)
    out, moved = evidence.renumber_citations(ZH_CLAIM, POOL, bilingual=True)
    assert moved == 1 and "[1]" in out and "[2]" not in out


def test_literal_ruler_cannot_see_the_support(monkeypatch):
    stub_gloss(monkeypatch)
    out, moved = evidence.renumber_citations(ZH_CLAIM, POOL)
    assert moved == 0 and out == ZH_CLAIM


def test_mock_leaves_the_reading_exactly_as_before():
    a = evidence.renumber_citations(ZH_CLAIM, POOL)
    b = evidence.renumber_citations(ZH_CLAIM, POOL, bilingual=True)
    assert a == b == (ZH_CLAIM, 0)


def test_english_artifacts_send_no_gloss_call(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def spy(prompt, **kw):
        calls.append(prompt)
        return {"items": []}

    monkeypatch.setattr(llm, "chat_json", spy)
    en = "We reach 92% accuracy on phishing detection with a graph neural network [1]."
    evidence.renumber_citations(en, POOL, bilingual=True)
    assert calls == []


def test_one_pass_sends_exactly_one_gloss_batch(monkeypatch):
    n = {"c": 0}
    real = evidence.english_gloss

    def counting(sents):
        n["c"] += 1
        return real(sents)

    monkeypatch.setattr(evidence, "english_gloss", counting)
    stub_gloss(monkeypatch)
    text = ZH_CLAIM + "\n" + ZH_CLAIM.replace("[2]", "[1]") + "\n" + ZH_CLAIM.replace("九成二", "九成三")
    evidence.renumber_citations(text, POOL, bilingual=True)
    assert n["c"] == 1, f"一次改指发了 {n['c']} 次取词，应该是一次批量"


def test_bilingual_declares_actual_usage_not_the_table(monkeypatch):
    """共用一份桥词时，某一节可能一句都没用上——那节就不该自称"经跨语言核对"。"""
    stub_gloss(monkeypatch)
    used = evidence.grounding_report(ZH_CLAIM, POOL, bilingual=True)
    assert used["bilingual"] is True and used["gloss_used"] == 1
    other = "这一节讲的是海水温度上升导致大堡礁珊瑚白化率上升[1]，遥感分类方法也用上了。"
    gm = evidence.english_gloss([ZH_CLAIM])
    got = evidence.grounding_report(other, POOL, gloss_map=gm)
    assert got["bilingual"] is False and got["gloss_used"] == 0, got


# ── 三、消费者清单：谁在用这座桥，逐文件点名 ───────────────────────────
# 🔴 管辖范围＝**源码树**，不是"这台机器这个目录此刻有什么"。轮 311 独立定位（队长要求不许"加个 skip 就完事"）：
#     原来两条遍历都是 `ROOT.rglob("*.py")` + `read_text(encoding="utf-8")`，于是环境目录
#     （venv / node_modules / site-packages / 构建产物）与介质故障后整份变成 0xFF 的件都在范围里，
#     门会红在产品之外，而且红形是裸 `UnicodeDecodeError`——**连哪个文件都不点名**，
#     分不清是环境件还是自家源码坏了（这一条由 `tests2/test_report_bridge_domain_r311.py` 的 D1/D2 现量，
#     D3 钉"范围收窄之后门不许变瞎"）。
#     轮 311 先加的 `.venv` 那味药已经撤掉：按**机制**剪（目录名以 `.` 开头、或环境目录名）而不是按名字点名，
#     换虚拟环境目录名不会再复发。
_ENV_DIRS = {"node_modules", "site-packages", "__pycache__", "build", "dist", "venv", ".venv"}


def _source_py(base: pathlib.Path):
    """产出 (相对路径 posix, 源码文本)；范围外的目录不进去，范围内的解不开就点名。"""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in sorted(dirnames)
                       if not d.startswith(".") and d not in _ENV_DIRS and d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            p = pathlib.Path(dirpath) / fn
            rel = p.relative_to(ROOT).as_posix()
            try:
                yield rel, p.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise AssertionError(f"源码域内的 .py 解不开 UTF-8：{rel}｜{exc}") from None


def test_bridge_consumers_are_exactly_the_read_only_paths():
    """只读侧（读数 + 点角标看依据）可以用桥；写路径不行。多一个消费者就必须在这里改一次。"""
    call = re.compile(r"renumber_citations\([^)]*bilingual\s*=\s*True[^)]*\)")
    skip = ("tests2", "snapshots", "companion/literature/evidence.py")
    hits = [rel for rel, text in _source_py(ROOT)
            if not any(s in rel for s in skip) and call.search(text)]
    assert hits == [], f"又有写路径开了桥（g183 撤的）：{hits}"

    counts = {}
    for rel, text in _source_py(ROOT / "companion"):
        n = text.count("english_gloss(") - text.count("def english_gloss(")
        if n:
            counts[rel] = n
    assert counts == {"companion/literature/evidence.py": 2,      # 读数 + 改指（仪器用）
                      "companion/api/router.py": 1}, counts       # 点角标看依据片段
