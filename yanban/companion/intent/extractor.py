"""对话 → ResearchIntent 增量提取。

双通道：
1. 规则通道（零 LLM、确定性、可测试）：关键词/正则捕获高信号字段；
2. LLM 通道（可选增强）：chat_json 提取规则覆盖不到的语义信息；LLM_MOCK=1 时跳过。
"""
from __future__ import annotations

import datetime
import re

from .model import RESEARCH_STAGES

# ── 规则库（确定性提取）───────────────────────────────────────────────
_STAGE_HINTS = [
    (r"第一次做科研|不知道从哪|刚接触|入门|不了解.*(研究|科研)", "exploration"),
    (r"大量看论文|读文献|综述|领域地图|研究现状", "literature_review"),
    (r"确定.*选题|定题|选方向|论文选题|还没人做的|没人做过|没被研究过|研究空白|创新点", "topic_selection"),
    (r"设计.*(方法|模型|方案)|方法设计|提出.*方法", "method_design"),
    (r"做实验|跑实验|复现|baseline", "experiment"),
    (r"写论文|投稿|写作", "writing"),
]

_GOAL_HINTS = [
    (r"(老师让|导师让|导师要求|老板让|老板要求|组会要求)", "导师要求的科研任务"),
    (r"(一年|1年|一年左右).*(SCI|投稿|论文|发表)", "一年左右发表 SCI"),
    (r"毕业(论文|设计)", "完成毕业论文"),
    (r"(比赛|竞赛)", "参加竞赛"),
    (r"(半年|6个月).*(投稿|论文)", "半年内投稿"),
    (r"老师让|导师让", "导师要求的科研任务"),
    # 「找一个还没人做的点」本身就是目标，实测一个字都没接住 → user_goal 空、置信度不涨
    (r"还没人做的|没人做过|没被人做|研究空白|创新点|新的?切入点", "找一个还没人做过、又做得动的切入点"),
]

_LEVEL_HINTS: dict[str, list[tuple[str, str]]] = {
    "dl_level": [
        (r"深度学习(学过一点|学过|基础|跑过|入门)", "basic"),
        (r"深度学习.*(熟练|熟悉|精通|较多)", "intermediate"),
        (r"(没|不).{0,2}(接触|了解|会).{0,3}深度学习", "none"),
        (r"深度学习.{0,4}(完全|根本)?(没|不)(接触|了解|学过|会)", "none"),
        # 实测漏掉的：学生不说"深度学习"，说的是"我不太会调参"（BraTS 那位的第二轮）
        (r"(不太|不|没怎么|不会).{0,3}(会)?调参", "basic"),
        (r"(调参|训练|显存|分布式).{0,4}(不熟|没弄过|没经验|头大)", "basic"),
    ],
    "llm_level": [
        (r"(LLM|大模型).{0,4}(不|没).{0,2}(了解|熟|会|接触)", "none"),
        (r"(LLM|大模型).{0,6}(不太|不|没).{0,3}(了解|熟|会)", "none"),
        (r"(了解|用过|学过).{0,4}(LLM|大模型)", "basic"),
        (r"(LLM|大模型).{0,6}(用过|熟悉|了解|调过)", "basic"),
        (r"(LLM|大模型).{0,6}(微调|部署|训练过)", "intermediate"),
    ],
    "ml_level": [
        (r"机器学习.*(基础|学过|入门)", "basic"),
    ],
    "paper_reading_level": [
        (r"(第一次|没怎么|很少).{0,4}(读|看).{0,3}论文", "needs_explanation"),
        (r"(读|看).{0,4}(英文)?论文.{0,6}(吃力|困难|费劲|头疼)", "needs_explanation"),
        (r"(读过大量|读过很多|经常读).{0,3}论文", "advanced"),
        (r"(论文|英文文献).{0,6}(还行|没问题|没什么问题|不费劲)", "advanced"),
        # "我已经读了二十来篇" 实测抽不出来，于是第二轮还在问"你读论文多吗"——都读二十篇了
        (r"(读了|看过|读过的?).{0,4}([一二三四五六七八九十两]\s*十|几十|\d{2,})\s*来?篇", "advanced"),
    ],
}

