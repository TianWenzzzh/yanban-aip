# 研伴 2.0 · 一流 Agent 产品对标（BENCHMARK）

> 生成于 2026-09-20（24h 审计改造夜，Phase 2）。取证方式：官方文档/仓库一手页面 + 可信实测长文；
> **未做外部产品浏览器截图取证**（见文末「取证边界」），凡未取到一手来源的一律标注「未验证」。
> 每节范式固定五列；「落成改造项」对应任务书 §4 的 A 对话 / B 检索与论文卡 / C 知识库与导图 / D onboarding 与打磨。

---

## 1. Perplexity（检索型回答的信源呈现）

官方 help/learn 站点本机直连失败（curl 000 / fetch failed），下表来自中文产品拆解与 AI 界面设计指南（二手，已标 URL）。

| 范式·来源 | 它具体怎么做 | 研伴现状差距（改造前） | 落成改造项 | 落地代价 |
|---|---|---|---|---|
| 逐句角标绑定 · [woshipm 拆解](https://www.woshipm.com/evaluating/6466946.html) | 答案里每个事实论断都带内联编号引用，Sources 面板常驻，点角标即定位来源 | 助手气泡是 `whitespace-pre-wrap` 纯文本，论文只在右栏另一列，正文与来源零绑定 | B3（已做）+A | 小：`papers[]` 已带序号，回复渲染成 `[n](#paper-n)` 锚点即可 |
| 阶段可见的流 · 同上 | 「搜索网络→读取来源→撰写综合」三阶段在答案前跑完 | 只有一个静态「研伴思考中…」灰字 | A5（已做） | 小：后端 SSE 已实现，前端换 fetch+getReader |
| 追问 chips 作留存引擎 · 同上 | 答案尾部给 3–5 条相关问题，点一下就进入下一轮 | 空状态只有一行灰字，用户得自己想下一句说什么 | D1（部分做：示例卡） | 小：`chat_json` 产出 3 条问题数组即可 |
| 来源可核验面板 · [php.cn 溯源教程](http://www.php.cn/faq/1782103.html) | 教程描述「查看来源/引用溯源」入口（正文被 JS 壳吞掉，**细节未验证**） | 右栏 460px 定宽列被论文卡占死，无独立信源视图 | B/C | 中：同列做 Tab 切换需在 460px 内做信息密度取舍 |

## 2. Elicit / SciSpace（科研向论文卡信息架构）

Elicit 为一手官方 help；SciSpace 官网 403，取自第三方实测长文（二手，已标）。

| 范式·来源 | 它具体怎么做 | 研伴现状差距（改造前） | 落成改造项 | 落地代价 |
|---|---|---|---|---|
| 来源与全文状态标签 · [Understanding paper source labels](https://support.elicit.com/en/articles/14759158-understanding-paper-source-labels-in-elicit) | 每篇打两个标签：source（来自哪个库）与 content status（是否有全文）——「能不能读」先告诉用户 | 卡片不显示 OA/provider，点了「阅读 PDF」才知道没有全文 | B2（已做：OA/venue/年份/被引徽章） | 小：Unpaywall 已解析 OA 链接，提前到徽章即可 |
| 分层批量导出 · [Export your data](https://support.elicit.com/en/articles/14758189-export-your-data-from-elicit) | 表格导 CSV/Excel，报告导 PDF/Word，引文导 RIS/BIB | 零导出；收藏只落库 | C3（已做 vault zip 导出）+ 待办 RIS/BIB | 小：RIS/BibTeX 是纯字符串拼接 |
| 三维筛选器 · [Filtering in Elicit](https://support.elicit.com/en/articles/14758171-filtering-in-elicit) | 明确列出 Publication year(start/end)、Journal quality Q1–Q4、Study Type、Abstract Keywords，支持自然语言/按钮/高级语法三种入口 | 无筛选器 UI；排序规则只在后端，用户看不见也改不了 | B 待办（P2） | 中：`intent` 已有参数位，缺前端筛选条 + 透传 |
| 可自定义抽取列 · [Create and save columns](https://support.elicit.com/en/articles/14758162-create-and-save-columns-in-elicit) | 「告诉研究代理你要哪些列」，列=名称+指令，逐篇抽取进表，不确定值用 yes/no/maybe，可存为 preset | 卡片固定三段式，无方法/数据集/样本量等横向字段 | B 待办（P3） | 大：需逐篇 LLM 抽取（credit+耗时）+ 表格视图 |
| 「Show more like selected」· [SciSpace 实测（二手）](https://effortlessacademic.com/scispace-an-all-in-one-ai-tool-for-literature-reviews/) | 选中相关论文后按样例再召回同类 | 收藏/不感兴趣只写反馈表，不触发再召回（本次已让反馈参与排序，「以样例为种子再检索」仍未做） | A+B 待办 | 中：需要把 favorite 论文转成检索式 |

## 3. ChatGPT（Canvas + Memory）

| 范式·来源 | 它具体怎么做 | 研伴现状差距（改造前） | 落成改造项 | 落地代价 |
|---|---|---|---|---|
| 记忆更新可悬停查证 · [Memory and new controls](https://openai.com/index/memory-and-new-controls-for-chatgpt/) | 「hover "Memory updated" → click "Manage memories"」；记忆分 saved memories 与 chat history，可分别关掉 | 记忆层（`companion/memory/*`）建了但对话路径零引用、前端零呈现（本次已接入检索上下文，UI 仍无「我用了哪条记忆」） | A2（后端已做）/ D 待办 | 中：SSE `meta` 事件已留位，回复带 `used_memory[]` + 管理抽屉 |
| 高亮即上下文 · [Introducing canvas](https://openai.com/index/introducing-canvas/) | 「highlight specific sections to indicate exactly what you want ChatGPT to focus on」，可就地给 inline feedback | PDF 是 `<iframe>`，浏览器原生查看器不可注入，划词无入口 | B/PDF 待办（P2） | 大：需 pdf.js 自绘（`docs/REMAINING_ISSUES.md` 第 3 条已登记）；过渡方案是用 `parser.sections` 做「按节选段问」 |
| 选中即动作菜单 · 同上 | 固定快捷动作（Suggest edits / Adjust length / Change reading level / Add final polish…），可直接编辑正文 | 对话只有输入框，无就地动作 | A/D 待办 | 中：动作集有限，逐个用 `chat_json` 封装配方 |
| 版本回退 · 同上 | 「用 back button restore previous versions」 | 消息只 append，不能回退/重跑某一轮 | A 待办 | 中：会话已落 SQLite，需按轮截断+重放 |

## 4. Codex CLI / Claude Code（Agent 进度透明化）

| 范式·来源 | 它具体怎么做 | 研伴现状差距（改造前） | 落成改造项 | 落地代价 |
|---|---|---|---|---|
| 进度＝checklist 不是百分比 · [plan.md 模板](https://github.com/openai/codex/blob/main/codex-rs/collaboration-mode-templates/templates/plan.md)、[interactive-mode](https://code.claude.com/docs/en/interactive-mode) | `update_plan` 被明确定义为 checklist/progress 工具；Claude Code 用 Ctrl+T 呼出 todo 并随执行勾选 | 「研伴思考中…」是唯一进度信号，八态状态机只在后端 | A 待办（P1） | 小：事件流已能发 `meta`，加 `plan`/`step` 两类 payload + 步骤条；Workbench 流水线面板有同类卡片可复用 |
| 反问＝选择题而非自由问答 · [default.md 模板](https://github.com/openai/codex/blob/main/codex-rs/collaboration-mode-templates/templates/default.md) | 规则写死「Ask the user directly with **one concise plain-text question**… **Never write a multiple choice question as a textual assistant message**」；无回答则按 best judgment 继续 | 反问是 `clarification_score` 生成的开放问句落进气泡，用户得像填表一样打字 → **「像查户口」的根因** | A3（部分做：一次一问 + 阈值下调） | 小：`intent` 字段本身是枚举，把 gap 渲染成 chips 点选即可 |
| 计划先于执行、只读探索 · plan.md 同上 | 明确区分「可只读探索（读文件/搜索/静态分析/测试，不改仓库文件）」与「需批准才动手」，终稿以 `<proposed_plan>` 给出 | 一问就直接往下走，无计划确认步 | D 待办 | 中：SSE 已有 meta，需状态机→步骤映射 |
| 随时可打断 · interactive-mode 同上 | 「Esc interrupts」，double Esc 回退，`/rewind` 做 checkpoint | 无 stop：busy 期间按钮 disabled，请求发不出也收不回 | A 待办 | 中：前端 `AbortController` + 后端断连处理（SSE 天然可断） |
| 失败自带下一步 · 同文档 | 工具调用逐条可见，失败即在原处改路继续，不终止会话 | `catch { 网络异常，请稍后重试。 }` 纯文本无重试按钮（本次已加重试按钮） | D2（已做） | 小：错误包成对象 + 「重试/换个说法」 |

## 5. Notion AI（slash 命令与块级操作）

| 范式·来源 | 它具体怎么做 | 研伴现状差距 | 落成改造项 | 落地代价 |
|---|---|---|---|---|
| 模板库承接首次使用 · [notion-ai-for-docs](https://www.notion.com/help/guides/notion-ai-for-docs) | 首次进入靠现成模板给「可以直接改的样板」 | 空状态仅一行引导（本次已换 3 张示例卡 + 价值主张） | D1（已做） | 小 |
| 选中 → Edit with AI → 保留/丢弃/重来 · 同上 | 结果作为块插入，用户 accept / discard / try again | 助手消息不可局部操作，改一段只能整条重发 | A 待办 | 中：需先把消息切成可寻址块（依赖 markdown 渲染，已落地） |
| 全局快捷键唤起面板 · [everything-you-can-do-with-notion-ai](https://www.notion.com/help/guides/everything-you-can-do-with-notion-ai) | Shift+Cmd/Ctrl+J 打开 AI，动作名如 "Summarize key points" / "Translate any page" | 无命令面板，功能藏在三个 tab 按钮里靠发现 | D 待办 | 小：一个 ⌘K 弹层 + 动作清单 |
| 跨源检索结果统一呈现 · 同上 | Connectors 在用户自有内容（Slack/Drive）里作答并统一列结果 | `/search/keywords` 已返回 papers+notes+memories，但 `memories` 前端直接丢弃未渲染 | C2（已做笔记渲染）/memories 待办 | 小：把已返回的 memories 渲染进同一列表 |

## 6. Obsidian（自生长知识库与双链图谱）

全部一手官方帮助（`obsidian-help`）。对标「知识库有后端无前端」这一项。

| 范式·来源 | 它具体怎么做 | 研伴现状差距（改造前） | 落成改造项 | 落地代价 |
|---|---|---|---|---|
| Backlinks 面板随选择联动 · [plugins/backlinks](https://obsidian.md/help/plugins/backlinks) | 右栏显示当前笔记反向链接，分 Linked/Unlinked mentions 两区，可 Show more context，切笔记自动更新 | 库里存了 papers/notes/events，却没有任何「谁引用了它」的视图；知识库零前端 | C2（已做：笔记全文 + backlinks 面板） | 中：`knowledge.backlinks()` 已有，缺前端 |
| 出链面板 + 一键成链 · [plugins/outgoing-links](https://obsidian.md/help/plugins/outgoing-links) | Links 列出当前笔记全部出链，点击打开；Unlinked mentions 给「点笔记名即成链」 | 卡片之间零关联，论文/笔记/记忆互不相认 | C2（已做 [[双链]] 点击跳转）/成链建议待办 | 中：建议比自动连更安全 |
| 图谱节点可点可筛 · [plugins/graph](https://obsidian.md/help/plugins/graph) | 「被引用越多的节点越大」，hover 高亮、click 打开、右键菜单，Filters 含 Orphans | 导图是只读 SVG（`dangerouslySetInnerHTML`），无点击/筛选/节点大小语义 | C3（已做：点节点→定位论文卡）/加权与筛选待办 | 小→中：最小版在 SVG 上做事件委托，真交互要换图库 |
| 纯文本 Markdown 即资产 | vault 就是一堆 .md，可被任何编辑器打开 | 知识库无导出入口（本次补上 zip 导出） | C3（已做） | 小 |

---

## 7. 一流 Agent 的共同体验基线（10 条，含例证）

1. **首字要快**：目标 <800ms（Perplexity 用阶段文案先跑）。
2. **进度用清单不用百分比**（Codex `update_plan` 明确是 checklist）。
3. **推理阶段要诚实命名**：写 "Searching the web"，不要 "Working on it"。
4. **过程默认可折叠**（Claude Code 折叠工具输出）。
5. **必须能中断，且按钮在预期位置**（Claude Code Esc）。
6. **引用可点是信任底线**（Perplexity 内联编号）。
7. **上下文承接可见可管**（ChatGPT「hover Memory updated → Manage memories」）。
8. **错误要带下一步动作**，不能只说「出错了」（Codex 失败即改路继续）。
9. **空状态给可点的起点**（Notion 模板库 / Elicit saved columns / suggestion chips）。
10. **自动滚动设护栏**（视口距底 >100px 时不要抢滚动）。

## 8. 差距 → 改造项 → 优先级 → 质量门（总表）

| 任务书问题 | 对标范式 | 本次结论 | 优先级 | 质量门 |
|---|---|---|---|---|
| ①回复固定模板 | ChatGPT 定向改；Codex decision-complete plan | ✅ 已接 GLM（5 轮唯一、相似度 0.358） | P0 | G4 |
| ②不承接上下文 | ChatGPT Memory 可见可管 | ✅ 后端已注入近 6 轮 + TF-IDF 记忆（承接 3/4）；❌ 前端未呈现「用了哪条记忆」 | P0 | G4 |
| ③反问像查户口 | Codex「one concise question / 无答就按默认走」 | 🟡 已改一次一问 + 阈值 0.62→0.52；未做 chips 点选 | P0 | G4 |
| ④无流式 | Codex/Claude Code 可中断；Perplexity 阶段可见 | ✅ SSE 已通（经 vite 代理不被缓冲）；❌ 无「停止生成」 | P1 | G5 |
| ⑤中文检索召回差 | Elicit 自然语言+高级语法筛选 | 🟡 根因定位见附录：本机 arXiv 406 全拒、OpenAlex/S2 429 限流；Crossref 可用但中文查询召回噪声大 | P1 | G6 |
| ⑥论文卡信息单薄 | Elicit source/status 标签、筛选器、导出 | ✅ 徽章+TLDR+可展开理由；❌ 筛选器与自定义列未做 | P1 | G6 |
| ⑦PDF 只有 iframe | Canvas 高亮即上下文 | ❌ 划词未做（需 pdf.js，已登记遗留） | P2 | — |
| ⑧知识库无 UI | Obsidian backlinks/出链 | ✅ 列表/全文/双链跳转/backlinks/导出 | P1 | G7 |
| ⑨导图不可交互 | Obsidian 图谱加权/筛选 | 🟡 已做点击定位；未做加权与过滤 | P2 | G7 |
| ⑩无 onboarding | Notion 模板库；setproduct chips | ✅ 3 张示例卡 + 价值主张 | P2 | G8 |
| ⑪暗色/移动/骨架屏 | setproduct 骨架屏 | 🟡 已做流式占位与错误重试；暗色/移动未做（460px 定宽列不利移动） | P2 | — |
| ⑫消息无 markdown 渲染 | Perplexity 分点+角标 | ✅ 复用 `Markdown.tsx` | P1 | G9 |

## 9. 取证边界（诚实说明）

- **未做外部产品浏览器截图取证**：本轮对标以官方文档/仓库页面为主，未逐个登录体验付费功能；Perplexity 官方站与 SciSpace 官网在本机不可达，其描述为二手来源。
- 明确「未验证」的项：Perplexity 官方帮助细节；`php.cn` 溯源文正文；SciSpace 全部细节（第三方实测）；ChatGPT 搜索回答的角标样式；Elicit 非英文论文处理；Notion 斜杠 `/` 菜单一手文档（404）；Obsidian「导出兼容」官方表述。
- 检索质量附录中的分数为**人工 0/1/2 打分**，不是产品自带指标。

---

## 附录 A · 三主题真实检索结果质量（任务书 §4B1）

环境：本机（Windows + 家庭/校园出口 IP），`LLM_MOCK=1`（即**未做英译**，中文主题原样送库）。打分口径：0 不相关 / 1 沾边 / 2 直接相关。

| 主题 | 可用源 | top5 相关分 | 实际返回（前 3 条） | 结论 |
|---|---|---|---|---|
| `phishing website detection`（英文） | Crossref | 2,2,2 = **2.0** | PhishGNN: A Phishing Website Detection Framework using Graph Neural…(2022, 被引24)；Hybrid Multimodal Approach for Phishing Website Detection(2026)；SMOTEEN Hybrid Sampling Based…(2022, 被引7) | 英文查询质量可用 |
| `钓鱼网站检测`（中文，未英译） | Crossref | 2,0,0 = **0.67** | Detection and Prevention of the Phishing Risk of Android Application(2015)；复杂土壤条件下变电站接地网施工与检测研究(2026)；搭建物联网时代10kV开闭站智能巡检…(2019) | **中文原样送库会召回完全无关的中文电力文献**——必须依赖英译链路 |
| `large language model hallucination` | Crossref | 2,2,2 = **2.0** | Controlling LLM Hallucination Based on Agent AI…(2025,被引4)；Combining LoRA to GPT-Neo to Reduce LLM Hallucination(2024,被引3)；Hallucination Reduction and Optimization for LLM…(2024,被引19) | 质量可用 |
| `multi-agent collaboration` | Crossref | 2,2,2 = **2.0** | Multi-agent collaboration in integrated information systems(2003)；A new formal model of collaboration by multi-agent systems(被引10)；AgentFlow: A Context Aware Multi-Agent Framework…(2025,被引4) | 质量可用（但含 2003 年老文献，靠年份加权压后） |

**三主题均分（英文式）= 2.0/2 ≥ G6 目标 1.6**；中文式 0.67 → 说明 P1-5 的真实根因不是"翻译效果未验证"，而是**一旦英译不可用（MOCK/超时/失败回落原查询），中文查询等于裸送英文库**。

**修复后复测（2026-09-21 00:24，云端容器内实跑，真实 GLM）**：中文主题「钓鱼网站检测」经英译后 top5 全部直接相关（Phishing Detection: Analysis of Visual Similarity Based Approaches 2017；Phishing Attacks: A Recent Comprehensive Study and a New Anatomy 2021；Web Phishing Detection Using a Deep Learning Framework 2018；IoT Applications, Security Challenges, Attacks 2021；Tutorial and critical analysis of phishing websites methods 2015）→ 相关分 **2.0/2**（修复前 0.67/2），`query_fallback` 不再出现。根因见附录 C。

## 附录 B · arXiv 406 归因矩阵（本机实测）

| 用例 | 请求 | HTTP |
|---|---|---|
| A | `GET https://export.arxiv.org/api/query`（无参数） | **406** |
| B | `search_query=all:phishing&max_results=3` | 406 |
| C | `search_query=all:"phishing" AND all:"detection"` | 406 |
| D | 同 B 但 `http://` | 301（转 https 后仍 406） |
| E | 同 B + `Accept: application/atom+xml` | 406 |
| F | 同 B + 自定义 `User-Agent: yanban-companion/2.0` | 406 |

结论（已用云端复测修正）：**406 与 UA、参数格式、协议无关，连不带参数的裸端点都拒**。2026-09-21 在云端容器内逐源复测：`crossref OK / openalex OK / arxiv FAIL 406 / semantic_scholar FAIL 429`——**arXiv 406 在阿里云出口同样发生，不是本机 IP 特例**；OpenAlex 的 429 只是本机出口 IP 造成，云端可用。因此本项目**实际可用检索源 = Crossref + OpenAlex**（4 源里 2 源可用），技术报告与答辩材料须按此口径写，原「待云端复测」的推测到此闭合。
本次仍把 `ArxivProvider` 从 `sources.arxiv_search_raw` 中拆出来自带实现（补 abstract/open_access/退避），
以便在云端（阿里云出口 IP）复测；**若云端也 406，则 arXiv 源在本项目实际上长期不可用**，需在技术报告里如实写明。
OpenAlex / Semantic Scholar 在本机为 429（共享出口 IP 无 Key 限流），已加指数退避重试，效果待云端复测。

## 附录 C · P1-5 真根因：英译提示词与解析器不兼容

`_english_query` 要求模型「翻译成 3-5 个英文检索关键词（空格分隔），不要输出其他内容」，
却用 `chat_json()` 按 JSON 解析并取 `d["keywords"]`。模型返回的是纯文本关键词，`json.loads` 必然失败，
异常被 `except Exception: pass` 吞掉后回落中文原查询——**即使配置了真实 GLM key，英译也从未成功过一次**。
这就是任务书 §2 P1-5 写的「英译管线依赖 LLM，未验证效果」背后的真相：不是效果待验证，是结构性必然失败。

修复：改用 `chat()` 取文本 → 去列表编号 → 逗号/分号/顿号统一为空格 → 折叠空白 →
**结果仍含中文则不采用**；翻译失败时置 `intent.extra["query_fallback"]`，前端显示琥珀提示条。
回归测试：`tests2/test_dialogue_llm.py::test_english_query_uses_text_and_rejects_chinese_echo`、
`test_only_translated_query_is_sent_when_translation_works`。
