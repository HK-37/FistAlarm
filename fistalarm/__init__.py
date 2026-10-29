"""FistAlarm —— 拳头闹钟。

到点触发"拳头击碎屏幕"提醒的 Windows 桌面闹钟。

模块划分:
    models     数据结构(闹钟长什么样)
    schedule   纯计算(下一次什么时候响)
    store      持久化(alarms.json 读写)
    runner     触发流程(截图 → 拉起播放器 → 处理贪睡)
    tray       托盘常驻
    ui         管理界面
"""
from __future__ import annotations

__version__ = "0.1.0"