# 算力/数据等现实约束：此前完全没有规则，用户说了也进不了 intent
_CONSTRAINT_HINTS: list[tuple[str, str, str]] = [
    ("research_constraints.compute", r"(有 ?GPU|有显卡|能跑实验|GPU 随便用|[0-9一两三四五六七八九十]+ ?块? ?(3080|3090|4090|A100|V100|T4|显卡))", "有 GPU"),
    ("research_constraints.compute", r"(没有 ?GPU|没显卡|跑不了实验|只能轻量)", "无 GPU"),
    ("research_constraints.compute", r"(只有.{0,6}(笔记本|轻薄本|电脑))", "仅笔记本"),
    ("research_constraints.dataset", r"(自己采集|没有数据集|找不到数据集)", "缺数据集"),
    ("research_constraints.dataset", r"(有.{0,6}(数据集|标注数据))", "已有数据集"),
    # 「有两年历史数据，15 分钟采样」实测全落空：原规则硬要求出现「数据集」三个字
    ("research_constraints.dataset",
     r"([一两二三四五六七八九十]|\d+)\s*年.{0,4}(历史|实测|现场)?数据", "已有多年历史数据"),
    ("research_constraints.dataset", r"(\d+\s*分钟.{0,4}采样|采样(间隔|频率)|时间分辨率)", "已有时序采样数据"),
    # "数据是公开的 BraTS 脑肿瘤 MRI 数据集" 实测抽不出来（原规则要求出现"有"字）
    ("research_constraints.dataset", r"(公开的?|公共的?|public).{0,12}(数据集|dataset)", "已有公开数据集"),
    ("research_constraints.dataset", r"(BraTS|ImageNet|COCO|KITTI|MIMIC|UCI|SQuAD|GLUE)", "已有公开数据集"),
    ("research_constraints.experiment_time", r"(三个月|3 个月|3个月).{0,8}(内|要|得|出)", "三个月内"),
    ("research_constraints.experiment_time", r"(明年|今年).{0,4}(三月|3 ?月|年中|春天|春季|上半年|底|毕业|前)", "一年内"),
    ("research_constraints.coding_difficulty", r"(不想写代码|代码能力(弱|差)|不会写代码)", "回避编码"),
]

# 发表类型：原来只认 SCI/CCF，会议/毕业论文/比赛说了也进不去
_PUB_TYPE_HINTS = [
    (r"SCI", "SCI"), (r"CCF", "CCF"),
    (r"(投|发|开|参加)?.{0,3}(会议|conference|workshop|顶会)", "conference"),
    (r"毕业(论文|设计)", "thesis"),
    (r"(参加|打)?(比赛|竞赛)", "competition"),
]

# 🔴 轮 312：这张表以前把相对说法写成**绝对年份**（`(r"近三年", 2023, 2026)` 一类），上界年年过期。
# 网页侧只把它当"从 X 年起"的口径，过期只是话说小了；而伙伴入口从本轮起会**按窗丢弃**条目
# （`sources.multi_source_search` 的 `lo <= y <= hi`），过期就变成"2027 年说近三年拿不到 2027 年的论文"。
# 所以相对说法只存**偏移年数**，绝对年份一律由 `_today_year()` 现推（2026 年内逐值与旧表完全相同）。
_YEAR_HINTS = [
    (r"近两年|最近两年", 2),
    (r"近三年", 3),
    (r"近五年|最近五年", 5),
    (r"最新|这两年", 2),
]

# 用户**明写**的区间是条件本身，不许跟着时钟走（这一枚只认 `2024-2026` 这一形；
# 把它推广成"任意四位年份区间"会新造出以前根本没有的窗＝改网页行为，已登记待拍板）。
_ABSOLUTE_YEAR_RANGES = [
    (r"2024[-—~至]+2026", 2024, 2026),
]


def _today_year() -> int:
    """年份窗里"今年"的唯一取法（用例 monkeypatch 这一枚，不去改全局时钟）。"""
    return datetime.date.today().year

_OA_HINTS = [(r"(能|可以|尽量|优先).{0,6}(下载|全文|开放获取|OA)", True),
             (r"(付费也行|不用管|无所谓|不需要|不在乎).{0,8}(开放获取|OA|全文|下载)", False)]


