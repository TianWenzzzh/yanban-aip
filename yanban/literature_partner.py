"""研伴·文献检索智能体 v1（真实能力版）。

与梧桐平台已提交 ACS（agent_id 01a06c73-9263-7171-a575-05b0dedbf68a）的两个技能一一对应：
- yanban.literature.search         → 多源学术检索（sources.py：Crossref 主力含中文期刊 + arXiv 并行补充，单源失败不影响另一源）
- yanban.literature.review-framework → GLM 生成综述框架（OpenAI 兼容接口）

行为契约（与 ACS 描述一致）：
- 缺研究主题 → awaiting-input 提示补充
- 输入含"综述/框架/脉络/开题" → 综述框架技能；引号内的示例不算，同一句里有取数要求（找/查/检索/列出＋论文/文献/DOI）时按检索走
- 检索失败（网络等） → failed；生成失败 → failed
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from acps_sdk.aip import (
    Product,
    TaskCommand,
    TaskResult,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    DefaultHandlers,
    TaskManager,
)
from acps_sdk.amp import AccessEmitter, HeartbeatEmitter
from fastapi import FastAPI

import logging  # noqa: E402

from sources import failure_kind, multi_source_search, outward_text  # noqa: E402

# 平台分配的正式 AIC。轮 309 第二次身份迁移：1.5.1 记录审批通过时平台授予的新 AIC（现读公开
# discovery：`输出/证据/轮309/discovery_1DHO_原卡.json`）。上一版那颗 …1022（1.5.0）已随版本作废——
# 平台规则实测（registry-server 源码+控制台）：客户端**不能自选 AIC**（create schema 无此字段），
# 审批通过时给新记录**生成全新 AIC**，且已通过记录客户端**永远不能再改**（`_ensure_agent_is_editable`）
# ⇒ 每换一版就换一颗身份，服务器必须跟着迁，否则「在册卡 / 运行身份 / 证书身份」三者不一致。
# MQ 侧身份必须留在 …05BB——那张已通过记录的 AMQP 收件箱冻结为 inbox_05BB（**平台侧 1.5.1 卡上现在
# 也是这一颗**，逐字见上面那份 discovery 原卡的 endPoints），跟着 HTTP 身份一起换会把群组那条路指空。
PARTNER_AIC = "1.2.156.3088.1.BUPT.BNN2UD.A0G0KD.YYTVI4.1DHO"
PARTNER_AIC_MQ = "1.2.156.3088.1.BUPT.BNN2UD.V1R7FH.U8O0NE.05BB"
PARTNER_NAME = "yanban-literature"

GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
# ── 外部调用方的时间预算（轮 307，两个数都要小于 SDK 客户端写死的 30 秒，
#    这条由 `tests2/test_call_budget.py` 钉着；来历与实测见 `_await_within` 的说明）
SEARCH_BUDGET_S = 15.0        # 单源超了就按「暂不可用」写进结果，另一支照发
CAPABILITY_BUDGET_S = 20.0    # 整次能力调用的外墙；轮 308 把综述的生成量降到能在它内跑完（实测 27—32s→见 _glm_review 上方）
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash")

AMP_LOG_DIR = Path(os.environ.get("AMP_LOG_DIR", "./logs"))
AMP_LOG_DIR.mkdir(parents=True, exist_ok=True)
heartbeat = HeartbeatEmitter(AMP_LOG_DIR / f"amp_heartbeat_{PARTNER_NAME}.jsonl", aic=PARTNER_AIC)
access_emitter = AccessEmitter(AMP_LOG_DIR / f"amp_access_{PARTNER_NAME}.jsonl", aic=PARTNER_AIC)

REVIEW_KEYWORDS = ("综述", "框架", "脉络", "开题", "研究空白", "相关工作")
GREETINGS = ("你好", "您好", "hello", "hi", "在吗", "谢谢", "再见", "是什么", "介绍一下你")

# 🔴 综述支的入场判据不能对**整段**文本做子串匹配（轮 309 队长交接文档实测）：
#    「检索钓鱼网页检测相关的 3 篇真实论文，……不要生成综述模板。」是一次检索请求，
#    却被否定句里的「综述」推进了 GLM 综述支——调用方要的是可核验论文，拿到的是提纲。
#    做法：把否定词带出的那一小段（到下一个句读为止）整段摘掉，再匹配关键词表。
#    两条边界都钉了用例：① 摘掉否定段后，同一句里**其余**位置的真综述需求照旧进综述支
#    （`test_review_request_with_a_negated_secondary_word_still_reviews`）；
#    ② 关键词表一字未松，正向请求照旧（`test_a_genuine_review_request_still_takes_the_review_branch`）。
_NEGATED_SEGMENT = re.compile(r"(?:不要|不用|不许|不能|别再|别|无需|无须|不是|请勿|禁止|避免|不想|不希望|拒绝)"
                              r"[^，。,；;、！!？?]*")


# 🔴 轮 310 线上实测：这条入场判据还会被**两种不是需求本身的东西**骗进去——
#    ① 引号里的示例：我们那句兜底提示里举的示例之一（含两枚提线词）被外部网关原样并回下一轮请求，
#      一次检索请求就进了综述支，产物一条 DOI 都没有；
#    ② 外部网关替用户重写需求时爱用提纲词，可同一句里明明写着"找/列出＋论文/DOI"这种取数要求。
#    所以加下面两条窄门，关键词表一字不松（轮 309 那两条边界用例照旧过）。
_QUOTED_SEGMENT = re.compile("[「『“][^」』”]*[」』”]")
_FETCH_VERB = re.compile("(?:找|查|检索|搜索|查找|查阅|列出|给我|推荐)")
_DOC_NOUN = re.compile("(?:论文|文献|DOI|篇)")


def _review_requested(raw: str) -> bool:
    """这句话是在**要**综述（不是在说"不要综述"，也不是在引用一个例子）。

    🔴 归一化必须在取数优先判据**之前**（轮 311 实测，S11 那发）：「只给 RAG 综述提纲，不要检索论文」
    是一次明确的提纲请求，而轮 310 那版把 `_FETCH_VERB`/`_DOC_NOUN` 跑在了**原文**上——
    否定段里的"检索"和"论文"各自命中，于是 `return False` 抢先发生、摘否定小句那步根本没跑到，
    真提纲需求被拉去检索（`输出/_logs/r311_p1_topic_echo_runB.log`，队长 §3.1 与用例 05 要的是反面）。
    现在的次序：先摘引号内示例与否定小句，剩下的话里既有取数要求就不给死提纲。
    """
    s = _NEGATED_SEGMENT.sub("", _QUOTED_SEGMENT.sub("", raw or ""))
    if _FETCH_VERB.search(s) and _DOC_NOUN.search(s):
        return False
    return any(k in s for k in REVIEW_KEYWORDS)


def _input_text(command: TaskCommand) -> str:
    for item in command.dataItems or []:
        if isinstance(item, TextDataItem):
            return item.text
    return ""


# 「条件变更／续找／复述」这一类说法里的动词不是主题的一部分（轮 312 探针
# `输出/_logs/r312_p276_probe_followup_topic.log`：改前 `改成近三年的`→候选「改成」、
# `再多给 4 篇`→「再多给」、`总结一下上一批`→「总结上一批」、`继续找`→「继续」，
# 而产品的 `_topic_ok` 只量长度与壳词，挡不住这类**动词残片**）。
# 这一支不收口，用例 09 的追加句就会拿着半个动词去检索、永远轮不到会话继承。
_CHANGE_VERBS = ("改成|换成|改为|调成|改一下|只要|再多给|再给|再来|再多|再找|继续找|继续|接着找|接着"
                 "|总结一下|总结|归纳|概括|上一批|刚才那些|哪几篇|那几篇|这些里|更多")


def _strip_candidate(text: str) -> str:
    """剥掉指令词后的**候选**主题——它只是候选，能不能当主题要过产品自己的谓词（见 `_extract_topic`）。"""
    t = re.sub(r"帮我|请|找|检索|查找|搜索|调研|梳理|整理|一下|关于|方向的|文献|论文|综述|框架|开题|报告"
               r"|近?五?三?年|\d+\s*篇|" + _CHANGE_VERBS, "", text)
    return re.sub(r"\s+", " ", t).strip()


# 🔴 主题提取一律走产品自己的尺子（`companion/intent/extractor.py`），partner 不再自成一派：
#    轮 301 实测（`输出/_logs/r301_a4b_topic_dist.log`）老形状在 1403 条真实 user 行上 **100% 非空**，
#    于是"我编程还行但没跑过模型"这种整句残片被当主题送进 Crossref，捞回观影/科普类垃圾条目，
#    再以「主题「…」多源检索结果」的形式交给外部调用方（未决 64(a) 的形状）。
#    也不能整体换成 `capture_topic`：它在这批语料上只命中 38.6%，会把 61.4% 的输入掐成"请补充主题"
#    （同一份日志的 M2 行）⇒ 现在的形状是**两条规则通道各出一枚候选，过筛者取最短**：
#      · 过筛 = 产品的 `_topic_ok` ∧ 中间不带标点（标点那道是线上 A2 实测补的，见 `_usable_topic`）；
#      · 取最短 = 实测 `capture_topic("检索多智能体协作方向的文献")` 给带动词的「检索多智能体协作」，
#        而剥词给「多智能体协作」——名词短语才是好检索式。
#    🔴 不接产品的 LLM 兜底通道 `llm_topic`：真 GLM 在场时它会把能力问句编成一句"合法主题"
#      （本地用空桩测不到这层，是部署后 A/B 抓出来的，`输出/_logs/r301_a23_before_after2.log`），
#      外部调用方于是拿到"被造出来的主题 + 一列论文"。宁缺毋造：抽不出规则可认的主题就回「请补充研究主题」。
#      已知代价（普查 `输出/_logs/r301_a7_llm_census.log`：12 条含真请求词的候选里 LLM 能救回 10）
#      ⇒ 这 10 条现在会被反问主题而不是给结果；要不要接回来是**产品口径**，等拍板（未决 64 未关闭）。
from companion.intent.extractor import _topic_ok, capture_topic, year_range  # noqa: E402


# 🔴 轮 311 实测（E5，`输出/_scratch/r311_p14_measure_quoted_echo.py`）：网关把请求包进 JSON 信封时，
#    剥词剩下的 `{"text` 居然过了筛当"主题"——`_EDGES` 只剥**两端**，剥不掉骑在中间的信封字面。
#    信封字符不是名词短语的一部分：加进"中间不许有标点"那道屏，宁可回一句澄清，也不拿半个信封去检索。
_PUNCT_IN_TOPIC = re.compile("[，。,、！!？?；;：:/\\\\{}\"“”]")
# 🔴 轮 311 实测：剥词出来的候选带着全角引号与尾「的」直接进了检索式
# （`找关于“钓鱼网站检测”的论文` ⇒ `'找“钓鱼网站检测”的'`，`输出/_logs/r311_p1_topic_echo_runB.log` 的 S5），
# 而 `_strip_candidate` 的动词表里当时没有「找」（S1 那发给出的是 `'找 多智能体协作'`）。
# 两头剥的字符集补上弯引号与「的」：只在**两端**剥，`多智能体协作中的任务分配` 这种中间的「的」照旧保住。
_EDGES = "：:，,、；;。！!？?…·「」『』《》“”‘’\"'()（）[]【】 \t的"


def _trim_edges(cand: str) -> str:
    """去掉候选主题**两头**的标点/空白。

    🔴 为什么要有这一步（轮 307 线上实测）：`综述：多模态大模型在科研文献理解里的应用脉络 3 篇`
    这句子本身是正常请求，剥词通道把「综述」剥掉后留下 `：多模态大模型…脉络`，
    而 `_usable_topic` 的「不带标点」那道屏（轮 301 为「多智能体协作，给」那种**中间**挂着残字的
    结果补的）把它整枚否掉 ⇒ 外部调用方第一次调用就收到「请补充研究主题」，
    主题它其实说清楚了。只修两头、不放松中间那道：`多智能体协作，给` 仍旧过不了屏（有回归用例钉住）。
    """
    return (cand or "").strip(_EDGES)


def _usable_topic(cand: str) -> bool:
    """一把尺抽出来的字符串能不能当检索主题：先修两头，再过产品谓词 + 「中间不带标点」那道。"""
    cand = _trim_edges(cand)
    return bool(cand) and _topic_ok(cand) and not _PUNCT_IN_TOPIC.search(cand)


def _near_miss(text: str) -> str:
    """规则通道**差一点**过筛的那枚候选——只用来把"该怎么写"回给调用方，绝不送去检索。

    🔴 为什么不在这里放宽尺子：产品谓词 `_topic_ok` 禁掉「科研/怎么/什么/这/那」这类泛指与疑问词、
    并把长度压在 20 字内，是轮 179/301 拿实测语料定的（放宽任何一条都会把整句残片放回检索式，
    那正是未决 64(a) 捞回观影/科普垃圾条目的根因）。所以这里**不改尺子**，只把被拦下的东西说清楚。
    🔴 但也不许把一整句话当成"我替你收的主题"回显（轮 307 第一版就这么干过：
    「只回一句：你现在能正常工作吗？不要」被原样印回调用方脸上）⇒ 带疑问/指代或超过 20 字的
    候选一律不回显，退回那句干净的建议。
    """
    for c in (capture_topic(text or ""), _strip_candidate(text or "")):
        t = _trim_edges(c)
        if len(t) < 4 or len(t) > 20:
            continue
        if re.search(r"[你我他这那什么怎么吗呢？?！!]", t):
            continue
        return t
    return ""


def _topic_hint(raw: str) -> str:
    """抽不出主题时回给调用方的话：**说清楚下一步发什么**，而不是让它猜。

    🔴 括号里那几条要逐字对得上产品的尺（`companion/intent/extractor.py` 的 `_topic_ok` 与
    `_topic_is_shell`），不许写成听着顺口的理由——写错理由会让下一个来改的人去放宽错的那一条。
    🔴 两个分支都以「请补充研究主题」开头：那是轮 301 立下的对外契约（有用例钉着），
    措辞可以加信息，不许把契约句换掉。
    """
    shape = ("长度 2—20 字、是名词短语（汉字不足 2 个又没有真术语的算壳）、"
             "不含「你/我/他/这/那/什么/怎么/科研」这类指代与疑问词、中间不带标点")
    near = _near_miss(raw)
    if not near:
        return (f"请补充研究主题：一个名词短语就行，例如「多智能体协作」「RAG 与知识图谱」。"
                f"（主题的尺子：{shape}。也可以直接接着这条任务再补一句，不必重发。）")
    return (f"请补充研究主题：我把你说的收成「{near}」，但它没通过检索主题的尺子（{shape}）。"
            "换个更窄的名词短语就行，例如「多模态大模型 文献理解」；"
            "也可以直接对着这条任务再补一句，不必重发。")


_CLAUSE_SPLIT = re.compile("[，。,、！!？?；;：:]")
# 只放**以取数动词开头**的小句进来当主题来源（可选前缀是「请/帮我/麻烦」这类客套）。
# 为什么是窄门而不是"所有小句"：轮 309 之前试过对每个小句剥词，「不查文献也想问清楚」被剥成
# 「不也想问清楚」——长度/壳/指代三关全过，于是 KNOWN_MISS 那两条钉着的宁缺毋造当场破了。
_REQUEST_CLAUSE = re.compile(r"^(?:请|帮我|麻烦|务|先|直接)*\s*"
                             r"(?:检索|搜索|查找|查阅|调研|梳理|整理)")


def _request_clauses(text: str) -> list[str]:
    """多分句外部请求里，那些「自己就是一条取数指令」的小句。

    🔴 为什么要有这一步（轮 309 队长交接文档实测）：`检索钓鱼网页检测相关的 3 篇真实论文，列出题名、
    作者、年份、DOI 或原文链接，不要生成综述模板。` 是一次主题说得很清楚的请求，但剥词通道吃**整段**
    文本，剥完把后半截的「题名、作者」这些句读一起留下 ⇒ `_usable_topic` 的「中间不带标点」必然否掉它
    ⇒ 调用方收到「请补充研究主题」（验收④要的就是这条不许发生）。分句后各句自己过筛。
    """
    out = []
    for clause in _CLAUSE_SPLIT.split(text or ""):
        c = clause.strip()
        if c and _REQUEST_CLAUSE.match(c):
            out.append(c)
    return out


# ── 回声分层：我们上一轮发给调用方的话不是用户意图 ──────────────────────────────
# 🔴 实测链（`输出/_logs/r311_p1_topic_echo_runB.log` 的 S2/S3/S4）：外部网关会把我们的兜底提示
#    **原样并回**下一轮请求文本，于是主题通道看见的输入是"用户补的那一个词 + 我们自己的整句话"。
#    把「主题」判成壳词（同轮 `companion/intent/extractor.py` 的 `_TOPIC_SHELL`）之后，顶上来的
#    换成了同一句里的 `'尺子'`——它也是从我们那句「（主题的尺子：…）」里抠出来的。
#    ⇒ 继续往壳词表里加词是打地鼠；按**来源**分层才是根：这些片段是**我们说的**，
#      哪怕它是合法名词短语，也不是用户这轮给的主题（队长 §2.2 明写"旧提示中的『主题』及示例不能参与竞争"）。
# 🔴 锚点取"这些字面只出现在我们自己的 `_topic_hint` 两条分支里"这一事实，**不是**"引号里的内容"——
#    后者会误删用户自己用引号括起来的真实主题（队长 §2.4 点名的风险；实测两面都在同日志的 S5/S6：
#    现况并没有删引号内容，真删了反成新缺陷，所以这里只摘自己的话、不摘引号）。
_OUR_PHRASES = ("请补充研究主题", "我把你说的收成", "主题的尺子", "换个更窄的名词短语",
                "也可以直接", "不必重发", "例如「",
                # 轮 312 用例 09：会话继承也会把我们自己的话发出去，调用方同样可能原样并回下一轮
                # ⇒ 这些锚点必须一起进回声表，否则「沿用上一轮主题『X』」里的 X 会被下一轮当新主题。
                "本会话接续", "这个会话在我这一侧没有上一轮记录", "相对上一批")
_ECHO_REGION = re.compile("(?:" + "|".join(re.escape(p) for p in _OUR_PHRASES) + r")[^。；）]*[。；）]?")
# 🔴 引号是**谁的**，决定这一刀停在哪（队长 §2.4 点名的误删风险；实测 E1/E2：用户把我们的兜底句括在
#    自己的引号里引述时，`[^。；）]*` 一路吃到句末，把他后半句的真需求一起摘走 ⇒ 该检索的被反问）。
#    带引号的那一份先按"只摘引号内"处理，剩下的常规回声（网关原文并回，不带引号）照旧摘到句末界。
_QPAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'"))
_ALTS = "|".join(re.escape(p) for p in sorted(_OUR_PHRASES, key=len, reverse=True))
_QUOTED_ECHO = re.compile("|".join(
    r"%s[^%s]*(?:%s)[^%s]*%s" % (re.escape(a), re.escape(b), _ALTS, re.escape(b), re.escape(b))
    for a, b in _QPAIRS))
# 裸名词通道的起点黑名单：以小句是"指令／否定／连接"的一半开头时，它不是名词短语。
# 这条是轮 309 的实测教训（`:190` 的窄门注释）：对所有小句剥词会把「不查文献也想问清楚」剥成
# '不也想问清楚'——长度/壳/指代三关全过 ⇒ 宁缺毋造那道防线就破了。
_BARE_NOUN_START = re.compile(r"^(?:不|别|没|也|但|而|或|且|要|想|请|帮|给|还|就|都|很|太|把|和|与|跟|对|由于|因为)"
                              r"|(?:怎么|什么|为何|如何|吗|呢)")
_CLAUSE_SPLIT_ALL = re.compile(r"[，。,、！!？?；;：:（）()]")


def _user_span(text: str) -> str:
    """把"我们上一轮的话"从**主题通道**的输入里摘出去；原文照旧完整保留给路由与诊断。

    跑到不动点 ⇒ 重复执行不逐次吞掉内容（幂等这条由 `tests2/test_partner_topic_priority_r311.py` 钉）。
    """
    s = text or ""
    for _ in range(5):
        nxt = _ECHO_REGION.sub("", _QUOTED_ECHO.sub("", s))
        if nxt == s:
            break
        s = nxt
    return s


def _topic_candidates(s: str, allow_bare: bool = False) -> list[str]:
    """一段文本里所有过筛的候选主题。

    两条老通道（产品的 `capture_topic` + 本文件的剥词 `_strip_candidate`，对整段与各条取数小句各跑一次）
    优先；它们全空且 `allow_bare` 时才走**裸名词通道**——用户这轮可能只补了一个词（「钓鱼网站」），
    没有任何取数触发词供正则命中，而那个词本身就是主题（队长 §2.2 要的就是这一格）。

    🔴 `allow_bare` 由调用方绑在"这发输入里确实检测到回声"上。无条件放开会重新踩进轮 309 那个坑，
    本轮就是这么红的（`输出/_logs/r311_p3_subset_after_fixes.log`）：
    「只回一句：你现在能正常工作吗？不要检索。」被收成 `'只回一句'` 直接去检索＝该反问的不反问；
    KNOWN_MISS 那条长句被收成 `'CiteSeer'`＝钉着的"宁缺毋造"被洗掉。
    绑在回声锚点上才叫"接续上一轮"，不是"任何小句都能当主题"。
    """
    out = []
    for src in [s, *_request_clauses(s)]:
        for cand in (capture_topic(src), _strip_candidate(src)):
            if _usable_topic(cand):
                out.append(_trim_edges(cand))
    if out or not allow_bare:
        return out
    for clause in _CLAUSE_SPLIT_ALL.split(s):
        c = clause.strip()
        if not c or _BARE_NOUN_START.search(c):
            continue
        cand = _strip_candidate(c)
        if _usable_topic(cand):
            out.append(_trim_edges(cand))
    return out


async def _extract_topic(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    # 🔴 来源优先于长度（轮 311 实测，队长 §2.1/§2.2）：先在**用户这轮的话**里挑主题；
    #    挑不出来时**不许**回落到"我们上一轮发给调用方的话"里去挑一个泛词——那正是
    #    截图那一格的机制（把「主题」判成壳词之后，顶上来的换成了同一句话里的「尺子」，
    #    `输出/_logs/r311_p1_topic_echo_runB.log`）。
    #    同一来源之内仍按最短兜底（轮 301 定的那条：`检索多智能体协作` 输给 `多智能体协作`），
    #    队长要的"按轮次/明确性/槽位打分"得先有会话状态才谈得上（伙伴侧现在连跨轮恢复都没有，
    #    见 `输出/_scratch/r311_stage1_findings.md` 的分叉点表），这一轮不假装实现了它。
    span = _user_span(t).strip()
    # 只有"这发里混着我们上一轮的话"时才允许走裸名词通道（理由见 `_topic_candidates` 的 🔴 段）
    pool = _topic_candidates(span, allow_bare=(span != t)) if span else []
    if pool:
        return _trim_edges(min(pool, key=len))
    # 🔴 这里**不接** LLM 兜底通道（`llm_topic`）。轮 301 线上实测：真 GLM 在场时它会把
    #    「只回一句：你现在能正常工作吗？不要检索。」编成一句合法主题（本地用空桩测不到这一层，
    #    是部署后 A/B 才抓出来的，`输出/_logs/r301_a23_before_after2.log`），于是外部调用方
    #    拿到的是"一个被造出来的主题 + 一列论文"。对一次性外部调用，宁缺毋造：
    #    抽不出规则可认的主题就回「请补充研究主题」。
    #    已知代价（普查 `输出/_logs/r301_a7_llm_census.log`）：12 条含真请求词、规则通道抽不出主题的
    #    候选里，LLM 能救回 10 条 ⇒ 这 10 条现在会被反问主题。要不要接回来是**产品口径**问题，等拍板。
    return ""


# ─────────────────────────────────────────────────────────────
# 跨轮会话状态 —— 队长 §5 用例 09（改年份 / 续找 / 总结上一批）
#
# 🔴 键是 `command.sessionId`，**不是** taskId：SDK 那张任务表是进程内按 taskId 存的
#    （`acps_sdk/aip/aip_rpc_server.py:170` `_tasks: Dict[str, TaskResult]`），同一会话里调用方
#    换一发 taskId 就接不上；而一次会话的多发指令带的是同一个 sessionId
#    （`partner_base.py:255` 拿它配「1 次调用」的对应关系）。
# 🔴 状态只活在本进程：重启／重部署就清（容器可写层不持久——`Dockerfile.partner` 里 pymupdf 那颗
#    教训就是「以为留着，其实 recreate 就没了」）。清掉之后的行为是**照直说这一侧没有上一轮记录、
#    请补主题**，不是编一个，所以「重启后不假装记得」这一格由常驻测试 S7 钉住。
# 🔴 为什么现在不挪进共用核心（`companion/conversation/dialogue_manager.py` + `yanban.db`）：
#    伙伴容器里那份 `data/yanban.db` 与网页后端那份**不是同一个文件**，写进去就是两条活副本、
#    两个都自称权威；而 `handle_message` 那条路会牵进 LLM 与状态机，等于给已注册的两种技能
#    换执行器。要不要打通（同一持久卷／换成后端 API 调用）是**产品口径**问题，等拍板（台账 91）。
# ─────────────────────────────────────────────────────────────
_SESSION_TTL_S = 1800     # 30 分钟没动静就当这一会话结束了
_SESSION_CAP = 64         # 只留最近 64 个会话，每份带一段清单文本
_MORE_STEP = 5            # 「再多找几篇」没写数字时一次加的量
_sessions: "OrderedDict[str, dict]" = OrderedDict()

_SUMMARIZE_R = re.compile(r"总结|归纳|概括|上一批|刚才那些|这些里|这几篇|哪几篇|哪些最")
_MORE_R = re.compile(r"再(?:来|多|给|找|查|检索|要|加)|还有(?:吗|别的|其他)?|"
                     r"继续(?:找|查|检索|给)|更多|换一批")
_COUNT_R = re.compile(r"(\d+)\s*篇")
# 🔴 这两条形是对 `sources._fmt` **实际发出来的行形状**的耦合，所以由常驻测试 S6 拿 `_fmt` 的真实
#    返回值钉住：那边一改形状，这里摘不到条目，S6 立刻红——不许出现「静默摘不到 ⇒ 复述成 0 条」。
_ENTRY_LINE = re.compile(r"^([0-9]+)\. \*\*(.+?)\*\*\s*$", re.M)
_ENTRY_DETAIL = re.compile(r"^ {3}\[([^\]]+)\] ([^｜]*)｜([^｜]*)｜", re.M)


def _session_store(sid: str, rec: dict) -> None:
    _sessions[sid] = rec
    _sessions.move_to_end(sid)
    while len(_sessions) > _SESSION_CAP:
        _sessions.popitem(last=False)


def _session_peek(sid: str | None) -> dict | None:
    """这一会话的上一轮记录；没有／过期都返回 None，调用方据此走「问主题」而不是猜。"""
    if not sid:
        return None
    rec = _sessions.get(sid)
    if rec is None:
        return None
    if time.monotonic() - rec["t"] > _SESSION_TTL_S:
        _sessions.pop(sid, None)
        return None
    return rec


def _titles_of(md: str) -> list[str]:
    return [t for _i, t in _ENTRY_LINE.findall(md or "")]


def _limit_for(raw: str, prior: dict | None, more: bool) -> int:
    """这一轮要几条。三条口子分开算，避免「改成近三年」被顺手加量：
    续找 ⇒ 上一轮的量 + 本轮点明的数（没写数就 + `_MORE_STEP`）；写了数 ⇒ 就用那个数；否则 10（轮 311 旧值）。"""
    m = _COUNT_R.search(raw or "")
    n = int(m.group(1)) if m else None
    if more:
        return (prior["limit"] if prior else 10) + (n or _MORE_STEP)
    if n:
        return n
    return prior["limit"] if prior else 10


def _looks_like_followup(raw: str) -> bool:
    """这句话是不是「接着上一轮说」的形状（点明年份窗／要更多／让复述），本身没有新主题。
    🔴 只有这个形状才配说"这一会话没有上一轮记录"：首句没主题与"记录丢了"是两回事，
       把前者也说成后者会让调用方以为我们记得却又装忘（第一轮就该是普通的反问主题）。"""
    return bool(_SUMMARIZE_R.search(raw or "") or _MORE_R.search(raw or "") or year_range(raw))


async def _resolve_turn(raw: str, sid: str | None) -> dict:
    """这一轮到底要查什么。只有**这个会话我们真发过一轮**才继承，认不出形状的照旧反问。"""
    topic = await _extract_topic(raw)
    yr = year_range(raw)
    if topic:
        # 有新主题就是新起一轮：换题必须显式断开，不许把上一轮的年份窗/篇数混进来
        return {"kind": "search", "topic": topic, "year": yr, "limit": _limit_for(raw, None, False)}
    prior = _session_peek(sid)
    if prior is None:
        return {"kind": "none", "sid": bool(sid), "followup": _looks_like_followup(raw)}
    if _SUMMARIZE_R.search(raw):
        return {"kind": "summarize", "prior": prior}
    more = bool(_MORE_R.search(raw))
    if yr or more:
        return {"kind": "search", "topic": prior["topic"], "year": yr or prior["year"],
                "limit": _limit_for(raw, prior, more), "inherited": True, "prior": prior,
                "why": "你这轮点明了新的年份窗" if yr else "你这轮要接着上一批再找"}
    return {"kind": "none", "sid": bool(sid), "followup": False}


def _turn_hint(raw: str, turn: dict) -> str:
    """反问主题：分「这一句是追加句但这侧没有记录」和「压根没主题」两种话说。"""
    if turn["kind"] == "none" and turn.get("sid") and turn.get("followup"):
        return ("请补充研究主题：这一句我读成「接着上一轮」，但这个会话在我这一侧没有上一轮记录"
                "（伙伴进程重启、或这一会话超过 30 分钟没动静就会清），我不替你猜一个主题。"
                "请把主题再写一次，例如「检索多智能体协作的文献 5 篇」。")
    return _topic_hint(raw)


def _inherited_head(turn: dict) -> str:
    """继承要说在脸面上——调用方得知道这一轮的主题不是它这句给的。
    🔴 这一句不带括号也不分号，整句到句号为止：`_OUR_PHRASES` 的「本会话接续」锚点要能一次摘干净，
       否则回声里剩下的那半截会被下一轮当成主题候选（轮 311 那个「尺子」就是这么来的）。
    🔴 这里**不重复报**年份窗和篇数——那两个数由 `sources` 的「筛选条件」行给（一个数只留一个出处）。"""
    return (f"本会话接续上一轮：沿用上一轮主题「{turn['topic']}」，"
            f"因为这一句里没有新主题，{turn['why']}。")


def _fresh_note(md: str, prior: dict | None) -> str:
    """续找要说清这轮里有几条是新的——重复的照直说重复，不装作换了血。
    🔴 句子必须以「相对上一批」这颗锚点**开头**：回声表是"锚点→句末界"一刀，锚点骑在句中会留下
       前半截残片（实测改前留下「本轮 2 条，」，S8 就是钉这件事）。"""
    now = _titles_of(md)
    if not prior or not now:
        return ""
    before = set(prior.get("titles") or ())
    fresh = [t for t in now if t not in before]
    return f"相对上一批：本轮 {len(now)} 条，新增 {len(fresh)} 条，重复 {len(now) - len(fresh)} 条。"


def _summarize(prior: dict) -> str:
    """对**已发出去的那一份清单**做机械复述：不发检索、不调模型、不添新条目。"""
    md = prior.get("list_md") or ""
    titles = _titles_of(md)
    head = "本会话接续上一轮：这是对上一批已给清单的复述，本轮没有再发检索。"
    if not titles:
        return (head + "但上一轮那发在我这边没有留下可复述的条目清单"
                "（那一发是综述提纲，或者清单形状对不上我的摘取尺）。"
                "要条目清单就点明主题再说一次，例如「检索多智能体协作的文献 5 篇」。")
    details = _ENTRY_DETAIL.findall(md)
    years = sorted({int(y) for y, _a, _v in details if y.isdigit()})
    venues: dict[str, int] = {}
    for _y, _a, v in details:
        key = (v or "").strip() or "未知来源"
        venues[key] = venues.get(key, 0) + 1
    span = f"{years[0]}—{years[-1]}，{len(years)} 个不同年份" if years else "上一批里没有可核实的年份"
    venue_line = "、".join(f"{k} {n} 条" for k, n in
                           sorted(venues.items(), key=lambda kv: (-kv[1], kv[0]))) or "无来源标注"
    listed = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
    return (f"{head}\n上一批（主题「{prior['topic']}」）共 {len(titles)} 条：\n"
            f"- 年份跨度：{span}\n- 来源分布：{venue_line}\n- 条目：\n{listed}\n"
            "要换条件再查，直接点明年份或篇数，例如「改成近三年」或「再多给 5 篇」。")


def _record_turn(sid: str | None, turn: dict, skill: str, content: str) -> None:
    """办成了的这一轮才进会话表（失败的轮次不许成为下一轮的「上一批」）。"""
    if not sid:
        return
    _session_store(sid, {"t": time.monotonic(), "topic": turn["topic"], "year": turn["year"],
                         "limit": turn["limit"], "skill": skill,
                         "list_md": content if skill.endswith(".search") else "",
                         "titles": _titles_of(content) if skill.endswith(".search") else []})


async def _search(topic: str, limit: int, year_range=None) -> str:
    """多源学术检索（sources.py：Crossref 主力 + arXiv 并行补充），返回结构化 Markdown 清单。

    `year_range` 是用户点明的年份窗（轮 312 补的：以前只把「近三年」从主题里剥掉就当它不存在）。
    """
    return await multi_source_search(topic, limit=min(limit, 20), budget_s=SEARCH_BUDGET_S,
                                     year_range=year_range)


async def _await_within(coro, budget: float, what: str) -> str:
    """在预算内拿结果；超预算抛一句写明"是什么没跑完"的 TimeoutError。

    🔴 为什么要主动收手而不是让对方等：平台那侧用 `acps_sdk` 默认客户端的调用方 **30 秒就超时**
    （`aip_rpc_client.py` 里 `timeout=30.0` 是写死的），而我们最坏一趟检索实测拖过 88.9 秒
    （未决 64，审计 durationMs=88915）——那种日子我们**答完了对方也已经放弃**，
    一次"被调用"就变成调用方视角的一次失败，还白占着一个还在跑的检索任务。
    """
    try:
        return await asyncio.wait_for(coro, timeout=budget)
    except asyncio.TimeoutError as exc:
        raise asyncio.TimeoutError(
            f"{what}未在 {budget:.0f} 秒预算内完成，请直接重试一次（或把主题写窄一点）") from exc


log = logging.getLogger("yanban.partner")


def _failure_line(exc: BaseException) -> str:
    """失败时回给调用方的那一句：**只分类，不外发原始异常**（队长 §4.1「移除…原始异常…低层诊断」）。

    🔴 但不是把有用的话一起砍掉：`_await_within` 的预算那句、`sources` 的「所有数据源均不可用」那句，
    是产品自己拟的、带下一步动作的原话，按形状白名单**一字不改**照发（`outward_text`）。
    原始异常仍然记进服务端日志——不外发≠不记录，§4.4 要的是「记录实际错误分类」而不是把它藏起来。
    """
    ours = outward_text(exc)
    if ours:
        return ours
    return (f"这一轮没能完成（{failure_kind(exc)}）。没有伪造结果，也不会把失败报成完成："
            "请稍后重试一次，或把主题写窄一点再发。")


async def _glm_review(topic: str, purpose: str) -> str:
    """调 GLM 生成综述框架（Markdown 提纲）。"""
    prompt = (
        f"你是科研综述顾问。针对研究主题「{topic}」，用途「{purpose or '学位论文相关工作章节'}」，"
        "给一份 Markdown 提纲：1) 研究脉络分期 2) 主要技术路线对比 3) 代表性工作分组 4) 研究空白与未来方向。"
        "每部分只写 2 条要点（各一句话）＋建议检索关键词，不展开成段落，全文 300 字内。"
    )
    # 轮 308 实测（容器内连打 3 次）：这一档要 27—32 秒，而调用方那把尺是 acps_sdk 客户端**写死的 30 秒**
    # （B4 那条门钉的就是它），所以同步回答只有一条路——让要生成的东西小到能在 20 秒预算内跑完。
    # 🔴 光在提示词里写"控制在 300 字内"没用：实测模型照样吐 815—1116 字、耗时只从 27.0—32.3s 挪到
    # 26.9—28.5s。真正管住输出量的是 `max_tokens`（实测生成速率 ~35—44 字/秒），所以上限一起压。
    # 哨兵＝`probe_r308_glm_timing.py`（直接 import 本模块量 `_glm_review` 自己，不在仪器里另抄一份 prompt）。
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GLM_API_KEY}"},
            json={"model": GLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 480},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


hb_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global hb_task
    hb_task = asyncio.create_task(heartbeat.run_periodic(15), name="amp-heartbeat")
    yield
    hb_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await hb_task


app = FastAPI(title="研伴·文献检索智能体", lifespan=lifespan)


def _with_sender(task: TaskResult) -> TaskResult:
    task.senderId = PARTNER_AIC
    return task


def _insufficient(raw: str, turn: dict) -> bool:
    """这一句能不能办事：纯寒暄，或者既没给出新主题、也不是"复述上一批"那种形状。"""
    lowered = raw.strip().lower()
    is_greeting = (len(raw.strip()) <= 8 and lowered.rstrip("!！.?？") in {g for g in GREETINGS}) or any(
        lowered.startswith(g) for g in GREETINGS
    )
    return is_greeting or (not turn.get("topic") and turn["kind"] != "summarize")


async def _fulfil(command: TaskCommand, task: TaskResult, raw: str, turn: dict) -> TaskResult:
    """把这一轮办在**已经存在的那颗任务**上——Start 与 Continue 共用的同一条腿。

    🔴 Continue 不许再走 `on_start(command, None)`：`TaskManager.create_task` 是按 taskId **原地覆盖**
       `_tasks`，等于把原始 `start` 命令从 commandHistory 里抹掉；任务办完之后补一句寒暄，更会把
       已经交付的清单整份换成 awaiting-input 的空任务（轮 312 实测 J2/J6，
       `输出/_logs/r312_p290_probe_dispatch_replay_r312.log`；常驻判据
       `tests2/test_partner_dispatch_replay_r312.py` D2/D3/D4）。
    """
    topic = turn.get("topic", "")
    is_review = _review_requested(raw)
    if task.status.state != TaskState.Working:
        TaskManager.update_task_status(task.taskId, TaskState.Working)
    head = ""
    try:
        if turn["kind"] == "summarize":
            # 复述已给过的清单：不发检索、不调模型，也不占调用预算
            content, skill = _summarize(turn["prior"]), "yanban.literature.search"
        elif is_review:
            content = await _await_within(_glm_review(topic, purpose=raw[:30]),
                                          CAPABILITY_BUDGET_S, "提纲生成")
            skill = "yanban.literature.review-framework"
        else:
            if turn.get("inherited"):
                head = _inherited_head(turn) + "\n"
            content = await _await_within(_search(topic, limit=turn["limit"], year_range=turn["year"]),
                                          CAPABILITY_BUDGET_S, "文献检索")
            skill = "yanban.literature.search"
            note = _fresh_note(content, turn.get("prior"))
            head += (note + "\n") if note else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("能力调用失败（原始异常不外发，只回分类）：%r", exc, exc_info=True)
        return _with_sender(TaskManager.update_task_status(
            task.taskId, TaskState.Failed, data_items=[TextDataItem(text=_failure_line(exc))],
        ))

    if turn["kind"] != "summarize":
        # 只记办成了的那一轮；复述那一发不改会话状态（它没有新的主题/篇数/清单可记）
        _record_turn(command.sessionId, turn, skill, content)
    content = head + content
    TaskManager.set_products(
        task.taskId,
        [Product(id=f"{skill}-{task.taskId[:8]}", name=skill, dataItems=[TextDataItem(text=content)])],
    )
    return _with_sender(TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion))


async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
    if task:
        # 重发的 Start（网关重试、重复回调）：返回现有快照，一次检索都不许多烧
        return _with_sender(task)

    raw = _input_text(command)
    turn = await _resolve_turn(raw, command.sessionId)
    if _insufficient(raw, turn):
        return _with_sender(TaskManager.create_task(
            command,
            initial_state=TaskState.AwaitingInput,
            data_items=[TextDataItem(text=_turn_hint(raw, turn))],
        ))
    task = TaskManager.create_task(command, initial_state=TaskState.Working)
    return await _fulfil(command, task, raw, turn)


async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
    """continue＝"补充后的重试"：办在**同一颗**任务上，历史只追加、不重置。"""
    TaskManager.add_command_to_history(task.taskId, command)
    raw = _input_text(command)
    turn = await _resolve_turn(raw, command.sessionId)
    if _insufficient(raw, turn):
        # 信息不够就不动已经给出去那份：只补一条"还缺什么"的状态读数，状态本身不变
        return _with_sender(TaskManager.update_task_status(
            task.taskId, task.status.state, data_items=[TextDataItem(text=_turn_hint(raw, turn))],
        ))
    return await _fulfil(command, task, raw, turn)


handlers = CommandHandlers(
    on_start=on_start,
    on_continue=on_continue,
    on_get=DefaultHandlers.get,
    on_cancel=DefaultHandlers.cancel,
    on_complete=DefaultHandlers.complete,
)

# 本地开发态关闭身份绑定；接入梧桐（mTLS 证书就绪）后开启
from identity_mode import identity_binding_enabled as _identity_binding  # noqa: E402
# 🔴 不能用 SDK 的 add_aip_rpc_router：它不写 AMP access 日志 ⇒ 被调用多少次都数不出来
# （2026-09-30 实测本智能体正是走裸路由，容器内打一次 /rpc，access 记录 +0）。
from partner_base import (  # noqa: E402
    add_audited_rpc_router, add_partner_card_routes, mark_counted, note_access)

add_audited_rpc_router(app, "/rpc", handlers, aic=PARTNER_AIC, name=PARTNER_NAME,
                       emitter=access_emitter, identity_binding_enabled=_identity_binding())
# 轮 306：群组那两条入站通道（HTTP 邀约 + MQ 里下发的任务指令）现在各会落一条账；
# 登记进计数面是给闸门 `tests2/test_counting_surface.py` 对账用的，不是注释。
mark_counted(PARTNER_NAME, "/group/rpc")
mark_counted(PARTNER_NAME, "mq:group")
# 轮 299：公网登记的 URL 经 caddy 就落到本应用（18031→8031），不发卡平台就没法自助取/刷新卡。
# 轮 308c：`inbox_aic=PARTNER_AIC_MQ`——卡上 aic 跟 HTTP 身份（…1022），AMQP 收件箱跟 MQ 身份（…05BB）。
add_partner_card_routes(app, name=PARTNER_NAME, aic=PARTNER_AIC, inbox_aic=PARTNER_AIC_MQ)


# ─────────────────────────────────────────────────────────────
# 群组模式（Group MQ）接入 —— 官方教程 aip-sdk-tutorial.md §4.1 形态
# 流程：Leader 经 Discovery 拿到本智能体 RPC URL → POST /group/rpc 下发
#       RabbitMQRequest（含平台 MQ 地址与本会话凭据）→ join_group 加入群组
#       队列 → 之后任务指令经平台 MQ 广播到达（一个 session 一个 MQ，无冲突）
# ─────────────────────────────────────────────────────────────
from acps_sdk.aip import GroupPartnerMqClient, RabbitMQRequest, RabbitMQResponse, TaskCommandType  # noqa: E402

group_clients: dict[str, GroupPartnerMqClient] = {}


async def _run_capability(raw: str, sid: str | None = None) -> tuple[str, str]:
    """复用现有能力执行群组任务。返回 (产物内容, 技能id)。

    群组那条也要有预算：MQ 里的任务指令没有人在同步等，但一条永远不回来的能力
    会把那颗客户端占住，而且平台侧看我们就是"接了任务不交付"。

    轮 312：`sid` 是这一发的 `command.sessionId`——群组路径同样要能接续上一轮（用例 09 的
    「换一发 taskId 也算同一会话」在这一支更常见，因为广播里 taskId 每发都换）。
    🔴 认不出主题时仍抛那枚契约原文「请补充研究主题」：这句在外发白名单里（台账 83 的同源腿），
       换成整段提示词会让 `_failure_line` 走通用分支，调用方反而拿不到下一步动作。
    """
    turn = await _resolve_turn(raw, sid)
    topic = turn.get("topic", "")
    if turn["kind"] == "summarize":
        return _summarize(turn["prior"]), "yanban.literature.search"
    if not topic:
        raise ValueError("请补充研究主题")
    if _review_requested(raw) and not turn.get("inherited"):
        return (await _await_within(_glm_review(topic, purpose=raw[:30]),
                                    CAPABILITY_BUDGET_S, "提纲生成"),
                "yanban.literature.review-framework")
    content = await _await_within(_search(topic, limit=turn["limit"], year_range=turn["year"]),
                                  CAPABILITY_BUDGET_S, "文献检索")
    _record_turn(sid, turn, "yanban.literature.search", content)
    head = (_inherited_head(turn) + "\n") if turn.get("inherited") else ""
    note = _fresh_note(content, turn.get("prior"))
    return head + ((note + "\n") if note else "") + content, "yanban.literature.search"


async def _on_group_task(command: TaskCommand, is_mentioned: bool) -> None:
    """群组队列里收到的任务指令 —— 这是"别的智能体经平台 MQ 调我们"的那条路。

    🔴 轮 306 之前这条路一条都不落账（现读线上账本 route 值只有 `/rpc` 与 `/`）：
    平台真用群组方式调用我们时，我们这边数不出来、也就拿不出互联交互记录。
    """
    t0 = time.monotonic()
    raw_len = len(command.model_dump_json())
    code = 500
    try:
        await _on_group_task_inner(command, is_mentioned)
        code = 200
    finally:
        await note_access(access_emitter, callee_aic=PARTNER_AIC, callee_name=PARTNER_NAME,
                          method="mq", route="mq:group", caller=command.senderId or "unknown",
                          code=code, raw_len=raw_len, duration_ms=int((time.monotonic() - t0) * 1000),
                          correlation_id=command.sessionId or command.taskId or None)


async def _on_group_task_inner(command: TaskCommand, is_mentioned: bool) -> None:
    if not command.groupId or not command.taskId or not command.sessionId:
        return
    client = group_clients.get(command.groupId)
    if not client:
        return
    if command.command == TaskCommandType.Start:
        await client.accept_task(command.taskId, command.sessionId)
        await client.start_working(command.taskId, command.sessionId)
        try:
            content, skill = await _run_capability(_input_text(command), command.sessionId)
            product = Product(
                id=f"{skill}-{command.taskId[:8]}",
                name=skill,
                dataItems=[TextDataItem(text=content)],
            )
            await client.submit_for_completion(command.taskId, command.sessionId, [product])
        except Exception as exc:  # noqa: BLE001
            log.warning("群组能力调用失败（原始异常不外发，只回分类）：%r", exc, exc_info=True)
            await client.fail_task(command.taskId, command.sessionId, _failure_line(exc))
    elif command.command == TaskCommandType.Complete:
        await client.complete_task(command.taskId, command.sessionId)
    elif command.command == TaskCommandType.Cancel:
        await client.cancel_task(command.taskId, command.sessionId)


async def _on_group_result(result: TaskResult) -> None:
    # 其他 Partner 的状态广播：本 Partner 只消费指派给自己的任务，这里保持观测即可
    return None


@app.post("/group/rpc", response_model=RabbitMQResponse)
async def handle_group_invite(request: RabbitMQRequest) -> RabbitMQResponse:
    """接收 Leader 的群组邀请（RabbitMQRequest），加入平台 MQ 并开始消费。

    这也是一条"被调用"：邀请方身份取 `params.group.leader.aic`（写死成本队就把
    "被谁拉进群组"这件事擦掉了，与 `/rpc` 那条同一套口径）。
    """
    t0 = time.monotonic()
    raw_len = len(request.model_dump_json())
    caller = request.params.group.leader.aic or "unknown"
    code = 500
    try:
        group_id = request.params.group.groupId
        client = GroupPartnerMqClient(partner_aic=PARTNER_AIC_MQ)
        client.set_command_handler(_on_group_task)
        client.set_task_result_handler(_on_group_result)
        response = await client.join_group(request)
        if response.result:
            group_clients[group_id] = client
            code = 200
        return response
    except Exception as exc:  # noqa: BLE001 — 连不上平台 MQ 也不许崩栈：调用方要拿到可解析的回答
        from acps_sdk.aip.aip_group_model import RabbitMQResponseError, RabbitMQResponseErrorData
        response = RabbitMQResponse(id=request.id, error=RabbitMQResponseError(
            code=-32603, message="group join failed",
            data=RabbitMQResponseErrorData(errorType="internal", details=str(exc)[:300])))
        return response
    finally:
        await note_access(access_emitter, callee_aic=PARTNER_AIC, callee_name=PARTNER_NAME,
                          route="/group/rpc", caller=caller, code=code, raw_len=raw_len,
                          duration_ms=int((time.monotonic() - t0) * 1000))


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "agent": PARTNER_NAME,
        "aic": PARTNER_AIC,
        "glm": bool(GLM_API_KEY),
        "groups": len(group_clients),
    }
