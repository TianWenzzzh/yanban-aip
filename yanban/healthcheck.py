"""研伴全家桶健康巡检：7 端口逐个探活，异常写日志，正常一行汇总。

用法：python healthcheck.py [--json]
退出码：0=全部在线，1=有离线。可挂 Windows 任务计划程序做定时巡检。
"""

import json
import sys
import time
import urllib.request

SERVICES = {
    8021: "MCP↔AIP 网关",
    8031: "文献 Partner",
    8032: "实验 Partner",
    8033: "数据分析 Partner",
    8034: "写作 Partner",
    8035: "审稿 Partner",
    8041: "研伴 Leader",
}

LOG = "logs/healthcheck.log"


def probe(port: int, timeout: float = 2.0) -> dict:
    url = f"http://127.0.0.1:{port}/health"
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(300).decode("utf-8", "replace")
            return {"port": port, "ok": True, "ms": int((time.time() - t0) * 1000), "body": body}
    except Exception as e:  # noqa: BLE001
        return {"port": port, "ok": False, "ms": int((time.time() - t0) * 1000), "error": str(e)}


def main() -> int:
    as_json = "--json" in sys.argv
    results = [probe(p) for p in SERVICES]
    down = [r for r in results if not r["ok"]]

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "OK " if r["ok"] else "DOWN"
            name = SERVICES[r["port"]]
            print(f"[{mark}] {r['port']:>5} {name}  {r['ms']}ms")

    if down:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as f:
            for r in down:
                f.write(f"{ts} DOWN {r['port']} {SERVICES[r['port']]} {r.get('error','')}\n")
        print(f"→ {len(down)} 个服务离线，已记录 {LOG}；恢复命令见 PROGRESS-20260905.md 服务清单")
        return 1
    print("→ 7/7 全部在线")
    return 0


if __name__ == "__main__":
    sys.exit(main())