_TOPIC_FILLER = re.compile(r"^(?:我|自己)?(?:想|打算|准备|要|正在|需要)?(?:先|再)?(?:找|发|投|做|从事|研究|关于|开展)+")
# 抽出来的尾巴常带着"方向是/对象是"这层连接词壳（实测 '我的研究方向是联邦学习中的隐私保护'
# → '方向是联邦学习中的隐私保护'）。关键词检查会放过它，送去英译不会——壳进检索式就是噪音。
_TOPIC_LINK = re.compile(r"^(?:方向|对象|题目|主题|课题|论题|内容|问题|领域)(?:选?的)?(?:是|为|叫|定为)")
# 同一层壳的尾巴版：'推荐系统冷启动这个方向' 要剥成 '推荐系统冷启动'
_TOPIC_TAIL = re.compile(r"(?:这个|这种|这一|那个)?(?:方向|领域|这块)$")
# 窗口从"做"后面起抓时，量词会跟着进来（实测「我在做一个电池寿命预测的课题」→ '一个电池寿命预测'）
_TOPIC_QUANT = re.compile(r"^(?:一|两|几|这|那)(?:个|种|类|项|篇|批)")
_TOPIC_PATTERNS = (
    # 学生直接说"题目是X / 主题是X"是最明确的信号，实测却一个都抽不出来：
    # "我在准备毕业论文，题目是医学图像分割" → topic='' → 永远进不了检索，只能一轮轮反问
    re.compile(r"(?:题目|主题|课题|论题|研究题目|题目定为|题目叫)(?:是|为|：|:|叫)?\s*[《“\"']?((?:[\w一-鿿]{2,18}))"),
    re.compile(r"(?:想|打算|准备|要)?研究((?:.{2,20}?)(?:方法|模型|算法|技术|系统|框架|的$|$))"),
    re.compile(r"((?:.{2,18}?)方向)"),
    re.compile(r"做((?:.{2,18}?)(?:检测|推荐|分类|生成|问答|识别|预测|分割|优化))"),
    # 轮 151：句集实测漏的三类最普通说法（「调研 X」「看看 X 综述」「是/做 X 的」「X 这个方向」）。
    # 追加在原有四条**之后**，先让老口径命中，免得把已经对的形状改坏。
    re.compile(r"调研(?:一下|了下|过|过一下)?((?:.{2,18}?)(?:方法|现状|进展|综述|技术|效率|问题|思路|方向|$))"),
    re.compile(r"(?:看看|看一下|看下|查查)((?:.{2,16}?)(?:综述|文献|论文|方向|进展))"),
    re.compile(r"(?:方向选的是|方向定的是|方向是|选的是|做的是)((?:.{2,16}?)(?:$|[，,。]))"),
    re.compile(r"(?<!怎么|如何)做((?:[\w一-鿿]{2,14}?))的"),
    re.compile(r"((?:[\w一-鿿]{2,16}?)这个方向)"),
    # 轮 152：「讲 X 的文章/论文/综述」，尾巴留在捕获组外（抽 '脉冲神经网络低功耗编码'，不是带「的文章」的整串）。
    # 同批另外两条候选——「(在)做 X$」「熟悉(一下)?X」——**实测否决**，别凭直觉加回来：它们的宾语经常不是研究主题
    # （'项目了'/'环境'/'实验室的服务器'），只靠水词黑名单才不开火。代价的数由仪器 T6 现量
    # （`输出/scripts/probe_topic_capture_corpus.py`），残渣形状已进夹具负例，加了就会当场红。
    re.compile(r"讲((?:[\w一-鿿]{2,16}?))(?:的)?(?:文章|论文|综述|报告)"),
    # 轮 309：外部一次性调用的说法（队长交接文档那句业务验收原话）。上面十条里**没有一条**认
    # 「检索/搜索/查找」这类纯取数动词，于是这条句子的主题只能落到 partner 自己的剥词通道上，
    # 而它对整段多分句请求剥完会残留中间标点 ⇒ 被「不带标点」那道屏否掉 ⇒ 调用方收到「请补充研究主题」。
    # `(?!不|没)` 是必需的：不加它，「检索不到相关文献怎么办」会被切成 '不到'（长度 2、无壳、无指代，
    # `_topic_ok` 全过）当主题送进 Crossref——动词后面跟补语「不/没+到」是"取不到"的意思，不是主题。
    # 这条的代价（会多接住哪些、有没有误捕）现量在 `输出/scripts/probe_topic_capture_corpus.py`，
    # 句子进 `tests2/fixtures_topic_corpus.py`（正例 + 「检索不到」负例各一条）。
    re.compile(r"(?:检索|搜索|查找)(?![不没])((?:[\w一-鿿]{2,16}?))(?:相关的?)?"
               r"(?:\s*(?:\d+|一两|几)\s*篇)?(?:真实|高质量|经典|最新|近期|核心)?(?:文献|论文)"),
    # 轮 311 实测（E6，`输出/_scratch/r311_p14_measure_quoted_echo.py`）：回声里只带「主题的尺子」那半句时，
    # 摘完剩下的是「我要查脉冲神经网络低功耗编码」——上面十一条**没有一条认裸「查」**（只认 看看/查查/调研/检索），
    # 于是用户的真主题选不出来 ⇒ 该给结果的被反问。
    # `(?<![调检排追核审不没别未])` 必需：不加它，「调查/检查/排查/追查/核查/审查」里的「查」会把后半句当主题，
    # 「不查文献也想问清楚」会被切成 '文献也想问清楚'（长度/壳/指代三关全过）——与轮 309 那条「检索不到」同源。
    # `(?!文献|论文|资料)` 是同一批实测逼出来的：「调研了一下怎么查文献，没什么」被切成 '文献' ——
    # 「查文献」这个宾语是**文档类词**而不是研究主题，代价由语料负例当场报出（`tests2/fixtures_topic_corpus.py`）。
    # 代价（会多接住哪些形状）由 `输出/scripts/probe_topic_capture_corpus.py` 现量。
    re.compile(r"(?<![调检排追核审不没别未])查(?:询|一下|看|阅)?(?!文献|论文|资料)((?:[\w一-鿿]{2,18}?))"
               r"(?:相关的?)?(?:的)?(?:文献|论文|文章|资料)?(?:\s*(?:\d+|一两|几)\s*篇)?$"),
    # 轮 311 实测（L2 那一族，`tests2/test_partner_echo_layering_r311.py`）：回声摘干净之后，
    # 「我现在要的是钓鱼网站的论文 3 篇」这句**照旧选不出主题**——上面十二条没有一条认「要的是 X 的文献/论文」，
    # 而 `_strip_candidate` 剥完留着「我」被打回（`_topic_ok` 禁指代）。这是共用尺子的形状缺口，与回声无关：
    # 同一句不带回声也一样空（实测两行对照在 `输出/_logs/r311_p14_quoted_echo_after_fix.log`）。
    # 尾巴（文献|论文|文章）留在捕获组外，跟轮 309 那条一个写法；篇数与「相关的」照旧吃掉。
    re.compile(r"(?:我)?(?:现在|这次|其实)?(?:真正)?要的是((?:[\w一-鿿]{2,16}?))(?:相关的?)?"
               r"(?:\s*(?:\d+|一两|几)\s*篇)?(?:文献|论文|文章)"),
)


