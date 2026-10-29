"""FistAlarm 的界面层。

这里的代码依赖 pywebview(Windows 上走系统自带的 WebView2)。

- `strike.html`   —— 碎屏动画页,全屏播放,由 strike_player.py 加载
- `manager.py`    —— 管理窗口的 Python 侧(bridge)
- `manager.html`  —— 管理窗口的页面

界面层刻意与逻辑层(models/schedule/store/runner)分开:逻辑层不导入这里,
所以逻辑层的测试不需要装 pywebview 就能跑。
"""
