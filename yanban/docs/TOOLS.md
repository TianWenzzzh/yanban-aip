# TOOLS · 研伴 2.0 认知层的对外命名工具表

首版：2026-10-01（轮 188，B8）

## 这份表是什么

研伴 2.0 要作为**被别的智能体调用的智能体**上梧桐平台，所以认知层得有一组稳定的、有名字的能力，
而不是一堆要靠读代码才知道的 URL。词表只有一份：

- 名字与一句话说明：`companion/api/tools.py` 的 `SPEC`
- 运行时读它：`GET /companion/tools`

**工具表里的方法与路径不在文档里、也不在 `SPEC` 里抄**——`tools.build()` 每次都从 `router.routes` 现取。
这份文档还是会写几条 URL（比如"从哪儿进"那两句），但它们不是没人对账的抄本：
`tests2/test_tool_table.py` 把文档里点名的每条 URL 拿去**在跑起来的服务上问一遍**（GET 判 404，`POST` 那条按路由表里的方法查），
端点改名或新增而表/文档没跟上时，`build()` 直接抛错、那条用例直接红——不会出现"文档里还留着、实际已经没了"的假工具。

要看某颗工具收什么参数、返什么形状：**读端点函数本体**（`companion/api/router.py`），
这里不抄参数表——抄一份就会漂一份，而且研伴的诚实性闸门全都长在服务端，不在参数说明里。

## 命名规则（往表里加东西之前先读这段）

- `snake_case`、动词在前、名词在后，说的是**用户要的结果**而不是实现细节：
  用 `read_pdf_span`（取回撑住这句话的原文片段），不用 `cite_context_post`。
- 一颗工具只点名一个端点；同一件事不许有两个名字。
- 每条路由都必须被点名：新增端点忘了起名，对账会红（"多一个少一个都红"是双向的）。
- 不许出现「账号体系」或「花了多少钱」这类概念——研伴最终是被调用的智能体，产品里没有这些东西。

## 不在对外清单里的两条

`inspect_user_profile`、`forget_user_profile` 在表里被点名，但标了 `internal`：
`/companion/users/{uid}/profile` 的鉴权是半截的（整套 API 还没有身份层）。
在身份设计定下来之前，不把这两条 advertised 给外部调用方——它们仍被对账覆盖，
只是 `tools.external()` 会滤掉。这一处要人拍板，不是这份表能自己解决的。

## 外部怎么真调一颗（被审计的那条入口）

词表有了，还缺一条**会被审计**的入站路径：`/companion/*` 是裸路由，`companion/` 自己不写 access 日志，
所以"这颗工具被外部调用过"这句话过去数不出来。轮 197 在 Leader 上补了 `POST /rpc`
（派发层在 `code/yanban/tool_rpc.py`，接线在 `code/yanban/leader.py`）：
一次入站 = 一条 `logs/amp_access_yanban-leader.jsonl`，caller 记请求里真实的 `senderId`，不冒充成本队。

- 请求体只收 `{"tool": "<名字>", "args": {...}}`，放在 AIP 的 `dataItems` 文本里，外层是 JSON-RPC 信封。
  参数细节仍不抄——必填项由端点函数自己的签名决定，缺了会把"缺哪一颗"原样回给调用方。
- 可派发的集合**不在这里抄名单**，由 `tools.dispatchable()` 现场算：对外宣告的 GET ∩ 端点直接返回 JSON 面。
  数量与逐颗判定以 `输出/_logs/r197_dispatchable_tools.log` 的现测为准。
- 写类（POST/DELETE）与返回文件流/纯文本的那几颗会被拒绝并给出理由：整套 API 还没有身份层，
  在没有鉴权的服务上开一条能被外部改数据的口子不属于现在（同"不在对外清单里的两条"那条理由）。
- 拒绝就是拒绝，不降级成"看起来成功"：任务停在 failed 并把原因原样回给调用方。
  诚实性闸门（`mentor.violations`、`report.clean_report_with_ledger`）都长在端点函数里面，这条入口没有绕开任何一道。

〔实测〕本地：用例见 `code/yanban/tests2/test_tool_rpc.py`——含"一次入站恰好一条记录且 caller 是真实
`senderId`"、"默认分支落盘的文件名与 callee"、"空派发表必须当场拒绝启动"，以及"写类工具／未知名字／
非 JSON／缺必填都得 failed 且带各自的理由"。植入负控的逐条读数见 `输出/_logs/r197_plant_tool_rpc.log`
（每个变异体改的是哪一行、该红在哪几条用例名下，都写在那份日志里）。
〔实测〕线上那一次真调（轮 197 部署之后）：在 Leader 容器内 `POST /rpc` 调 `list_tools`——
HTTP 200、任务停在 awaiting-completion、`/app/logs/amp_access_yanban-leader.jsonl` 由 0 条变 1 条，
那条记录的 caller 就是请求里发出去的 senderId、callee 是本队 aic。逐行读数见
`输出/_logs/r197_leader_rpc_live.log`；部署链的备份与探活见 `输出/_logs/r197_sync_cloud.log`、
漂移核对 RC=0 逐文件一致见 `输出/_logs/r197_cloud_drift.log`（部署前那份"还差三颗加一颗线上缺失"的
读数留在 `输出/_logs/r197_drift_before_deploy.log`）。
🔴 视角要说清：这是**容器内发起的入站调用**（与 §6 那条伙伴审计线同一视角），不是从公网打进来；
平台侧真正的"别的智能体来调"要等上面未决第 1、2 两处接线做完才另有一份读数。

## 谁该读这份表

1. **mcp-aip-gateway**（1.0 侧）：它是 MCP↔A2A 桥，工具是从上游服务透传出来的，
   自己不再手写一份清单——上游把 `GET /companion/tools` 当作 `tools/list` 的来源即可。
   🔴 实况（现读，别把上面那句读成「已经接上了」）：网关配置里 `yanban` 这一路今天指着镜像内置的 `demo_mcp_server.py`，它对外的两颗工具返回的是拼出来的假条目，**没有**读这张表；把这张表登记成一路上游仍未做，口径见 `docs/UNDERMIND_BORROW.md` 未决第 2 条。
2. **梧桐 AIP 注册侧**：技能名取这张表的 `name`，描述取 `summary`。

🔴 这两处都在 `code/yanban` 之外，本轮没有改（改动范围只允许落在这个仓）。
表与端点是按"它们可以直接读"来做的，但**接线本身还没做**，登记在未决里。