# 轮 311 实测补「主题」：它不在表里，于是 `_topic_ok("主题")` 三条判据（长度 2—20、非壳、无指代疑问）全过，
# 而我们那句兜底提示里就写着「请补充研究主题」——外部网关把它并回下一轮请求后，
# 2 个字的「主题」在"过筛者取最短"里必然压掉用户补的 4 字真词（`输出/_logs/r311_p1_topic_echo_runB.log`
# 的 S2/S3/S4/S12 四发，队长截图那格的机制）。同批实测还钉住保护面：`主题模型`/`主题发现` 必须照旧可用
# （尾巴壳词只在句末剥，复合术语剥不到），用例在 `tests2/test_partner_topic_priority_r311.py`。
_TOPIC_SHELL = re.compile(r"(?:的)?(?:方向|领域|课题|题目|主题)$")
# 只在"壳词表"里的拉丁词不能当主题：实测过 topic="IEEE 的方向" → 检索回一堆 MIMO 雷达与 WLAN
_BOILER_LATIN = {"ieee", "acm", "sci", "scie", "ccf", "gpu", "cpu", "cssci", "top", "pami", "if"}


def _topic_is_shell(topic: str) -> bool:
    core = _TOPIC_SHELL.sub("", topic).strip(" ，,的")
    cjk = re.findall(r"[一-鿿]", core)
    latin = re.findall(r"[a-zA-Z]+", core)
    # 有没有剩下"真术语"（不在水词表里的拉丁词）——这一条原来只在**一个汉字都没有**那一支用，
    # 于是纯中文残渣被拦住了，中英混写的真术语（「RAG 与 Agent」「GNN 与 Transformer」）却照样被
    # `len(cjk) < 2` 判成壳：轮 307 外部实测，`检索：RAG 与 Agent 方向的文献 5 篇` 的调用方
    # 收到的正是「请补充研究主题」。这与下面注释里那条 GNN/CRISPR 的缺陷同源，只是隔了一层汉字。
    has_real_latin = any(w.lower() not in _BOILER_LATIN for w in latin)
    if cjk:
        return len(cjk) < 2 and not has_real_latin
    # 一个汉字都没有：只有「拉丁词全是水词」或「什么都没剩下」才算壳。
    # 原来这里直接写 `len(cjk) < 2`，于是 GNN/CRISPR 这类真术语被整条判掉，
    # 而它下面那半句 `_BOILER_LATIN` 判定永远轮不到（轮 147 的 ast 变异体实测：
    # 只留前半句与原函数 0 处不同、只留后半句有 2 处不同）。
    return not latin or not has_real_latin


