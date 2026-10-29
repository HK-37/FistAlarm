# FistAlarm — 拳头闹钟

> Windows 桌面「久坐提醒 / 闹钟」。到点后屏幕会被一只拳头砸碎,碎片飞散,再浮现提醒文字——用一记"实感冲击"提醒你起来活动,而不是温吞吞弹个框。

```
    拳头砸入  →  冲击波 + 裂纹蔓延  →  截图像玻璃一样碎成多边形掉落  →  显示提醒
```

## 特性

- **真的砸碎你的屏幕**——触发时先给桌面截一张图,然后把这张图当玻璃砸碎。看起来就是屏幕被击穿,不是弹个对话框。
- **两类提醒方式**:
  - **固定时间点**——每天 / 工作日 / 自选星期 / 仅一次。
  - **每隔一段时间(久坐提醒)**——从一个开始时间起,每隔 N 分钟提醒一次,可设结束时间(当天到点停、次日再来)。
- **对表计时,不靠 sleep**——电脑睡眠唤醒后不会漏掉闹钟。
- **提醒文案、拳头停留时长都可配**——在管理界面里改,改完即时生效。
- **截图失败也能响**——全屏独占程序等场景截不到图时,自动降级为纯黑背景,提醒照常。
- **托盘常驻**——托盘菜单能看"接下来要响的几条"、开管理界面、试播。
- **一个 exe 两种模式**——托盘常驻与动画播放共用同一个可执行文件。

## 安装与运行

### 方式一:直接用打包好的 exe

从 [Releases](../../releases) 下载 `FistAlarm.exe`,双击即可。托盘出现拳头图标后,右键 → 闹钟管理。

Windows 上需要 WebView2 运行时——Win11 自带;Win10 若缺失,装一次
[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 即可。

### 方式二:从源码运行

需要 Python 3.11+(用到 `StrEnum`)。

```bash
python -m pip install -r requirements.txt

# 托盘常驻(日常用这个)
python -m fistalarm

# 立刻试播一次动画
python -m fistalarm --test

# 命令行管理(调试用)
python -m fistalarm --list
python -m fistalarm --add 09:00 --message "开晨会"
```

### 自己打包 exe

```bash
python -m pip install pyinstaller
python -m PyInstaller FistAlarm.spec --noconfirm
# 产物:dist/FistAlarm.exe
```

## 数据与日志

| 内容 | 位置 |
|------|------|
| 闹钟列表 | `%APPDATA%\FistAlarm\alarms.json` |
| 播放日志 | `%APPDATA%\FistAlarm\player.log` |
| 截图缓存 | `%APPDATA%\FistAlarm\shots\` |

`alarms.json` 是可读的 JSON,可以直接手改。文件损坏时程序不会静默覆盖,而是改名成
`alarms.json.bak-<时间戳>` 保留下来,再以空列表启动——闹钟不会因为一次解析错误就永久消失。

## 结构

```
fistalarm/
├── models.py         闹钟的数据结构(不含计算)
├── schedule.py       纯计算:下一次什么时候响(整个程序最容易出错的地方)
├── store.py          持久化:原子写 + 坏文件备份 + 全局配置(默认文案/停留时长)
├── capture.py        截屏(唯一依赖 mss 的地方,失败要能降级)
├── player.py         拉起播放器子进程 / 管理窗口子进程,读退出码
├── runner.py         编排:对表 → 截图 → 播放
├── tray.py           托盘常驻(后台线程跑调度,文件变了就重新读盘)
├── strike_player.py  动画播放器:全屏窗口 + WebView2
├── procutil.py       进程工具:单实例锁等
├── paths.py          资源路径(兼容源码运行与 PyInstaller 打包)
├── ui/
│   ├── strike.html     碎屏动画(Voronoi 切割 + Canvas)
│   ├── manager.py      管理窗口的 Python 侧(独立进程)
│   └── manager.html    管理页面
│   └── assets/fist.png 拳头素材
└── __main__.py       入口:按参数分发(托盘 / --strike 播放 / --manage 管理)
```

**分层原则**:`models / schedule / store / capture / player / runner` 是逻辑层,
不导入 `ui/` 与 pywebview。所以跑测试不需要装 UI 依赖。

**为什么管理窗口和动画都是独立子进程**:pywebview 的 GUI 事件循环必须独占主线程,
在托盘进程里直接开窗会和 pystray 的事件循环抢消息泵而死锁。拆成子进程后互不干扰,
子进程退出即被系统回收。

## 测试

```bash
python -m pytest tests/ -q
```

155 个用例,覆盖跨日、跨周(周末跳过)、月末、年末、间隔模式各种边界、单次闹钟生命周期、
截图失败降级、同分钟内不重复触发、多进程共享文件不互相覆盖等。

动画是视觉产物,靠 `--test` 人工验收,不适合断言。

## 开发提示

- 动画页支持 `?debug` 参数,会出现"重播 / 跳到结尾"按钮,便于反复调效果。
- 改动画时优先调 `ui/strike.html` 顶部的 `cfg`(碎片密度、屏震强度)与 `T`(时序)。
- `player.log` 记录了每次播放各节点的耗时,排查"卡顿"时看它。

## 素材说明

`fistalarm/ui/assets/fist.png` 的拳头图由 AI 生成。你可以换成任意等价的透明背景 PNG
(替换这个文件即可),动画会自动使用;文件缺失时回退为代码绘制的矢量拳头。

## License

[MIT](LICENSE) © 2026 HK-37
