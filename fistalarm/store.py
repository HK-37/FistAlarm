"""闹钟数据的持久化。

存成一份 JSON,默认落在 %APPDATA%\\FistAlarm\\alarms.json。

三条约束:
1. **原子写**——先写临时文件再替换,避免掉电/崩溃留下半个文件。
2. **坏文件不静默吞掉**——解析失败时把原文件改名备份,而不是覆盖,
   用户的闹钟不该因为一次解析错误就永久丢失。
3. **纯逻辑,不碰 UI**——便于单测。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .models import Alarm

APP_DIR_NAME = "FistAlarm"
DATA_FILE_NAME = "alarms.json"

# 某条闹钟没填备注时,碎屏动画上显示的兜底文案。
# 可在配置界面里改,改后写进 alarms.json 的 default_message 字段。
FACTORY_DEFAULT_MESSAGE = "改起来活动活动了！"

# 拳头命中后停留多久再缓缓收回(秒)。可在配置界面改,限定 1~3 秒。
FACTORY_FIST_HOLD_SECONDS = 2.0
FIST_HOLD_MIN = 1.0
FIST_HOLD_MAX = 3.0


def clamp_hold_seconds(value: float) -> float:
    """把停留秒数夹到合法区间;非数值回退出厂值。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return FACTORY_FIST_HOLD_SECONDS
    return max(FIST_HOLD_MIN, min(FIST_HOLD_MAX, v))


def default_data_dir() -> Path:
    """返回数据目录。优先 %APPDATA%,取不到时退回到用户主目录。"""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


class AlarmStore:
    """闹钟的读写与增删改。

    内存里持有一份列表,改动后显式调用 save() 落盘。
    这样界面上的连续编辑不会每次都写文件。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_data_dir() / DATA_FILE_NAME
        self.alarms: list[Alarm] = []
        # 全局默认文案:哪条闹钟没写备注,就用它。独立于单条闹钟而存在,
        # 所以放 store 层(整份文件级配置),不塞进每条 Alarm。
        self.default_message: str = FACTORY_DEFAULT_MESSAGE
        # 拳头停留时长(秒),同为文件级配置。
        self.fist_hold_seconds: float = FACTORY_FIST_HOLD_SECONDS

    def message_for(self, alarm: Alarm) -> str:
        """这条闹钟最终要显示的文案:自己的备注优先,空则用全局默认。"""
        return alarm.message.strip() or self.default_message

    # ---------- 读 ----------

    def load(self) -> list[Alarm]:
        """从磁盘读取。文件不存在视为空列表(首次运行)。

        解析失败时:把坏文件备份成 alarms.json.bak-<时间戳>,然后按空列表处理,
        并在返回前把备份路径记在 self.last_backup 里,供界面提示用户。
        """
        self.last_backup: Path | None = None
        if not self.path.exists():
            self.alarms = []
            return self.alarms

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            items = raw.get("alarms", []) if isinstance(raw, dict) else raw
            self.alarms = [Alarm.from_dict(item) for item in items]
            if isinstance(raw, dict) and isinstance(raw.get("default_message"), str):
                # 空字符串没意义,回退到出厂文案
                self.default_message = raw["default_message"].strip() or FACTORY_DEFAULT_MESSAGE
            if isinstance(raw, dict) and "fist_hold_seconds" in raw:
                self.fist_hold_seconds = clamp_hold_seconds(raw["fist_hold_seconds"])
        except Exception:
            self.last_backup = self._backup_broken_file()
            self.alarms = []
        return self.alarms

    def _backup_broken_file(self) -> Path | None:
        """把无法解析的文件改名备份,返回备份路径。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.bak-{stamp}")
        try:
            shutil.move(str(self.path), str(target))
            return target
        except Exception:
            return None

    # ---------- 写 ----------

    def to_json(self) -> str:
        """序列化成 JSON 文本。ensure_ascii=False 让中文备注可读。"""
        payload = {
            "version": 1,
            "default_message": self.default_message,
            "fist_hold_seconds": self.fist_hold_seconds,
            "alarms": [a.to_dict() for a in self.alarms],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def save(self) -> None:
        """原子写:先写 .tmp 再替换,避免留下半个文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------- 增删改 ----------

    def get(self, alarm_id: str) -> Alarm | None:
        for alarm in self.alarms:
            if alarm.id == alarm_id:
                return alarm
        return None

    def add(self, alarm: Alarm) -> Alarm:
        self.alarms.append(alarm)
        return alarm

    def remove(self, alarm_id: str) -> bool:
        """按 id 删除,返回是否真的删掉了。"""
        before = len(self.alarms)
        self.alarms = [a for a in self.alarms if a.id != alarm_id]
        return len(self.alarms) != before

    def replace(self, alarm: Alarm) -> bool:
        """用同 id 的新对象替换旧的,返回是否找到。"""
        for index, existing in enumerate(self.alarms):
            if existing.id == alarm.id:
                self.alarms[index] = alarm
                return True
        return False

    def enabled_alarms(self) -> list[Alarm]:
        return [a for a in self.alarms if a.enabled]

    def sorted_by_time(self) -> list[Alarm]:
        """按时间排序,便于界面展示。"""
        return sorted(self.alarms, key=lambda a: (a.time, a.repeat_label()))
