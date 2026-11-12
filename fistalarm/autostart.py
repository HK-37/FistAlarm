"""开机自启:在当前用户的注册表 Run 键里写/删一条启动项。

真相只有一处——注册表里有没有我们这条值,就代表开没开启自启。不另存一份到
alarms.json 再同步(两处容易不一致)。界面读开关时实时查注册表。

用 HKCU(当前用户)不需要管理员权限。自启的目标是托盘模式(不带参数的 exe)。
源码运行(非打包)时 sys.executable 是 python.exe,自启没有意义,available=False。
"""
from __future__ import annotations

import sys
from typing import Callable, Protocol

# 注册表 Run 键下的值名,也用来判断"是不是我们写的"
RUN_VALUE_NAME = "FistAlarm"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class Registry(Protocol):
    """注册表 Run 键的最小读写接口,便于测试注入假后端。"""

    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class _WinregRegistry:
    """真实后端:操作 HKCU 的 Run 键。"""

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return value
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass  # 键或值本就不存在,当作已关闭


def _default_launch_command() -> str:
    """开机要执行的命令:打包后就是 exe 自身(带引号防路径含空格)。"""
    return f'"{sys.executable}"'


def _default_available() -> bool:
    """仅 Windows 且已打包成 exe 时,自启才有意义。"""
    return sys.platform == "win32" and getattr(sys, "frozen", False)


class AutoStart:
    """开机自启开关。is_enabled / enable / disable / set,外加 available 属性。"""

    def __init__(
        self,
        registry: Registry | None = None,
        launch_command: Callable[[], str] = _default_launch_command,
        available: bool | None = None,
    ) -> None:
        self.registry = registry or _WinregRegistry()
        self._launch_command = launch_command
        self.available = _default_available() if available is None else available

    def is_enabled(self) -> bool:
        if not self.available:
            return False
        return self.registry.get(RUN_VALUE_NAME) is not None

    def enable(self) -> None:
        if not self.available:
            return
        # 总是写当前命令:exe 换过位置时,旧路径会被刷新成新路径
        self.registry.set(RUN_VALUE_NAME, self._launch_command())

    def disable(self) -> None:
        if not self.available:
            return
        self.registry.delete(RUN_VALUE_NAME)

    def set(self, enabled: bool) -> None:
        self.enable() if enabled else self.disable()
