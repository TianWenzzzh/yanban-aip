"""万桥一致性套件独立运行器。

用法（二选一）：
    uv run python tests/conformance/run_conformance.py   # 运行并输出报告
    uv run pytest tests/conformance -q                   # 常规 pytest 方式

报告写入 docs/conformance_report.md：实测时间戳 + 通过/失败计数 + 逐用例结果表，
作为 M4 验收证据（诚实原则：失败原样入报告，不粉饰）。
"""

from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "docs" / "conformance_report.md"

HEADER = """# 万桥一致性测试报告（M4）

- 运行时间：{run_at}（`datetime.now()` 实测）
- 范围：`tests/conformance/`（AIP 侧 + A2A 侧，规约事实来源见 `tests/conformance/spec_tables.py`）
- 结果：**{passed} 通过 / {failed} 失败 / {skipped} 跳过**（pytest 退出码 {exit_code}）
- 复跑方式：`uv run python tests/conformance/run_conformance.py`

| 用例 | 结果 | 耗时(s) |
|---|---|---|
{rows}
"""


class ReportPlugin:
    """收集逐用例结果（setup/call 阶段分开看，error/fail 均计入失败）。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, float]] = []

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "call":
            self.rows.append((report.nodeid, report.outcome, report.duration))
        elif report.when == "setup" and report.outcome in ("failed", "skipped"):
            self.rows.append((report.nodeid, report.outcome, report.duration))


def main() -> int:
    plugin = ReportPlugin()
    exit_code = pytest.main([str(ROOT / "tests" / "conformance"), "-q", "--no-header"], plugins=[plugin])
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for _, outcome, _ in plugin.rows:
        key = "passed" if outcome == "passed" else "skipped" if outcome == "skipped" else "failed"
        counts[key] += 1
    rows = "\n".join(
        f"| `{nodeid.split('/')[-1]}` | {outcome} | {duration:.3f} |"
        for nodeid, outcome, duration in plugin.rows
    )
    REPORT_PATH.write_text(
        HEADER.format(
            run_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            passed=counts["passed"], failed=counts["failed"], skipped=counts["skipped"],
            exit_code=int(exit_code), rows=rows,
        ),
        encoding="utf-8",
    )
    print(f"[run_conformance] 报告已写入 {REPORT_PATH}")
    print(f"[run_conformance] passed={counts['passed']} failed={counts['failed']} skipped={counts['skipped']}")
    return 0 if int(exit_code) in (0, 5) else 1  # 5 = 无用例收集，也视为非失败


if __name__ == "__main__":
    sys.exit(main())