_TOPIC_RESIDUE = re.compile(r"^(?:否|是否|会不会|能不能|有没有|是不是)")


def _topic_ok(topic: str) -> bool:
    """抓出来的字符串能不能当主题：长度、指代/疑问残渣、壳词三类各判一次。

    轮 179 加 `_TOPIC_RESIDUE` 这一条：外部池普查实测「课题是否有意义」被窗口切成
    '否有意义' 送去检索——跨过"是否"抓就会留下这种没名词性的尾巴。按语义类判
    （疑问格式的后半截开头），不逐条钉字面；`^不` 一类**故意不收**，因为
    "不确定性量化"是真主题（正例钉在 `tests2/fixtures_topic_corpus.py` 里）。
    """
    if not 2 <= len(topic) <= 20 or _topic_is_shell(topic) or _TOPIC_RESIDUE.match(topic):
        return False
    return not re.search(r"你|我|他|这|那|什么|怎么|科研", topic)


def capture_topic(text: str) -> str:
    """按小句逐句抽研究主题，并剥掉"想做/从事/关于"这类动作前缀。

    线上实测的根因：原来对**整句话**做窗口抓取，
    "我第一次做科研，想做多模态钓鱼网站检测" 被抽成 "科研，想做多模态钓鱼网站"——
    带着逗号和"想做"的碎片送去英译直接翻不出来（extra.query_fallback=True），
    中文碎片原词进 Crossref 就捞回海洋经济与航空摄影的期刊，一路污染到推荐列表与综述。
    """
    for clause in re.split(r"[，。,、！!？?；;：:]", text or ""):
        if not clause:
            continue
        for pat in _TOPIC_PATTERNS:
            m = pat.search(clause)
            if not m:
                continue
            topic = _TOPIC_FILLER.sub("", m.group(1).strip()).strip(" ，,的")
            topic = _TOPIC_QUANT.sub("", _TOPIC_TAIL.sub("", _TOPIC_LINK.sub("", topic))).strip(" ，,的")
            if _topic_ok(topic):
                return topic
    return ""


_TOPIC_PROMPT = (
    "从下面这位研究生的原话里抽出一个**研究主题**（名词短语，不超过 16 字，"
    "不要动词、不要标点、不要只剩「方向/方法/课题」这类壳词）。"
    "只能用原话里出现过的词，不要补写原话里没有的术语；抽不出来就返回空字符串。"
    '只输出 JSON：{{"topic": "…"}}\n原话：{}'
)


