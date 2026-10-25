"""主题壳词闸门 `_topic_is_shell` 的「意图 vs 行为」对账——零模型调用、零网络，本机跑。

起因（轮 146 顺手读到的）：`extractor.py:114-123` 的注释写着
「只在『壳词表』里的拉丁词不能当主题：实测过 topic=「IEEE 的方向」→ 检索回一堆 MIMO 雷达与 WLAN」，
可判定写的是 `len(cjk) < 2 or (not cjk and latin and all(... in _BOILER_LATIN))`。
第一句在 `cjk` 为空时已经为真，第二句的 `not cjk` 永远轮不到 ⇒ 注释想做的「只拦水词」被自己的短路吃掉。
如果这是真的，受害的不只是英文提问——**中文学生嘴里的 GNN／BERT／CRISPR 这类术语词**也会被当壳词丢掉。

判据（跑前写死，三条都要开）：
  A 死支证明：用 `ast` 从**产品源码**里造两个变异体（不改产品文件）——
     A1「只留第一句」与原函数在全语料上判定必须**逐条相同**；
     A2「只留第二句」与原函数必须**至少有一条不同**（否则 A1 的「相同」是两边都恒真造成的假象）。
     A2 不过 ⇒ 这条证明不作数（RC=2）。
  B 意图对账：`_BOILER_LATIN` 里的词逐条喂产品函数（注释承诺它们该被判壳）；
     再从真实中文语料里捞「含拉丁 token 且不是水词」的主题候选喂进去——
     任何一条被判壳 ⇒ 与注释不一致，缺陷成立。
  C 用户可见后果（这一维只量「修复前」）：对 in-repo 语料的每句话跑产品的 `capture_topic`，
     数 topic 非空条数；其中「原话里带拉丁术语」的子集单独数一遍。
退出码：0=闸门行为与注释一致（缺陷不成立）｜2=控制不过（A2 或 B 的对照失效）｜3=缺陷成立。
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "code" / "yanban")]
EVD = ROOT / "code" / "yanban" / "docs" / "evidence"
CORPUS_FILES = ["g114_question_pool_full70.json", "g98_cite_pool.json", "g37_blind_pairs_n18.json"]
TEXT_KEYS = ("user_text", "question", "text", "q", "scenario", "first_message")

# 我构造的中文句（**不是真人语料**，只用来量闸门对用户话的影响）
CONSTRUCTED = [
    "我想做 GNN 的交通流量预测，第一次写论文",
    "老师让我调研 BERT 在医学文本上的应用",
    "题目是 CRISPR 脱靶检测的深度学习模型",
    "准备研究 YOLO 的目标检测改进方法",
    "想了解 Transformer 的注意力机制方向",
    "我的课题是 PINN 求解偏微分方程",
    "做 EEG 的癫痫发作检测",
    "方向是 LLM 智能体的规划能力评测",
    "题目是联邦学习里的差分隐私",
    "我想研究多模态钓鱼网站检测",
]
MUST_NOT_SHELL = ["医学图像分割", "GNN", "BERT", "多模态钓鱼网站检测"]   # 注释口径下这些都有实义
MUST_SHELL = ["IEEE 的方向", "ACM", "SCI"]                              # 注释点名的水词形态


def collect_strings(node, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and (k in TEXT_KEYS):
                out.append(v)
            else:
                collect_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_strings(v, out)


def mutants() -> tuple[ast.FunctionDef, ast.FunctionDef]:
    """从产品源码里造「只留第一句」与「只留第二句」两个 `_topic_is_shell` 变异体。"""
    path = ROOT / "code" / "yanban" / "companion" / "intent" / "extractor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_topic_is_shell")
    # 形状检查要**精确**：`ast.walk` 会按 BFS 顺序返回节点，用 `next(...)` 取到的 `return`
    # 不一定是函数末尾那条（轮 147 改成三段 if 之后就是这里先误判了一次，把变异体做到了
    # 一条内层 return 上，A1/A2 因此给出莫名读数）。只认「函数体顶层恰好一条 return 且是两句 or」。
    rets = [n for n in fn.body if isinstance(n, ast.Return)]
    if any(isinstance(n, ast.If) for n in fn.body):
        # 轮 147 修完之后就是这种形状（先 `if cjk:` 分流，末尾才是一条两句 or）。
        # 这时候再对末尾那条 or 做变异，量的是**新表达式**，与「修复前那句是不是死支」无关——
        # 上一版就只按「末尾一条 return 且是两句 or」判，修完仍然自洽，于是 A 又跑了一遍并给出莫名读数。
        # 死支证明只在修复前那份字节上成立，那一次读数连仪器已冻进快照；这里只复量 B/C。
        return None, None
    if len(rets) != 1 or not isinstance(rets[0].value, ast.BoolOp) or len(rets[0].value.values) != 2:
        return None, None
    ret = rets[0]
    outs = []
    for keep in ret.value.values:
        f2 = copy.deepcopy(fn)
        r2 = next(n for n in ast.walk(f2) if isinstance(n, ast.Return))
        r2.value = copy.deepcopy(keep)
        outs.append(f2)
    return outs[0], outs[1]


def build(fn: ast.FunctionDef):
    """把变异函数装进**独立模块**执行；常量与 `re` 从已导入的产品模块拿。
    （连整份 extractor 一起 exec 会撞上相对 import；把常量赋值插到 `from __future__`
    前面则直接 SyntaxError——两处都是这一轮实测踩到的。）"""
    from companion.intent import extractor
    mod = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    ns: dict = {"re": re, "_TOPIC_SHELL": extractor._TOPIC_SHELL, "_BOILER_LATIN": extractor._BOILER_LATIN}
    exec(compile(mod, "<mutant>", "exec"), ns)
    return ns["_topic_is_shell"]


def main() -> int:
    from companion.intent import extractor
    from companion.intent.extractor import _BOILER_LATIN, _topic_is_shell, capture_topic

    texts: list[str] = []
    for name in CORPUS_FILES:
        p = EVD / name
        if p.is_file():
            found: list[str] = []
            collect_strings(json.loads(p.read_text(encoding="utf-8")), found)
            found = [t.strip() for t in found if len(t.strip()) >= 6]
            print(f"语料 {name}：取到 {len(found)} 条文本")
            texts += found
    corpus = [t for t in dict.fromkeys(texts) if re.search(r"[一-鿿]", t)]
    print(f"合并去重后含中文的文本 {len(corpus)} 条（全部是仓内**自造**语料，不是真人分布）")

    muts = mutants()
    m_first, m_second = (None, None) if muts[0] is None else (build(muts[0]), build(muts[1]))
    probe = list(dict.fromkeys(MUST_NOT_SHELL + MUST_SHELL + [
        t for s in corpus for t in [capture_topic(s)] if t]))
    if m_first is None:
        same_first, diff_second = [], []
        print("A 闸门形状已改（轮 147 修成三段 if）⇒ 死支证明只适用于修复前那份字节"
              "（那一次读数连仪器已冻进快照），这里只复量 B/C")
    else:
        same_first = [(t, _topic_is_shell(t), m_first(t)) for t in probe if _topic_is_shell(t) != m_first(t)]
        diff_second = [(t, _topic_is_shell(t), m_second(t)) for t in probe if _topic_is_shell(t) != m_second(t)]
        print(f"A1 只留第一句：与产品原判定不同的条数={len(same_first)}（要求 0）")
        print(f"A2 只留第二句：与产品原判定不同的条数={len(diff_second)}（要求 ≥1，否则这条死支证明是假象）")
        for t, a, b in diff_second[:6]:
            print(f"     · 只有第二句判时才翻转：topic={t!r} 产品={a} 变异={b}")
        if len(diff_second) < 1:
            print("判读：A2 不成立 ⇒ 无法区分『第二句从不改变判定』与『两句恒同』，本证明不作数（RC=2）")
            return 2
        if same_first:
            print("判读：A1 竟然有差异 ⇒ 我对闸门形状的读解错了，停手人工判（RC=2）")
            return 2

    for t in MUST_SHELL:
        if not _topic_is_shell(t):
            print(f"判读：控制失效——注释点名的水词形态 {t!r} 没被判壳（RC=2）")
            return 2
    if m_first is None:
        print("控制：水词形态全部判壳 ✅（A 这一维本轮不适用，见上面那行）")
    else:
        print(f"控制：{MUST_SHELL} 全部判壳 ✅｜A1={len(same_first)}（要求 0）"
              f"且 A2={len(diff_second)}（要求 ≥1）⇒ 第二句确为死支")

    victims = [t for t in MUST_NOT_SHELL if _topic_is_shell(t)]
    latin_real = [t for t in (capture_topic(s) for s in corpus)
                  if t and re.search(r"[A-Za-z]{2,}", t)]
    latin_victims = [t for t in latin_real if _topic_is_shell(t)]
    print(f"B 意图对账：水词表 {len(_BOILER_LATIN)} 个词按注释该全判壳；"
          f"被判『不该壳却壳』的样本={victims}")
    print(f"B2 仓内语料里 capture_topic 抽出且含拉丁 token 的主题 {len(latin_real)} 条，其中被判壳 {len(latin_victims)} 条")

    hit_all = [s for s in CONSTRUCTED if capture_topic(s)]
    hit_latin = [s for s in CONSTRUCTED if re.search(r"[A-Za-z]{2,}", s) and capture_topic(s)]
    n_latin_src = sum(1 for s in CONSTRUCTED if re.search(r"[A-Za-z]{2,}", s))
    print(f"C 修复前（构造中文句，标注非真人）：含英文术语的 {n_latin_src} 条里 capture_topic 抽出非空 topic={len(hit_latin)} 条；"
          f"不含术语的 {len(CONSTRUCTED) - n_latin_src} 条里={len(hit_all) - len(hit_latin)} 条")
    for s in CONSTRUCTED:
        print(f"     · topic={capture_topic(s)!r}｜{s[:34]}")

    # 归因要分开：抽不出 topic 有两种成因，处置不同。用产品自己的四条模式判「有没有命中模板」，
    # 命中了却还是空 ⇒ 才可能是闸门吃掉的（不自己重写判断）。
    def matched(s: str) -> bool:
        return any(p.search(s) for p in extractor._TOPIC_PATTERNS)

    empty = [s for s in CONSTRUCTED if not capture_topic(s)]
    no_pattern = [s for s in empty if not matched(s)]
    eaten = [s for s in empty if matched(s)]
    print(f"归因（构造句）：抽不出 topic 共 {len(empty)} 条 = 模板没命中 {len(no_pattern)} 条"
          f" + 命中却被丢 {len(eaten)} 条")
    for s in no_pattern + eaten:
        print(f"     · {'模板未命中' if not matched(s) else '命中却被判空'}｜{s[:34]}")

    if victims or eaten:
        print("判读：**壳词闸门仍在误伤**——存在被判成壳词的实义主题，或有命中模板却被丢空的句子（RC=3）")
        return 3
    print(f"判读：壳词闸门不再误伤拉丁术语（victims 空、水词仍判壳、命中模板的句子全部留住了 topic）；"
          f"剩下 {len(no_pattern)} 条是 `capture_topic` 的模板没覆盖（「调研 X」「方向是 X」这类说法），"
          f"那是另一条待办、与本轮这道闸门无关（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
