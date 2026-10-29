# -*- mode: python ; coding: utf-8 -*-
"""FistAlarm 打包配置。

打成单文件(onefile)、无控制台窗口(windowed,托盘程序不该弹黑框)。
资源(HTML、拳头 PNG)按 fistalarm/ui 的相对结构收进包,运行时经 paths.resource() 定位。

打包:
    python -m PyInstaller FistAlarm.spec --noconfirm
产物:
    dist/FistAlarm.exe
"""

datas = [
    ("fistalarm/ui/strike.html", "fistalarm/ui"),
    ("fistalarm/ui/manager.html", "fistalarm/ui"),
    ("fistalarm/ui/assets/fist.png", "fistalarm/ui/assets"),
]

# pywebview / pystray 的后端有时不被静态分析发现,显式声明以防漏收
hiddenimports = [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "pystray._win32",
    "clr_loader",
]

a = Analysis(
    ["run_fistalarm.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FistAlarm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 托盘程序:不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
