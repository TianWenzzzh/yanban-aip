"""轮 302：`sync_cloud.py` 的部署前置门必须**长在注入之前**，且拒绝时真的中止进程。

为什么钉这条（而不是只靠一次性植入）：前置门是本轮把"可迭代"与"不可再生数据"分开的那道牙——
它读的是宿主机的回滚包（两棵数据目录 + 清单外依赖）。而它的效力只取决于**一行顺序**：
一旦哪天有人把 `precheck_gate()` 挪到 `docker cp 注入` 之后（或把 `raise SystemExit(1)` 改成打印），
门照旧打印、照旧判红，线上却已经被改了。这类"闸门还在、牙没了"的形状本项目已踩过几次。

判线来自 `sync_cloud.py` 现读，不抄常量：断言的是**跨度顺序**与**中止语句在场**，
等价改写（换引号、换空白、把条件写成 `not (...)`）不该红——红必须发生在顺序被真的改动时。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]          # code/yanban
PKG = ROOT.parents[1]                                       # 迁移包根（与 test_doc_evidence_paths.py 同一条取法）
SYNC = PKG / "输出" / "scripts" / "sync_cloud.py"
SRC = SYNC.read_text(encoding="utf-8")


def _pos(pattern):
    m = re.search(pattern, SRC)
    assert m, "在 sync_cloud.py 里找不到 %s（结构变了就重新核对，别把断言改掉）" % pattern
    return m.start()


def test_precheck_gate_exists_and_returns_bool():
    assert re.search(r"def precheck_gate\(\):", SRC), "前置门函数没了"
    # 🔴 门必须**现读回滚包三件齐**（两棵数据目录 + 清单外依赖），且 `complete` 必须进那条合取。
    #    断言写成结构而不是字面：`ok = (not (not fresh)) and hasdb and complete` 这种等价改写不许红，
    #    而 `ok = fresh and hasdb  # 摘掉 complete` 必须红（植入 P3 就是拿这条验分辨力的）。
    assert re.search(r"ok\s*=\s*[^\n#]*\bcomplete\b", SRC), "件齐检查没接进拒绝条件 ⇒ 门只报不拦"
    for piece in ("data/yanban.db", "companion_data", "sp_pymupdf", "sp_pip"):
        assert piece in SRC, "回滚包检查不认 %s" % piece


def test_gate_runs_before_any_injection():
    gate = _pos(r"if not precheck_gate\(\)")
    inject = _pos(r"run\(cp, label=\"docker cp 注入\"")
    restart = _pos(r'run\("docker restart')
    assert gate < inject, "前置门被挪到注入之后 ⇒ 它会打印判红但线上已经改了"
    assert gate < restart, "前置门被挪到重启之后 ⇒ 同上"


def test_refusal_actually_exits_nonzero():
    i = _pos(r"if not precheck_gate\(\)")
    tail = SRC[i:i + 400]
    assert re.search(r"raise SystemExit\(1\)", tail), "拒绝分支没有非零退出（打印判红 + 继续跑 = 没门）"
    assert "不注入" in tail or "拒绝" in tail, "拒绝时要在判定行里写清它拦住了什么"


def test_gate_is_on_the_precheck_only_path_too():
    """`--precheck-only` 是植入与自检走的那条路：它必须在判红之后才允许 0。"""
    i = _pos(r'if "--precheck-only" in sys.argv')
    assert _pos(r"if not precheck_gate\(\)") < i, "只跑前置门那条分支跑在门之前 = 空转"
    assert re.search(r"raise SystemExit\(0\)", SRC[i:i + 300]), "precheck-only 没有显式 0（会被读成没跑）"
