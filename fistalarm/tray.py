"""托盘常驻模式。

托盘图标是常驻入口:后台线程跑调度循环,菜单里能开管理界面、试播、退出。

为什么调度放后台线程而不是主线程:pywebview 开窗口时会阻塞在它自己的
事件循环里,主线程腾不出来。调度循环必须独立跑。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__, updater
from .runner import Runner, RunnerHooks
from .store import AlarmStore, default_data_dir

ICON_NAME = "FistAlarm"


def tray_log(message: str) -> None:
    """写一行到 %APPDATA%\\FistAlarm\\tray.log。

    打包后的 exe 没有控制台,后台线程里的异常不会显示在任何地方;
    不写日志的话,出问题时外面只能看到"点了没反应"(实际遇到过)。
    """
    try:
        log_dir = default_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "tray.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass  # 日志失败不能影响托盘

# 启动后多久做第一次更新检查(别和开机时一堆程序抢网络),之后每隔多久查一次
UPDATE_FIRST_DELAY_SECONDS = 30
UPDATE_INTERVAL_SECONDS = 24 * 60 * 60

# 托盘图标:程序化画一个,避免为一个图标文件引入额外资源依赖
def _make_icon_image():
    """画一个简单的拳头图标(32×32)。返回 PIL Image。"""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 深色圆底
    draw.ellipse((2, 2, size - 2, size - 2), fill=(22, 28, 36, 255))
    # 白色圆角方块(拳头的简化形)
    draw.rounded_rectangle((18, 20, 46, 44), radius=8, fill=(240, 245, 250, 255))
    # 四个指节
    for i in range(4):
        x = 20 + i * 7
        draw.rounded_rectangle((x, 14, x + 5, 24), radius=2, fill=(240, 245, 250, 255))
    # 拇指
    draw.rounded_rectangle((14, 30, 22, 40), radius=4, fill=(210, 220, 232, 255))
    return image


class TrayApp:
    """托盘应用。持有 store、runner 和后台线程。"""

    def __init__(self, store: AlarmStore | None = None) -> None:
        self.store = store or AlarmStore()
        self.store.load()

        self._stop_event = threading.Event()
        # messages 必须早于 _make_hooks() 初始化:那里要用 self.messages.append。
        self.messages: list[str] = []
        self.runner = Runner(self.store, self._make_hooks())
        self._thread: threading.Thread | None = None

        # ---- 检查更新 ----
        # checker / notifier / on_update_changed 都可替换:测试传假的,
        # run_tray 里接上真实的托盘通知与菜单刷新。
        self.update_checker: Callable[[], updater.CheckResult] = updater.check_for_update
        self.notifier: Callable[[str, str], None] | None = None
        # 手动检查用的对话框:dialog(title, message, ask) -> 用户是否点了「是」。
        # 弹窗通知在 Win11 上会被吞掉,手动检查必须有看得见的反馈。
        self.dialog: Callable[[str, str, bool], bool] | None = None
        self.on_update_changed: Callable[[], None] | None = None
        self.update_info: updater.CheckResult | None = None
        self._notified_tag: str | None = None
        # 托盘菜单里常驻显示的"最近一次检查结果"。系统气泡通知可能被专注助手/
        # 勿扰模式吞掉(实际遇到:点检查更新没任何反应),所以结果必须也能在
        # 菜单里看到,不只靠气泡。
        self.update_status_text: str = f"更新：未检查（当前 v{__version__}）"

    def _make_hooks(self) -> RunnerHooks:
        hooks = RunnerHooks()
        hooks.on_error = self.messages.append
        return hooks

    # ---------- 生命周期 ----------

    def start_scheduler(self) -> None:
        """在后台线程里跑调度循环。"""

        def loop() -> None:
            import os

            last_mtime = 0.0
            while not self._stop_event.is_set():
                try:
                    # 关键:管理窗口是独立进程,改了 alarms.json(文案/频率/闹钟)后,
                    # 托盘必须重新读盘,否则它会用启动时的旧内存在下次 save 时把
                    # 用户的修改覆盖回去(实锤过:改"该"变回"改"、改1分钟变回5分钟)。
                    # 只在文件真的变过时 reload,不必每秒重复读。
                    try:
                        mtime = os.path.getmtime(self.store.path)
                        if mtime != last_mtime:
                            self.store.load()
                            last_mtime = mtime
                    except OSError:
                        pass  # 文件暂不存在等,忽略
                    self.runner.tick_once()
                except Exception as exc:
                    self.messages.append(f"调度出错：{exc}")
                # 用 wait 而不是 sleep:退出时能立刻响应,不用等这一秒走完
                self._stop_event.wait(1.0)

        self._thread = threading.Thread(target=loop, daemon=True, name="fistalarm-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.runner.stop()

    # ---------- 菜单动作 ----------

    def open_manager(self) -> None:
        """打开管理界面——用独立子进程,绝不在托盘进程里开窗(否则 GUI 事件循环冲突会死锁)。"""
        from . import player

        player.launch_manager()

    def test_strike(self) -> None:
        """立刻试播一次动画,用来调效果。"""
        from . import capture, player

        shot: Path | None = None
        try:
            shot = capture.capture_primary_screen(
                self.store.path.parent / "shots" / "preview.png"
            )
        except capture.CaptureError:
            shot = None
        # 试播用全局默认文案与停留时长,让你看到真实触发时的样子
        player.run_strike(
            self.store.default_message, shot, hold_seconds=self.store.fist_hold_seconds
        )

    def upcoming_text(self) -> str:
        """托盘菜单里显示的"接下来"。"""
        pairs = self.runner.upcoming(limit=3)
        if not pairs:
            return "（没有启用的闹钟）"
        lines = []
        for alarm, moment in pairs:
            if moment is None:
                lines.append(f"{alarm.time:%H:%M} {alarm.repeat_label()}（已过期）")
            else:
                delta = moment - datetime.now()
                minutes = int(delta.total_seconds() // 60)
                if minutes < 60:
                    when = f"{minutes} 分钟后"
                else:
                    when = f"{minutes // 60} 小时 {minutes % 60} 分钟后"
                lines.append(f"{alarm.time:%H:%M} {alarm.repeat_label()} — {when}")
        return "\n".join(lines)

    # ---------- 检查更新 ----------

    @property
    def update_available(self) -> bool:
        return self.update_info is not None and self.update_info.status == "update"

    def update_menu_text(self) -> str:
        if self.update_available:
            return f"发现新版本 {self.update_info.latest}，点击下载"
        return ""

    def _notify(self, message: str) -> None:
        if self.notifier is not None:
            try:
                self.notifier("拳头闹钟", message)
            except Exception as exc:
                tray_log(f"弹窗通知失败：{exc!r}")  # 通知失败不影响闹钟

    def _refresh_menu(self) -> None:
        if self.on_update_changed is not None:
            try:
                self.on_update_changed()
            except Exception as exc:
                tray_log(f"刷新托盘菜单失败：{exc!r}")

    def run_update_check(self, manual: bool = False) -> None:
        """查一次更新。

        结果有两个去处:
        - **托盘菜单的状态项**(update_status_text):每次检查都更新,点开菜单就能看到。
        - **系统气泡通知**(_notify):自动检查只在发现没提醒过的新版时弹;手动检查都弹。
          气泡可能被系统的勿扰/专注模式吞掉,所以不能只靠它——菜单状态项是兜底。

        任何异常都在这里接住并变成"检查失败":它跑在后台线程里,
        exe 又没有控制台,漏出去的异常不会留下任何痕迹。
        """
        tray_log(f"开始检查更新(manual={manual},当前 v{__version__})")
        if manual:
            self.update_status_text = "更新：正在检查…"
            self._refresh_menu()

        try:
            result = self.update_checker()
        except Exception as exc:
            result = updater.CheckResult("error", error=str(exc) or exc.__class__.__name__)
        tray_log(f"检查结果:status={result.status} latest={result.latest} error={result.error}")

        if result.status == "update":
            self.update_info = result
            self.update_status_text = f"发现新版本 {result.latest}（当前 v{__version__}）"
        elif result.status == "latest":
            self.update_status_text = f"已是最新版本（v{__version__}）"
        else:  # error
            self.update_status_text = f"更新：检查失败（{result.error}）"
        self._refresh_menu()

        if manual and self.dialog is not None:
            self._show_manual_result(result)
            return

        # 没有对话框(自动检查,或测试里未注入)时退回弹窗通知
        if result.status == "update":
            if manual or result.latest != self._notified_tag:
                self._notify(f"发现新版本 {result.latest}（当前 v{__version__}），右键托盘图标点「下载」")
                self._notified_tag = result.latest
        elif manual and result.status == "latest":
            self._notify(f"已是最新版本（v{__version__}）")
        elif manual:
            self._notify(f"检查更新失败：{result.error}")

    def _show_manual_result(self, result: updater.CheckResult) -> None:
        """手动检查:用对话框告诉用户结果;有新版时问要不要打开下载页。"""
        try:
            if result.status == "update":
                self._notified_tag = result.latest
                yes = self.dialog(
                    "拳头闹钟 - 检查更新",
                    f"发现新版本 {result.latest}（当前 v{__version__}）。\n\n是否打开下载页？",
                    True,
                )
                if yes:
                    self.open_download_page()
            elif result.status == "latest":
                self.dialog("拳头闹钟 - 检查更新", f"已是最新版本（v{__version__}）。", False)
            else:
                self.dialog("拳头闹钟 - 检查更新", f"检查更新失败：{result.error}", False)
        except Exception as exc:
            tray_log(f"显示检查结果对话框失败：{exc!r}")

    def start_update_checker(self) -> None:
        """后台线程:启动后延迟一会儿查一次,之后每天查一次。"""

        def loop() -> None:
            if self._stop_event.wait(UPDATE_FIRST_DELAY_SECONDS):
                return
            while True:
                try:
                    self.run_update_check(manual=False)
                except Exception as exc:
                    self.messages.append(f"检查更新出错：{exc}")
                if self._stop_event.wait(UPDATE_INTERVAL_SECONDS):
                    return

        threading.Thread(target=loop, daemon=True, name="fistalarm-updater").start()

    def open_download_page(self) -> None:
        """用浏览器打开新版本的下载页(没查到新版时打开发布列表)。"""
        import webbrowser

        url = self.update_info.url if self.update_available else updater.RELEASES_URL
        webbrowser.open(url)


def win_message_box(title: str, message: str, ask: bool) -> bool:
    """Windows 自带消息框,置顶显示。ask=True 时是"是/否",返回是否点了"是"。

    会阻塞调用它的线程,所以只在检查更新的后台线程里调,不卡托盘。
    """
    import ctypes

    MB_YESNO, MB_ICONINFORMATION, MB_ICONQUESTION = 0x4, 0x40, 0x20
    MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x40000, 0x10000, 6
    flags = MB_TOPMOST | MB_SETFOREGROUND
    flags |= (MB_YESNO | MB_ICONQUESTION) if ask else MB_ICONINFORMATION
    return ctypes.windll.user32.MessageBoxW(None, message, title, flags) == IDYES


def run_tray(store: AlarmStore | None = None) -> int:
    """启动托盘应用,阻塞直到用户退出。返回进程退出码。"""
    try:
        import pystray  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("未安装 pystray") from exc

    app = TrayApp(store)
    app.start_scheduler()

    def on_manager(_icon, _item) -> None:
        app.open_manager()

    def on_test(_icon, _item) -> None:
        app.test_strike()

    def on_quit(icon, _item) -> None:
        app.stop()
        icon.stop()

    def on_download(_icon, _item) -> None:
        app.open_download_page()

    def on_check_update(_icon, _item) -> None:
        tray_log("收到点击:检查更新")

        def work() -> None:
            try:
                app.run_update_check(manual=True)
            except Exception as exc:  # 兜底:后台线程的异常在 exe 里会无声消失
                tray_log(f"检查更新线程异常：{exc!r}")

        # 放到后台线程:网络请求最多等 10 秒,别卡住托盘菜单
        threading.Thread(target=work, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: app.upcoming_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda _item: app.update_menu_text(),
            on_download,
            visible=lambda _item: app.update_available,
        ),
        pystray.MenuItem("闹钟管理…", on_manager, default=True),
        pystray.MenuItem("试播(用默认文案)", on_test),
        pystray.Menu.SEPARATOR,
        # 常驻显示最近一次检查结果(灰色只读):气泡被系统吞了也能在这里看到
        pystray.MenuItem(lambda _item: app.update_status_text, None, enabled=False),
        pystray.MenuItem("检查更新", on_check_update),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )

    icon = pystray.Icon(ICON_NAME, _make_icon_image(), "拳头闹钟", menu)
    app.notifier = lambda title, message: icon.notify(message, title)
    app.dialog = win_message_box
    app.on_update_changed = icon.update_menu
    app.start_update_checker()
    icon.run()
    return 0
