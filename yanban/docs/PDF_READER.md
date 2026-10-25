# PDF 阅读

链路：`paper → resolver（显式 pdf_url → arXiv PDF → Unpaywall OA）→ downloader（50MB 上限 + %PDF 魔数校验 + 缓存 data/papers/pdf/{project}/）→ parser（PyMuPDF 页文本 + 八类章节启发式）→ 阅读与问答`。

前端：论文卡「阅读 PDF」→ 右侧 Drawer（55% 宽）内嵌浏览器原生 PDF 查看器（翻页/缩放/搜索/选中，验收 8 全覆盖）；顶部「问研伴」输入框携带论文上下文提问（qa.py 相关节选择 + LLM，MOCK 可测）。
诚实说明：PDF.js 自定义渲染列为 P1 迭代项（原生查看器已满足验收 8 的全部行为）；PyMuPDF 不可用时未再降级（安装失败率低，REMAINING_ISSUES 有记录）。

划词翻译/解释（§19）：P1——浮层 UI 未实现，后端能力可由 /qa 覆盖。
