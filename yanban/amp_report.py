"""AMP 可观测性汇总：聚合各 Agent 的 NDJSON 日志，输出运行统计表。

用法：uv run python amp_report.py
比赛用途：直接生成"应用成效"的后台数据（心跳/调用量/耗时/成功率）。
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LOG_DIRS = [_ROOT / "yanban" / "logs", _ROOT / "mcp-aip-gateway" / "logs"]


def scan() -> dict:
    stats: dict[str, dict] = defaultdict(lambda: {
        "heartbeat": 0, "access": 0, "ok": 0, "err": 0,
        "durations": [], "bytes_in": 0, "bytes_out": 0, "last_uptime": 0.0,
    })
    for d in LOG_DIRS:
        for f in d.glob("amp_*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                aic = rec.get("aic", "?")[-12:]
                lt = rec.get("log_type")
                s = stats[aic]
                if lt == "heartbeat":
                    s["heartbeat"] += 1
                    s["last_uptime"] = max(s["last_uptime"], rec.get("body", {}).get("uptimeSeconds", 0))
                elif lt == "access":
                    s["access"] += 1
                    body = rec.get("body", {})
                    code = (body.get("response") or {}).get("statusCode", 0)
                    if 200 <= code < 400:
                        s["ok"] += 1
                    elif code:
                        s["err"] += 1
                    dur = body.get("duration_ms", body.get("durationMs"))
                    if dur is not None:
                        s["durations"].append(dur)
                    s["bytes_in"] += (body.get("request") or {}).get("bodySizeBytes", 0) or 0
                    s["bytes_out"] += (body.get("response") or {}).get("bodySizeBytes", 0) or 0
    return stats


def main() -> None:
    stats = scan()
    total_access = sum(s["access"] for s in stats.values())
    total_heartbeat = sum(s["heartbeat"] for s in stats.values())
    print("研伴系统 AMP 运行统计")
    print("=" * 88)
    print(f"{'Agent(AIC尾段)':<16}{'心跳':>6}{'调用':>6}{'成功':>6}{'失败':>6}{'平均耗时ms':>11}{'P95ms':>9}{'流量KB':>9}{'存活s':>8}")
    print("-" * 88)
    for aic, s in sorted(stats.items(), key=lambda kv: -kv[1]["access"]):
        dur = s["durations"]
        p95 = sorted(dur)[int(len(dur) * 0.95) - 1] if dur else 0
        avg = statistics.mean(dur) if dur else 0
        kb = (s["bytes_in"] + s["bytes_out"]) / 1024
        print(f"{aic:<16}{s['heartbeat']:>6}{s['access']:>6}{s['ok']:>6}{s['err']:>6}{avg:>11.1f}{p95:>9.0f}{kb:>9.1f}{s['last_uptime']:>8.0f}")
    print("-" * 88)
    print(f"合计：心跳 {total_heartbeat} 条，AIP 调用 {total_access} 次，Agent {len(stats)} 个")
    if "--md" in sys.argv:
        md = ["| Agent | 心跳 | 调用 | 成功 | 失败 | 平均耗时ms |", "|---|---|---|---|---|---|"]
        for aic, s in sorted(stats.items(), key=lambda kv: -kv[1]["access"]):
            avg = statistics.mean(s["durations"]) if s["durations"] else 0
            md.append(f"| {aic} | {s['heartbeat']} | {s['access']} | {s['ok']} | {s['err']} | {avg:.0f} |")
        Path("amp-report.md").write_text("\n".join(md), encoding="utf-8")
        print("saved -> amp-report.md")


if __name__ == "__main__":
    main()
