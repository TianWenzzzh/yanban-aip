"""pytest 根配置：把 LLM_MOCK 定值从「测试模块顶部」提到「收集之前」。

不写这个文件的后果（实测）：单独跑 tests2 全绿，跑全套件 16 个用例红——
因为 tests/ 会先把 llm 导进来，llm.LLM_MOCK 在 import 那一刻按当时的环境变量定值，
之后 tests2/*.py 顶部的 os.environ["LLM_MOCK"]="1" 再设也没用了。
于是"零真实调用"的 mock 用例实际去打真实 API，在没 key 的机器上全报 LLMError。
"""
import os


def pytest_configure(config):
    markexpr = (config.getoption("markexpr") or "").strip()
    if markexpr in ("", "not live"):        # 默认离线口径；自定义 -m 表达式时不替调用方做主
        os.environ.setdefault("LLM_MOCK", "1")
