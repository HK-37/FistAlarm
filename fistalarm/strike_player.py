"""提醒播放器 —— 全屏窗口 + 碎屏动画。

进这个模式的方式是命令行带 --strike,由托盘端拉起,播完即退。

**退出码约定**(托盘端据此决定要不要贪睡,见 player.py):
    点"关闭"   → 0
    点"贪睡"   → 2

窗口用 pywebview(Windows 上是系统自带的 WebView2),动画是纯 HTML/Canvas。
这样做的理由:碎屏效果在 Web 动画里最好做,而 pywebview 让这层不需要
额外背一个 Electron 的体积。
"""
from __future__ import annotations

import time as _time_module
from pathlib import Path

from .paths import resource

# 进程启动早期的时刻(import 本模块时)。_log 用它算"进程已运行多久",
# 方便定位偶发卡顿到底卡在哪一步(进程启动 / WebView2 初始化 / 页面加载)。
_T0 = _time_module.perf_counter()


class PlayerError(RuntimeError):
    """播放器起不来(缺 pywebview、WebView2 不可用等)。"""


# 这两个码是与托盘端的契约,改这里必须同步改 player.py
EXIT_CLOSED = 0
EXIT_SNOOZE = 2

# 动画页位置(兼容源码运行与 PyInstaller 打包)
_PAGE = resource("ui", "strike.html")


def _log(message: str) -> None:
    """把播放器行为记到日志文件,带毫秒时间戳 + 进程相对耗时。

    播放器是独立进程,窗口一闪而过时看不到控制台输出;
    出问题时没有日志就只能靠猜。路径与 alarms.json 同目录。
    末尾 [+N.NNs] 是从进程启动到这一步的累计耗时,用来定位偶发卡顿节点。
    """
    import os  # noqa: F401  (保留:历史依赖,无副作用)

    from .store import default_data_dir

    try:
        elapsed = _time_module.perf_counter() - _T0
        log_dir = default_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "player.log").open("a", encoding="utf-8") as handle:
            stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            handle.write(f"[{stamp}] [+{elapsed:5.2f}s] {message}\n")
    except Exception:
        pass  # 日志失败不能影响播放


def play(message: str, shot_path: Path | None, hold_seconds: float | None = None) -> int:
    """开全屏窗口播碎屏动画,返回退出码。

    shot_path 为 None 时动画自动切纯黑背景(截图失败的降级路径)。
    hold_seconds 为拳头停留秒数(None 时页面用自己的默认 2 秒)。
    """
    try:
        import webview  # type: ignore[import-untyped]
    except ImportError as exc:
        raise PlayerError("未安装 pywebview,无法播放提醒动画") from exc

    if not _PAGE.exists():
        raise PlayerError(f"找不到动画页面：{_PAGE}")

    _log(f"开始播放：message={message!r} shot={shot_path}")

    # 把结果从 JS 回调里带出来。pywebview 的 js_api 无法直接改变进程退出码,
    # 所以用一个可变容器接住,等窗口关闭后再读。
    outcome = {"code": EXIT_CLOSED}

    class Api:
        """暴露给页面的接口。页面通过 pywebview.api.close_window() 之类调用。"""

        def close_window(self) -> None:
            outcome["code"] = EXIT_CLOSED
            _log("用户选择：关闭")
            window.destroy()

        def log(self, text: str) -> None:
            """页面端回传计时/诊断,统一记进 player.log(带进程相对耗时)。"""
            _log(f"[页面] {text}")

    window = webview.create_window(
        title="FistAlarm",
        url=_PAGE.as_uri(),
        js_api=Api(),
        fullscreen=True,
        frameless=True,
        on_top=True,
        background_color="#000000",
        # 不显示右键菜单/缩放条,避免破坏"整个屏幕被砸碎"的错觉
        easy_drag=False,
    )
    _log("窗口已创建")

    # 页面加载完再把数据递进去:url 传参对本地文件受限,用 evaluate_js 更稳。
    def _inject() -> None:
        shot_url = shot_path.as_uri() if shot_path and shot_path.exists() else ""
        _log(f"页面已加载，注入数据 shot_url={shot_url!r} hold={hold_seconds}")
        hold_js = "null" if hold_seconds is None else repr(float(hold_seconds))
        window.evaluate_js(
            f"window.__fistalarm_init("
            f"{_js_string(shot_url)}, {_js_string(message)}, {hold_js})"
        )

    # 页面里的 JS 报错也要能看到——否则页面白屏了也没人知道
    def _on_js_error(*args) -> None:
        _log(f"页面 JS 报错：{args}")

    window.events.loaded += _inject
    try:
        window.events.error += _on_js_error
    except Exception:
        pass

    gui_backend = "edgechromium" if _has_edge() else None
    _log(f"准备启动 webview：gui={gui_backend!r} page={_PAGE}")

    # 不再用 WebviewReaper 清理 WebView2 子进程:它按 PID 差集 taskkill /T,
    # 在多个窗口并发时会误杀正在启动的动画进程(实锤过:自动触发黑屏的根因)。
    # WebView2 子进程在本进程正常退出时会被系统回收,不需要激进清理。
    try:
        webview.start(gui=gui_backend)
    except Exception as exc:
        _log(f"❌ webview.start 抛异常：{exc!r}")
        raise
    _log(f"窗口关闭，返回退出码 {outcome['code']}")
    return outcome["code"]


def _has_edge() -> bool:
    """是否可以用 Edge Chromium 后端(Windows 上需要 WebView2 运行时)。

    检测不到时返回 False,交给 pywebview 自己挑后端,
    而不是硬报错——用户机器上可能装了别的可用后端。
    """
    import os
    import sys

    if sys.platform != "win32":
        return False
    program_files = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles")
    if not program_files:
        return False
    runtime = Path(program_files) / "Microsoft" / "EdgeWebView" / "Application"
    return runtime.exists()


def _js_string(text: str) -> str:
    """把 Python 字符串转成安全的 JS 字面量。

    用 json.dumps 而不是手写引号拼接:备注里出现引号、反斜杠、换行时
    拼接会直接语法错误或注入。
    """
    import json

    return json.dumps(text, ensure_ascii=False)