def llm_topic(message: str) -> str:
    """规则通道抽不出主题时的兜底（extractor 的 docstring 早就写了这条通道，此前并没有实现）。

    返工过的点：模型给的词必须逐字能在原话里找到——主题一被补写，检索式就跟着跑偏，
    而这条链路的下游（英译 → Provider → 相关性判定）全都建立在主题之上。
    """
    from llm import LLM_MOCK, chat_json, run_async

    text = (message or "").strip()
    if not text or LLM_MOCK:
        return ""
    try:
        data = run_async(chat_json(_TOPIC_PROMPT.format(text[:400]),
                                   temperature=0.0, max_tokens=120, timeout=25.0, retries=1))
    except Exception:  # noqa: BLE001 — 兜底通道失败就留空，交由上层问学生
        return ""
    topic = str((data or {}).get("topic") or "").strip() if isinstance(data, dict) else ""
    if not 2 <= len(topic) <= 20 or re.search(r"[，。,、！!？?；;：:/\\]", topic):
        return ""
    if _topic_is_shell(topic) or _TOPIC_FILLER.sub("", topic) != topic:
        return ""
    invented = [ch for ch in re.findall(r"[一-鿿]", topic) if ch not in text]
    return "" if invented else topic


def year_range(text: str) -> "tuple[int, int] | None":
    """用户话里的年份窗（`(起, 止)`），没说年份就返回 `None`。

    🔴 这是产品唯一的年份口径（`_YEAR_HINTS` 的偏移量＋`_ABSOLUTE_YEAR_RANGES`），网页侧 `extract_update`
    与伙伴入口 `sources.multi_source_search` 都吃这一枚：轮 312 之前伙伴那条只把「近三年」从主题里**剥掉**
    就当它不存在，用户要近三年却拿回 2019 年的条目，产物里也没写这一轮按哪年筛过
    （`输出/交付/研伴2.0_轮311_能力对照清单.md` §2「年份范围」那行记的就是这条缺口）。
    """
    y = _today_year()
    for pat, back in _YEAR_HINTS:
        if re.search(pat, text or ""):
            return (y - back, y)
    for pat, y1, y2 in _ABSOLUTE_YEAR_RANGES:
        if re.search(pat, text or ""):
            return (y1, y2)
    return None


def extract_update(message: str, intent) -> dict:
    """规则通道：从用户消息提取 ResearchIntent 增量 dict（未命中键不出现）。"""
    text = (message or "").strip()
    if not text:
        return {}
    update: dict = {}

    for pat, stage in _STAGE_HINTS:
        if re.search(pat, text):
            update["research_stage"] = stage
            break

    for pat, goal in _GOAL_HINTS:
        if re.search(pat, text):
            update["user_goal"] = goal
            break

    for field, hints in _LEVEL_HINTS.items():
        for pat, level in hints:
            if re.search(pat, text):
                update[f"user_background.{field}"] = level
                break

    win = year_range(text)
    if win:
        update["paper_preferences.year_from"], update["paper_preferences.year_to"] = win

    for pat, flag in _OA_HINTS:
        if re.search(pat, text):
            update["paper_preferences.open_access_preferred"] = flag
            break

    for field, pat, val in _CONSTRAINT_HINTS:
        if field not in update and re.search(pat, text, re.I):
            update[field] = val

    # 发表类型：顺序即优先级（SCI > CCF > 会议 > 毕业论文 > 比赛）
    for pat, val in _PUB_TYPE_HINTS:
        if re.search(pat, text, re.I):
            update["publication_goal.type"] = val
            break

    # 主题捕获：仅当 topic 为空时从"研究X / X方向 / 做X"捕获（避免覆盖明确主题）
    if not getattr(intent, "topic", ""):
        topic = capture_topic(text) or llm_topic(text)
        if topic:
            update["topic"] = topic

    # 研究阶段中文映射校验
    if update.get("research_stage") and update["research_stage"] not in RESEARCH_STAGES:
        update.pop("research_stage")
    return update
