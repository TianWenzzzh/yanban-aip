"""生成 4 个研伴 Partner 的 ACS 文件（与梧桐已提交文献 Partner 同规格）并过官方 schema 校验。"""

import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gateway_compat import build_acs  # noqa: E402  # 见下

OUT = Path(__file__).parent / "acs"
OUT.mkdir(exist_ok=True)

AGENTS = [
    {
        "file": "acs-experiment.json",
        "aic": "1.2.156.3088.1.1.YNBAN1.EXP001.1.0000",
        "name": "研伴·实验设计智能体",
        "desc": "研伴科研协作团队的实验设计 Partner：根据研究问题与数据条件生成结构化实验方案（研究假设 H1/H0、变量设计表、对照组设置、评估指标与计算方式、样本量与统计功效、风险与缓解）。通过 AIP 协议与研伴 Leader 及其他科研 Partner 协同。缺研究问题描述时返回 awaiting-input。",
        "skills": [
            {
                "id": "yanban.experiment.design",
                "name": "实验方案设计服务",
                "description": "输入研究问题与可用数据条件，输出定制化实验方案：假设、变量设计（自变量/因变量/控制变量）、分组对照、主/次评估指标及计算方式、样本量说明、风险缓解。\n【输入要求】\n必填：研究问题+数据条件（合计不少于 12 字）。\n可选：指标偏好、约束条件。\n当缺少必填项时返回 Task.state=\"awaiting-input\"。",
                "tags": ["实验设计", "对照实验", "评估指标", "科研"],
                "examples": ["对比 RAG 与微调在医疗问答的准确率，有 1000 条标注数据，请设计实验方案"],
            },
        ],
    },
    {
        "file": "acs-analysis.json",
        "file_note": "analysis 的统计为纯 Python 标准库真实计算",
        "aic": "1.2.156.3088.1.1.YNBAN1.ANA001.1.0000",
        "name": "研伴·数据分析智能体",
        "desc": "研伴科研协作团队的数据分析 Partner：对用户提供的 CSV 数据用 Python 标准库执行真实统计计算（各数值列的均值/标准差/最值/中位数，数值列两两皮尔逊相关系数），并由大模型基于真实数字撰写分析解读，不编造数据。缺 CSV 数据时返回 awaiting-input。",
        "skills": [
            {
                "id": "yanban.analysis.stats",
                "name": "统计分析服务",
                "description": "输入 ```csv 代码块（表头+数值行）与分析目标，输出真实统计结果 JSON（描述统计+皮尔逊相关）与 200 字内基于数字的解读。\n【输入要求】\n必填：```csv 数据块。\n可选：分析目标文字。\n当缺少 csv 块时返回 Task.state=\"awaiting-input\"。",
                "tags": ["统计分析", "相关系数", "数据解读", "科研"],
                "examples": ["分析这两组实验指标的相关性：```csv\\naccuracy,latency\\n0.81,320\\n0.85,410\\n```"],
            },
        ],
    },
    {
        "file": "acs-writing.json",
        "aic": "1.2.156.3088.1.1.YNBAN1.WRT001.1.0000",
        "name": "研伴·论文写作智能体",
        "desc": "研伴科研协作团队的论文写作 Partner：把要点列表扩写为学术语体段落，逻辑连贯、术语准确，引用位置以 [作者, 年份] 占位，并建议该段落在论文中的位置。缺要点时返回 awaiting-input。",
        "skills": [
            {
                "id": "yanban.writing.paragraph",
                "name": "学术段落写作服务",
                "description": "输入写作要点（≥15 字），输出学术语体段落（≤500 字）+建议章节位置+引用占位。\n【输入要求】\n必填：要点列表。\n可选：目标章节、语体要求。\n当缺少必填项时返回 Task.state=\"awaiting-input\"。",
                "tags": ["论文写作", "学术语体", "科研写作"],
                "examples": ["写相关工作段落：要点1 MCP统一工具接入；要点2 A2A智能体任务委派；要点3 二者互补需桥接"],
            },
        ],
    },
    {
        "file": "acs-review.json",
        "aic": "1.2.156.3088.1.1.YNBAN1.REV001.1.0000",
        "name": "研伴·模拟审稿智能体",
        "desc": "研伴科研协作团队的模拟审稿 Partner：以方法/写作/贡献三位审稿人视角评审草稿，意见具体引用原文，各给主要/次要问题与评分，输出 Meta 结论（接收/小修/大修/拒稿+最优先修改项）。草稿不足 50 字时返回 awaiting-input。",
        "skills": [
            {
                "id": "yanban.review.simulate",
                "name": "模拟评审服务",
                "description": "输入草稿文本（≥50 字），输出 R1 方法/R2 写作/R3 贡献三份意见（主要问题 2 条+次要 1 条+评分 x/10）与 Meta 结论。\n【输入要求】\n必填：草稿文本。\n可选：评审侧重、目标会议级别。\n当草稿不足 50 字时返回 Task.state=\"awaiting-input\"。",
                "tags": ["模拟审稿", "同行评审", "质量评估", "科研"],
                "examples": ["请评审这段结论：多智能体系统通过标准化协议实现协作，实验显示准确率提升5%"],
            },
        ],
    },
]

schema = json.load(open(Path(__file__).resolve().parent.parent / "ACPs-community" / "registry-server" / "app" / "agent" / "acsSchema.json", encoding="utf-8"))
from jsonschema import Draft202012Validator  # noqa: E402

v = Draft202012Validator(schema)
ok = True
for spec in AGENTS:
    acs = build_acs(aic=spec["aic"], name=spec["name"], desc=spec["desc"], skills=spec["skills"])
    errors = list(v.iter_errors(acs))
    if errors:
        ok = False
        print(f"[FAIL] {spec['file']}: {errors[0].json_path} {errors[0].message[:100]}")
    (OUT / spec["file"]).write_text(json.dumps(acs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{'PASS' if not errors else 'FAIL'}] {spec['file']} skills={len(acs['skills'])}")

sys.exit(0 if ok else 1)
