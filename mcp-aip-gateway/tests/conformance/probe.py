"""万桥生态探针 v0（N6）：对任意 A2A/AIP 端点做协议形状一致性探测。

用法：
    uv run python tests/conformance/probe.py --target http://127.0.0.1:8060 \
        --profile a2a|aip|all [--out docs/probe_self_report.md]

探针定位（边界声明）：**只测协议形状，不测业务正确性**——
- a2a profile：well-known 双路径可达且同卡 / Card 必填字段 / tasks/send 与 message/send
  双方法名可达（未知工具应得 failed 任务而非协议 error）/ 非法 JSON 得 -32700 /
  未知方法 -32601 / tasks/get 未知 id -32001、缺 id -32602
- aip profile：/health 存活 / /acs 过官方 acsSchema 校验 / /rpc 非法输入 -32700

退出码：全部 PASS（含 SKIP 计零）= 0；任一 FAIL = 1。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx
import jsonschema

HERE = Path(__file__).resolve().parent
ACS_SCHEMA = json.loads((HERE / "fixtures" / "acsSchema.json").read_text(encoding="utf-8"))


@dataclass
class Check:
    check_id: str
    name: str
    expect: str
    actual: str
    verdict: str  # PASS | FAIL | SKIP
    note: str = ""


def _jrpc(method: str, params: dict | None, rpc_id: str = "probe") -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}


class Probe:
    def __init__(self, target: str) -> None:
        self.target = target.rstrip("/")
        self.client = httpx.Client(timeout=15.0)
        self.results: list[Check] = []

    def record(self, check_id: str, name: str, expect: str, ok: bool, actual: str, note: str = "") -> None:
        self.results.append(Check(check_id, name, expect, actual, "PASS" if ok else "FAIL", note))

    # ── a2a profile ──────────────────────────────────────────────────────

    def probe_a2a(self) -> None:
        c = self.client
        # A1 双路径可达且同卡
        try:
            r_new = c.get(f"{self.target}/.well-known/agent-card.json")
            r_old = c.get(f"{self.target}/.well-known/agent.json")
            same = r_new.status_code == r_old.status_code == 200 and r_new.json() == r_old.json()
            self.record("A2A-1", "well-known 双路径可达且同卡",
                        "两路径 200 且 JSON 相同", same,
                        f"agent-card={r_new.status_code} agent.json={r_old.status_code} 同卡={same}")
        except Exception as exc:  # noqa: BLE001
            self.record("A2A-1", "well-known 双路径可达且同卡", "两路径 200 且 JSON 相同", False, f"异常 {exc}")
            return  # 拿不到卡，后续 Card 级检查失去意义

        # A2 Card 必填字段（§2.1：name/url/skills 必填，capabilities 建议给全）
        card = r_new.json()
        missing = [f for f in ("name", "url", "capabilities", "skills") if f not in card]
        skills_ok = isinstance(card.get("skills"), list)
        self.record("A2A-2", "Card 必填字段存在", "name/url/capabilities/skills 齐备且 skills 为列表",
                    not missing and skills_ok, f"缺失={missing or '无'} skills类型={'list' if skills_ok else type(card.get('skills')).__name__}")

        # A3 双方法名可达：未知工具应得 failed 任务而非协议 error（§2.2 错误语义）
        for method in ("tasks/send", "message/send"):
            try:
                r = c.post(self.target + "/", json=_jrpc(method, {
                    "id": f"probe-{method.replace('/', '-')}",
                    "message": {"parts": [{"type": "text", "text": '{"tool": "probe_no_such_tool"}'}]},
                }))
                body = r.json()
                state = ((body.get("result") or {}).get("status") or {}).get("state")
                ok = r.status_code == 200 and state == "failed"
                self.record(f"A2A-3{'a' if method == 'tasks/send' else 'b'}",
                            f"{method} 可达（未知工具→failed 任务）",
                            "HTTP 200 + result.status.state=failed",
                            ok, f"HTTP {r.status_code} state={state}")
            except Exception as exc:  # noqa: BLE001
                self.record(f"A2A-3{'a' if method == 'tasks/send' else 'b'}",
                            f"{method} 可达（未知工具→failed 任务）", "HTTP 200 + failed", False, f"异常 {exc}")

        # A3c 流式可达：message/stream → SSE 且含 final 帧（§2.4）
        try:
            r = c.post(self.target + "/", json=_jrpc("message/stream", {
                "id": "probe-stream",
                "message": {"parts": [{"type": "text", "text": '{"tool": "probe_no_such_tool"}'}]},
            }))
            is_sse = r.headers.get("content-type", "").startswith("text/event-stream")
            has_final = '"final": true' in r.text or '"final":true' in r.text
            self.record("A2A-3c", "message/stream 流式可达", "text/event-stream + final 帧",
                        is_sse and has_final, f"cta={r.headers.get('content-type', '')[:24]} final帧={has_final}")
        except Exception as exc:  # noqa: BLE001
            self.record("A2A-3c", "message/stream 流式可达", "text/event-stream + final 帧", False, f"异常 {exc}")

        # A4 非法 JSON → -32700
        try:
            r = c.post(self.target + "/", content=b"{not-json", headers={"content-type": "application/json"})
            code = (r.json().get("error") or {}).get("code")
            self.record("A2A-4", "非法 JSON → -32700", "error.code=-32700", code == -32700, f"code={code}")
        except Exception as exc:  # noqa: BLE001
            self.record("A2A-4", "非法 JSON → -32700", "error.code=-32700", False, f"异常 {exc}")

        # A5 未知方法 → -32601
        try:
            r = c.post(self.target + "/", json=_jrpc("tasks/subscribe", {}))
            code = (r.json().get("error") or {}).get("code")
            self.record("A2A-5", "未知方法 → -32601", "error.code=-32601", code == -32601, f"code={code}")
        except Exception as exc:  # noqa: BLE001
            self.record("A2A-5", "未知方法 → -32601", "error.code=-32601", False, f"异常 {exc}")

        # A6 tasks/get 语义（未知 id -32001；缺 id -32602）
        try:
            r = c.post(self.target + "/", json=_jrpc("tasks/get", {"id": "probe-ghost"}))
            code = (r.json().get("error") or {}).get("code")
            if code == -32601:
                self.record("A2A-6a", "tasks/get 未知 id", "-32001", False,
                            "对端返回 -32601（tasks/get 未实现）", note="对端版本早于 v0.2 任务接口")
            else:
                self.record("A2A-6a", "tasks/get 未知 id", "-32001", code == -32001, f"code={code}")
            r2 = c.post(self.target + "/", json=_jrpc("tasks/get", {}))
            code2 = (r2.json().get("error") or {}).get("code")
            self.record("A2A-6b", "tasks/get 缺 id", "-32602", code2 == -32602, f"code={code2}")
        except Exception as exc:  # noqa: BLE001
            self.record("A2A-6a", "tasks/get 语义", "-32001/-32602", False, f"异常 {exc}")

    # ── aip profile ──────────────────────────────────────────────────────

    def probe_aip(self) -> None:
        c = self.client
        # I1 /health 存活
        try:
            r = c.get(f"{self.target}/health")
            ok = r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("status") == "ok"
            self.record("AIP-1", "/health 存活", "200 且 status=ok", ok,
                        f"HTTP {r.status_code} status={r.json().get('status') if r.status_code == 200 else 'n/a'}")
        except Exception as exc:  # noqa: BLE001
            self.record("AIP-1", "/health 存活", "200 且 status=ok", False, f"异常 {exc}")

        # I2 /acs 过官方 acsSchema 校验（404 = 对端无 AIP 能力面，记 SKIP 非 FAIL）
        try:
            r = c.get(f"{self.target}/acs")
            if r.status_code == 404:
                self.results.append(Check("AIP-2", "/acs 过官方 acsSchema", "200 + jsonschema 校验通过",
                                          "SKIP", "对端 404（无 AIP 能力面，A2A 独立形态）"))
            elif r.status_code != 200:
                self.record("AIP-2", "/acs 过官方 acsSchema", "200 + jsonschema 校验通过", False, f"HTTP {r.status_code}")
            else:
                jsonschema.validate(r.json(), ACS_SCHEMA)
                self.record("AIP-2", "/acs 过官方 acsSchema", "200 + jsonschema 校验通过", True,
                            f"skills={len(r.json().get('skills') or [])}")
        except jsonschema.ValidationError as exc:
            self.record("AIP-2", "/acs 过官方 acsSchema", "200 + jsonschema 校验通过", False,
                        f"schema 违规：{exc.message[:80]}")
        except Exception as exc:  # noqa: BLE001
            self.record("AIP-2", "/acs 过官方 acsSchema", "200 + jsonschema 校验通过", False, f"异常 {exc}")

        # I3 /rpc 非法输入 → 协议级错误（-32700 解析失败）
        try:
            r = c.post(f"{self.target}/rpc", content=b"{bad", headers={"content-type": "application/json"})
            err = (r.json() or {}).get("error") or {}
            self.record("AIP-3", "/rpc 非法输入 → 协议错误", "error.code=-32700", err.get("code") == -32700,
                        f"code={err.get('code')}")
        except Exception as exc:  # noqa: BLE001
            self.record("AIP-3", "/rpc 非法输入 → 协议错误", "error.code=-32700", False, f"异常 {exc}")

    def close(self) -> None:
        self.client.close()


def render_report(target: str, profile: str, results: list[Check], started: str) -> str:
    passed = sum(1 for r in results if r.verdict == "PASS")
    failed = sum(1 for r in results if r.verdict == "FAIL")
    skipped = sum(1 for r in results if r.verdict == "SKIP")
    lines = [
        "# 万桥生态探针 · 自测报告（首轮）",
        "",
        f"- 探针：`tests/conformance/probe.py`（N6）｜ 目标：`{target}` ｜ profile：`{profile}`",
        f"- 启动：{started}（time.strftime 实测）｜ 结果：**PASS {passed} / FAIL {failed} / SKIP {skipped}**",
        "- 边界声明：**只测协议形状，不测业务正确性**（不构造真实业务负载，不评价上游返回内容）",
        "",
        "| # | 检查项 | 预期 | 实际 | 判定 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.check_id} | {r.name} | {r.expect} | {r.actual} | {r.verdict} | {r.note} |")
    lines += ["", "---",
              f"*探针边界：协议形状探测工具，供生态伙伴自查与互查；业务正确性由各上游自己的 E2E 负责。*"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="万桥生态探针（协议形状一致性）")
    ap.add_argument("--target", required=True, help="基地址，如 http://127.0.0.1:8060")
    ap.add_argument("--profile", choices=["a2a", "aip", "all"], default="all")
    ap.add_argument("--out", default="", help="Markdown 报告落盘路径（缺省只打印）")
    args = ap.parse_args()

    probe = Probe(args.target)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.profile in ("a2a", "all"):
        probe.probe_a2a()
    if args.profile in ("aip", "all"):
        probe.probe_aip()
    probe.close()

    print(f"\n生态探针（target={args.target} profile={args.profile}）")
    for r in probe.results:
        mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ "}[r.verdict]
        print(f"{mark} {r.check_id:<8} {r.name:<36} {r.actual[:60]}")
    failed = sum(1 for r in probe.results if r.verdict == "FAIL")
    print(f"\n结果：PASS {sum(1 for r in probe.results if r.verdict == 'PASS')} / "
          f"FAIL {failed} / SKIP {sum(1 for r in probe.results if r.verdict == 'SKIP')}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_report(args.target, args.profile, probe.results, started), encoding="utf-8")
        print(f"报告已写入 {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
