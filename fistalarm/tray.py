"""托盘常驻模式。

托盘图标是常驻入口:后台线程跑调度循环,菜单里能开管理界面、试播、退出。

为什么调度放后台线程而不是主线程:pywebview 开窗口时会阻塞在它自己的
事件循环里,主线程腾不出来。调度循环必须独立跑。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from .runner import Runner, RunnerHooks
from .store import AlarmStore

ICON_NAME = "FistAlarm"

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

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: app.upcoming_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("闹钟管理…", on_manager, default=True),
        pystray.MenuItem("试播(用默认文案)", on_test),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )

    icon = pystray.Icon(ICON_NAME, _make_icon_image(), "拳头闹钟", menu)
    icon.run()
    return 0
