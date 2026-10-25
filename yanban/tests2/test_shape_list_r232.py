"""轮 232：线上实例把「整篇不带句号的清单」暴露成 `over_shape` 的漏判面。

为什么单开一份文件：这三条钉的不是轮 228 那条闸门的**界**（那条有六枚植守护），
而是它自己的 docstring 里写着"会漏判"的那一型——轮 232 第一次在线上把它撞见（截图 r232s1_shape_1280.png：
学生点名"用一句话…不要展开"，屏上最终正文是三条编号列表，句数=1、字数=81）。
"""
from companion.conversation import mentor

_TERSE = "用一句话说明图神经网络在异常检测里最常用的一个公开数据集是什么，不要展开。"
_LIVE = "1. KDD Cup 99\n2. Enron Corpus\n3. CIC IDS 2012"      # 轮 232 线上屏上那份原文
_ONE = "最常用的是 KDD Cup 99。"


def test_a_list_without_periods_still_breaks_a_one_line_request():
    assert mentor.over_shape(_LIVE, _TERSE), "整篇不带句号的清单被切句数成 1 句 ⇒ 闸门看不见这次违约"


def test_a_real_one_liner_still_passes():
    assert not mentor.over_shape(_ONE, _TERSE), "接住了还判违规 ⇒ 这条闸门在替学生删不需要删的东西"


def test_the_n_count_bound_is_untouched():
    """另一条界（学生自己点的条数）不许被这次收紧顺带动到：数得出 3 条就不判，第 4 条才判。"""
    assert not mentor.over_shape(_LIVE, "只给三条就行")
    assert mentor.over_shape(_LIVE + "\n4. Cora", "只给三条就行")
