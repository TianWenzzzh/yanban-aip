"""pytest 路径自举：无 build-system 的平铺布局下，把仓库根加入 sys.path。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
