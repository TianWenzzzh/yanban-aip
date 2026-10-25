"""轮 151：把 `capture_topic` 的构造句集接进回归（正例覆盖 + 负例零误捕 + 形状无壳）。

句集只有**一份**：`tests2/fixtures_topic_corpus.py`，改动前后的对照仪器
（`输出/scripts/probe_topic_capture_corpus.py`）读的是同一个模块，不存在两处各抄一遍。
参数化到每一条 ⇒ 谁漂了就红在自己身上，不连坐。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from companion.intent import extractor
from fixtures_topic_corpus import BAD_PREFIX, BAD_SUFFIX, LLM_ONLY, NEGATIVE, POSITIVE, shape_ok

import pytest  # noqa: E402


def _positive_params() -> list[pytest.ParameterSet]:
    """正例逐条参数化；`LLM_ONLY` 名单里的标 xfail(strict)。

    名单来自夹具（句子只有一份），这里不重抄句子；strict 的意义是**双向**的：
    规则通道哪天接住了会 XPASS 判红，逼人来把这条从名单里移走。
    """
    out = []
    for text, want in POSITIVE:
        marks = [pytest.mark.xfail(strict=True, reason=LLM_ONLY[text])] if text in LLM_ONLY else []
        out.append(pytest.param(text, want, id=text[:14], marks=marks))
    return out


@pytest.mark.parametrize("text,want", _positive_params())
def test_corpus_positive_topic_is_captured(text: str, want: str):
    """真实中文说法里点到了研究主题，就必须抽出**含该关键词、且不带连接词壳**的 topic。

    空 topic 的代价不是"抽得不准"而是**整条检索路径进不去**：`ready_for_search` 恒假，
    学生被一轮轮反问（轮 147 量过的受害面）。
    """
    got = extractor.capture_topic(text)
    assert want in got, f"没抽出含 {want!r} 的主题，实抽 {got!r}"
    assert shape_ok(got), f"抽出来的东西带壳/带句读：{got!r}"


@pytest.mark.parametrize("text", NEGATIVE, ids=[t[:14] for t in NEGATIVE])
def test_corpus_negative_stays_empty(text: str):
    """这些句子里没有研究主题 ⇒ 必须抽出空串。

    误捕的前例就写在 `extractor.py` 的注释里：带逗号和"想做"的碎片送去英译翻不出来，
    中文碎片原词进 Crossref 捞回海洋经济与航空摄影的期刊，一路污染推荐列表与综述。
    """
    assert extractor.capture_topic(text) == ""


def test_corpus_shape_bans_are_not_dead_rules():
    """形状禁令自己也要有活性探针：禁令里的词真的出现在抽取结果里才算抓到东西，
    全仓一个都不认得的话，`shape_ok` 就是恒真的装饰。"""
    assert extractor.capture_topic("我的研究方向是联邦学习中的隐私保护").startswith(BAD_PREFIX) is False
    for prefix in BAD_PREFIX[:3]:
        assert not shape_ok(prefix + "联邦学习"), f"前缀壳 {prefix} 没被形状判据拦住"
    for suffix in BAD_SUFFIX[:2]:
        assert not shape_ok("推荐系统冷启动" + suffix), f"后缀壳 {suffix} 没被形状判据拦住"
    assert shape_ok("联邦学习中的隐私保护")


def test_residue_rule_rejects_question_tails_but_not_real_topics():
    """轮 179：疑问结构切下来的残渣（'否有意义'）要拦，但**不许顺手把真主题拦掉**——
    "不确定性量化""没有标注的数据"都以 不/没 开头，是正经研究主题。
    收紧一条判据时，必须同时钉一条"不该红的照旧绿"。"""
    from companion.intent.extractor import _topic_ok
    for bad in ("否有意义", "会不会过审", "能不能毕业", "是不是真的"):
        assert not _topic_ok(bad), f"疑问残渣没拦住：{bad}"
    for good in ("不确定性量化", "没有标注的数据集", "不可靠通信下的感知"):
        assert _topic_ok(good), f"真主题被误杀：{good}"
    # 已知窄处（记下来不粉饰）：`^否` 是前缀判，真以"否"开头的术语会被一起拦掉。
    # 外部池 352 条里没见到这种词，等真出现再按词表放行，不现在猜。


def test_llm_only_list_is_not_stale():
    """xfail 名单不能悬空：名单里的句子必须仍在正例集中。

    否则夹具一改句子，xfail 就失去作用对象——"接住了要判红"的那半边护栏等于没装。
    """
    texts = {t for t, _ in POSITIVE}
    orphan = set(LLM_ONLY) - texts
    assert LLM_ONLY, "名单空了就直接删掉标记，别留着"
    assert not orphan, f"LLM_ONLY 里有 {len(orphan)} 条已不在正例集中：{orphan}"
