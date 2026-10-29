from __future__ import annotations

"""进程清理工具。

pywebview 在 Windows 上用 WebView2,它会派生多个 msedgewebview2.exe 子进程。
当本进程结束时,这些子进程**不保证**被系统回收——尤其是进程被强杀时,
它们会变成孤儿留在后台,累积起来吃内存、拖慢机器(实际踩过:攒到几十个)。

这里提供一个务实的兜底:在打开窗口前记录已存在的 WebView2 进程,关闭后把
"本次新冒出来的"杀掉。只杀新增的,避免误伤用户其他正在用 WebView2 的程序。

仅 Windows 有效(本程序本就是 Windows 专用);其他平台下所有函数都是空操作。
"""

import subprocess
import sys

_WEBVIEW_IMAGE = "msedgewebview2.exe"

# 打包成 windowed exe 后,subprocess 拉 tasklist/taskkill 会各弹一个控制台黑框。
# CREATE_NO_WINDOW 抑制它。仅 Windows 有此标志。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _is_windows() -> bool:
    return sys.platform == "win32"


def webview_pids() -> set[int]:
    """返回当前所有 msedgewebview2.exe 的 PID 集合。取不到时返回空集。"""
    if not _is_windows():
        return set()
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_WEBVIEW_IMAGE}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        ).stdout
    except Exception:
        return set()

    pids: set[int] = set()
    for line in out.splitlines():
        # CSV 行形如: "msedgewebview2.exe","12345","Console","1","50,000 K"
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() == _WEBVIEW_IMAGE:
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def kill_pids(pids: set[int]) -> None:
    """强杀指定 PID 及其子进程树。

    用**一次** taskkill 批量传入所有 PID(而不是每个 PID 起一次进程):
    窗口关闭时往往同时有多个 WebView2 子进程要清,逐个起进程会很慢
    (每个进程启动都可能被杀软扫描拖住,表现为"关窗口卡一下")。
    """
    if not _is_windows() or not pids:
        return
    args = ["taskkill"]
    for pid in pids:
        args.extend(["/PID", str(pid)])
    args.extend(["/T", "/F"])
    try:
        subprocess.run(
            args, capture_output=True, timeout=15, check=False, creationflags=_NO_WINDOW,
        )
    except Exception:
        pass


_SINGLE_INSTANCE_HANDLE = None


def acquire_single_instance(name: str) -> bool:
    """单实例锁。抢到返回 True;已有同名实例在跑返回 False。

    用 Windows 命名 mutex:进程退出时系统自动释放,不留残留锁文件
    (锁文件方案怕崩溃后残留导致再也开不起来)。句柄存模块全局,防止被 GC
    提前释放——必须活到进程结束。非 Windows 一律返回 True(不拦)。
    """
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True  # 创建失败就别拦,宁可多开也别开不出来
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _SINGLE_INSTANCE_HANDLE = handle  # 持有到进程结束
        return True
    except Exception:
        return True


class WebviewReaper:
    """上下文管理器:进入时记录已有 WebView2,退出时清掉本次新增的。

    用法:
        with WebviewReaper():
            webview.start()
        # start() 返回(窗口关闭)后,本次窗口派生的 WebView2 子进程被回收
    """

    def __enter__(self) -> WebviewReaper:
        self._before = webview_pids()
        return self

    def __exit__(self, *exc) -> None:
        new = webview_pids() - self._before
        kill_pids(new)
        return None
