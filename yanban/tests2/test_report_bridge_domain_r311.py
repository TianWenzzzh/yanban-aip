# -*- coding: utf-8 -*-
"""轮 311（队长 §2 独立定位）：`test_report_bridge` 的消费者清点什么情况下会红——红在谁的 domain 上。

被复核的那条门（`tests2/test_report_bridge.py::test_bridge_consumers_are_exactly_the_read_only_paths`）
用 `ROOT.rglob("*.py")` + `read_text(encoding="utf-8")` 扫**整棵树**。它的判据本意是
"我们仓里的源码谁开了这座跨语言桥"，但它的**管辖范围**却等于"这台机器上这个目录里此刻有什么 .py"——
venv、node_modules、构建产物、以及介质故障后整份变成 0xFF 的文件，全都在范围里。
⇒ 门会红在跟产品无关的东西上，且红形是裸 `UnicodeDecodeError`（不点名、看不出是环境件还是源码件）。

这台仪器钉三件，每件只钉一件事：
  D1 环境目录里的非 UTF-8 件**不许**把门弄红（范围＝源码，不是这台机器的目录树）。
  D2 源码目录里的非 UTF-8 件**必须**把门弄红，而且**点名**——修 D1 不许顺手把坏文件也放行。
  D3 门不许被修瞎：源码里真多一个开桥的消费者，必须被点名（正向对照）。
反向判据：把 prune 去掉 ⇒ D1 红；把"解不开就点名"改成"解不开就跳过" ⇒ D2 红。
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def _load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_under_test_r311", str(HERE / "test_report_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load_bridge()
GATE = "test_bridge_consumers_are_exactly_the_read_only_paths"
NONUTF8 = b"\xff\xfe\x00\x01 no-decodable-bytes-g183-plant\n"


def _plant(rel: str, data: bytes) -> pathlib.Path:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture()
def plants():
    made = []

    def add(rel, data=NONUTF8):
        made.append(ROOT / rel)
        return _plant(rel, data)

    yield add
    for p in made:                      # 逐件摘掉；目录只摘本轮自己建的那个
        if p.exists():
            p.unlink()
    for d in (ROOT / ".r311_plant_env",):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def test_D1_environment_dir_non_utf8_does_not_redden_the_gate(plants):
    """环境目录（名字以 . 开头）里的非 UTF-8 .py 落在门的范围之外——红在产品源码，不在这台机器的目录树。"""
    p = plants(".r311_plant_env/site_plant.py")
    assert p.read_bytes() == NONUTF8, "夹具没落成非 UTF-8 字节，这条用例在空跑"
    getattr(bridge, GATE)()


def test_D2_source_dir_non_utf8_still_red_and_names_the_file(plants):
    """D1 的修法不许变成"解不开就算了"：源码域里的坏件必须红，且红话里要点名是哪个文件。"""
    p = plants("_plant_broken_source.py")
    with pytest.raises(Exception) as exc:
        getattr(bridge, GATE)()
    msg = str(exc.value)
    assert "_plant_broken_source.py" in msg, f"红了但没点名（等于把坏文件藏起来）：{msg[:160]}"


def test_D3_the_census_still_sees_a_real_consumer(plants):
    """正向对照：门没被修瞎——源码里真多一个 `bilingual=True` 的消费者，必须被逐文件点名。"""
    body = ("# 夹具：故意写一条开桥的调用（本轮用例用完即删）\n"
            "from companion.literature.evidence import renumber_citations\n"
            "out = renumber_citations(text, pool, bilingual=True)\n")
    plants("companion/_plant_consumer.py", body.encode("utf-8"))
    with pytest.raises(AssertionError) as exc:
        getattr(bridge, GATE)()
    assert "companion" in str(exc.value) and "_plant_consumer.py" in str(exc.value), str(exc.value)[:200]
