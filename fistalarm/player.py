"""提醒播放器的调用与结果约定。

托盘端和播放器之间用**退出码**通信,不用 socket / 文件:
- 播放器是独立子进程,崩了也不会拖垮托盘
- 退出码是操作系统级的,不需要额外依赖
- 语义简单,一眼能懂

约定:
    0  用户点了关闭(或动画播完正常退出)
    2  用户点了"贪睡 5 分钟"
    其他 异常退出,托盘端按"关闭"处理并记一条日志
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXIT_SNOOZE = 2
EXIT_CLOSED = 0

# 打包成 windowed exe 后,用 subprocess 拉子进程会新建一个控制台黑框。
# CREATE_NO_WINDOW 让子进程不带控制台。仅 Windows 有这个标志。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class StrikeResult:
    """一次提醒的结果。"""

    def __init__(self, returncode: int, snooze_minutes: int = 5) -> None:
        self.returncode = returncode
        self.snooze_minutes = snooze_minutes

    @property
    def snoozed(self) -> bool:
        """用户是否选择了贪睡。异常退出(崩溃/被强杀)不算贪睡。"""
        return self.returncode == EXIT_SNOOZE

    @property
    def failed(self) -> bool:
        return self.returncode not in (EXIT_CLOSED, EXIT_SNOOZE)


def build_strike_command(
    message: str, shot_path: Path | None, hold_seconds: float | None = None
) -> list[str]:
    """拼出拉起播放器的命令行。

    独立成函数是为了可测——不用真的起进程就能验证参数拼装。
    用 sys.executable 保证在打包成 exe 后也指向自己。
    """
    args: list[str] = []
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后,sys.executable 就是 FistAlarm.exe 本身
        args.append(sys.executable)
    else:
        args.extend([sys.executable, "-m", "fistalarm"])
    # 消息必须紧跟 --strike 作为它的值。若放到命令行末尾当位置参数,
    # argparse 会因为 --strike 后面直接是 --shot 而判定 --strike 没取到值,
    # 末尾的中文就成了"无法识别的参数"(实际踩过:管理界面点试播崩在这里)。
    args.extend(["--strike", message or ""])
    if shot_path is not None:
        args.extend(["--shot", str(shot_path)])
    if hold_seconds is not None:
        args.extend(["--hold", str(hold_seconds)])
    return args


def build_manage_command() -> list[str]:
    """拼出拉起「管理窗口」子进程的命令行。

    管理窗口必须独立进程:pywebview.start() 要独占主线程的 GUI 事件循环,
    在托盘进程里开窗会和 pystray 的事件循环抢消息泵而死锁(实际踩过假死)。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--manage"]
    return [sys.executable, "-m", "fistalarm", "--manage"]


# 管理窗口的标题。单例检测靠它:已有同标题窗口就激活,不再开新的。
# 必须与 ui/manager.py 里 create_window(title=...) 完全一致。
MANAGER_WINDOW_TITLE = "拳头闹钟 — 管理"


def _activate_existing_manager() -> bool:
    """若管理窗口已开着,把它拉到前台并返回 True;没有则返回 False。

    用 Win32 FindWindow 按标题找窗口——不依赖 psutil,打包后也可靠。
    非 Windows 直接返回 False(退化为每次新开)。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, MANAGER_WINDOW_TITLE)
        if not hwnd:
            return False
        # 若最小化了先还原,再置前
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def launch_manager() -> None:
    """打开管理窗口。已经开着就激活那一个(单例),否则起一个新子进程。

    不等待子进程结束——托盘不能被它阻塞。
    """
    if _activate_existing_manager():
        return
    # --manage 是 GUI 子进程,不加 CREATE_NO_WINDOW(会伤 WebView2,同 run_strike)。
    # 打包后 FistAlarm.exe windowed,无控制台黑框。
    subprocess.Popen(build_manage_command(), close_fds=True)


def run_strike(
    message: str,
    shot_path: Path | None,
    timeout: float = 300.0,
    hold_seconds: float | None = None,
) -> StrikeResult:
    """拉起播放器并等它结束,返回结果。

    ``timeout`` 是保险:万一播放器卡死,不能让托盘端一起卡住。
    超时按"关闭"处理。
    """
    command = build_strike_command(message, shot_path, hold_seconds)
    try:
        # 不加 CREATE_NO_WINDOW:--strike 是 GUI 子进程(要显示全屏动画),
        # 这个标志会破坏子进程里 WebView2 的消息环境,导致页面 loaded 事件不触发、
        # 黑屏一下就结束(实际踩过:自动触发黑屏、手动命令行正常)。
        # 打包后的 FistAlarm.exe 是 windowed,拉起自身 --strike 本就无控制台黑框,
        # 不需要这个标志。黑框只需在 tasklist/taskkill 那类控制台工具上抑制。
        completed = subprocess.run(command, timeout=timeout)
        return StrikeResult(completed.returncode)
    except subprocess.TimeoutExpired:
        return StrikeResult(EXIT_CLOSED)
    except Exception:
        return StrikeResult(EXIT_CLOSED)
