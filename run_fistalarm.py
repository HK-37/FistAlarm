"""PyInstaller 打包入口。

不直接拿 fistalarm/__main__.py 当入口:它用包内相对导入(from .models ...),
被 PyInstaller 当顶层脚本跑时没有包上下文,相对导入会失败。
这里用绝对导入调 main(),干净可靠。
"""
from __future__ import annotations

import sys

from fistalarm.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
