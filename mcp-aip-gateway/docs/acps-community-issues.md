# ACPs-community Issue 终审版（2026-09-06 夜间终审，可直接提交）

> 提交前检查：①搜索仓库无重复 Issue ②每个 Issue 附复现环境（Windows 11 + Git Bash/MSYS + Python 3.14.6 + acps-sdk v2.2.0）③提交后截图存档（比赛'开源贡献'佐证）④提交顺序 A→B→C→D

# ACPs-community Issue 草稿（2026-09-04 实测整理）

> 提交前建议先搜仓库确认没有重复 Issue。每个都可附复现环境：Windows 11 + Git Bash (MSYS) + Python 3.14.6 + acps-sdk v2.2.0。
> 提交地址：https://github.com/AIP-PUB/ACPs-community/issues

---

## Issue A：文档教程的最小示例与 SDK v2.2.0 默认行为不兼容（identity binding）

**标题**：[Docs] agent-development.md 最小示例在 v2.2.0 下无法直接运行（identity binding / AIC 格式）

**正文**：

`acps-docs/tutorials/agent-development.md` 1.3/1.4 节的最小 Partner/Leader 示例在 SDK v2.2.0 下按文档原样运行会连续报错，建议文档更新或在示例中显式说明：

1. **Leader 端**：`AipRpcClient` 的 `identity_binding_enabled` 默认为 `True`，构造时若未传 `expected_partner_aic` 会抛
   `ValueError: expected_partner_aic is required when identity_binding_enabled=True`；
   传入占位 AIC 后又因纯 HTTP 无对端证书抛 `PeerAicMissingError: TLS server AIC is required for identity binding`。
   纯 HTTP 本地示例需要显式 `identity_binding_enabled=False`（与教程 1.5 节"本地最小示例可先用 HTTP"的表述呼应）。

2. **Partner 端**：`add_aip_rpc_router` 同样默认 `identity_binding_enabled=True`，纯 HTTP 下请求会 500：
   `ValueError: local_aic is required when identity_binding_enabled=True`。

3. **AIC 格式**：示例中的 `"example-partner-aic"` / `"example-leader-aic"` 不符合 AIC 10 段格式，即使关闭身份绑定，
   `TaskCommand.senderId` 也会被 `normalize_and_validate_aic` 拒绝（`AIC 必须包含 10 段，当前为 1 段`）。
   建议示例改用形如 `1.2.156.3088.1.1.XXXXXX.XXXXXX.1.XXXX` 的占位值。

**建议**：文档示例更新为 v2.2.0 可直接运行的版本，并在教程中加一段"本地 HTTP 开发态 vs mTLS 部署态"的开关说明。

---

## Issue B：AMP 测试在 Windows 上失败（POSIX 绝对路径）

**标题**：[Bug] tests/amp/test_amp_metrics_emitter.py 在 Windows 上断言失败（/readonly_nonexistent_dir 不是有效故障路径）

**正文**：

`tests/amp/test_emit_sync_write_failure_only_warns` 使用 `Path("/readonly_nonexistent_dir/amp_metrics.jsonl")` 模拟"目录不可写"。
在 Windows 上该路径会解析为当前盘符根目录下的相对可创建路径（或 `C:\readonly_nonexistent_dir`，通常可创建），写入不会失败，
导致 `assert any("写入失败" in m ...)` 断言失败：

```
1 failed, 552 passed
FAILED tests/amp/test_amp_metrics_emitter.py::test_emit_sync_write_failure_only_warns
```

**建议**：用平台无关的方式构造不可写路径，例如指向一个已存在的**文件**路径当作目录（`tmp_log / "f.txt" / "m.jsonl"`），或按 `sys.platform` 分支。

---

## Issue C：dev-cert.sh 在 Git Bash (Windows) 下失败（MSYS 路径转换改写 -subj）

**标题**：[Bug] dev-cert.sh 在 Git Bash 下 openssl req -subj 被 MSYS 路径转换破坏

**正文**：

在 Git for Windows 的 Git Bash 中执行 `acps-infra/dev-infra/dev-cert.sh init-ca`（或任何触发 `issue-leaf` 的命令），
根 CA 初始化静默失败（`set -e` 下 openssl req 返回非零但 stderr 被 `>/dev/null 2>&1` 吞掉）。

根因：MSYS 启发式把 `-subj "/CN=ACPs Development Root CA"` 的参数值当作 POSIX 路径转换成了
`D:/GitHub/Git/CN=ACPs Development Root CA`（Git 安装根目录），openssl 报：

```
req: subject name is expected to be in the format /type0=value0/... This name is not in that format:
'D:/GitHub/Git/CN=ACPs Development Root CA'
```

**复现**：Git Bash 中 `bash dev-cert.sh init-ca`。

**解决/建议**：
- 用户侧临时方案：`export MSYS2_ARG_CONV_EXCL="/CN="` 后一切正常（仅排除 CN 值的转换，证书路径转换保留）。
- 脚本侧建议：在脚本开头检测 MSYS 环境并自动设置 `MSYS2_ARG_CONV_EXCL="/CN="`，
  或改用 `-config` 文件传递 subject；同时建议 openssl 失败时不要完全吞掉 stderr（至少 log_error 输出）。

---

## Issue D（可选，较轻）：教程 3.2 节发现服务示例地址不可达

**标题**：[Docs] agent-development.md 3.2 节 Discovery 示例端点 bupt.ioa.pub:9005 已不可达

**正文**：

教程中的示例 `http://bupt.ioa.pub:9005/acps-adp-v2/discover` 当前无法访问（超时）。
梧桐平台的实际发现服务为 `https://wt.ioa.pub/discovery/acps-adp-v2/discover`（见 wt.ioa.pub/docs.html）。
建议教程更新示例地址或改用占位符并注明以平台文档为准。

（附：实测该端点可用，附验证请求/响应示例更有说服力——约 1/3 概率超时，建议演示时加超时重试。）

---

## 提交节奏建议

1. 先提 **Issue A**（最有分量，展示对 SDK 的深度理解）；
2. **Issue B/C** 紧随（带补丁级修复建议，可顺势提 PR）；
3. **Issue D** 顺手（文档修正）。
4. 每个 Issue 提交后截图存档，作为比赛"开源贡献/社会效益"佐证材料。
