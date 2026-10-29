"""资源路径解析 —— 兼容「源码运行」和「PyInstaller 打包后运行」。

源码运行时资源就在包目录下;PyInstaller 单文件(onefile)运行时,资源被解压到
一个临时目录 `sys._MEIPASS`,此时 `__file__` 不一定指向它。所以统一走这里:

    from .paths import resource

    page = resource("ui", "strike.html")

打包时 spec 里要把 `fistalarm/ui/*.html` 和 `fistalarm/ui/assets/*.png`
按同样的相对结构放进包,例如 `_MEIPASS/fistalarm/ui/strike.html`。
"""
from __future__ import annotations

import sys
from pathlib import Path


def _bundle_root() -> Path:
    """返回打包后的资源根目录;源码运行时返回包所在目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # 打包后:_MEIPASS/fistalarm/ 下就是包内相对结构
        return Path(meipass) / "fistalarm"
    return Path(__file__).parent


def resource(*parts: str) -> Path:
    """拼出资源文件的绝对路径。parts 相对 fistalarm/ 目录,如 ("ui","strike.html")。"""
    return _bundle_root().joinpath(*parts)
