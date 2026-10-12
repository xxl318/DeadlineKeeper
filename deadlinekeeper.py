#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeadlineKeeper —— 跨平台桌面任务提醒程序（Windows 显示修复版）
========================================================
适用于 macOS / Windows / Linux，无需联网，数据本地保存。

本版修复：
1. Windows 高缩放 / 小屏幕下，新建任务弹窗底部按钮被裁切的问题。
2. 新建任务后可能因为筛选条件导致“看起来没有保存”的问题。
3. 编辑已完成任务时，completed 状态被误重置的问题。
4. Windows 下数据目录改为更稳定的 AppData，并兼容迁移旧数据。
5. 保存数据改为原子写入，降低 tasks.json 损坏风险。
"""

import calendar
import csv
import ctypes
import json
import math
import os
import platform
import random
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    _PIL_OK = True
except Exception:
    _PIL_OK = False


def resource_path(*parts):
    """资源路径：开发时用脚本所在目录，打包后用 PyInstaller 的临时目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_NAME = "DeadlineKeeper"

# ============== 跨平台数据目录 ==============
SYSTEM = platform.system()


def get_data_dir() -> Path:
    """
    Windows: %APPDATA%\\DeadlineKeeper
    macOS:   ~/Library/Application Support/DeadlineKeeper
    Linux:   ~/.deadlinekeeper
    """
    if SYSTEM == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / ".deadlinekeeper"

    if SYSTEM == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    return Path.home() / ".deadlinekeeper"


DATA_DIR = get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "tasks.json"
CONFIG_FILE = DATA_DIR / "config.json"
ARCHIVE_FILE = DATA_DIR / "archive.json"
PET_FILE = DATA_DIR / "pet.json"

# ============== 桌宠 · 奖励系统 ==============
# 积分获取
POINTS_PER_TASK = 5       # 完成一项任务
POINTS_PER_ARCHIVE = 10   # 归档一次
POINTS_CHECKIN = 20       # 每日下班打卡
CHECKIN_REMIND_HOUR = 17
CHECKIN_REMIND_MINUTE = 25
CHECKIN_REMIND_TEXT = f"{CHECKIN_REMIND_HOUR:02d}:{CHECKIN_REMIND_MINUTE:02d}"

# 食物（喂一次消耗积分，回复饱食度）。一日份「日常餐」成本 12，
# 打卡每天稳定 +20，吃一顿后还剩 8，一周约剩 56，够买一件 ~50 的衣服。
PET_FOODS = {
    "richan": {"name": "日常餐", "cost": 12, "fill": 70, "desc": "一日份，回复较多饱食度"},
    "dacan":  {"name": "大餐",   "cost": 20, "fill": 100, "desc": "一次喂饱饱"},
    "xiaoyu": {"name": "小鱼干", "cost": 6,  "fill": 35, "desc": "解馋小零食"},
}

# 衣服 / 装扮（购买一次永久拥有，可随时切换）
PET_OUTFITS = {
    "none":    {"name": "原样",   "cost": 0},
    "glasses": {"name": "墨镜",   "cost": 45},
    "bow":     {"name": "蝴蝶结", "cost": 50},
    "scarf":   {"name": "围巾",   "cost": 55},
    "hat":     {"name": "帽子",   "cost": 65},
    "dress":   {"name": "小裙子", "cost": 90},
}

PET_DECAY_PER_HOUR = 4.0   # 饱食度每小时下降，约一天见底，提醒每天喂食

# 老版本路径。用于自动迁移旧数据，避免升级后任务看起来丢失。
LEGACY_DATA_DIR = Path.home() / ".deadlinekeeper"
LEGACY_DATA_FILE = LEGACY_DATA_DIR / "tasks.json"
LEGACY_CONFIG_FILE = LEGACY_DATA_DIR / "config.json"


def migrate_legacy_data():
    """如果新目录没有数据，但旧目录有数据，则自动迁移。"""
    try:
        if DATA_DIR.resolve() == LEGACY_DATA_DIR.resolve():
            return
    except Exception:
        pass

    try:
        if not DATA_FILE.exists() and LEGACY_DATA_FILE.exists():
            shutil.copy2(LEGACY_DATA_FILE, DATA_FILE)
        if not CONFIG_FILE.exists() and LEGACY_CONFIG_FILE.exists():
            shutil.copy2(LEGACY_CONFIG_FILE, CONFIG_FILE)
    except Exception:
        # 迁移失败不影响程序启动。
        pass


migrate_legacy_data()

PRIORITIES = ["高", "中", "低"]
PRIORITY_RANK = {"高": 0, "中": 1, "低": 2}

# 重复（周期任务）选项
REPEAT_OPTIONS = ["无", "每天", "每周", "每月", "每年"]


def parse_datetime_flexible(s: str):
    """
    更宽容地解析用户输入的时间。
    支持：2026-06-26 09:00 / 2026/6/26 9:00 / 2026.6.26 9:00 /
          2026年6月26日 9点30 / 全角数字 / 只填日期(默认当天 23:59)。
    解析失败返回 None。
    """
    if not s:
        return None
    s = s.strip()
    # 全角数字 / 冒号 / 空格 -> 半角
    s = s.translate(str.maketrans("０１２３４５６７８９：　", "0123456789: "))
    # 中文/其它分隔符统一成标准格式
    for a, b in [("年", "-"), ("月", "-"), ("日", " "),
                 ("点", ":"), ("时", ":"), ("分", ""),
                 ("/", "-"), (".", "-")]:
        s = s.replace(a, b)
    s = " ".join(s.split())  # 折叠多余空格
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d %H", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=23, minute=59)
            elif fmt == "%Y-%m-%d %H":
                dt = dt.replace(minute=0)
            return dt
        except ValueError:
            continue
    return None


def _add_months(dt: datetime, n: int) -> datetime:
    """在 dt 上加 n 个月，自动处理月末（如 1/31 + 1 月 -> 2/28）。"""
    m = dt.month - 1 + n
    year = dt.year + m // 12
    month = m % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def advance_deadline(dt: datetime, repeat: str, now: datetime = None):
    """
    按重复周期把截止时间向后顺延一格；若顺延后仍是过去时间，
    则继续顺延，直到落在「现在」之后。repeat 为「无」时返回 None。
    """
    now = now or datetime.now()
    if repeat == "每天":
        step = lambda d: d + timedelta(days=1)
    elif repeat == "每周":
        step = lambda d: d + timedelta(days=7)
    elif repeat == "每月":
        step = lambda d: _add_months(d, 1)
    elif repeat == "每年":
        step = lambda d: _add_months(d, 12)
    else:
        return None
    nd = step(dt)
    guard = 0
    while nd <= now and guard < 5000:
        nd = step(nd)
        guard += 1
    return nd


def dp(widget, value):
    """
    把按 96 DPI（100% 缩放）设计的像素值，换算成当前屏幕 DPI 下的像素。
    字体用「点」自动随 tk scaling 放大，但列宽 / 行高 / 边距是像素，
    必须手动按同样比例放大，否则高分屏下文字会被裁切、列会错位。
    """
    try:
        scale = max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
    except Exception:
        scale = 1.0
    return int(round(value * scale))


# ============== 归档：档期分组 ==============
ARCHIVE_GRANULARITIES = ["年", "月", "周", "日"]
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def archive_record_dt(rec):
    """取归档记录用于分档的时间：优先完成时间，其次截止/创建时间。"""
    for key in ("completed_at", "deadline", "created"):
        v = rec.get(key)
        if v:
            try:
                return datetime.fromisoformat(v)
            except (ValueError, TypeError):
                continue
    return None


def archive_period_key_label(dt, gran):
    """
    返回 (排序键, 显示标签)。排序键可直接字典序排序（越大越新）。
    """
    if dt is None:
        return ("0000-00-00", "未知时间")
    if gran == "年":
        return (f"{dt.year:04d}", f"{dt.year} 年")
    if gran == "月":
        return (f"{dt.year:04d}-{dt.month:02d}",
                f"{dt.year} 年 {dt.month:02d} 月")
    if gran == "周":
        iso_year, iso_week, _ = dt.isocalendar()
        monday = dt - timedelta(days=dt.weekday())
        sunday = monday + timedelta(days=6)
        return (
            f"{iso_year:04d}-W{iso_week:02d}",
            f"{iso_year} 年第 {iso_week} 周"
            f"（{monday.strftime('%m-%d')} ~ {sunday.strftime('%m-%d')}）",
        )
    # 日
    return (dt.strftime("%Y-%m-%d"),
            f"{dt.strftime('%Y-%m-%d')}  {_WEEKDAY_CN[dt.weekday()]}")

# ============== 字体（按平台适配） ==============
# Windows 上整个程序统一使用「宋体」：按钮、任务列表、右键菜单、各弹窗
# 的中文都用同一种字体，避免英文字体回退导致的粗细 / 字形不一致。
# 想整体换成楷体，把下面的 "宋体" 改成 "楷体" 即可。
if SYSTEM == "Darwin":
    UI_FAMILY = "SF Pro Display"
elif SYSTEM == "Windows":
    UI_FAMILY = "宋体"
else:
    UI_FAMILY = "Helvetica"

# 按钮文字字体（与全局保持一致）
BUTTON_FAMILY = UI_FAMILY

# 副标题“小琳专属Keeper”统一使用的粉色
SUBTITLE_PINK = "#e6418a"

# 大标题保持加粗；副标题：中文用行书（华文行楷），英文用艺术字（Monotype Corsiva）
if SYSTEM == "Windows":
    F_SUB_CN = ("华文行楷", 17)        # “小琳专属” —— 行书
    F_SUB_EN = ("Monotype Corsiva", 20)  # “Keeper” —— 艺术字
else:
    F_SUB_CN = (UI_FAMILY, 14)
    F_SUB_EN = (UI_FAMILY, 15, "italic")

F_HERO = (UI_FAMILY, 30, "bold")
F_SUBTITLE = (UI_FAMILY, 13)
F_LABEL = (UI_FAMILY, 11)
F_BUTTON = (UI_FAMILY, 12)
# 所有快捷按钮（新建/编辑/…/导出/归档/数据目录）统一字体、字号、粗细
F_BTN = (BUTTON_FAMILY, 12)
F_TASK = (UI_FAMILY, 13)
F_HEADER = (UI_FAMILY, 10, "bold")
F_STATUS = (UI_FAMILY, 10)
F_EMPTY = (UI_FAMILY, 13)
F_DLG_LBL = (UI_FAMILY, 11)
F_DLG_INPUT = (UI_FAMILY, 13)

# ============== 4 套主题色板 ==============
THEMES = {
    "白色": {
        "bg": "#ffffff",
        "elevated": "#f6f8fa",
        "hover": "#eaeef2",
        "border": "#d0d7de",
        "border_focus": "#8c959f",
        "fg": "#1f2328",
        "fg_strong": "#0d1117",
        "fg_muted": "#656d76",
        "fg_subtle": "#afb8c1",
        "accent": "#0969da",
        "accent_alt": "#0550ae",
        "accent_hover": "#0860c9",
        "accent_bg": "#ddf4ff",
        "accent_fg": "#ffffff",
        "select_fg": "#0d1117",
        "high": "#cf222e",
        "medium": "#9a6700",
        "low": "#1a7f37",
        "done": "#8c959f",
        "overdue": "#cf222e",
    },
    "淡黄色": {
        "bg": "#fdf6e3",
        "elevated": "#f5efd9",
        "hover": "#f0e8c8",
        "border": "#d4cdb3",
        "border_focus": "#a39d8c",
        "fg": "#1a1a1a",
        "fg_strong": "#000000",
        "fg_muted": "#6b665a",
        "fg_subtle": "#a39d8c",
        "accent": "#2563eb",
        "accent_alt": "#0e7490",
        "accent_hover": "#1d4ed8",
        "accent_bg": "#bfdbfe",
        "accent_fg": "#ffffff",
        "select_fg": "#000000",
        "high": "#b91c1c",
        "medium": "#a16207",
        "low": "#15803d",
        "done": "#a39d8c",
        "overdue": "#b91c1c",
    },
    "淡绿色": {
        "bg": "#e8f0e3",
        "elevated": "#dfe8d9",
        "hover": "#d3e0cb",
        "border": "#b8ccac",
        "border_focus": "#8aa080",
        "fg": "#1a2417",
        "fg_strong": "#0a1408",
        "fg_muted": "#5a6e54",
        "fg_subtle": "#8aa080",
        "accent": "#15803d",
        "accent_alt": "#0d7d6e",
        "accent_hover": "#166534",
        "accent_bg": "#c4e8c4",
        "accent_fg": "#ffffff",
        "select_fg": "#0a1408",
        "high": "#b91c1c",
        "medium": "#a16207",
        "low": "#0d7d6e",
        "done": "#8aa080",
        "overdue": "#b91c1c",
    },
    "黑色": {
        "bg": "#0d1117",
        "elevated": "#161b22",
        "hover": "#1c232c",
        "border": "#21262d",
        "border_focus": "#30363d",
        "fg": "#e6edf3",
        "fg_strong": "#f0f6fc",
        "fg_muted": "#7d8590",
        "fg_subtle": "#484f58",
        "accent": "#58a6ff",
        "accent_alt": "#39c5cf",
        "accent_hover": "#79b8ff",
        "accent_bg": "#1f6feb",
        "accent_fg": "#0d1117",
        "select_fg": "#ffffff",
        "high": "#f85149",
        "medium": "#d29922",
        "low": "#3fb950",
        "done": "#6e7681",
        "overdue": "#ff7b72",
    },
}

DEFAULT_THEME = "淡黄色"
C = dict(THEMES[DEFAULT_THEME])


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def atomic_write_json(path: Path, data):
    """
    先写临时文件，再替换正式文件。
    这样可以减少中途崩溃导致 json 文件损坏的概率。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def save_config(cfg):
    try:
        atomic_write_json(CONFIG_FILE, cfg)
    except OSError:
        pass


# ============== 数据持久层 ==============
class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.tasks = self._load()

    def _load(self):
        if not self.path.exists():
            return []

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            # 如果文件坏了，先备份坏文件，再用空列表启动。
            try:
                backup = self.path.with_suffix(".broken.json")
                shutil.copy2(self.path, backup)
            except Exception:
                pass
            return []

    def save(self):
        with self._lock:
            atomic_write_json(self.path, self.tasks)

    def add(self, task: dict) -> dict:
        with self._lock:
            task = dict(task)
            task["id"] = max((int(t.get("id", 0)) for t in self.tasks), default=0) + 1
            task["created"] = datetime.now().isoformat()
            task["notified_1d"] = False
            task["notified_1h"] = False
            task["notified_overdue"] = False
            self.tasks.append(task)
            self.save()
            return task

    def update(self, task_id: int, **kwargs):
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    t.update(kwargs)
                    self.save()
                    return t
        return None

    def delete(self, task_id: int):
        with self._lock:
            self.tasks = [
                t for t in self.tasks
                if int(t.get("id", -1)) != int(task_id)
            ]
            self.save()

    def get(self, task_id: int):
        with self._lock:
            task = next(
                (t for t in self.tasks if int(t.get("id", -1)) == int(task_id)),
                None,
            )
            return dict(task) if task else None

    def all(self):
        with self._lock:
            return [dict(t) for t in self.tasks]

    # ---------- 子标题（子任务） ----------
    @staticmethod
    def _sync_parent_completion(task: dict):
        """
        如果一个大标题下挂着子标题，则它的完成状态由子标题决定：
        子标题全部完成 -> 大标题完成；只要有一个未完成 -> 大标题未完成。
        没有子标题时不动它的状态（仍由用户手动控制）。
        完成时记录 completed_at（用于归档分档期）。
        """
        subs = task.get("subtasks") or []
        if subs:
            done = all(s.get("completed", False) for s in subs)
            if done and not task.get("completed"):
                task["completed_at"] = datetime.now().isoformat()
            elif not done:
                task.pop("completed_at", None)
            task["completed"] = done

    def add_subtask(self, task_id: int, title: str):
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    subs = t.setdefault("subtasks", [])
                    new_id = max(
                        (int(s.get("id", 0)) for s in subs), default=0
                    ) + 1
                    subs.append(
                        {"id": new_id, "title": title, "completed": False}
                    )
                    self._sync_parent_completion(t)
                    self.save()
                    return t
        return None

    def toggle_subtask(self, task_id: int, sub_id: int):
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    for s in t.get("subtasks", []):
                        if int(s.get("id", -1)) == int(sub_id):
                            s["completed"] = not s.get("completed", False)
                            self._sync_parent_completion(t)
                            self.save()
                            return t
        return None

    def delete_subtask(self, task_id: int, sub_id: int):
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    t["subtasks"] = [
                        s for s in t.get("subtasks", [])
                        if int(s.get("id", -1)) != int(sub_id)
                    ]
                    self._sync_parent_completion(t)
                    self.save()
                    return t
        return None

    def rename_subtask(self, task_id: int, sub_id: int, title: str):
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    for s in t.get("subtasks", []):
                        if int(s.get("id", -1)) == int(sub_id):
                            s["title"] = title
                            self.save()
                            return t
        return None

    def set_completed(self, task_id: int, completed: bool):
        """
        设置大标题完成状态。若它有子标题，则把所有子标题一并设为同一状态，
        保证「大标题已完成 = 所有子标题已完成」始终一致。
        """
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    for s in t.get("subtasks", []):
                        s["completed"] = completed
                    t["completed"] = completed
                    if completed:
                        # 记录完成时间，用于归档分档期（保留已有的不覆盖）
                        if not t.get("completed_at"):
                            t["completed_at"] = datetime.now().isoformat()
                    else:
                        t.pop("completed_at", None)
                    self.save()
                    return t
        return None

    def recur(self, task_id: int):
        """
        把一个重复任务顺延到下一个周期：推进截止时间、重置提醒标记与子标题，
        并保持未完成状态。非重复任务返回 None。
        """
        with self._lock:
            for t in self.tasks:
                if int(t.get("id", -1)) == int(task_id):
                    repeat = t.get("repeat", "无")
                    if repeat == "无":
                        return None
                    try:
                        base = datetime.fromisoformat(t["deadline"])
                    except (KeyError, ValueError, TypeError):
                        base = datetime.now()
                    nd = advance_deadline(base, repeat)
                    if nd is None:
                        return None
                    t["deadline"] = nd.isoformat()
                    t["completed"] = False
                    t.pop("completed_at", None)
                    t["notified_1d"] = False
                    t["notified_1h"] = False
                    t["notified_overdue"] = False
                    for s in t.get("subtasks", []):
                        s["completed"] = False
                    self.save()
                    return dict(t)
        return None


# ============== 归档数据层 ==============
class ArchiveStore:
    """已完成工作的归档仓库，独立于 tasks.json，存放于 archive.json。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.records = self._load()

    def _load(self):
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            try:
                shutil.copy2(self.path, self.path.with_suffix(".broken.json"))
            except Exception:
                pass
            return []

    def save(self):
        with self._lock:
            atomic_write_json(self.path, self.records)

    def add_many(self, tasks):
        """把若干任务归档；为每条分配唯一 archive_id 与归档时间。"""
        with self._lock:
            now = datetime.now().isoformat()
            next_id = max(
                (int(r.get("archive_id", 0)) for r in self.records),
                default=0,
            ) + 1
            for t in tasks:
                rec = dict(t)
                rec["archive_id"] = next_id
                next_id += 1
                rec["archived_at"] = now
                if not rec.get("completed_at"):
                    rec["completed_at"] = (
                        rec.get("deadline") or rec.get("created") or now
                    )
                self.records.append(rec)
            self.save()

    def all(self):
        with self._lock:
            return [dict(r) for r in self.records]

    def get(self, archive_id):
        with self._lock:
            return next(
                (dict(r) for r in self.records
                 if int(r.get("archive_id", -1)) == int(archive_id)),
                None,
            )

    def delete(self, archive_id):
        with self._lock:
            self.records = [
                r for r in self.records
                if int(r.get("archive_id", -1)) != int(archive_id)
            ]
            self.save()


# ============== 桌宠数据层（积分 / 饱食度 / 装扮） ==============
class PetStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.data = self._load()

    def _defaults(self):
        return {
            "points": 0,
            "total_earned": 0,
            "fullness": 80.0,
            "fullness_ts": datetime.now().isoformat(),
            "outfit": "none",
            "owned": ["none"],
            "last_fed": None,
            "last_checkin": None,
        }

    def _load(self):
        d = self._defaults()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                d.update(data)
        except (json.JSONDecodeError, OSError):
            pass
        if "none" not in d.get("owned", []):
            d["owned"].append("none")
        return d

    def save(self):
        with self._lock:
            try:
                atomic_write_json(self.path, self.data)
            except OSError:
                pass

    # ---- 饱食度 ----
    def _decay(self):
        now = datetime.now()
        try:
            ts = datetime.fromisoformat(self.data.get("fullness_ts"))
        except (TypeError, ValueError):
            ts = now
        hours = max(0.0, (now - ts).total_seconds() / 3600.0)
        dec = hours * PET_DECAY_PER_HOUR
        if dec >= 1:
            self.data["fullness"] = max(
                0.0, float(self.data.get("fullness", 0)) - dec)
            self.data["fullness_ts"] = now.isoformat()

    def fullness(self):
        self._decay()
        return int(round(self.data.get("fullness", 0)))

    def mood(self):
        f = self.fullness()
        if f >= 60:
            return "happy"
        if f >= 30:
            return "normal"
        return "hungry"

    # ---- 积分 ----
    @property
    def points(self):
        return int(self.data.get("points", 0))

    def add_points(self, n):
        with self._lock:
            self.data["points"] = self.points + int(n)
            self.data["total_earned"] = int(
                self.data.get("total_earned", 0)) + int(n)
            self.save()

    def spend(self, n):
        with self._lock:
            if self.points >= n:
                self.data["points"] = self.points - int(n)
                self.save()
                return True
            return False

    # ---- 喂食 ----
    def feed(self, food_id):
        food = PET_FOODS.get(food_id)
        if not food:
            return False
        self._decay()
        if not self.spend(food["cost"]):
            return False
        with self._lock:
            self.data["fullness"] = min(
                100.0, float(self.data.get("fullness", 0)) + food["fill"])
            self.data["fullness_ts"] = datetime.now().isoformat()
            self.data["last_fed"] = date.today().isoformat()
            self.save()
        return True

    # ---- 装扮 ----
    def owns(self, outfit_id):
        return outfit_id in self.data.get("owned", [])

    def buy_outfit(self, outfit_id):
        item = PET_OUTFITS.get(outfit_id)
        if not item:
            return "missing"
        if self.owns(outfit_id):
            return "owned"
        if not self.spend(item["cost"]):
            return "poor"
        with self._lock:
            self.data.setdefault("owned", []).append(outfit_id)
            self.save()
        return "ok"

    def wear(self, outfit_id):
        if self.owns(outfit_id):
            with self._lock:
                self.data["outfit"] = outfit_id
                self.save()
            return True
        return False

    @property
    def outfit(self):
        return self.data.get("outfit", "none")

    # ---- 每日打卡 ----
    def can_checkin(self):
        return self.data.get("last_checkin") != date.today().isoformat()

    def checkin(self):
        if not self.can_checkin():
            return False
        with self._lock:
            self.data["last_checkin"] = date.today().isoformat()
            self.save()
        self.add_points(POINTS_CHECKIN)
        return True


# ============== 后台提醒引擎 ==============
class ReminderEngine(threading.Thread):
    def __init__(self, store: TaskStore, callback):
        super().__init__(daemon=True)
        self.store = store
        self.callback = callback
        self.running = True

    def run(self):
        while self.running:
            try:
                now = datetime.now()
                for t in self.store.all():
                    if t.get("completed"):
                        continue

                    try:
                        due = datetime.fromisoformat(t["deadline"])
                    except (KeyError, ValueError, TypeError):
                        continue

                    delta = due - now
                    if delta < timedelta(0) and not t.get("notified_overdue"):
                        self.callback(t, "已经过期！")
                        self.store.update(t["id"], notified_overdue=True)
                    elif delta <= timedelta(hours=1) and not t.get("notified_1h"):
                        self.callback(t, "还有不到 1 小时到期！")
                        self.store.update(t["id"], notified_1h=True)
                    elif delta <= timedelta(days=1) and not t.get("notified_1d"):
                        self.callback(t, "还有不到 24 小时到期")
                        self.store.update(t["id"], notified_1d=True)
            except Exception:
                pass

            for _ in range(60):
                if not self.running:
                    return
                time.sleep(1)

    def stop(self):
        self.running = False


# ============== 扁平按钮 ==============
class FlatButton(tk.Frame):
    def __init__(self, parent, text, command, *, variant="default"):
        base_bg = C["elevated"] if variant in ("dialog",) else C["bg"]
        super().__init__(parent, bg=base_bg, bd=0, highlightthickness=0)

        if variant == "primary":
            self._bg = C["accent"]
            self._fg = C["accent_fg"]
            self._hover_bg = C["accent_hover"]
            self._hover_fg = C["accent_fg"]
        elif variant == "danger":
            self._bg = base_bg
            self._fg = C["fg_muted"]
            self._hover_bg = C["hover"]
            self._hover_fg = C["high"]
        else:
            self._bg = base_bg
            self._fg = C["fg"]
            self._hover_bg = C["hover"]
            self._hover_fg = C["fg_strong"]

        self.command = command
        self.configure(bg=self._bg)

        self.label = tk.Label(
            self,
            text=text,
            bg=self._bg,
            fg=self._fg,
            font=F_BTN,
            padx=14,
            pady=8,
            bd=0,
        )
        self.label.pack()

        for w in (self, self.label):
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
            w.bind("<Button-1>", self._click)
            w.configure(cursor="hand2")

    def _enter(self, _):
        self.configure(bg=self._hover_bg)
        self.label.configure(bg=self._hover_bg, fg=self._hover_fg)

    def _leave(self, _):
        self.configure(bg=self._bg)
        self.label.configure(bg=self._bg, fg=self._fg)

    def _click(self, _):
        if self.command:
            self.command()


# ============== 任务编辑对话框 ==============
class TaskDialog(tk.Toplevel):
    def __init__(self, parent, task: dict = None):
        super().__init__(parent)
        self.task = task
        self.result = None

        self.title("编辑任务" if task else "新建任务")
        self.configure(bg=C["elevated"])

        # Windows 高缩放下，520x500 很容易裁掉底部按钮。
        # 这里改为更高、可缩放，并在后面根据屏幕自动调整。
        self.geometry(f"{dp(parent, 560)}x{dp(parent, 660)}")
        self.minsize(dp(parent, 520), dp(parent, 580))
        self.resizable(True, True)

        self.transient(parent)
        self.grab_set()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._build()

        if task:
            self._populate()

        self._fit_to_screen()
        self._center_over_parent(parent)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Control-Return>", lambda _e: self._save())
        self.after(100, self._focus_title)

    def _focus_title(self):
        try:
            self.title_entry.focus_set()
        except Exception:
            pass

    def _fit_to_screen(self):
        """根据屏幕大小调整弹窗，避免按钮跑到屏幕外。"""
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        req_w = max(dp(self, 560), self.winfo_reqwidth())
        req_h = max(dp(self, 660), self.winfo_reqheight())

        final_w = min(req_w, max(dp(self, 520), screen_w - 80))
        final_h = min(req_h, max(dp(self, 580), screen_h - 100))

        self.geometry(f"{final_w}x{final_h}")
        self.minsize(min(dp(self, 520), final_w), min(dp(self, 580), final_h))

    def _center_over_parent(self, parent):
        """把弹窗放到主窗口中间。"""
        try:
            self.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
            x = max(0, x)
            y = max(0, y)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build(self):
        wrap = tk.Frame(self, bg=C["elevated"])
        wrap.grid(row=0, column=0, sticky="nsew", padx=32, pady=(28, 12))
        wrap.columnconfigure(0, weight=1)

        tk.Label(
            wrap,
            text=("编辑任务" if self.task else "新建任务"),
            bg=C["elevated"],
            fg=C["fg_strong"],
            font=(UI_FAMILY, 18),
        ).pack(anchor="w")

        tk.Frame(wrap, bg=C["border"], height=1).pack(fill="x", pady=(16, 20))

        self._field(wrap, "任务标题")
        self.title_var = tk.StringVar()
        self.title_entry = self._entry(wrap, self.title_var)

        self._field(wrap, "任务描述", top=14)
        self.desc_text = tk.Text(
            wrap,
            height=5,
            bg=C["bg"],
            fg=C["fg"],
            insertbackground=C["accent"],
            relief="flat",
            font=F_DLG_INPUT,
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["border_focus"],
            bd=0,
            wrap="word",
        )
        self.desc_text.pack(fill="both", expand=True)

        self._field(wrap, "截止时间   （点上下箭头或滚动鼠标滚轮选择）", top=14)
        self._build_datetime_picker(
            wrap, datetime.now() + timedelta(days=1))

        self._field(wrap, "优先级", top=14)
        self.priority_var = tk.StringVar(value="中")
        prio_row = tk.Frame(wrap, bg=C["elevated"])
        prio_row.pack(fill="x", pady=(4, 0))

        for p in PRIORITIES:
            tk.Radiobutton(
                prio_row,
                text=p,
                variable=self.priority_var,
                value=p,
                bg=C["elevated"],
                fg=C["fg"],
                selectcolor=C["bg"],
                activebackground=C["elevated"],
                activeforeground=C["accent"],
                font=(UI_FAMILY, 12),
                bd=0,
                highlightthickness=0,
            ).pack(side="left", padx=(0, 18))

        self._field(wrap, "重复", top=14)
        self.repeat_var = tk.StringVar(value="无")
        repeat_cb = ttk.Combobox(
            wrap,
            textvariable=self.repeat_var,
            values=REPEAT_OPTIONS,
            state="readonly",
            style="Flat.TCombobox",
            font=F_DLG_INPUT,
        )
        repeat_cb.pack(fill="x", ipady=4, pady=(4, 0))

        hint = tk.Label(
            wrap,
            text="提示：按 Ctrl + Enter 可以保存，按 Esc 可以取消。",
            bg=C["elevated"],
            fg=C["fg_subtle"],
            font=(UI_FAMILY, 10),
        )
        hint.pack(anchor="w", pady=(14, 0))

        # 底部按钮单独放在 row=1。这样窗口高度变化时，保存/取消不会被挤没。
        btn_row = tk.Frame(self, bg=C["elevated"])
        btn_row.grid(row=1, column=0, sticky="ew", padx=32, pady=(0, 24))
        FlatButton(btn_row, "保存", self._save, variant="primary").pack(
            side="right", padx=(8, 0)
        )
        FlatButton(btn_row, "取消", self.destroy, variant="dialog").pack(side="right")

    def _field(self, parent, text, top=0):
        tk.Label(
            parent,
            text=text,
            bg=C["elevated"],
            fg=C["fg_muted"],
            font=F_DLG_LBL,
            anchor="w",
        ).pack(fill="x", pady=(top, 6))

    def _entry(self, parent, var):
        e = tk.Entry(
            parent,
            textvariable=var,
            bg=C["bg"],
            fg=C["fg"],
            insertbackground=C["accent"],
            relief="flat",
            font=F_DLG_INPUT,
            bd=0,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["border_focus"],
        )
        e.pack(fill="x", ipady=8)
        return e

    # ---------- 截止时间选择器（带边框 + 滚轮滑动） ----------
    def _build_datetime_picker(self, parent, default_dt):
        self.dt_year = tk.IntVar(value=default_dt.year)
        self.dt_month = tk.IntVar(value=default_dt.month)
        self.dt_day = tk.IntVar(value=default_dt.day)
        self.dt_hour = tk.IntVar(value=default_dt.hour)
        self.dt_minute = tk.IntVar(value=default_dt.minute)
        self._day_spin = None

        # 外层带边框的容器，聚焦时边框高亮
        box = tk.Frame(
            parent, bg=C["bg"],
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["border_focus"],
            bd=0,
        )
        box.pack(fill="x", pady=(4, 0))
        inner = tk.Frame(box, bg=C["bg"])
        inner.pack(padx=12, pady=10)

        cur_year = datetime.now().year

        def _wheel(spin, ev):
            try:
                spin.invoke("buttonup" if ev.delta > 0 else "buttondown")
            except Exception:
                pass
            return "break"

        def make_spin(var, frm, to, width, fmt="%02.0f"):
            # state 默认为 normal：既能点箭头/滚轮滑动，也能直接用键盘输入。
            sb = tk.Spinbox(
                inner, textvariable=var, from_=frm, to=to, width=width,
                format=fmt, wrap=True, justify="center",
                bg=C["bg"], fg=C["fg_strong"],
                insertbackground=C["accent"],
                buttonbackground=C["elevated"],
                relief="flat", bd=0, highlightthickness=0,
                font=F_DLG_INPUT,
            )
            sb.bind("<MouseWheel>", lambda ev, s=sb: _wheel(s, ev))
            # Linux 滚轮事件
            sb.bind("<Button-4>", lambda ev, s=sb: _wheel(
                s, type("E", (), {"delta": 1})))
            sb.bind("<Button-5>", lambda ev, s=sb: _wheel(
                s, type("E", (), {"delta": -1})))
            return sb

        def sep(text, pad=(2, 10)):
            tk.Label(
                inner, text=text, bg=C["bg"], fg=C["fg_muted"],
                font=F_DLG_LBL,
            ).grid(row=0, column=sep.col, padx=pad)
            sep.col += 1
        sep.col = 1

        make_spin(self.dt_year, cur_year - 1, cur_year + 30, 5,
                  fmt="%.0f").grid(row=0, column=0)
        sep("年")
        make_spin(self.dt_month, 1, 12, 3).grid(row=0, column=sep.col)
        sep.col += 1
        sep("月")
        self._day_spin = make_spin(self.dt_day, 1, 31, 3)
        self._day_spin.grid(row=0, column=sep.col)
        sep.col += 1
        sep("日", pad=(2, 18))
        make_spin(self.dt_hour, 0, 23, 3).grid(row=0, column=sep.col)
        sep.col += 1
        sep(":", pad=(2, 2))
        make_spin(self.dt_minute, 0, 59, 3).grid(row=0, column=sep.col)
        sep.col += 1
        sep("时分", pad=(8, 0))

        # 年/月变化时同步“日”的可选范围（如 2 月只到 28/29）
        for v in (self.dt_year, self.dt_month):
            v.trace_add("write", lambda *_a: self._update_day_range())
        self._update_day_range()

    def _update_day_range(self):
        try:
            y = int(self.dt_year.get())
            mo = int(self.dt_month.get())
            max_day = calendar.monthrange(y, mo)[1]
        except (tk.TclError, ValueError, calendar.IllegalMonthError):
            return
        if self._day_spin is not None:
            self._day_spin.config(to=max_day)
            try:
                if int(self.dt_day.get()) > max_day:
                    self.dt_day.set(max_day)
            except (tk.TclError, ValueError):
                pass

    def _set_datetime(self, dt):
        self.dt_year.set(dt.year)
        self.dt_month.set(dt.month)
        self._update_day_range()
        self.dt_day.set(dt.day)
        self.dt_hour.set(dt.hour)
        self.dt_minute.set(dt.minute)

    def _get_datetime(self):
        return datetime(
            int(self.dt_year.get()), int(self.dt_month.get()),
            int(self.dt_day.get()), int(self.dt_hour.get()),
            int(self.dt_minute.get()),
        )

    def _populate(self):
        self.title_var.set(self.task.get("title", ""))
        self.desc_text.insert("1.0", self.task.get("description", ""))

        try:
            d = datetime.fromisoformat(self.task["deadline"])
            self._set_datetime(d)
        except (KeyError, ValueError, TypeError):
            pass

        self.priority_var.set(self.task.get("priority", "中"))
        self.repeat_var.set(self.task.get("repeat", "无"))

    def _save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showerror("错误", "标题不能为空", parent=self)
            return

        try:
            dl = self._get_datetime()
        except (ValueError, tk.TclError):
            messagebox.showerror(
                "错误", "截止时间不正确，请重新选择。", parent=self)
            return

        # 编辑已有任务时保留 completed 状态，避免编辑后自动变回“待办”。
        completed = bool(self.task.get("completed", False)) if self.task else False

        self.result = {
            "title": title,
            "description": self.desc_text.get("1.0", "end").strip(),
            "deadline": dl.isoformat(),
            "priority": self.priority_var.get(),
            "repeat": self.repeat_var.get(),
            "completed": completed,
        }
        self.destroy()


# ============== 延期对话框 ==============
class PostponeDialog(tk.Toplevel):
    """
    用于推迟任务截止时间的轻量对话框。
    支持:
      - 单/多任务:相对推迟(按当前截止时间 + N)
      - 单/多任务:固定基准推迟(按"现在" + N,常用于已过期任务)
      - 仅单任务:设置为具体的新时间
    """

    QUICK_OPTIONS = [
        ("1 小时", timedelta(hours=1)),
        ("6 小时", timedelta(hours=6)),
        ("1 天", timedelta(days=1)),
        ("3 天", timedelta(days=3)),
        ("1 周", timedelta(days=7)),
    ]

    def __init__(self, parent, tasks: list):
        super().__init__(parent)
        self.tasks = tasks  # 任务字典列表
        self.result = None  # 返回 {task_id: new_deadline_iso}

        n = len(tasks)
        if n == 1:
            title_text = f"延期任务 — {tasks[0].get('title', '')}"
        else:
            title_text = f"批量延期 — {n} 个任务"
        self.title(title_text)
        self.configure(bg=C["elevated"])
        # 多任务时高度多一点放任务列表;单任务且支持"设置为具体时间"也需要空间
        self.geometry(
            f"{dp(parent, 540)}x{dp(parent, 540 if n == 1 else 560)}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._build()
        self.after(50, lambda: self._center_over_parent(parent))

    def _center_over_parent(self, parent):
        try:
            self.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _build(self):
        wrap = tk.Frame(self, bg=C["elevated"])
        wrap.pack(fill="both", expand=True, padx=28, pady=24)

        n = len(self.tasks)
        header_text = "延期任务" if n == 1 else f"批量延期 ({n} 个任务)"
        tk.Label(
            wrap, text=header_text,
            bg=C["elevated"], fg=C["fg_strong"],
            font=(UI_FAMILY, 17),
        ).pack(anchor="w")
        tk.Frame(wrap, bg=C["border"], height=1).pack(fill="x", pady=(14, 16))

        # 任务列表(多任务时显示)
        if n > 1:
            preview_box = tk.Frame(wrap, bg=C["bg"],
                                   highlightthickness=1,
                                   highlightbackground=C["border"])
            preview_box.pack(fill="x", pady=(0, 14))
            preview_inner = tk.Frame(preview_box, bg=C["bg"])
            preview_inner.pack(fill="x", padx=10, pady=8)

            shown = self.tasks[:4]
            for t in shown:
                line = self._task_summary(t)
                tk.Label(
                    preview_inner, text=f"·  {line}",
                    bg=C["bg"], fg=C["fg_muted"],
                    font=(UI_FAMILY, 10), anchor="w", justify="left",
                ).pack(fill="x")
            if n > 4:
                tk.Label(
                    preview_inner, text=f"…  共 {n} 个",
                    bg=C["bg"], fg=C["fg_subtle"],
                    font=(UI_FAMILY, 10), anchor="w",
                ).pack(fill="x", pady=(2, 0))
        else:
            t = self.tasks[0]
            tk.Label(
                wrap, text="当前截止时间",
                bg=C["elevated"], fg=C["fg_muted"],
                font=(UI_FAMILY, 10), anchor="w",
            ).pack(fill="x")
            try:
                dt = datetime.fromisoformat(t["deadline"])
                cur_text = dt.strftime("%Y-%m-%d  %H:%M")
            except (KeyError, ValueError):
                cur_text = "(未知)"
            tk.Label(
                wrap, text=cur_text,
                bg=C["elevated"], fg=C["fg"],
                font=(UI_FAMILY, 13), anchor="w",
            ).pack(fill="x", pady=(2, 14))

        # 基准选择
        # 默认:单任务时基准为"当前截止时间"
        # 但如果选中的任务里有已过期的,把基准默认设为"现在",避免再次过期
        now = datetime.now()
        has_overdue = any(
            self._safe_parse(t["deadline"]) and
            self._safe_parse(t["deadline"]) < now
            for t in self.tasks
        )
        self.base_var = tk.StringVar(value="now" if has_overdue else "current")

        tk.Label(
            wrap, text="推迟基准",
            bg=C["elevated"], fg=C["fg_muted"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x")
        base_row = tk.Frame(wrap, bg=C["elevated"])
        base_row.pack(fill="x", pady=(4, 12))
        for label, value, hint in [
            ("当前截止时间 +", "current", "在原截止时间基础上推迟"),
            ("现在 +", "now", "从此刻起重新计时(常用于过期任务)"),
        ]:
            tk.Radiobutton(
                base_row, text=label, variable=self.base_var, value=value,
                bg=C["elevated"], fg=C["fg"], selectcolor=C["bg"],
                activebackground=C["elevated"],
                activeforeground=C["accent"],
                font=(UI_FAMILY, 11), bd=0, highlightthickness=0,
            ).pack(side="left", padx=(0, 16))
        if has_overdue and n > 1:
            tk.Label(
                wrap,
                text="⚠  选中任务里有已过期的,建议使用「现在 +」",
                bg=C["elevated"], fg=C["overdue"],
                font=(UI_FAMILY, 10), anchor="w",
            ).pack(fill="x", pady=(0, 8))

        # 快捷推迟按钮
        tk.Label(
            wrap, text="快速推迟",
            bg=C["elevated"], fg=C["fg_muted"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x", pady=(4, 6))
        quick_row = tk.Frame(wrap, bg=C["elevated"])
        quick_row.pack(fill="x", pady=(0, 12))
        for label, delta in self.QUICK_OPTIONS:
            FlatButton(
                quick_row, f"+ {label}",
                lambda d=delta: self._apply_relative(d),
                variant="primary",
            ).pack(side="left", padx=(0, 6))

        # 自定义推迟
        tk.Label(
            wrap, text="自定义推迟",
            bg=C["elevated"], fg=C["fg_muted"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x", pady=(4, 6))
        custom_row = tk.Frame(wrap, bg=C["elevated"])
        custom_row.pack(fill="x", pady=(0, 6))
        self.custom_num = tk.StringVar(value="2")
        self.custom_unit = tk.StringVar(value="天")
        num_entry = tk.Entry(
            custom_row, textvariable=self.custom_num,
            bg=C["bg"], fg=C["fg"], insertbackground=C["accent"],
            relief="flat", font=(UI_FAMILY, 12), bd=0, width=8,
            highlightthickness=1, highlightbackground=C["border"],
            highlightcolor=C["border_focus"], justify="center",
        )
        num_entry.pack(side="left", ipady=6)
        unit_cb = ttk.Combobox(
            custom_row, textvariable=self.custom_unit,
            values=["分钟", "小时", "天", "周"],
            state="readonly", width=6, style="Flat.TCombobox",
            font=(UI_FAMILY, 11),
        )
        unit_cb.pack(side="left", padx=(8, 8))
        FlatButton(
            custom_row, "应用",
            self._apply_custom, variant="primary",
        ).pack(side="left")

        # 仅单任务:设置为具体时间
        if n == 1:
            tk.Label(
                wrap, text="或:设置为具体时间   YYYY-MM-DD HH:MM",
                bg=C["elevated"], fg=C["fg_muted"],
                font=(UI_FAMILY, 10), anchor="w",
            ).pack(fill="x", pady=(10, 6))
            abs_row = tk.Frame(wrap, bg=C["elevated"])
            abs_row.pack(fill="x")
            self.abs_var = tk.StringVar(
                value=(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
            tk.Entry(
                abs_row, textvariable=self.abs_var,
                bg=C["bg"], fg=C["fg"], insertbackground=C["accent"],
                relief="flat", font=(UI_FAMILY, 12), bd=0,
                highlightthickness=1, highlightbackground=C["border"],
                highlightcolor=C["border_focus"],
            ).pack(side="left", fill="x", expand=True, ipady=6)
            FlatButton(
                abs_row, "应用",
                self._apply_absolute, variant="primary",
            ).pack(side="left", padx=(8, 0))

        # 底部:取消
        btn_row = tk.Frame(self, bg=C["elevated"])
        btn_row.pack(side="bottom", fill="x", padx=28, pady=(0, 20))
        FlatButton(btn_row, "取消", self.destroy).pack(side="right")

    @staticmethod
    def _safe_parse(s):
        try:
            return datetime.fromisoformat(s)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _task_summary(t):
        title = t.get("title", "(无标题)")
        try:
            dt = datetime.fromisoformat(t["deadline"])
            s = dt.strftime("%m-%d %H:%M")
        except (KeyError, ValueError):
            s = "未知时间"
        if len(title) > 30:
            title = title[:30] + "…"
        return f"{title}    ({s})"

    def _apply_relative(self, delta: timedelta):
        base_mode = self.base_var.get()
        now = datetime.now()
        result = {}
        for t in self.tasks:
            if base_mode == "now":
                base = now
            else:
                base = self._safe_parse(t["deadline"]) or now
            new_dt = base + delta
            result[t["id"]] = new_dt.isoformat()
        self.result = result
        self.destroy()

    def _apply_custom(self):
        raw = self.custom_num.get().strip()
        try:
            n = float(raw)
            if n <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "请输入大于 0 的数字", parent=self)
            return
        unit = self.custom_unit.get()
        if unit == "分钟":
            delta = timedelta(minutes=n)
        elif unit == "小时":
            delta = timedelta(hours=n)
        elif unit == "天":
            delta = timedelta(days=n)
        elif unit == "周":
            delta = timedelta(weeks=n)
        else:
            return
        self._apply_relative(delta)

    def _apply_absolute(self):
        dt = parse_datetime_flexible(self.abs_var.get())
        if dt is None:
            messagebox.showerror(
                "错误", "时间格式不正确\n例：2026-06-26 09:00",
                parent=self,
            )
            return
        self.result = {self.tasks[0]["id"]: dt.isoformat()}
        self.destroy()


# ============== 导出对话框 ==============
class ExportDialog(tk.Toplevel):
    """
    选择导出范围与格式。
    范围: 待办 / 已完成 / 全部 / 选中项
    格式: 文本(.txt) / 表格(.csv) / Markdown(.md)
    排序方式跟随主界面当前选择(只读展示)。
    """

    def __init__(self, parent, current_sort: str, has_selection: bool = False):
        super().__init__(parent)
        self.result = None
        self.current_sort = current_sort
        self.has_selection = has_selection

        self.title("导出任务")
        self.configure(bg=C["elevated"])
        self.geometry(f"{dp(parent, 470)}x{dp(parent, 490)}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.scope_var = tk.StringVar(value="选中项" if has_selection else "待办")
        self.fmt_var = tk.StringVar(value="txt")

        self._build()
        self.after(50, lambda: self._center_over_parent(parent))
        self.bind("<Escape>", lambda _e: self.destroy())

    def _center_over_parent(self, parent):
        try:
            self.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _build(self):
        wrap = tk.Frame(self, bg=C["elevated"])
        wrap.pack(fill="both", expand=True, padx=28, pady=24)

        tk.Label(
            wrap, text="导出任务",
            bg=C["elevated"], fg=C["fg_strong"],
            font=(UI_FAMILY, 17),
        ).pack(anchor="w")
        tk.Frame(wrap, bg=C["border"], height=1).pack(fill="x", pady=(14, 16))

        # 导出范围
        tk.Label(
            wrap, text="导出范围",
            bg=C["elevated"], fg=C["fg_muted"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x")
        scope_row = tk.Frame(wrap, bg=C["elevated"])
        scope_row.pack(fill="x", pady=(4, 14))
        scope_items = [("待办", "待办"), ("已完成", "已完成"), ("全部", "全部")]
        scope_items.append(("选中项", "选中项"))
        for label, value in scope_items:
            state = "normal"
            if value == "选中项" and not self.has_selection:
                state = "disabled"
            tk.Radiobutton(
                scope_row, text=label, variable=self.scope_var, value=value,
                bg=C["elevated"], fg=C["fg"], selectcolor=C["bg"],
                activebackground=C["elevated"], activeforeground=C["accent"],
                disabledforeground=C["fg_subtle"],
                font=(UI_FAMILY, 12), bd=0, highlightthickness=0, state=state,
            ).pack(side="left", padx=(0, 16))

        # 导出格式
        tk.Label(
            wrap, text="导出格式",
            bg=C["elevated"], fg=C["fg_muted"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x")
        fmt_grid = tk.Frame(wrap, bg=C["elevated"])
        fmt_grid.pack(fill="x", pady=(4, 14))
        fmt_items = [("文本 (.txt)", "txt"), ("表格 (.csv)", "csv"),
                     ("Markdown (.md)", "md"), ("Excel (.xlsx)", "xlsx")]
        for idx, (label, value) in enumerate(fmt_items):
            tk.Radiobutton(
                fmt_grid, text=label, variable=self.fmt_var, value=value,
                bg=C["elevated"], fg=C["fg"], selectcolor=C["bg"],
                activebackground=C["elevated"], activeforeground=C["accent"],
                font=(UI_FAMILY, 12), bd=0, highlightthickness=0,
            ).grid(row=idx // 2, column=idx % 2, sticky="w",
                   padx=(0, 24), pady=3)

        # 排序方式（跟随主界面）
        tk.Label(
            wrap,
            text=f"排序方式：{self.current_sort}（与主界面一致）",
            bg=C["elevated"], fg=C["fg_subtle"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x", pady=(2, 0))
        tk.Label(
            wrap,
            text="提示：导出内容会包含每个任务下的子标题。",
            bg=C["elevated"], fg=C["fg_subtle"],
            font=(UI_FAMILY, 10), anchor="w",
        ).pack(fill="x", pady=(8, 0))

        # 底部按钮
        btn_row = tk.Frame(self, bg=C["elevated"])
        btn_row.pack(side="bottom", fill="x", padx=28, pady=(0, 20))
        FlatButton(btn_row, "导出", self._confirm, variant="primary").pack(
            side="right", padx=(8, 0)
        )
        FlatButton(btn_row, "取消", self.destroy, variant="dialog").pack(
            side="right"
        )

    def _confirm(self):
        self.result = {
            "scope": self.scope_var.get(),
            "fmt": self.fmt_var.get(),
        }
        self.destroy()


# ============== 归档窗口 ==============
class ArchiveWindow(tk.Toplevel):
    """
    工作归档：把已完成任务按「年/月/周/日」分档展示，
    每个档期可单独导出（TXT / CSV / Markdown），方便写工作日报。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("工作归档")
        self.configure(bg=C["bg"])
        self.geometry(f"{dp(app, 960)}x{dp(app, 620)}")
        self.minsize(dp(app, 760), dp(app, 480))
        self.transient(app)

        self.gran_var = tk.StringVar(value="日")  # 默认按「日」，便于写日报
        self._groups = {}  # key -> (label, [records])

        self._build()
        self._reload()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self._center)

    def _center(self):
        try:
            self.update_idletasks()
            p = self.app
            x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
            y = p.winfo_rooty() + (p.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _build(self):
        EDGE = dp(self, 24)

        top = tk.Frame(self, bg=C["bg"])
        top.pack(fill="x", padx=EDGE, pady=(20, 0))
        tk.Label(
            top, text="工作归档", bg=C["bg"], fg=C["fg_strong"],
            font=(UI_FAMILY, 20, "bold"),
        ).pack(side="left")

        gran_box = tk.Frame(top, bg=C["bg"])
        gran_box.pack(side="right")
        tk.Label(
            gran_box, text="归档粒度", bg=C["bg"], fg=C["fg_muted"], font=F_LABEL,
        ).pack(side="left", padx=(0, 10))
        for g in ARCHIVE_GRANULARITIES:
            tk.Radiobutton(
                gran_box, text=g, variable=self.gran_var, value=g,
                command=self._reload,
                bg=C["bg"], fg=C["fg"], selectcolor=C["elevated"],
                activebackground=C["bg"], activeforeground=C["accent"],
                font=(UI_FAMILY, 12), bd=0, highlightthickness=0,
            ).pack(side="left", padx=(0, 8))

        tb = tk.Frame(self, bg=C["bg"])
        tb.pack(fill="x", padx=EDGE, pady=(16, 0))
        FlatButton(tb, "↧  归档已完成任务", self._archive_completed,
                   variant="primary").pack(side="left", padx=(0, 8))
        FlatButton(tb, "⤓  导出选中档期", self._export_selected).pack(
            side="left", padx=(0, 8))
        FlatButton(tb, "↩  恢复选中", self._restore_selected).pack(
            side="left", padx=(0, 8))
        FlatButton(tb, "✕  删除选中", self._delete_selected,
                   variant="danger").pack(side="left", padx=(0, 8))
        FlatButton(tb, "⟳  刷新", self._reload).pack(side="left")

        tk.Frame(self, bg=C["border"], height=1).pack(
            fill="x", padx=EDGE, pady=(16, 0))

        wrap = tk.Frame(self, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=EDGE)

        cols = ("title", "priority", "deadline", "done")
        self.tree = ttk.Treeview(
            wrap, columns=cols, show="tree headings",
            selectmode="browse", style="Flat.Treeview",
        )
        self.tree.heading("#0", text="档期", anchor="w")
        self.tree.heading("title", text="任务", anchor="w")
        self.tree.heading("priority", text="优先级", anchor="w")
        self.tree.heading("deadline", text="截止时间", anchor="w")
        self.tree.heading("done", text="完成时间", anchor="w")
        self.tree.column("#0", width=dp(self, 260), minwidth=dp(self, 200),
                         anchor="w", stretch=False)
        self.tree.column("title", width=dp(self, 320), anchor="w")
        self.tree.column("priority", width=dp(self, 80), anchor="w",
                         stretch=False)
        self.tree.column("deadline", width=dp(self, 150), anchor="w",
                         stretch=False)
        self.tree.column("done", width=dp(self, 150), anchor="w",
                         stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(
            wrap, orient="vertical", command=self.tree.yview,
            style="Flat.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.tag_configure(
            "period", font=(UI_FAMILY, 12, "bold"),
            foreground=C["accent_alt"],
        )
        self.tree.bind("<Button-3>", self._context)
        self.tree.bind("<Button-2>", self._context)
        self.tree.bind("<Double-1>", lambda _e: self._export_selected())

        self.empty = tk.Label(
            wrap,
            text="暂无归档记录\n\n先在主界面完成一些任务，"
                 "再点「↧ 归档已完成任务」",
            bg=C["bg"], fg=C["fg_subtle"], font=F_EMPTY, justify="center",
        )

        self.status = tk.Label(
            self, text="", bg=C["bg"], fg=C["fg_muted"], anchor="w",
            font=F_STATUS, padx=EDGE, pady=12,
        )
        self.status.pack(fill="x", side="bottom")

    # ---------- 渲染 ----------
    def _reload(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        gran = self.gran_var.get()
        records = self.app.archive.all()
        groups = {}
        for r in records:
            dt = archive_record_dt(r)
            key, label = archive_period_key_label(dt, gran)
            groups.setdefault(key, [label, []])[1].append(r)
        self._groups = {k: (v[0], v[1]) for k, v in groups.items()}

        for key in sorted(groups.keys(), reverse=True):
            label, recs = groups[key]
            recs.sort(key=lambda r: archive_record_dt(r) or datetime.min)
            pid = f"period::{key}"
            self.tree.insert(
                "", "end", iid=pid,
                text=f"{label}   ({len(recs)})",
                values=("", "", "", ""), tags=("period",), open=True,
            )
            for r in recs:
                dl = self.app._fmt_iso(r.get("deadline"))
                done = self.app._fmt_iso(r.get("completed_at"))
                self.tree.insert(
                    pid, "end", iid=f"rec::{r.get('archive_id')}",
                    text="·",
                    values=(r.get("title", ""), r.get("priority", "中"),
                            dl, done),
                )

        if not records:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty.place_forget()
        self.status.config(
            text=f"共 {len(records)} 条归档   ·   {len(groups)} 个档期"
                 f"   ·   按「{gran}」分组")

    # ---------- 选择辅助 ----------
    def _selected_period_key(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid.startswith("period::"):
            return iid[len("period::"):]
        if iid.startswith("rec::"):
            parent = self.tree.parent(iid)
            if parent.startswith("period::"):
                return parent[len("period::"):]
        return None

    def _selected_record_ids(self):
        ids = []
        for iid in self.tree.selection():
            if iid.startswith("rec::"):
                try:
                    ids.append(int(iid[len("rec::"):]))
                except ValueError:
                    pass
            elif iid.startswith("period::"):
                key = iid[len("period::"):]
                for r in self._groups.get(key, (None, []))[1]:
                    ids.append(int(r.get("archive_id")))
        return ids

    # ---------- 操作 ----------
    def _archive_completed(self):
        if self.app._archive_completed_tasks(parent=self):
            self._reload()

    def _restore_selected(self):
        ids = self._selected_record_ids()
        if not ids:
            messagebox.showinfo("工作归档", "请先选择要恢复的档期或记录。",
                                parent=self)
            return
        if not messagebox.askyesno(
            "工作归档",
            f"将 {len(ids)} 条记录恢复到任务列表？",
            parent=self,
        ):
            return
        drop = {"archive_id", "archived_at", "id", "created",
                "notified_1d", "notified_1h", "notified_overdue"}
        restored = 0
        for aid in ids:
            rec = self.app.archive.get(aid)
            if not rec:
                continue
            clean = {k: v for k, v in rec.items() if k not in drop}
            try:
                self.app.store.add(clean)
                self.app.archive.delete(aid)
                restored += 1
            except OSError:
                pass
        self.app._refresh()
        self._reload()
        messagebox.showinfo("工作归档", f"已恢复 {restored} 条到任务列表。",
                            parent=self)

    def _delete_selected(self):
        ids = self._selected_record_ids()
        if not ids:
            messagebox.showinfo("工作归档", "请先选择要删除的档期或记录。",
                                parent=self)
            return
        if not messagebox.askyesno(
            "工作归档",
            f"确定从归档中永久删除 {len(ids)} 条记录？\n此操作无法撤销。",
            parent=self,
        ):
            return
        for aid in ids:
            self.app.archive.delete(aid)
        self._reload()

    def _export_selected(self):
        key = self._selected_period_key()
        if not key:
            messagebox.showinfo("工作归档", "请先选择一个档期（或其中的任务）。",
                                parent=self)
            return
        self._post_export_menu(key)

    def _post_export_menu(self, key):
        menu = tk.Menu(self, tearoff=0,
                       bg=C["elevated"], fg=C["fg"],
                       activebackground=C["accent"],
                       activeforeground=C["accent_fg"],
                       bd=0, font=F_BUTTON)
        for label, fmt in [("导出为 TXT", "txt"),
                           ("导出为 CSV", "csv"),
                           ("导出为 Markdown", "md"),
                           ("导出为 Excel", "xlsx")]:
            menu.add_command(
                label=label,
                command=lambda f=fmt, k=key: self._do_export(k, f),
            )
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _do_export(self, key, fmt):
        entry = self._groups.get(key)
        if not entry:
            return
        label, recs = entry
        recs_sorted = sorted(
            recs, key=lambda r: archive_record_dt(r) or datetime.min)

        ext = {"csv": "csv", "md": "md", "xlsx": "xlsx"}.get(fmt, "txt")
        safe_key = key.replace(":", "-")
        type_map = {
            "csv": [("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            "md": [("Markdown 文件", "*.md"), ("所有文件", "*.*")],
            "xlsx": [("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            "txt": [("文本文件", "*.txt"), ("所有文件", "*.*")],
        }
        default_name = (
            f"工作记录_{safe_key}_{datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"
        )
        path = filedialog.asksaveasfilename(
            parent=self, title="导出此档期",
            initialfile=default_name, defaultextension=f".{ext}",
            filetypes=type_map.get(fmt, type_map["txt"]),
        )
        if not path:
            return
        try:
            if fmt == "csv":
                self.app._write_csv(path, recs_sorted, label)
            elif fmt == "md":
                self.app._write_md(path, recs_sorted, label)
            elif fmt == "xlsx":
                self.app._write_xlsx(path, recs_sorted, label)
            else:
                self.app._write_txt(path, recs_sorted, label)
        except OSError as e:
            messagebox.showerror("工作归档", f"导出失败:\n{e}", parent=self)
            return
        if messagebox.askyesno(
            "工作归档",
            f"已导出 {len(recs_sorted)} 项到:\n{path}\n\n是否打开所在文件夹?",
            parent=self,
        ):
            self.app._reveal_in_folder(path)

    def _context(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        menu = tk.Menu(self, tearoff=0,
                       bg=C["elevated"], fg=C["fg"],
                       activebackground=C["accent"],
                       activeforeground=C["accent_fg"],
                       bd=0, font=F_BUTTON)
        key = self._selected_period_key()
        if key:
            menu.add_command(label="导出此档期…",
                             command=lambda k=key: self._post_export_menu(k))
            menu.add_separator()
        menu.add_command(label="恢复到任务列表", command=self._restore_selected)
        menu.add_command(label="从归档删除", command=self._delete_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


# ============== 桌宠商店（买食物 / 买衣服 / 换装） ==============
class PetShopDialog(tk.Toplevel):
    def __init__(self, app, pet=None):
        super().__init__(app)
        self.app = app
        self.pet = pet
        self.store = app.pet_store
        self.title("宠物商店")
        self.configure(bg=C["elevated"])
        self.geometry(f"{dp(app, 440)}x{dp(app, 560)}")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        self._build()
        self.after(50, self._center)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _center(self):
        try:
            self.update_idletasks()
            p = self.app
            x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
            y = p.winfo_rooty() + (p.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _build(self):
        wrap = tk.Frame(self, bg=C["elevated"])
        wrap.pack(fill="both", expand=True, padx=24, pady=20)

        tk.Label(wrap, text="宠物商店", bg=C["elevated"], fg=C["fg_strong"],
                 font=(UI_FAMILY, 17)).pack(anchor="w")
        self.bal = tk.Label(wrap, text="", bg=C["elevated"], fg=C["accent"],
                            font=(UI_FAMILY, 13))
        self.bal.pack(anchor="w", pady=(4, 0))
        tk.Frame(wrap, bg=C["border"], height=1).pack(fill="x", pady=(12, 12))

        # 食物
        tk.Label(wrap, text="食物（喂食消耗积分，回复饱食度）",
                 bg=C["elevated"], fg=C["fg_muted"],
                 font=F_DLG_LBL).pack(anchor="w")
        for fid, food in PET_FOODS.items():
            row = tk.Frame(wrap, bg=C["elevated"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=f"{food['name']}", bg=C["elevated"], fg=C["fg"],
                     font=(UI_FAMILY, 12), width=8, anchor="w").pack(side="left")
            tk.Label(row, text=f"{food['desc']}", bg=C["elevated"],
                     fg=C["fg_subtle"], font=(UI_FAMILY, 10),
                     anchor="w").pack(side="left", padx=(4, 0))
            FlatButton(row, f"喂食 ({food['cost']}分)",
                       lambda i=fid: self._feed(i),
                       variant="primary").pack(side="right")

        tk.Frame(wrap, bg=C["border"], height=1).pack(fill="x", pady=(14, 12))

        # 衣服
        tk.Label(wrap, text="衣服 / 装扮（购买后永久拥有，可切换）",
                 bg=C["elevated"], fg=C["fg_muted"],
                 font=F_DLG_LBL).pack(anchor="w")
        self.outfit_rows = tk.Frame(wrap, bg=C["elevated"])
        self.outfit_rows.pack(fill="x", pady=(4, 0))
        self._build_outfits()

        btn = tk.Frame(self, bg=C["elevated"])
        btn.pack(side="bottom", fill="x", padx=24, pady=(0, 16))
        FlatButton(btn, "关闭", self.destroy, variant="dialog").pack(side="right")

        self._refresh()

    def _build_outfits(self):
        for w in self.outfit_rows.winfo_children():
            w.destroy()
        for oid, item in PET_OUTFITS.items():
            row = tk.Frame(self.outfit_rows, bg=C["elevated"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=item["name"], bg=C["elevated"], fg=C["fg"],
                     font=(UI_FAMILY, 12), width=8,
                     anchor="w").pack(side="left")
            owned = self.store.owns(oid)
            worn = self.store.outfit == oid
            if worn:
                tk.Label(row, text="（穿着中）", bg=C["elevated"],
                         fg=C["accent"], font=(UI_FAMILY, 10)).pack(side="right")
            elif owned:
                FlatButton(row, "穿上", lambda i=oid: self._wear(i),
                           variant="primary").pack(side="right")
            else:
                FlatButton(row, f"购买 ({item['cost']}分)",
                           lambda i=oid: self._buy(i),
                           variant="primary").pack(side="right")

    def _refresh(self):
        self.bal.config(
            text=f"当前积分：{self.store.points}　·　"
                 f"饱食度：{self.store.fullness()}%")
        self._build_outfits()
        if self.pet:
            try:
                self.pet.draw()
            except Exception:
                pass

    def _feed(self, fid):
        if self.store.feed(fid):
            if self.pet:
                self.pet.say(f"{PET_FOODS[fid]['name']}好好吃～")
            messagebox.showinfo("宠物商店",
                                f"喂了「{PET_FOODS[fid]['name']}」，饱食度回复了！",
                                parent=self)
        else:
            messagebox.showwarning("宠物商店", "积分不够买这个食物哦。",
                                   parent=self)
        self._refresh()

    def _buy(self, oid):
        res = self.store.buy_outfit(oid)
        if res == "ok":
            self.store.wear(oid)
            if self.pet:
                self.pet.say(f"换上{PET_OUTFITS[oid]['name']}啦！")
            messagebox.showinfo("宠物商店",
                                f"购买并穿上了「{PET_OUTFITS[oid]['name']}」！",
                                parent=self)
        elif res == "poor":
            messagebox.showwarning("宠物商店", "积分不够买这件衣服哦。",
                                   parent=self)
        self._refresh()

    def _wear(self, oid):
        self.store.wear(oid)
        if self.pet:
            self.pet.say(f"换上{PET_OUTFITS[oid]['name']}啦！")
        self._refresh()


# ============== 桌面宠物（透明置顶小窗，可拖动） ==============
class DesktopPet(tk.Toplevel):
    TRANSPARENT = "#ff00fe"

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.store = app.pet_store

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._transparent_ok = False
        try:
            self.attributes("-transparentcolor", self.TRANSPARENT)
            self._transparent_ok = True
        except Exception:
            pass

        bg = self.TRANSPARENT if self._transparent_ok else C["bg"]
        self.configure(bg=bg)
        self.W = dp(app, 150)
        self.H = dp(app, 180)
        self.canvas = tk.Canvas(self, width=self.W, height=self.H, bg=bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = sw - self.W - dp(app, 60)
        y = sh - self.H - dp(app, 110)
        self.geometry(f"{self.W}x{self.H}+{max(0, x)}+{max(0, y)}")

        self._press_xy = None
        self._moved = False
        self._bubble_text = None
        self._bubble_after = None
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._open_menu)

        self.draw()
        self._tick()
        self.say("你好呀～我会陪你完成工作！")

    # ---------- 拖动 / 点击 ----------
    def _on_press(self, e):
        self._press_xy = (e.x_root, e.y_root,
                          self.winfo_x(), self.winfo_y())
        self._moved = False

    def _on_motion(self, e):
        if not self._press_xy:
            return
        dx = e.x_root - self._press_xy[0]
        dy = e.y_root - self._press_xy[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._moved = True
        self.geometry(f"+{self._press_xy[2] + dx}+{self._press_xy[3] + dy}")

    def _on_release(self, e):
        if not self._moved:
            self._open_menu(e)
        self._press_xy = None

    def _open_menu(self, e):
        m = tk.Menu(self, tearoff=0, bg=C["elevated"], fg=C["fg"],
                    activebackground=C["accent"],
                    activeforeground=C["accent_fg"], bd=0, font=F_BTN)
        m.add_command(
            label=f"喂食 · 日常餐 ({PET_FOODS['richan']['cost']}分)",
            command=lambda: self.do_feed("richan"))
        m.add_command(label="宠物商店 / 换装…", command=self.open_shop)
        if self.store.can_checkin():
            m.add_command(label=f"今日打卡 (+{POINTS_CHECKIN}分)",
                          command=self.do_checkin)
        m.add_separator()
        m.add_command(
            label=f"积分 {self.store.points} · 饱食 {self.store.fullness()}%",
            state="disabled")
        m.add_command(label="打开主程序", command=self._open_main)
        m.add_separator()
        m.add_command(label="收起桌宠", command=self.app._toggle_pet)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _open_main(self):
        try:
            self.app.deiconify()
            self.app.lift()
            self.app.focus_force()
        except Exception:
            pass

    # ---------- 行为 ----------
    def do_feed(self, food_id):
        food = PET_FOODS.get(food_id, {})
        if self.store.feed(food_id):
            self.say(f"{food.get('name', '')}好好吃～")
        else:
            self.say(f"积分不够买{food.get('name', '食物')}…")
        self.draw()

    def do_checkin(self):
        if self.store.checkin():
            self.say(f"打卡成功！下班啦 +{POINTS_CHECKIN}分")
        else:
            self.say("今天已经打过卡啦～")
        self.draw()

    def open_shop(self):
        PetShopDialog(self.app, pet=self)

    def prompt_checkin(self):
        if self.store.can_checkin():
            self.say(f"{CHECKIN_REMIND_TEXT} 啦，记得下班打卡！点我→今日打卡")

    def on_points(self, n, reason):
        self.say(f"+{n}分 · {reason}")
        self.draw()

    # ---------- 气泡 ----------
    def say(self, text, secs=4500):
        self._bubble_text = text
        if self._bubble_after:
            try:
                self.after_cancel(self._bubble_after)
            except Exception:
                pass
        self._bubble_after = self.after(secs, self._clear_bubble)
        self.draw()

    def _clear_bubble(self):
        self._bubble_text = None
        self._bubble_after = None
        try:
            self.draw()
        except Exception:
            pass

    # ---------- 定时 ----------
    def _tick(self):
        try:
            self.draw()
        except Exception:
            pass
        self._tick_after = self.after(60000, self._tick)

    # ---------- 绘制 ----------
    def draw(self):
        c = self.canvas
        c.delete("all")
        W, H = self.W, self.H
        cx = W / 2
        head_cy = H * 0.52
        R = W * 0.30
        mood = self.store.mood()
        outfit = self.store.outfit

        body = "#F7C873"
        body_line = "#E0A94E"
        ear_in = "#F6A6B2"
        cheek = "#F6A6B2"
        dark = "#3A2E2E"

        # 身体
        c.create_oval(cx - W * 0.25, H * 0.64, cx + W * 0.25, H * 0.98,
                      fill=body, outline=body_line, width=2)
        # 连衣裙覆盖身体
        if outfit == "dress":
            c.create_polygon(
                cx - W * 0.10, H * 0.70, cx + W * 0.10, H * 0.70,
                cx + W * 0.27, H * 0.98, cx - W * 0.27, H * 0.98,
                fill="#E86A92", outline="#C84F77", width=2, smooth=False)

        # 耳朵
        c.create_polygon(cx - R * 0.85, head_cy - R * 0.55,
                         cx - R * 0.30, head_cy - R * 1.15,
                         cx - R * 0.05, head_cy - R * 0.55,
                         fill=body, outline=body_line, width=2)
        c.create_polygon(cx + R * 0.85, head_cy - R * 0.55,
                         cx + R * 0.30, head_cy - R * 1.15,
                         cx + R * 0.05, head_cy - R * 0.55,
                         fill=body, outline=body_line, width=2)
        c.create_polygon(cx - R * 0.62, head_cy - R * 0.62,
                         cx - R * 0.33, head_cy - R * 0.98,
                         cx - R * 0.18, head_cy - R * 0.62,
                         fill=ear_in, outline="")
        c.create_polygon(cx + R * 0.62, head_cy - R * 0.62,
                         cx + R * 0.33, head_cy - R * 0.98,
                         cx + R * 0.18, head_cy - R * 0.62,
                         fill=ear_in, outline="")

        # 头
        c.create_oval(cx - R, head_cy - R, cx + R, head_cy + R,
                      fill=body, outline=body_line, width=2)

        # 帽子
        if outfit == "hat":
            c.create_arc(cx - R * 0.9, head_cy - R * 1.45,
                         cx + R * 0.9, head_cy - R * 0.25,
                         start=0, extent=180, fill="#5B6CC9", outline="#3F4DA0",
                         width=2, style="chord")
            c.create_rectangle(cx - R * 1.05, head_cy - R * 0.5,
                               cx + R * 1.05, head_cy - R * 0.32,
                               fill="#5B6CC9", outline="#3F4DA0", width=2)

        # 眼睛（随心情）
        eye_y = head_cy - R * 0.05
        ex = R * 0.42
        if mood == "happy":
            c.create_arc(cx - ex - 7, eye_y - 7, cx - ex + 7, eye_y + 7,
                         start=20, extent=140, style="arc", width=3,
                         outline=dark)
            c.create_arc(cx + ex - 7, eye_y - 7, cx + ex + 7, eye_y + 7,
                         start=20, extent=140, style="arc", width=3,
                         outline=dark)
        elif mood == "hungry":
            c.create_line(cx - ex - 6, eye_y - 5, cx - ex + 6, eye_y + 3,
                          width=3, fill=dark)
            c.create_line(cx + ex - 6, eye_y + 3, cx + ex + 6, eye_y - 5,
                          width=3, fill=dark)
        else:
            c.create_oval(cx - ex - 5, eye_y - 6, cx - ex + 5, eye_y + 6,
                          fill=dark, outline="")
            c.create_oval(cx + ex - 5, eye_y - 6, cx + ex + 5, eye_y + 6,
                          fill=dark, outline="")

        # 腮红 + 鼻嘴
        c.create_oval(cx - R * 0.78, eye_y + R * 0.18, cx - R * 0.42,
                      eye_y + R * 0.46, fill=cheek, outline="")
        c.create_oval(cx + R * 0.42, eye_y + R * 0.18, cx + R * 0.78,
                      eye_y + R * 0.46, fill=cheek, outline="")
        nose_y = eye_y + R * 0.34
        c.create_oval(cx - 3, nose_y - 3, cx + 3, nose_y + 3,
                      fill="#9A5B3E", outline="")
        if mood == "hungry":
            c.create_oval(cx - 6, nose_y + 4, cx + 6, nose_y + 12,
                          outline=dark, width=2)
        else:
            c.create_arc(cx - 8, nose_y, cx + 8, nose_y + 12, start=200,
                         extent=140, style="arc", width=2, outline=dark)

        # 墨镜
        if outfit == "glasses":
            for sx in (-ex, ex):
                c.create_oval(cx + sx - 11, eye_y - 9, cx + sx + 11, eye_y + 9,
                              fill="#222831", outline="#111418", width=2)
            c.create_line(cx - ex + 11, eye_y, cx + ex - 11, eye_y,
                          width=3, fill="#111418")

        # 围巾
        if outfit == "scarf":
            c.create_rectangle(cx - R * 0.9, head_cy + R * 0.78,
                               cx + R * 0.9, head_cy + R * 1.05,
                               fill="#D94A4A", outline="#B23636", width=2)
            c.create_rectangle(cx + R * 0.25, head_cy + R * 0.95,
                               cx + R * 0.55, head_cy + R * 1.6,
                               fill="#D94A4A", outline="#B23636", width=2)

        # 蝴蝶结
        if outfit == "bow":
            bx, by = cx + R * 0.7, head_cy - R * 0.8
            c.create_polygon(bx, by, bx - 14, by - 9, bx - 14, by + 9,
                             fill="#E86A92", outline="#C84F77", width=1)
            c.create_polygon(bx, by, bx + 14, by - 9, bx + 14, by + 9,
                             fill="#E86A92", outline="#C84F77", width=1)
            c.create_oval(bx - 4, by - 4, bx + 4, by + 4, fill="#C84F77",
                          outline="")

        # 气泡
        if self._bubble_text:
            self._draw_bubble(self._bubble_text)

    def _draw_bubble(self, text):
        c = self.canvas
        W = self.W
        pad = 8
        c.create_rectangle(6, 4, W - 6, self.H * 0.30,
                           fill="#FFFFFF", outline="#C8C8C8", width=1)
        c.create_text(W / 2, self.H * 0.17, text=text, width=W - 6 - pad * 2,
                      font=(UI_FAMILY, 9), fill="#222222", justify="center")


# ============== 桌面宠物（图片 · 逐像素透明 · 动画） ==============
from ctypes import wintypes  # noqa: E402

if SYSTEM == "Windows":
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32

    _GWL_EXSTYLE = -20
    _WS_EX_LAYERED = 0x80000
    _ULW_ALPHA = 0x02
    _AC_SRC_OVER = 0x00
    _AC_SRC_ALPHA = 0x01
    _HWND_TOPMOST = -1
    _SWP_NOMOVE = 0x2
    _SWP_NOSIZE = 0x1

    class _BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                    ("SourceConstantAlpha", ctypes.c_byte),
                    ("AlphaFormat", ctypes.c_byte)]

    class _BMIHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    def _win_hwnd(win):
        win.update_idletasks()
        return _user32.GetParent(win.winfo_id())

    def _win_make_layered(hwnd):
        ex = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)
        _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                             _SWP_NOMOVE | _SWP_NOSIZE)

    def _premultiplied_bgra(img):
        r, g, b, a = img.convert("RGBA").split()
        r = ImageChops.multiply(r, a)
        g = ImageChops.multiply(g, a)
        b = ImageChops.multiply(b, a)
        return Image.merge("RGBA", (r, g, b, a)).tobytes("raw", "BGRA")

    def _win_blit(hwnd, w, h, bgra, x, y):
        screen = _user32.GetDC(0)
        mem = _gdi32.CreateCompatibleDC(screen)
        bmi = _BMIHEADER()
        bmi.biSize = ctypes.sizeof(_BMIHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        ppv = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0,
                                       ctypes.byref(ppv), None, 0)
        ctypes.memmove(ppv, bgra, len(bgra))
        old = _gdi32.SelectObject(mem, hbmp)
        size = wintypes.SIZE(w, h)
        psrc = wintypes.POINT(0, 0)
        pdst = wintypes.POINT(int(x), int(y))
        blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
        _user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(pdst),
                                    ctypes.byref(size), mem,
                                    ctypes.byref(psrc), 0,
                                    ctypes.byref(blend), _ULW_ALPHA)
        _gdi32.SelectObject(mem, old)
        _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(0, screen)


class PetBubble(tk.Toplevel):
    """桌宠头顶的小气泡（独立置顶小窗）。"""

    def __init__(self, app):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.96)
        except Exception:
            pass
        self.configure(bg="#FFF7FB")
        self.lbl = tk.Label(
            self, text="", bg="#FFF7FB", fg="#7A5A6A",
            font=(BUTTON_FAMILY, 10), wraplength=200, justify="center",
            padx=12, pady=7, bd=0,
            highlightthickness=1, highlightbackground="#F0C8DC",
        )
        self.lbl.pack()
        self.withdraw()
        self._after = None

    def show(self, text, cx, cy, secs=4.0):
        self.lbl.config(text=text)
        self.update_idletasks()
        w = self.winfo_reqwidth()
        self.geometry(f"+{int(cx - w / 2)}+{int(cy)}")
        self.deiconify()
        self.lift()
        if self._after:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
        self._after = self.after(int(secs * 1000), self.hide)

    def hide(self):
        try:
            self.withdraw()
        except Exception:
            pass


class LayeredPet(tk.Toplevel):
    """
    用原画图片做的桌面宠物：逐像素透明、置顶、可拖动，
    带待机呼吸、眨眼/伸懒腰/睡觉、点击互动、动作切换淡入淡出。
    动作以「状态」组织，新增动作只要往 POSES / 调度里加即可。
    """

    # 8 个动作姿势（来自 poses.png，已按统一比例导出）
    POSES = ["lookup", "pawtap", "pawreach", "lying",
             "standing", "walk", "tailwag1", "tailwag2"]
    IDLE_POSE = "lookup"
    SLEEP_POSE = "lying"
    # 摇尾巴：在两张尾巴帧之间来回切换，形成真正的摆动
    WAG_SEQ = [("tailwag1", 0.32), ("tailwag2", 0.32)]
    CANVAS_W = 220
    CANVAS_H = 300
    FADE = 0.26
    SLEEP_AFTER = 50.0
    AURA_PARTICLES = 54

    REGION_BOXES = {
        "lookup": {
            "ears": (0.12, 0.03, 0.92, 0.32),
            "chest": (0.24, 0.35, 0.82, 0.82),
            "tail": (0.00, 0.48, 0.48, 0.90),
        },
        "pawtap": {
            "ears": (0.10, 0.05, 0.92, 0.36),
            "chest": (0.20, 0.38, 0.80, 0.88),
            "paw": (0.55, 0.34, 0.98, 0.70),
            "tail": (0.00, 0.54, 0.42, 0.93),
        },
        "pawreach": {
            "ears": (0.06, 0.03, 0.92, 0.34),
            "chest": (0.18, 0.36, 0.78, 0.86),
            "paw": (0.58, 0.15, 1.00, 0.52),
            "tail": (0.00, 0.55, 0.45, 0.94),
        },
        "lying": {
            "ears": (0.12, 0.18, 0.74, 0.55),
            "chest": (0.08, 0.48, 0.66, 0.88),
            "tail": (0.48, 0.12, 1.00, 0.58),
        },
        "standing": {
            "ears": (0.15, 0.03, 0.92, 0.34),
            "chest": (0.30, 0.35, 0.88, 0.84),
            "paw": (0.46, 0.58, 0.88, 0.98),
            "tail": (0.00, 0.32, 0.48, 0.84),
        },
        "walk": {
            "ears": (0.08, 0.02, 0.82, 0.36),
            "chest": (0.18, 0.38, 0.82, 0.88),
            "paw": (0.00, 0.48, 0.44, 0.92),
            "tail": (0.48, 0.00, 1.00, 0.56),
        },
        "tailwag1": {
            "ears": (0.12, 0.18, 0.90, 0.45),
            "chest": (0.20, 0.45, 0.82, 0.88),
            "tail": (0.28, 0.00, 1.00, 0.48),
        },
        "tailwag2": {
            "ears": (0.08, 0.20, 0.90, 0.45),
            "chest": (0.18, 0.44, 0.84, 0.88),
            "tail": (0.18, 0.00, 0.92, 0.48),
        },
    }

    EYE_SPOTS = {
        "lookup": ((0.414, 0.333, 0.135, 0.060), (0.626, 0.305, 0.135, 0.060)),
        "pawtap": ((0.432, 0.314, 0.135, 0.065), (0.633, 0.263, 0.130, 0.065)),
        "pawreach": ((0.329, 0.246, 0.140, 0.060), (0.512, 0.189, 0.130, 0.055)),
        "lying": ((0.283, 0.584, 0.140, 0.075), (0.498, 0.564, 0.140, 0.075)),
        "standing": ((0.430, 0.376, 0.130, 0.060), (0.598, 0.328, 0.125, 0.060)),
        "walk": ((0.297, 0.423, 0.130, 0.065), (0.492, 0.434, 0.130, 0.065)),
        "tailwag1": ((0.274, 0.458, 0.135, 0.065), (0.495, 0.419, 0.130, 0.065)),
    }

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.store = app.pet_store
        if not (SYSTEM == "Windows" and _PIL_OK):
            raise RuntimeError("LayeredPet 需要 Windows + Pillow")

        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.W, self.H = self.CANVAS_W, self.CANVAS_H
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        pos = app.config.get("pet_pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.x, self.y = int(pos[0]), int(pos[1])
        else:
            self.x, self.y = sw - self.W - 60, sh - self.H - 90
        self.geometry(f"{self.W}x{self.H}+{self.x}+{self.y}")

        self._hwnd = _win_hwnd(self)
        _win_make_layered(self._hwnd)

        self.frames = self._load_frames()
        self._mask_cache = {}
        self._pose_meta = self._prepare_pose_meta()
        self._glow = self._make_glow()
        self._glow_warm = self._make_glow(color=(255, 198, 96))
        self.particles = []
        self._spawn_acc = 0.0
        self._last_t = time.time()
        self._blink_start = 0.0
        self._blink_until = 0.0
        self._next_blink = self._last_t + random.uniform(2.2, 4.8)
        self._blink_amount = 0.0

        self.state = self.IDLE_POSE
        self._prev_state = self.IDLE_POSE
        self._fade_start = 0.0
        self._fading = False
        self.sleeping = False
        self.oneshot_until = None
        self._seq = None          # 摇尾巴等多帧动作
        self._seq_t0 = 0.0
        self._seq_end = 0.0
        self.last_interact = time.time()
        self._last_click_react = 0.0
        self.next_action = time.time() + random.uniform(6, 11)

        self.bubble = PetBubble(app)

        self._press = None
        self._moved = False
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-3>", self._on_menu)

        self._running = True
        self._tick()
        self.after(400, lambda: self.say("喵～我会陪你一起完成工作！"))

    # ---------- 资源 ----------
    def _load_frames(self):
        """每个姿势「只含猫」的 RGBA 图（已按统一比例导出，直接用）。"""
        frames = {}
        for name in self.POSES:
            try:
                im = Image.open(
                    resource_path("assets", "pet", f"cat_{name}.png")
                ).convert("RGBA")
            except Exception:
                im = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
            frames[name] = im
        return frames

    @staticmethod
    def _make_glow(size=34, color=(255, 228, 150)):
        m = Image.new("L", (size, size), 0)
        d = ImageDraw.Draw(m)
        d.ellipse([size * 0.28, size * 0.28, size * 0.72, size * 0.72],
                  fill=255)
        m = m.filter(ImageFilter.GaussianBlur(size * 0.17))
        spr = Image.new("RGBA", (size, size), color + (0,))
        spr.putalpha(m)
        return spr

    def _prepare_pose_meta(self):
        meta = {}
        for name, im in self.frames.items():
            alpha = im.getchannel("A")
            bbox = alpha.getbbox() or (0, 0, im.width, im.height)
            padded = Image.new("L", (im.width + 36, im.height + 36), 0)
            padded.paste(alpha, (18, 18))
            expanded = padded.filter(ImageFilter.MaxFilter(11))
            softened = padded.filter(ImageFilter.MinFilter(5))
            edge = ImageChops.subtract(expanded, softened)
            halo_alpha = edge.filter(ImageFilter.GaussianBlur(6))
            halo_alpha = halo_alpha.point(lambda v: min(205, int(v * 1.24)))
            halo = Image.new("RGBA", halo_alpha.size, (255, 216, 128, 0))
            halo.putalpha(halo_alpha)

            edge_local = ImageChops.subtract(
                alpha.filter(ImageFilter.MaxFilter(9)),
                alpha.filter(ImageFilter.MinFilter(5)),
            ).filter(ImageFilter.GaussianBlur(1.0))
            points = self._edge_points(edge_local)
            if not points:
                points = [(bbox[0], bbox[1]), (bbox[2], bbox[1]),
                          (bbox[2], bbox[3]), (bbox[0], bbox[3])]
            meta[name] = {
                "alpha": alpha,
                "bbox": bbox,
                "edge_points": points,
                "halo": halo,
                "halo_pad": 18,
            }
        return meta

    @staticmethod
    def _edge_points(edge):
        pix = edge.load()
        points = []
        step = 4
        for y in range(0, edge.height, step):
            for x in range(0, edge.width, step):
                if pix[x, y] > 20:
                    points.append((x, y))
        return points

    @staticmethod
    def _scale_alpha(img, factor):
        factor = max(0.0, min(1.0, factor))
        out = img.copy()
        out.putalpha(out.getchannel("A").point(lambda v: int(v * factor)))
        return out

    def _region_mask(self, state, name, blur=5):
        key = (state, name, blur)
        if key in self._mask_cache:
            return self._mask_cache[key]
        im = self.frames[state]
        alpha = self._pose_meta[state]["alpha"]
        box = self.REGION_BOXES.get(state, {}).get(name)
        if box is None:
            box = (0.12, 0.12, 0.88, 0.92)
        x0 = int(im.width * box[0])
        y0 = int(im.height * box[1])
        x1 = int(im.width * box[2])
        y1 = int(im.height * box[3])
        m = Image.new("L", im.size, 0)
        d = ImageDraw.Draw(m)
        d.ellipse([x0, y0, x1, y1], fill=255)
        if blur:
            m = m.filter(ImageFilter.GaussianBlur(blur))
        m = ImageChops.multiply(m, alpha.point(lambda v: min(255, v * 2)))
        self._mask_cache[key] = m
        return m

    def _warp_region(self, img, mask, dx=0.0, dy=0.0, angle=0.0,
                     scale=1.0, erase=0.0):
        if (abs(dx) < 0.05 and abs(dy) < 0.05 and abs(angle) < 0.05
                and abs(scale - 1.0) < 0.004):
            return img
        box = mask.getbbox()
        if not box:
            return img
        x0, y0, x1, y1 = box
        pad = 8
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(img.width, x1 + pad)
        y1 = min(img.height, y1 + pad)
        region = img.crop((x0, y0, x1, y1))
        mask_crop = mask.crop((x0, y0, x1, y1))
        region.putalpha(ImageChops.multiply(region.getchannel("A"), mask_crop))

        out = img.copy()
        if erase > 0:
            erase_mask = mask_crop.point(lambda v: int(v * min(0.72, erase)))
            blank = Image.new("RGBA", region.size, (0, 0, 0, 0))
            out.paste(blank, (x0, y0), erase_mask)

        if abs(scale - 1.0) >= 0.004:
            nw = max(1, int(region.width * scale))
            nh = max(1, int(region.height * scale))
            region = region.resize((nw, nh), Image.BICUBIC)
        if abs(angle) >= 0.05:
            region = region.rotate(angle, resample=Image.BICUBIC,
                                   expand=True)

        cx = (x0 + x1) / 2 + dx
        cy = (y0 + y1) / 2 + dy
        px = int(round(cx - region.width / 2))
        py = int(round(cy - region.height / 2))
        out.alpha_composite(region, (px, py))
        return out

    def _update_blink(self, now):
        if now >= self._next_blink:
            self._blink_start = now
            self._blink_until = now + random.uniform(0.16, 0.24)
            self._next_blink = self._blink_until + random.uniform(2.8, 6.4)
        if now >= self._blink_until:
            return 0.0
        span = max(0.01, self._blink_until - self._blink_start)
        t = (now - self._blink_start) / span
        return max(0.0, math.sin(math.pi * t))

    def force_blink(self, duration=0.22):
        now = time.time()
        self._blink_start = now
        self._blink_until = now + max(0.12, duration)
        self._next_blink = self._blink_until + random.uniform(2.8, 5.4)

    @staticmethod
    def _local_fur_color(img, x, y, ww, hh):
        pix = img.load()
        colors = []
        rx = max(6, int(ww * 0.85))
        ry = max(5, int(hh * 1.15))
        inner_rx = max(1.0, ww * 0.42)
        inner_ry = max(1.0, hh * 0.42)
        for yy in range(max(0, y - ry), min(img.height, y + ry + 1)):
            for xx in range(max(0, x - rx), min(img.width, x + rx + 1)):
                nx = (xx - x) / inner_rx
                ny = (yy - y) / inner_ry
                if nx * nx + ny * ny < 1.0:
                    continue
                r, g, b, a = pix[xx, yy]
                if a < 120:
                    continue
                brightness = r * 0.299 + g * 0.587 + b * 0.114
                if brightness < 115:
                    continue
                colors.append((r, g, b))
        if len(colors) < 8:
            return (234, 190, 132)
        colors.sort(key=lambda c: c[0] + c[1] + c[2])
        keep = colors[len(colors) // 5:max(len(colors) // 5 + 1, len(colors) * 4 // 5)]
        n = len(keep)
        return (
            int(sum(c[0] for c in keep) / n),
            int(sum(c[1] for c in keep) / n),
            int(sum(c[2] for c in keep) / n),
        )

    def _apply_blink(self, img, state, amount):
        if amount <= 0.05 or state == "tailwag2":
            return img
        spots = self.EYE_SPOTS.get(state)
        if not spots:
            return img
        closed = min(1.0, amount * 1.55)
        cover = Image.new("RGBA", img.size, (0, 0, 0, 0))
        line_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(line_layer)
        for cx, cy, rw, rh in spots:
            x = int(img.width * cx)
            y = int(img.height * cy)
            ww = max(7, int(img.width * rw * (0.95 + 0.10 * closed)))
            hh = max(5, int(img.height * rh * (0.95 + 0.08 * closed)))
            fur = self._local_fur_color(img, x, y, ww, hh)
            mask = Image.new("L", img.size, 0)
            md = ImageDraw.Draw(mask)
            md.ellipse([x - ww // 2, y - hh // 2, x + ww // 2,
                        y + hh // 2], fill=int(238 * closed))
            mask = mask.filter(ImageFilter.GaussianBlur(1.2))
            patch = Image.new("RGBA", img.size, fur + (0,))
            patch.putalpha(mask)
            cover = Image.alpha_composite(cover, patch)

            line = (82, 48, 34, int(225 * closed))
            arc_h = max(4, int(hh * 1.15))
            arc_box = [x - ww // 2, y - arc_h // 2,
                       x + ww // 2, y + arc_h // 2]
            try:
                d.arc(arc_box, 12, 168, fill=line,
                      width=max(1, int(1 + 1.4 * closed)))
            except TypeError:
                d.arc(arc_box, 12, 168, fill=line)
        return Image.alpha_composite(Image.alpha_composite(img, cover),
                                     line_layer)

    def _ear_wiggle(self, now):
        span = 0.58
        t = now % 7.2
        if t > span:
            return 0.0
        envelope = math.sin(math.pi * t / span)
        return envelope * math.sin(math.tau * 4.0 * t / span)

    def _animated_pose_frame(self, state, breath, now):
        if state not in self.frames:
            state = self._base_state()
        img = self.frames[state].copy()

        chest = self._region_mask(state, "chest", blur=7)
        img = self._warp_region(img, chest, dy=-0.9 * breath,
                                scale=1.0 + 0.004 * max(0.0, breath),
                                erase=0.0)

        ear = self._ear_wiggle(now)
        if abs(ear) > 0.04 and not self.sleeping:
            img = self._warp_region(
                img, self._region_mask(state, "ears", blur=4),
                dx=1.2 * ear, dy=-0.35 * abs(ear), angle=1.2 * ear,
                erase=0.0,
            )

        if state in ("tailwag1", "tailwag2"):
            wag = math.sin(now * math.tau / 0.64)
            img = self._warp_region(
                img, self._region_mask(state, "tail", blur=6),
                dx=3.6 * wag, dy=0.8 * math.sin(now * math.tau / 1.28),
                angle=1.4 * wag, erase=0.46,
            )
        elif state in ("lookup", "standing", "walk", "pawtap",
                       "pawreach", "lying"):
            tail = math.sin(now * math.tau / (2.8 if state == "walk" else 4.6))
            img = self._warp_region(
                img, self._region_mask(state, "tail", blur=7),
                dx=1.2 * tail, dy=0.45 * math.sin(now * math.tau / 3.4),
                angle=0.55 * tail, erase=0.20,
            )

        if state == "pawtap":
            tap = 0.5 + 0.5 * math.sin(now * math.tau / 0.78)
            img = self._warp_region(
                img, self._region_mask(state, "paw", blur=5),
                dx=-0.35 * tap, dy=3.4 * tap, angle=-1.1 * tap,
                erase=0.34,
            )
        elif state == "pawreach":
            reach = math.sin(now * math.tau / 1.8)
            img = self._warp_region(
                img, self._region_mask(state, "paw", blur=5),
                dx=1.2 * reach, dy=-1.8 * (0.5 + 0.5 * reach),
                angle=1.0 * reach, erase=0.30,
            )
        elif state == "walk":
            step = math.sin(now * math.tau / 0.72)
            img = self._warp_region(
                img, self._region_mask(state, "paw", blur=5),
                dx=2.1 * step, dy=-2.0 * abs(step), angle=1.0 * step,
                erase=0.36,
            )
        elif state == "standing":
            turn = math.sin(now * math.tau / 3.2)
            img = self._warp_region(
                img, self._region_mask(state, "paw", blur=6),
                dx=0.8 * turn, dy=-0.7 * abs(turn), angle=0.7 * turn,
                erase=0.18,
            )

        img = self._apply_blink(img, state, self._blink_amount)
        return img

    def _pose_offset(self, state, now, breath):
        if state == "walk":
            step = math.sin(now * math.tau / 0.72)
            return 3.2 * step, -2.0 * abs(step)
        if state == "pawreach":
            return 0.8 * math.sin(now * math.tau / 1.8), -0.8 * abs(breath)
        if state == "pawtap":
            return 0.5 * math.sin(now * math.tau / 0.78), 0.0
        if state == "standing":
            return 0.9 * math.sin(now * math.tau / 3.4), 0.0
        return 0.0, 0.0

    def _draw_sleep_zzz(self, cv, now, x0, y0, scale, breath):
        phase = (now % 4.8) / 4.8
        for i, text in enumerate(("Z", "z", "z")):
            t = (phase + i * 0.22) % 1.0
            a = int(118 * math.sin(math.pi * t)
                    * (0.86 + 0.10 * max(0.0, breath)))
            if a <= 4:
                continue
            x = x0 + int((104 + 12 * i + 6 * math.sin(t * math.tau)) * scale)
            y = y0 + int((18 - 34 * t - 4 * i) * scale)
            d = ImageDraw.Draw(cv)
            d.text((x, y), text, fill=(255, 222, 132, a))

    # ---------- 状态机 ----------
    def set_state(self, name, oneshot=None):
        now = time.time()
        if name not in self.frames:
            name = self._base_state()
        if name != self.state:
            self._prev_state = self.state
            self.state = name
            self._fade_start = now
            self._fading = True
        if oneshot:
            self.oneshot_until = now + oneshot

    def play_seq(self, seq, dur):
        now = time.time()
        self._seq = [(pose, d) for pose, d in seq if pose in self.frames]
        if not self._seq:
            self.set_state(self._base_state(), oneshot=dur)
            return
        self._seq_t0 = now
        self._seq_end = now + dur

    def _base_state(self):
        return self.SLEEP_POSE if self.sleeping else self.IDLE_POSE

    def _start_random_action(self, now):
        # 摇尾巴用多帧序列；其余是单姿势定格
        kind = random.choice(
            ["wag", "wag", "pawtap", "pawreach", "standing", "walk",
             "walk", "lookup"])
        if kind == "wag":
            self.play_seq(self.WAG_SEQ, random.uniform(2.2, 3.0))
        else:
            self.set_state(kind, oneshot=random.uniform(1.6, 2.1))

    def _update_state(self, now):
        # 多帧序列动作（摇尾巴）优先
        if self._seq is not None:
            if now >= self._seq_end:
                self._seq = None
                self.set_state(self._base_state())
            else:
                cyc = sum(d for _, d in self._seq) or 0.6
                tt = (now - self._seq_t0) % cyc
                acc = 0.0
                for pose, d in self._seq:
                    acc += d
                    if tt < acc:
                        if self.state != pose:
                            self.set_state(pose)
                        break
                return
        if self.oneshot_until and now >= self.oneshot_until:
            self.oneshot_until = None
            self.set_state(self._base_state())
        if self.oneshot_until:
            return
        if self.sleeping:
            if self.state != self.SLEEP_POSE:
                self.set_state(self.SLEEP_POSE)
            return
        if now - self.last_interact > self.SLEEP_AFTER:
            self.sleeping = True
            self.set_state(self.SLEEP_POSE)
            return
        if now >= self.next_action:
            self.next_action = now + random.uniform(7, 13)
            self._start_random_action(now)

    # ---------- 合成 + 绘制 ----------
    def _legacy_pose_canvas(self, state, breath):
        """把某姿势的猫按呼吸缩放后，底部居中放到透明画布上。"""
        cat = self.frames[state]
        sy = 1.0 + 0.018 * breath      # 胸口/身体随呼吸起伏
        sx = 1.0 + 0.007 * breath
        nw = max(1, int(cat.width * sx))
        nh = max(1, int(cat.height * sy))
        catr = cat.resize((nw, nh), Image.BILINEAR)
        cv = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        cv.alpha_composite(catr, (max(0, (self.W - nw) // 2),
                                  max(0, self.H - nh - 6)))
        return cv

    # ---------- 金色绒毛粒子 ----------
    def _legacy_spawn_particle(self):
        self.particles.append({
            "x": random.uniform(self.W * 0.12, self.W * 0.88),
            "y": random.uniform(self.H * 0.30, self.H * 0.92),
            "vx": random.uniform(-7, 7),
            "vy": random.uniform(-16, -4),
            "life": 0.0,
            "max": random.uniform(1.6, 3.4),
            "size": random.uniform(8, 20),
            "tw": random.uniform(0, math.tau),
            "tws": random.uniform(2.0, 4.2),
            "warm": random.random() < 0.45,
        })

    def _legacy_update_particles(self, dt):
        dt = min(dt, 0.1)
        alive = []
        for p in self.particles:
            p["life"] += dt
            if p["life"] >= p["max"]:
                continue
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vx"] *= 0.99
            alive.append(p)
        self.particles = alive
        self._spawn_acc += dt
        while len(self.particles) < 32 and self._spawn_acc > 0.035:
            self._spawn_acc -= 0.035
            self._spawn_particle()

    def _legacy_draw_particles(self, cv, now):
        W, H = self.W, self.H
        for p in self.particles:
            fr = p["life"] / p["max"]
            fade = math.sin(math.pi * fr)            # 聚合(淡入)->散开(淡出)
            tw = 0.55 + 0.45 * math.sin(p["tw"] + now * p["tws"])  # 闪烁
            a = fade * tw
            if a <= 0.03:
                continue
            s = max(2, int(p["size"] * (0.5 + 0.6 * fade)))
            base = self._glow_warm if p["warm"] else self._glow
            spr = base.resize((s, s), Image.BILINEAR)
            spr.putalpha(spr.getchannel("A").point(lambda v: int(v * a)))
            x0 = int(p["x"] - s / 2)
            y0 = int(p["y"] - s / 2)
            cx0, cy0 = max(0, -x0), max(0, -y0)
            cx1, cy1 = min(s, W - x0), min(s, H - y0)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            sub = spr.crop((cx0, cy0, cx1, cy1))
            cv.alpha_composite(sub, (x0 + cx0, y0 + cy0))

    def _legacy_compose(self, now, dt):
        period = 4.5 if self.sleeping else 3.2
        breath = math.sin(now * (math.tau / period))
        if self._fading:
            t = (now - self._fade_start) / self.FADE
            if t >= 1:
                self._fading = False
                cv = self._pose_canvas(self.state, breath)
            else:
                cv = Image.blend(self._pose_canvas(self._prev_state, breath),
                                 self._pose_canvas(self.state, breath), t)
        else:
            cv = self._pose_canvas(self.state, breath)
        self._update_particles(dt)
        self._draw_particles(cv, now)
        offy = round((1.0 if self.sleeping else 2.0) *
                     math.sin(now * (math.tau / period)))
        return cv, offy

    def _pose_canvas(self, state, breath, now):
        """Render one animated pose on a transparent desktop-pet canvas."""
        if state not in self.frames:
            state = self._base_state()
        cat = self._animated_pose_frame(state, breath, now)
        sy = 1.0 + 0.018 * breath
        sx = 1.0 + 0.007 * breath
        nw = max(1, int(cat.width * sx))
        nh = max(1, int(cat.height * sy))
        catr = cat.resize((nw, nh), Image.BILINEAR)
        cv = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ox, oy = self._pose_offset(state, now, breath)
        x = int(round((self.W - nw) / 2 + ox))
        y = int(round(self.H - nh - 6 + oy))

        meta = self._pose_meta[state]
        halo = meta["halo"]
        hw = max(1, int(halo.width * sx))
        hh = max(1, int(halo.height * sy))
        halo_r = halo.resize((hw, hh), Image.BILINEAR)
        halo_power = 0.42 + 0.10 * (0.5 + 0.5 * breath)
        halo_r = self._scale_alpha(halo_r, halo_power)
        pad = meta["halo_pad"]
        cv.alpha_composite(halo_r, (
            int(round(x - pad * sx)),
            int(round(y - pad * sy)),
        ))
        cv.alpha_composite(catr, (x, y))
        if state == self.SLEEP_POSE:
            self._draw_sleep_zzz(cv, now, x, y, sx, breath)
        return cv

    def _spawn_particle(self):
        state = self.state if self.state in self.frames else self._base_state()
        frame = self.frames[state]
        points = self._pose_meta[state]["edge_points"]
        px, py = random.choice(points)
        x0 = (self.W - frame.width) / 2
        y0 = self.H - frame.height - 6
        home_x = x0 + px + random.uniform(-8, 8)
        home_y = y0 + py + random.uniform(-8, 8)
        self.particles.append({
            "x": home_x + random.uniform(-12, 12),
            "y": home_y + random.uniform(-10, 10),
            "home_x": home_x,
            "home_y": home_y,
            "vx": random.uniform(-4.5, 4.5),
            "vy": random.uniform(-7.5, 1.5),
            "life": 0.0,
            "max": random.uniform(2.4, 4.8),
            "size": random.uniform(5, 17),
            "tw": random.uniform(0, math.tau),
            "tws": random.uniform(2.2, 5.0),
            "orbit": random.uniform(0.7, 2.4),
            "warm": random.random() < 0.58,
        })

    def _update_particles(self, dt):
        dt = min(dt, 0.1)
        alive = []
        for p in self.particles:
            p["life"] += dt
            if p["life"] >= p["max"]:
                continue
            fr = p["life"] / p["max"]
            gather = math.sin(math.pi * fr)
            p["vx"] += (p["home_x"] - p["x"]) * (0.55 + 0.45 * gather) * dt
            p["vy"] += (p["home_y"] - p["y"]) * (0.45 + 0.35 * gather) * dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["x"] += math.sin(p["tw"] + p["life"] * p["orbit"]) * 0.18
            p["y"] += math.cos(p["tw"] * 0.7 + p["life"] * p["orbit"]) * 0.12
            p["vx"] *= 0.985
            p["vy"] *= 0.985
            alive.append(p)
        self.particles = alive
        self._spawn_acc += dt
        while len(self.particles) < self.AURA_PARTICLES and self._spawn_acc > 0.025:
            self._spawn_acc -= 0.025
            self._spawn_particle()

    def _draw_particles(self, cv, now, breath):
        W, H = self.W, self.H
        for p in self.particles:
            fr = p["life"] / p["max"]
            fade = math.sin(math.pi * fr)
            tw = 0.55 + 0.45 * math.sin(p["tw"] + now * p["tws"])
            a = fade * tw * (0.76 + 0.16 * (0.5 + 0.5 * breath))
            if a <= 0.03:
                continue
            s = max(2, int(p["size"] * (0.5 + 0.6 * fade)))
            base = self._glow_warm if p["warm"] else self._glow
            spr = base.resize((s, s), Image.BILINEAR)
            spr.putalpha(spr.getchannel("A").point(lambda v: int(v * a)))
            x0 = int(p["x"] - s / 2)
            y0 = int(p["y"] - s / 2)
            cx0, cy0 = max(0, -x0), max(0, -y0)
            cx1, cy1 = min(s, W - x0), min(s, H - y0)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            sub = spr.crop((cx0, cy0, cx1, cy1))
            cv.alpha_composite(sub, (x0 + cx0, y0 + cy0))

    def _compose(self, now, dt):
        period = 4.8 if self.sleeping else 3.6
        breath = math.sin(now * (math.tau / period))
        self._blink_amount = self._update_blink(now)
        if self._fading:
            t = (now - self._fade_start) / self.FADE
            if t >= 1:
                self._fading = False
                cv = self._pose_canvas(self.state, breath, now)
            else:
                cv = Image.blend(
                    self._pose_canvas(self._prev_state, breath, now),
                    self._pose_canvas(self.state, breath, now),
                    t,
                )
        else:
            cv = self._pose_canvas(self.state, breath, now)
        self._update_particles(dt)
        self._draw_particles(cv, now, breath)
        offy = round((1.0 if self.sleeping else 2.0) *
                     math.sin(now * (math.tau / period)))
        return cv, offy

    def _tick(self):
        if not self._running:
            return
        now = time.time()
        dt = now - self._last_t
        self._last_t = now
        try:
            self._update_state(now)
            img, offy = self._compose(now, dt)
            _win_blit(self._hwnd, self.W, self.H,
                      _premultiplied_bgra(img), self.x, self.y + offy)
        except Exception:
            pass
        interval = 90 if (self.sleeping and not self._fading) else 68
        self._tick_id = self.after(interval, self._tick)

    # ---------- 交互 ----------
    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.x, self.y)
        self._moved = False

    def _on_motion(self, e):
        if not self._press:
            return
        dx = e.x_root - self._press[0]
        dy = e.y_root - self._press[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._moved = True
        self.x = self._press[2] + dx
        self.y = self._press[3] + dy

    def _on_release(self, e):
        if self._press and not self._moved:
            self._react()
        elif self._moved:
            self.app.config["pet_pos"] = [int(self.x), int(self.y)]
            save_config(self.app.config)
        self._press = None

    def _react(self):
        now = time.time()
        if now - getattr(self, "_last_click_react", 0.0) < 0.45:
            return
        self._last_click_react = now
        self.last_interact = now
        self._seq = None
        self.oneshot_until = None
        if self.sleeping:
            self.sleeping = False
            self.say("呜……醒啦～")
        else:
            self.say(random.choice(["喵～", "蹭蹭你", "在呢！"]))
        self.force_blink(0.34)
        self.set_state("pawtap", oneshot=0.9)

    def _bubble_anchor(self):
        return self.x + self.W // 2, self.y + 14

    def say(self, text, secs=4.0):
        try:
            cx, cy = self._bubble_anchor()
            self.bubble.show(text, cx, cy, secs)
        except Exception:
            pass

    def on_points(self, n, reason):
        self.last_interact = time.time()
        if self.sleeping:
            self.sleeping = False
        self.force_blink(0.36)
        self.play_seq(self.WAG_SEQ, 1.6)
        self.say(f"+{n} 积分 · {reason}")

    def prompt_checkin(self):
        if self.store.can_checkin():
            self.last_interact = time.time()
            self.sleeping = False
            self.force_blink(0.34)
            self.set_state("pawtap", oneshot=1.6)
            self.say(f"{CHECKIN_REMIND_TEXT} 啦，记得下班打卡！\n右键我 →「今日打卡」")

    def do_feed(self, food_id):
        food = PET_FOODS.get(food_id, {})
        if self.store.feed(food_id):
            self.say(f"{food.get('name', '')}好好吃～")
            self.force_blink(0.32)
            self.set_state("pawtap", oneshot=1.4)
        else:
            self.say(f"积分不够买{food.get('name', '食物')}…")

    def do_checkin(self):
        if self.store.checkin():
            self.say(f"打卡成功！下班啦 +{POINTS_CHECKIN}")
            self.force_blink(0.36)
            self.play_seq(self.WAG_SEQ, 1.6)
        else:
            self.say("今天已经打过卡啦～")

    def open_shop(self):
        PetShopDialog(self.app, pet=self)

    def _play_pose_from_menu(self, pose):
        self.last_interact = time.time()
        self.sleeping = False
        self._seq = None
        self.oneshot_until = None
        if pose == "wag":
            self.play_seq(self.WAG_SEQ, 2.2)
        else:
            hold = 2.8 if pose == self.SLEEP_POSE else 2.0
            self.set_state(pose, oneshot=hold)

    def draw(self):
        pass

    def _on_menu(self, e):
        m = tk.Menu(self, tearoff=0, bg=C["elevated"], fg=C["fg"],
                    activebackground=C["accent"],
                    activeforeground=C["accent_fg"], bd=0, font=F_BTN)
        m.add_command(
            label=f"喂食 · 日常餐 ({PET_FOODS['richan']['cost']}积分)",
            command=lambda: self.do_feed("richan"))
        m.add_command(label="宠物商店 / 换装…", command=self.open_shop)
        action_menu = tk.Menu(
            m, tearoff=0, bg=C["elevated"], fg=C["fg"],
            activebackground=C["accent"],
            activeforeground=C["accent_fg"], bd=0, font=F_BTN)
        for label, pose in (
                ("抬头期待", "lookup"),
                ("拍爪", "pawtap"),
                ("伸爪", "pawreach"),
                ("趴下", "lying"),
                ("站立转身", "standing"),
                ("小跑", "walk"),
                ("摇尾巴", "wag")):
            action_menu.add_command(
                label=label,
                command=lambda p=pose: self._play_pose_from_menu(p))
        m.add_cascade(label="动作", menu=action_menu)
        if self.store.can_checkin():
            m.add_command(label=f"今日打卡 (+{POINTS_CHECKIN})",
                          command=self.do_checkin)
        m.add_separator()
        m.add_command(
            label=f"积分 {self.store.points} · 饱食 {self.store.fullness()}%",
            state="disabled")
        m.add_command(label="打开主程序", command=self._open_main)
        m.add_separator()
        m.add_command(label="收起桌宠", command=self.app._toggle_pet)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _open_main(self):
        try:
            self.app.deiconify()
            self.app.lift()
            self.app.focus_force()
        except Exception:
            pass

    def destroy(self):
        self._running = False
        try:
            if getattr(self, "_tick_id", None):
                self.after_cancel(self._tick_id)
        except Exception:
            pass
        try:
            self.bubble.destroy()
        except Exception:
            pass
        super().destroy()


# ============== 主窗口 ==============
class TodoApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # 根据屏幕 DPI 设置 Tk 缩放，让文字在高分屏 / 不同缩放下都清晰一致。
        self._ui_scale = 1.0
        try:
            dpi = self.winfo_fpixels("1i")  # 每英寸像素数
            if dpi and dpi > 0:
                self.tk.call("tk", "scaling", dpi / 72.0)
                self._ui_scale = max(1.0, dpi / 96.0)
        except Exception:
            pass

        self.config = load_config()
        self.theme_name = self.config.get("theme", DEFAULT_THEME)
        if self.theme_name not in THEMES:
            self.theme_name = DEFAULT_THEME

        C.clear()
        C.update(THEMES[self.theme_name])

        self.title(APP_NAME)
        # 记忆上次窗口大小/位置；首次启动按 DPI 缩放默认尺寸，避免高分屏下过小
        geo = self.config.get("geometry")
        if isinstance(geo, str) and "x" in geo:
            self.geometry(geo)
        else:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w = min(int(1180 * self._ui_scale), sw - 40)
            h = min(int(720 * self._ui_scale), sh - 80)
            self.geometry(f"{w}x{h}")
        min_w = min(int(960 * self._ui_scale), self.winfo_screenwidth() - 40)
        min_h = min(int(580 * self._ui_scale), self.winfo_screenheight() - 80)
        self.minsize(min_w, min_h)
        self.configure(bg=C["bg"])

        self.store = TaskStore(DATA_FILE)
        self.archive = ArchiveStore(ARCHIVE_FILE)
        self.pet_store = PetStore(PET_FILE)
        self.pet = None
        self._pet_remind_date = None

        # 记忆上次的筛选 / 排序
        saved_filter = self.config.get("filter", "全部")
        if saved_filter not in ("全部", "待办", "已完成", "今日到期", "已过期"):
            saved_filter = "全部"
        saved_sort = self.config.get("sort", "截止时间")
        if saved_sort not in ("截止时间", "优先级", "创建时间"):
            saved_sort = "截止时间"
        self.filter_mode = tk.StringVar(value=saved_filter)
        self.sort_mode = tk.StringVar(value=saved_sort)
        self.search_var = tk.StringVar()
        self._banner_after = None

        self._setup_style()
        self._build_ui()
        self._refresh()

        self.reminder = ReminderEngine(self.store, self._on_reminder)
        self.reminder.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 桌宠：默认开启；每 30 秒检查一次下班打卡提醒
        if self.config.get("pet_enabled", True):
            self.after(600, self._show_pet)
        self.after(5000, self._app_tick)

    # ---------- ttk 主题样式 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "Flat.Treeview",
            background=C["bg"],
            foreground=C["fg"],
            fieldbackground=C["bg"],
            borderwidth=0,
            rowheight=dp(self, 42),
            font=F_TASK,
        )
        style.configure(
            "Flat.Treeview.Heading",
            background=C["bg"],
            foreground=C["accent_alt"],
            font=F_HEADER,
            borderwidth=0,
            relief="flat",
            padding=(dp(self, 12), dp(self, 12)),
        )
        style.map(
            "Flat.Treeview.Heading",
            background=[("active", C["bg"])],
            foreground=[("active", C["accent"])],
        )
        style.map(
            "Flat.Treeview",
            background=[("selected", C["accent_bg"])],
            foreground=[("selected", C["select_fg"])],
        )
        style.layout("Flat.Treeview", [("Flat.Treeview.treearea", {"sticky": "nswe"})])

        style.configure(
            "Flat.Vertical.TScrollbar",
            background=C["bg"],
            troughcolor=C["bg"],
            bordercolor=C["bg"],
            arrowcolor=C["fg_muted"],
            lightcolor=C["bg"],
            darkcolor=C["bg"],
            relief="flat",
            borderwidth=0,
        )

        style.configure(
            "Flat.TCombobox",
            fieldbackground=C["elevated"],
            background=C["elevated"],
            foreground=C["fg"],
            bordercolor=C["border"],
            lightcolor=C["border"],
            darkcolor=C["border"],
            arrowcolor=C["accent_alt"],
            selectbackground=C["elevated"],
            selectforeground=C["fg"],
            relief="flat",
            borderwidth=1,
            padding=(10, 6),
            font=F_BTN,
        )
        style.map(
            "Flat.TCombobox",
            fieldbackground=[("readonly", C["elevated"]), ("focus", C["elevated"])],
            foreground=[("readonly", C["fg"])],
            bordercolor=[("focus", C["accent"])],
            arrowcolor=[("active", C["accent"])],
        )

        self.option_add("*TCombobox*Listbox*Background", C["elevated"])
        self.option_add("*TCombobox*Listbox*Foreground", C["fg"])
        self.option_add("*TCombobox*Listbox*selectBackground", C["accent_bg"])
        self.option_add("*TCombobox*Listbox*selectForeground", C["select_fg"])
        # 下拉选项也用统一的宋体（与按钮、显示/排序文字一致）
        self.option_add("*TCombobox*Listbox*Font", F_BTN)
        self.option_add("*TCombobox*Listbox*relief", "flat")
        self.option_add("*TCombobox*Listbox*borderWidth", 0)

    # ---------- 主界面 ----------
    def _build_ui(self):
        EDGE = dp(self, 40)
        self._edge = EDGE

        # 应用内提醒横幅（默认隐藏，不抢焦点）
        self.banner = tk.Frame(self, bg=C["accent"])
        self.banner_label = tk.Label(
            self.banner, text="", bg=C["accent"], fg=C["accent_fg"],
            font=F_BUTTON, anchor="w", justify="left",
            padx=EDGE, pady=10,
        )
        self.banner_label.pack(side="left", fill="x", expand=True)
        banner_close = tk.Label(
            self.banner, text="✕", bg=C["accent"], fg=C["accent_fg"],
            font=F_BUTTON, padx=18, cursor="hand2",
        )
        banner_close.pack(side="right")
        banner_close.bind("<Button-1>", lambda _e: self._hide_banner())

        header = tk.Frame(self, bg=C["bg"])
        header.pack(fill="x", padx=EDGE, pady=(36, 0))
        self._header_frame = header

        title_block = tk.Frame(header, bg=C["bg"])
        title_block.pack(side="left", anchor="w")

        tk.Label(
            title_block,
            text="DeadlineKeeper",
            bg=C["bg"],
            fg=C["fg_strong"],
            font=F_HERO,
        ).pack(anchor="w")

        # 副标题：中文“小琳专属”用行书，英文“Keeper”用艺术字，统一粉色
        sub_row = tk.Frame(title_block, bg=C["bg"])
        sub_row.pack(anchor="w", pady=(4, 0))
        tk.Label(
            sub_row, text="小琳专属", bg=C["bg"], fg=SUBTITLE_PINK,
            font=F_SUB_CN,
        ).pack(side="left", anchor="s")
        tk.Label(
            sub_row, text="Keeper", bg=C["bg"], fg=SUBTITLE_PINK,
            font=F_SUB_EN,
        ).pack(side="left", anchor="s", padx=(3, 0))

        picker = self._build_theme_picker(header)
        picker.pack(side="right", anchor="ne", pady=(10, 0))

        toolbar = tk.Frame(self, bg=C["bg"])
        toolbar.pack(fill="x", padx=EDGE, pady=(28, 0))

        FlatButton(toolbar, "＋  新建", self._add_task, variant="primary").pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "✎  编辑", self._edit_task, variant="primary").pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "⟳  延期", self._postpone_task, variant="primary").pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "✓  完成", self._toggle_complete, variant="primary").pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "✕  删除", self._delete_task, variant="primary").pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "⤓  导出", self._export_tasks).pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "▤  归档", self._open_archive).pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "桌宠", self._toggle_pet).pack(
            side="left", padx=4
        )
        FlatButton(toolbar, "数据目录", self._open_data_folder).pack(
            side="left", padx=4
        )

        sort_cb = ttk.Combobox(
            toolbar,
            textvariable=self.sort_mode,
            values=["截止时间", "优先级", "创建时间"],
            state="readonly",
            width=10,
            style="Flat.TCombobox",
            font=F_BTN,
        )
        sort_cb.pack(side="right")
        sort_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_view_changed())

        tk.Label(
            toolbar,
            text="排序",
            bg=C["bg"],
            fg=C["fg_muted"],
            font=F_BTN,
        ).pack(side="right", padx=(0, 10))

        filter_cb = ttk.Combobox(
            toolbar,
            textvariable=self.filter_mode,
            values=["全部", "待办", "已完成", "今日到期", "已过期"],
            state="readonly",
            width=10,
            style="Flat.TCombobox",
            font=F_BTN,
        )
        filter_cb.pack(side="right", padx=(0, 22))
        filter_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_view_changed())

        tk.Label(
            toolbar,
            text="显示",
            bg=C["bg"],
            fg=C["fg_muted"],
            font=F_BTN,
        ).pack(side="right", padx=(0, 10))

        # 搜索栏
        search_bar = tk.Frame(self, bg=C["bg"])
        search_bar.pack(fill="x", padx=EDGE, pady=(16, 0))
        tk.Label(
            search_bar, text="搜索", bg=C["bg"], fg=C["fg_muted"], font=F_BTN,
        ).pack(side="left", padx=(0, 10))
        search_entry = tk.Entry(
            search_bar, textvariable=self.search_var,
            bg=C["elevated"], fg=C["fg"], insertbackground=C["accent"],
            relief="flat", font=F_BTN, bd=0,
            highlightthickness=1, highlightbackground=C["border"],
            highlightcolor=C["border_focus"],
        )
        search_entry.pack(side="left", fill="x", expand=True, ipady=5)
        search_entry.bind("<KeyRelease>", lambda _e: self._refresh())
        search_entry.bind("<Escape>",
                          lambda _e: (self.search_var.set(""), self._refresh()))
        FlatButton(
            search_bar, "清除",
            lambda: (self.search_var.set(""), self._refresh()),
        ).pack(side="left", padx=(8, 0))

        tk.Frame(self, bg=C["border"], height=1).pack(
            fill="x", padx=EDGE, pady=(20, 0)
        )

        list_wrap = tk.Frame(self, bg=C["bg"])
        list_wrap.pack(fill="both", expand=True, padx=EDGE)

        # 「ops（＋ 子项）」列放在最左侧（紧挨树列），与折叠箭头分属不同列、
        # 有列分隔，不会再误触。
        cols = ("ops", "priority", "title", "deadline", "remaining", "status")
        self.tree = ttk.Treeview(
            list_wrap,
            columns=cols,
            show="tree headings",
            selectmode="extended",
            style="Flat.Treeview",
        )
        # #0 是树列：仅用于展开/收起箭头，以及子标题前的「☐ / ☑」勾选框。
        self.tree.heading("#0", text="", anchor="w")
        self.tree.heading("ops", text="子项", anchor="center")
        self.tree.heading("priority", text="优先级", anchor="w")
        self.tree.heading("title", text="任务", anchor="w")
        self.tree.heading("deadline", text="截止时间", anchor="w")
        self.tree.heading("remaining", text="剩余时间", anchor="w")
        self.tree.heading("status", text="状态", anchor="w")

        self.tree.column("#0", width=dp(self, 52), minwidth=dp(self, 46),
                         anchor="w", stretch=False)
        self.tree.column("ops", width=dp(self, 96), minwidth=dp(self, 84),
                         anchor="center", stretch=False)
        self.tree.column("priority", width=dp(self, 104), anchor="w",
                         stretch=False)
        self.tree.column("title", width=dp(self, 380), anchor="w")
        self.tree.column("deadline", width=dp(self, 180), anchor="w",
                         stretch=False)
        self.tree.column("remaining", width=dp(self, 160), anchor="w",
                         stretch=False)
        self.tree.column("status", width=dp(self, 130), anchor="w",
                         stretch=False)

        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)
        # 选区变化时刷新状态栏的"已选 N"显示
        self.tree.bind("<<TreeviewSelect>>",
                       lambda _e: self._update_status_only())

        # 左键拖动:橡皮筋多选
        self._drag_anchor_iid = None
        self.tree.bind("<ButtonPress-1>", self._on_drag_start)
        self.tree.bind("<B1-Motion>", self._on_drag_motion)

        # 右键:批量操作菜单(Mac 上是 Button-2,Windows/Linux 是 Button-3)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-2>", self._show_context_menu)

        # 快捷键
        self.bind_all("<Control-a>", lambda _e: self._select_all())
        self.bind_all("<Control-A>", lambda _e: self._select_all())
        if SYSTEM == "Darwin":
            self.bind_all("<Command-a>", lambda _e: self._select_all())
            self.bind_all("<Command-A>", lambda _e: self._select_all())
        self.tree.bind("<Delete>", lambda _e: self._delete_task())
        self.tree.bind("<BackSpace>", lambda _e: self._delete_task())

        scrollbar = ttk.Scrollbar(
            list_wrap,
            orient="vertical",
            command=self.tree.yview,
            style="Flat.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.tag_configure(
            "high",
            foreground=C["high"],
            font=(UI_FAMILY, 13, "bold"),
        )
        self.tree.tag_configure("medium", foreground=C["medium"])
        self.tree.tag_configure("low", foreground=C["low"])
        self.tree.tag_configure("normal", foreground=C["fg"])
        self.tree.tag_configure(
            "done",
            foreground=C["done"],
            font=(UI_FAMILY, 13, "overstrike"),
        )
        self.tree.tag_configure(
            "overdue",
            foreground=C["overdue"],
            font=(UI_FAMILY, 13, "bold"),
        )
        self.tree.tag_configure("subtask", foreground=C["fg_muted"])
        self.tree.tag_configure(
            "subtask_done",
            foreground=C["done"],
            font=(UI_FAMILY, 13, "overstrike"),
        )

        self.empty_state = tk.Label(
            list_wrap,
            text="暂无任务\n\n点击「＋  新建」开始添加",
            bg=C["bg"],
            fg=C["fg_subtle"],
            font=F_EMPTY,
            justify="center",
        )

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=EDGE)

        self.status = tk.Label(
            self,
            text="",
            bg=C["bg"],
            fg=C["fg_muted"],
            anchor="w",
            font=F_STATUS,
            padx=EDGE,
            pady=14,
        )
        self.status.pack(fill="x", side="bottom")

    # ---------- 工具方法 ----------
    @staticmethod
    def _parse_deadline(task):
        try:
            return datetime.fromisoformat(task["deadline"])
        except (KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _fmt_delta(d: timedelta) -> str:
        total = max(0, int(d.total_seconds()))
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)

        if days > 0:
            return f"{days} 天 {hours} 小时"
        if hours > 0:
            return f"{hours} 小时 {minutes} 分钟"
        return f"{minutes} 分钟"

    def _selected_id(self):
        """返回单选时的任务 id;多选或无选返回 None。"""
        sel = self.tree.selection()
        if len(sel) == 1:
            try:
                return int(sel[0])
            except (TypeError, ValueError):
                return None
        return None

    def _selected_ids(self):
        """返回当前所有选中的任务 id 列表(按表格显示顺序)。"""
        ids = []
        for iid in self.tree.selection():
            try:
                ids.append(int(iid))
            except (TypeError, ValueError):
                continue
        return ids

    def _select_task(self, task_id):
        iid = str(task_id)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def _select_all(self):
        """选中所有当前可见的任务。"""
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)
            self.tree.focus(children[0])
        return "break"  # 阻止默认行为

    def _update_status_only(self):
        """仅刷新状态栏(用于选区变化,避免全表重建)。"""
        try:
            current_text = self.status.cget("text")
        except tk.TclError:
            return
        sel_n = len(self._selected_ids())
        # 把旧的"已选 N"和后面的内容剥离掉,再拼上新的
        # 旧格式: "共 X 项 · 待办 Y · 过期 Z [   ·   已选 N]      数据  ..."
        head, sep, tail = current_text.partition("      数据")
        if not sep:
            return
        # 去掉 head 里旧的 "   ·   已选 N"
        marker = "   ·   已选 "
        if marker in head:
            head = head.split(marker)[0]
        sel_text = f"{marker}{sel_n}" if sel_n > 1 else ""
        self.status.config(text=f"{head}{sel_text}{sep}{tail}")

    # ---------- 拖动多选 ----------
    def _on_drag_start(self, event):
        # 先处理树列(#0)上的点击：大标题 -> 新增子标题；子标题 -> 切换完成。
        if self._maybe_handle_tree_click(event):
            self._drag_anchor_iid = None
            return "break"
        iid = self.tree.identify_row(event.y)
        self._drag_anchor_iid = iid if iid else None

    def _is_ops_column(self, event):
        """事件是否落在「ops（＋ 子项）」列上。"""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return False
        colid = self.tree.identify_column(event.x)
        cols = self.tree["columns"]
        try:
            idx = int(colid.replace("#", ""))
        except (ValueError, AttributeError):
            return False
        return 1 <= idx <= len(cols) and cols[idx - 1] == "ops"

    def _maybe_handle_tree_click(self, event):
        """
        - 点击「＋ 子项」列(且为大标题) -> 弹出输入框新增子标题
        - 点击 #0 树列上的子标题       -> 切换该子标题的完成状态
        返回 True 表示已处理(调用方应阻止默认行为)。
        """
        iid = self.tree.identify_row(event.y)
        if not iid:
            return False

        # 「＋ 子项」列：仅大标题可新增子标题。
        if self._is_ops_column(event):
            if self.tree.parent(iid) == "":
                self._add_subtask_to(iid)
                return True
            return False

        # #0 树列上的子标题勾选框：切换完成。展开箭头保留默认行为。
        if self.tree.identify_region(event.x, event.y) == "tree":
            if "indicator" in self.tree.identify_element(event.x, event.y):
                return False
            if self.tree.parent(iid) != "":
                self._toggle_subtask(self.tree.parent(iid), iid)
                return True
        return False

    def _on_drag_motion(self, event):
        if not self._drag_anchor_iid:
            return
        current_iid = self.tree.identify_row(event.y)
        if not current_iid:
            return
        all_children = self.tree.get_children()
        try:
            start_idx = all_children.index(self._drag_anchor_iid)
            end_idx = all_children.index(current_iid)
        except ValueError:
            return
        lo, hi = sorted([start_idx, end_idx])
        self.tree.selection_set(all_children[lo : hi + 1])

    # ---------- 右键菜单 ----------
    def _show_context_menu(self, event):
        # 如果右键点到了具体某行且该行未被选中,则先把它单独选上;
        # 如果右键点到了已选中的行(可能是已多选状态),保持当前多选状态。
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self.tree.focus(iid)

        # 右键点在子标题上：弹出子标题专属菜单。
        if iid and self.tree.parent(iid) != "":
            sub_iid = iid
            sub_menu = tk.Menu(self, tearoff=0,
                               bg=C["elevated"], fg=C["fg"],
                               activebackground=C["accent"],
                               activeforeground=C["accent_fg"],
                               bd=0, font=F_BUTTON)
            sub_menu.add_command(
                label="切换完成状态",
                command=lambda: self._toggle_subtask(
                    self.tree.parent(sub_iid), sub_iid),
            )
            sub_menu.add_command(
                label="重命名",
                command=lambda: self._rename_subtask(sub_iid),
            )
            sub_menu.add_separator()
            sub_menu.add_command(
                label="删除子标题",
                command=lambda: self._delete_subtask(sub_iid),
            )
            try:
                sub_menu.tk_popup(event.x_root, event.y_root)
            finally:
                sub_menu.grab_release()
            return

        ids = self._selected_ids()
        n = len(ids)

        menu = tk.Menu(self, tearoff=0,
                       bg=C["elevated"], fg=C["fg"],
                       activebackground=C["accent"],
                       activeforeground=C["accent_fg"],
                       bd=0, font=F_BUTTON)
        if n == 1:
            menu.add_command(label="编辑", command=self._edit_task)
            menu.add_command(
                label="添加子标题",
                command=lambda i=ids[0]: self._add_subtask_to(str(i)),
            )
        else:
            menu.add_command(label="编辑", command=self._edit_task, state="disabled")
        if n >= 1:
            menu.add_command(label=f"延期…  ({n} 项)" if n > 1 else "延期…",
                             command=self._postpone_task)
            menu.add_command(label=f"切换完成状态  ({n} 项)" if n > 1 else "切换完成状态",
                             command=self._toggle_complete)
            menu.add_separator()
            menu.add_command(label=f"删除  ({n} 项)" if n > 1 else "删除",
                             command=self._delete_task)
            menu.add_separator()
        menu.add_command(label="全选", command=self._select_all,
                         accelerator="Ctrl+A")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---------- 筛选 / 排序（供刷新与导出共用） ----------
    def _filter_tasks(self, tasks, f):
        now = datetime.now()
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        if f == "待办":
            return [t for t in tasks if not t.get("completed")]
        if f == "已完成":
            return [t for t in tasks if t.get("completed")]
        if f == "今日到期":
            return [
                t for t in tasks
                if not t.get("completed")
                and (self._parse_deadline(t) is not None)
                and now <= self._parse_deadline(t) <= today_end
            ]
        if f == "已过期":
            return [
                t for t in tasks
                if not t.get("completed")
                and (self._parse_deadline(t) is not None)
                and self._parse_deadline(t) < now
            ]
        return list(tasks)

    def _sort_tasks(self, tasks, sm):
        tasks = list(tasks)
        if sm == "截止时间":
            tasks.sort(key=lambda t: self._parse_deadline(t) or datetime.max)
        elif sm == "优先级":
            tasks.sort(
                key=lambda t: (
                    PRIORITY_RANK.get(t.get("priority", "中"), 9),
                    self._parse_deadline(t) or datetime.max,
                )
            )
        elif sm == "创建时间":
            tasks.sort(key=lambda t: t.get("created", ""))
        return tasks

    # ---------- 子标题（子任务）操作 ----------
    @staticmethod
    def _split_subtask_iid(sub_iid):
        """'12::3' -> (12, 3)；失败返回 (None, None)。"""
        try:
            tid_str, sid_str = str(sub_iid).split("::", 1)
            return int(tid_str), int(sid_str)
        except (ValueError, AttributeError):
            return None, None

    def _add_subtask_to(self, parent_iid):
        try:
            tid = int(parent_iid)
        except (TypeError, ValueError):
            return
        task = self.store.get(tid)
        if not task:
            self._refresh()
            return

        title = simpledialog.askstring(
            "新增子标题",
            f"为「{task.get('title', '')}」添加一个子标题：",
            parent=self,
        )
        if not title or not title.strip():
            return
        try:
            self.store.add_subtask(tid, title.strip())
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"子标题保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return
        self._refresh()
        self._select_task(tid)

    def _toggle_subtask(self, parent_iid, sub_iid):
        tid, sid = self._split_subtask_iid(sub_iid)
        if tid is None:
            return
        try:
            self.store.toggle_subtask(tid, sid)
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"子标题状态保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return
        self._refresh()
        if self.tree.exists(str(sub_iid)):
            self.tree.selection_set(str(sub_iid))

    def _delete_subtask(self, sub_iid):
        tid, sid = self._split_subtask_iid(sub_iid)
        if tid is None:
            return
        if not messagebox.askyesno(APP_NAME, "确定删除该子标题吗?"):
            return
        try:
            self.store.delete_subtask(tid, sid)
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"子标题删除失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return
        self._refresh()
        self._select_task(tid)

    def _rename_subtask(self, sub_iid):
        tid, sid = self._split_subtask_iid(sub_iid)
        if tid is None:
            return
        task = self.store.get(tid)
        cur = ""
        if task:
            cur = next(
                (s.get("title", "") for s in task.get("subtasks", [])
                 if int(s.get("id", -1)) == sid),
                "",
            )
        new_title = simpledialog.askstring(
            "重命名子标题", "新的子标题：",
            initialvalue=cur, parent=self,
        )
        if not new_title or not new_title.strip():
            return
        try:
            self.store.rename_subtask(tid, sid, new_title.strip())
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"子标题保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return
        self._refresh()
        if self.tree.exists(str(sub_iid)):
            self.tree.selection_set(str(sub_iid))

    def _on_view_changed(self):
        """筛选 / 排序变化：刷新并记忆选择。"""
        self._save_ui_state()
        self._refresh()

    # ---------- 提醒横幅 ----------
    def _show_banner(self, text, overdue=False):
        color = C["overdue"] if overdue else C["accent"]
        self.banner.config(bg=color)
        for child in self.banner.winfo_children():
            child.config(bg=color, fg=C["accent_fg"])
        self.banner_label.config(text=text)
        if not self.banner.winfo_manager():
            self.banner.pack(fill="x", side="top", before=self._header_frame)
        if self._banner_after:
            try:
                self.after_cancel(self._banner_after)
            except Exception:
                pass
        self._banner_after = self.after(9000, self._hide_banner)

    def _hide_banner(self):
        if self._banner_after:
            try:
                self.after_cancel(self._banner_after)
            except Exception:
                pass
            self._banner_after = None
        try:
            self.banner.pack_forget()
        except Exception:
            pass

    def _notify_recurred(self, task):
        """重复任务完成本次后，提示已顺延到的下一个时间。"""
        try:
            d = datetime.fromisoformat(task["deadline"]).strftime("%Y-%m-%d %H:%M")
        except (KeyError, ValueError, TypeError):
            d = task.get("deadline", "")
        self._show_banner(
            f"↻  「{task.get('title', '')}」已完成本次，自动顺延至 {d}")

    def _on_double_click(self, event):
        """双击大标题 -> 编辑任务；双击子标题 -> 切换其完成状态。"""
        iid = self.tree.identify_row(event.y)
        # 「＋ 子项」列已在单击时处理，双击不要再触发编辑。
        if self._is_ops_column(event):
            return "break"
        if iid and self.tree.parent(iid) != "":
            self._toggle_subtask(self.tree.parent(iid), iid)
            return "break"
        self._edit_task()

    # ---------- 刷新界面 ----------
    def _refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        all_tasks = self.store.all()
        now = datetime.now()

        tasks = self._filter_tasks(all_tasks, self.filter_mode.get())
        tasks = self._sort_tasks(tasks, self.sort_mode.get())

        # 关键字搜索（标题或描述，忽略大小写）
        q = self.search_var.get().strip().lower()
        if q:
            tasks = [
                t for t in tasks
                if q in t.get("title", "").lower()
                or q in (t.get("description") or "").lower()
            ]

        overdue_n, todo_n = 0, 0
        prio_dot = {"高": "●  高", "中": "●  中", "低": "●  低"}

        for t in tasks:
            due = self._parse_deadline(t)
            prio = t.get("priority", "中")
            prio_text = prio_dot.get(prio, prio)

            if t.get("completed"):
                remaining = "—"
                status = "✓  已完成"
                tag = "done"
            elif due is None:
                remaining = "时间格式错误"
                status = "⚠  异常"
                tag = "overdue"
            else:
                delta = due - now
                if delta < timedelta(0):
                    remaining = f"过期 {self._fmt_delta(-delta)}"
                    status = "⚠  已过期"
                    tag = "overdue"
                    overdue_n += 1
                else:
                    remaining = self._fmt_delta(delta)
                    status = "○  待办"
                    tag = {"高": "high", "中": "normal", "低": "low"}.get(
                        prio,
                        "normal",
                    )
                    todo_n += 1

            deadline_text = due.strftime("%Y-%m-%d  %H:%M") if due else "无效时间"
            subs = t.get("subtasks") or []
            title_text = t.get("title", "")
            markers = []
            if subs:
                done_n = sum(1 for s in subs if s.get("completed"))
                markers.append(f"{done_n}/{len(subs)} 子项")
            rep = t.get("repeat", "无")
            if rep and rep != "无":
                markers.append(f"↻{rep}")
            if markers:
                title_text = f"{title_text}   ({' · '.join(markers)})"

            self.tree.insert(
                "",
                "end",
                iid=str(t.get("id")),
                text="",
                values=("＋ 子项", prio_text, title_text, deadline_text,
                        remaining, status),
                tags=(tag,),
                open=True,
            )

            # 子标题（子任务）
            for s in subs:
                s_done = bool(s.get("completed"))
                box = "☑" if s_done else "☐"
                s_status = "✓  已完成" if s_done else "○  待办"
                self.tree.insert(
                    str(t.get("id")),
                    "end",
                    iid=f"{t.get('id')}::{s.get('id')}",
                    text=box,
                    values=("", "", s.get("title", ""), "", "", s_status),
                    tags=("subtask_done" if s_done else "subtask",),
                )

        if not tasks:
            self.empty_state.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_state.place_forget()

        total = len(all_tasks)
        path_str = str(DATA_FILE)
        try:
            path_str = path_str.replace(str(Path.home()), "~")
        except Exception:
            pass

        sel_n = len(self._selected_ids())
        sel_text = f"   ·   已选 {sel_n}" if sel_n > 1 else ""

        self.status.config(
            text=(
                f"共 {total} 项   ·   待办 {todo_n}   ·   过期 {overdue_n}"
                f"{sel_text}      数据  {path_str}"
            )
        )

    # ---------- 业务逻辑 ----------
    def _add_task(self):
        dlg = TaskDialog(self)
        self.wait_window(dlg)

        if dlg.result:
            try:
                new_task = self.store.add(dlg.result)
            except OSError as e:
                messagebox.showerror(
                    APP_NAME,
                    f"任务保存失败：\n{e}\n\n数据目录：\n{DATA_DIR}",
                    parent=self,
                )
                return

            # 防止当前筛选为“已完成/已过期”等时，新任务被过滤掉，看起来像没保存。
            self.filter_mode.set("全部")
            self._refresh()
            self._select_task(new_task["id"])

    def _edit_task(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo(APP_NAME, "请先选择一个任务")
            return
        if len(ids) > 1:
            messagebox.showinfo(
                APP_NAME,
                "编辑只能针对单个任务。\n如需批量修改截止时间,请使用「延期」按钮。",
            )
            return

        tid = ids[0]
        task = self.store.get(tid)
        if not task:
            messagebox.showerror(APP_NAME, "没有找到该任务,可能已经被删除。")
            self._refresh()
            return

        dlg = TaskDialog(self, task)
        self.wait_window(dlg)

        if dlg.result:
            try:
                self.store.update(
                    tid,
                    **dlg.result,
                    notified_1d=False,
                    notified_1h=False,
                    notified_overdue=False,
                )
            except OSError as e:
                messagebox.showerror(
                    APP_NAME,
                    f"任务保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                    parent=self,
                )
                return

            self._refresh()
            self._select_task(tid)

    def _toggle_complete(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo(APP_NAME, "请先选择任务(可拖动多选或 Ctrl+A 全选)")
            return

        if len(ids) == 1:
            task = self.store.get(ids[0])
            if not task:
                messagebox.showerror(APP_NAME, "没有找到该任务,可能已经被删除。")
                self._refresh()
                return
            was_completed = bool(task.get("completed", False))
            try:
                if not was_completed and task.get("repeat", "无") != "无":
                    # 重复任务：不标记完成，而是顺延到下一个周期
                    newt = self.store.recur(ids[0])
                    if newt:
                        self._notify_recurred(newt)
                    self._award_points(POINTS_PER_TASK, "完成任务")
                else:
                    # 大标题与其子标题级联设置完成状态
                    self.store.set_completed(ids[0], not was_completed)
                    if not was_completed:
                        self._award_points(POINTS_PER_TASK, "完成任务")
            except OSError as e:
                messagebox.showerror(
                    APP_NAME,
                    f"任务状态保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                    parent=self,
                )
                return
            self._refresh()
            self._select_task(ids[0])
            return

        # 多选:如果全部未完成 -> 全部标完成
        # 如果至少有一个已完成 -> 全部标未完成(便于一键"反悔")
        tasks = [self.store.get(i) for i in ids]
        tasks = [t for t in tasks if t]
        if not tasks:
            self._refresh()
            return

        any_done = any(t.get("completed", False) for t in tasks)
        new_state = not any_done

        awarded = 0
        try:
            for t in tasks:
                if new_state:
                    if not t.get("completed", False):
                        if t.get("repeat", "无") != "无":
                            self.store.recur(t["id"])
                        else:
                            self.store.set_completed(t["id"], True)
                        awarded += 1
                else:
                    self.store.set_completed(t["id"], False)
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"任务状态保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return

        if awarded:
            self._award_points(POINTS_PER_TASK * awarded, f"完成{awarded}项")

        self._refresh()
        # 恢复多选高亮
        existing = [str(t["id"]) for t in tasks if self.tree.exists(str(t["id"]))]
        if existing:
            self.tree.selection_set(existing)

    def _postpone_task(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo(
                APP_NAME, "请先选择任务(可拖动多选或 Ctrl+A 全选)")
            return

        tasks = [self.store.get(i) for i in ids]
        tasks = [t for t in tasks if t]
        if not tasks:
            messagebox.showerror(APP_NAME, "没有找到任务,可能已经被删除。")
            self._refresh()
            return

        dlg = PostponeDialog(self, tasks)
        self.wait_window(dlg)

        if not dlg.result:
            return

        try:
            for tid, new_deadline in dlg.result.items():
                # 延期会重置提醒标记,这样新的截止时间到了仍能正常通知
                self.store.update(
                    tid,
                    deadline=new_deadline,
                    notified_1d=False,
                    notified_1h=False,
                    notified_overdue=False,
                )
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"延期保存失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return

        self._refresh()
        # 恢复多选高亮
        ok = [str(tid) for tid in dlg.result if self.tree.exists(str(tid))]
        if ok:
            self.tree.selection_set(ok)
            self.tree.see(ok[0])

    def _delete_task(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo(APP_NAME, "请先选择任务(可拖动多选或 Ctrl+A 全选)")
            return

        if len(ids) == 1:
            prompt = "确定删除该任务吗?"
        else:
            prompt = f"确定删除选中的 {len(ids)} 个任务吗?\n此操作无法撤销。"

        if not messagebox.askyesno(APP_NAME, prompt):
            return

        try:
            for tid in ids:
                self.store.delete(tid)
        except OSError as e:
            messagebox.showerror(
                APP_NAME,
                f"任务删除失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=self,
            )
            return
        self._refresh()

    # ---------- 提醒回调 ----------
    def _on_reminder(self, task, msg):
        self.after(0, lambda: self._show_reminder(task, msg))

    def _show_reminder(self, task, msg):
        # 系统通知（如果可用），失败也不阻塞。
        try:
            from plyer import notification

            notification.notify(
                title=f"{APP_NAME} 提醒",
                message=f"{task.get('title', '')}\n{msg}",
                timeout=10,
            )
        except Exception:
            pass
        # 应用内横幅提醒（非模态，不抢焦点）。
        try:
            overdue = "过期" in msg
            self._show_banner(
                f"●  提醒：{task.get('title', '')} — {msg}", overdue=overdue)
        except Exception:
            pass
        self._refresh()

    # ---------- 主题切换 ----------
    def _build_theme_picker(self, parent):
        picker = tk.Frame(parent, bg=C["bg"])

        for theme_name, theme_data in THEMES.items():
            is_active = theme_name == self.theme_name
            ring_color = C["accent"] if is_active else C["bg"]

            outer = tk.Frame(picker, bg=ring_color)
            swatch = tk.Frame(
                outer,
                bg=theme_data["bg"],
                width=22,
                height=22,
                highlightthickness=1,
                highlightbackground=theme_data["border"],
                bd=0,
            )
            swatch.pack(padx=2, pady=2)
            swatch.pack_propagate(False)
            outer.pack(side="left", padx=4)

            for w in (outer, swatch):
                w.bind("<Button-1>", lambda _e, t=theme_name: self.switch_theme(t))
                w.configure(cursor="hand2")

        return picker

    def switch_theme(self, theme_name):
        if theme_name == self.theme_name or theme_name not in THEMES:
            return

        self.theme_name = theme_name
        self.config["theme"] = theme_name
        save_config(self.config)

        C.clear()
        C.update(THEMES[theme_name])

        for widget in self.winfo_children():
            widget.destroy()

        self.configure(bg=C["bg"])
        self._setup_style()
        self._build_ui()
        self._refresh()

    # ---------- 归档 ----------
    @staticmethod
    def _fmt_iso(s):
        """ISO 字符串 -> 'YYYY-MM-DD HH:MM'，无法解析则原样返回。"""
        if not s:
            return ""
        try:
            return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return str(s)

    def _open_archive(self):
        ArchiveWindow(self)

    def _archive_completed_tasks(self, parent=None):
        """把当前已完成的任务移入归档仓库；返回归档数量。"""
        parent = parent or self
        completed = [t for t in self.store.all() if t.get("completed")]
        if not completed:
            messagebox.showinfo(
                APP_NAME, "当前没有已完成的任务可归档。", parent=parent)
            return 0
        if not messagebox.askyesno(
            APP_NAME,
            f"将 {len(completed)} 个已完成任务归档？\n"
            "归档后会从主列表移除，可在「归档」窗口查看与导出。",
            parent=parent,
        ):
            return 0
        try:
            self.archive.add_many(completed)
            for t in completed:
                self.store.delete(t["id"])
        except OSError as e:
            messagebox.showerror(
                APP_NAME, f"归档失败:\n{e}\n\n数据目录:\n{DATA_DIR}",
                parent=parent)
            return 0
        self._refresh()
        self._award_points(POINTS_PER_ARCHIVE, "归档")
        messagebox.showinfo(
            APP_NAME, f"已归档 {len(completed)} 个任务，奖励 +{POINTS_PER_ARCHIVE} 积分。",
            parent=parent)
        return len(completed)

    # ---------- 桌宠 / 奖励 ----------
    def _pet_alive(self):
        try:
            return self.pet is not None and bool(self.pet.winfo_exists())
        except Exception:
            return False

    def _show_pet(self):
        if self._pet_alive():
            try:
                self.pet.lift()
            except Exception:
                pass
            return
        self.pet = None
        # 优先用原画图片版桌宠（Windows + Pillow）；否则退回矢量版
        if SYSTEM == "Windows" and _PIL_OK:
            try:
                self.pet = LayeredPet(self)
            except Exception:
                self.pet = None
        if self.pet is None:
            try:
                self.pet = DesktopPet(self)
            except Exception:
                self.pet = None
        if self.pet is not None:
            self.config["pet_enabled"] = True
            save_config(self.config)

    def _ensure_pet(self):
        if not self._pet_alive():
            self._show_pet()

    def _toggle_pet(self):
        if self._pet_alive():
            try:
                self.pet.destroy()
            except Exception:
                pass
            self.pet = None
            self.config["pet_enabled"] = False
            save_config(self.config)
        else:
            self._show_pet()

    def _award_points(self, n, reason):
        if n <= 0:
            return
        self.pet_store.add_points(n)
        if self._pet_alive():
            try:
                self.pet.on_points(n, reason)
            except Exception:
                pass

    def _app_tick(self):
        """每 30 秒检查：到了提醒时间且今天没打卡，就让桌宠提醒下班打卡。"""
        try:
            now = datetime.now()
            today = date.today().isoformat()
            after_checkin_time = (
                now.hour > CHECKIN_REMIND_HOUR
                or (now.hour == CHECKIN_REMIND_HOUR
                    and now.minute >= CHECKIN_REMIND_MINUTE)
            )
            if (after_checkin_time and self.pet_store.can_checkin()
                    and self._pet_remind_date != today):
                self._pet_remind_date = today
                if self.config.get("pet_enabled", True):
                    self._ensure_pet()
                if self._pet_alive():
                    self.pet.prompt_checkin()
                else:
                    # 桌宠被关掉时，用应用内横幅提醒
                    self._show_banner(
                        f"● {CHECKIN_REMIND_TEXT} 马上下班啦老婆，记得下班打卡！（打开桌宠点「今日打卡」）")
        except Exception:
            pass
        finally:
            self.after(30000, self._app_tick)

    # ---------- 导出 ----------
    @staticmethod
    def _task_status_text(t, now):
        """用于导出的状态文案。"""
        if t.get("completed"):
            return "已完成"
        due = TodoApp._parse_deadline(t)
        if due is None:
            return "时间格式错误"
        return "已过期" if (due - now) < timedelta(0) else "待办"

    def _export_tasks(self):
        selected_ids = self._selected_ids()
        dlg = ExportDialog(
            self, self.sort_mode.get(), has_selection=bool(selected_ids))
        self.wait_window(dlg)
        if not dlg.result:
            return

        scope = dlg.result["scope"]
        fmt = dlg.result["fmt"]

        if scope == "选中项":
            tasks = [self.store.get(i) for i in selected_ids]
            tasks = [t for t in tasks if t]
        else:
            # 范围筛选；“全部”按字面导出全部任务。
            tasks = self._filter_tasks(self.store.all(), scope)
        # 始终按主界面当前选定的排序方式导出。
        tasks = self._sort_tasks(tasks, self.sort_mode.get())

        if not tasks:
            messagebox.showinfo(
                APP_NAME, "当前范围没有可导出的任务。", parent=self)
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        ext = {"csv": "csv", "md": "md", "xlsx": "xlsx"}.get(fmt, "txt")
        type_map = {
            "csv": [("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            "md": [("Markdown 文件", "*.md"), ("所有文件", "*.*")],
            "xlsx": [("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
            "txt": [("文本文件", "*.txt"), ("所有文件", "*.*")],
        }
        default_name = f"DeadlineKeeper_{scope}_{stamp}.{ext}"

        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出到",
            initialfile=default_name,
            defaultextension=f".{ext}",
            filetypes=type_map.get(fmt, type_map["txt"]),
        )
        if not path:
            return

        try:
            if fmt == "csv":
                self._write_csv(path, tasks, scope)
            elif fmt == "md":
                self._write_md(path, tasks, scope)
            elif fmt == "xlsx":
                self._write_xlsx(path, tasks, scope)
            else:
                self._write_txt(path, tasks, scope)
        except OSError as e:
            messagebox.showerror(
                APP_NAME, f"导出失败:\n{e}", parent=self)
            return

        if messagebox.askyesno(
            APP_NAME,
            f"已导出 {len(tasks)} 项到:\n{path}\n\n是否打开所在文件夹?",
            parent=self,
        ):
            self._reveal_in_folder(path)

    def _write_txt(self, path, tasks, scope):
        now = datetime.now()
        lines = []
        lines.append(f"{APP_NAME} 任务导出")
        lines.append(f"导出时间：{now.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"导出范围：{scope}    排序方式：{self.sort_mode.get()}")
        lines.append(f"共 {len(tasks)} 项")
        lines.append("=" * 48)
        lines.append("")

        for i, t in enumerate(tasks, 1):
            due = self._parse_deadline(t)
            deadline_text = due.strftime("%Y-%m-%d %H:%M") if due else "无效时间"
            status = self._task_status_text(t, now)
            lines.append(
                f"{i}. [{t.get('priority', '中')}] {t.get('title', '')}"
            )
            meta = f"    截止：{deadline_text}    状态：{status}"
            rep = t.get("repeat", "无")
            if rep and rep != "无":
                meta += f"    重复：{rep}"
            ca = self._fmt_iso(t.get("completed_at"))
            if ca:
                meta += f"    完成：{ca}"
            lines.append(meta)
            desc = (t.get("description") or "").strip()
            if desc:
                indented = desc.replace("\n", "\n          ")
                lines.append(f"    描述：{indented}")
            subs = t.get("subtasks") or []
            if subs:
                done_n = sum(1 for s in subs if s.get("completed"))
                lines.append(f"    子标题（{done_n}/{len(subs)} 完成）：")
                for s in subs:
                    mark = "✓" if s.get("completed") else "○"
                    lines.append(f"      [{mark}] {s.get('title', '')}")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _write_csv(self, path, tasks, scope):
        now = datetime.now()
        # newline="" 是 csv 模块在 Windows 上的推荐写法，避免空行。
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["序号", "标题", "优先级", "截止时间", "状态", "完成时间", "重复",
                 "描述", "子项完成", "子项总数", "子标题"]
            )
            for i, t in enumerate(tasks, 1):
                due = self._parse_deadline(t)
                deadline_text = (
                    due.strftime("%Y-%m-%d %H:%M") if due else "无效时间"
                )
                status = self._task_status_text(t, now)
                subs = t.get("subtasks") or []
                done_n = sum(1 for s in subs if s.get("completed"))
                sub_text = " / ".join(
                    f"{'✓' if s.get('completed') else '○'} {s.get('title', '')}"
                    for s in subs
                )
                writer.writerow([
                    i,
                    t.get("title", ""),
                    t.get("priority", "中"),
                    deadline_text,
                    status,
                    self._fmt_iso(t.get("completed_at")),
                    t.get("repeat", "无"),
                    (t.get("description") or "").strip(),
                    done_n,
                    len(subs),
                    sub_text,
                ])

    def _write_md(self, path, tasks, scope):
        now = datetime.now()
        lines = []
        lines.append(f"# {APP_NAME} 任务导出")
        lines.append("")
        lines.append(
            f"> 导出时间：{now.strftime('%Y-%m-%d %H:%M')}　|　"
            f"范围：{scope}　|　排序：{self.sort_mode.get()}　|　共 {len(tasks)} 项"
        )
        lines.append("")

        for i, t in enumerate(tasks, 1):
            due = self._parse_deadline(t)
            deadline_text = due.strftime("%Y-%m-%d %H:%M") if due else "无效时间"
            status = self._task_status_text(t, now)
            lines.append(f"## {i}. {t.get('title', '')}  `{t.get('priority', '中')}`")
            meta = f"- 截止：{deadline_text} ｜ 状态：{status}"
            rep = t.get("repeat", "无")
            if rep and rep != "无":
                meta += f" ｜ 重复：{rep}"
            ca = self._fmt_iso(t.get("completed_at"))
            if ca:
                meta += f" ｜ 完成：{ca}"
            lines.append(meta)
            desc = (t.get("description") or "").strip()
            if desc:
                lines.append(f"- 描述：{desc}")
            subs = t.get("subtasks") or []
            if subs:
                done_n = sum(1 for s in subs if s.get("completed"))
                lines.append(f"- 子标题（{done_n}/{len(subs)}）：")
                for s in subs:
                    box = "x" if s.get("completed") else " "
                    lines.append(f"  - [{box}] {s.get('title', '')}")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # Excel 导出标题（主标题）
    XLSX_TITLE = "工作事项清单"

    def _write_xlsx(self, path, tasks, scope):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import (
                Alignment, Border, Font, PatternFill, Side)
            from openpyxl.utils import get_column_letter
        except ImportError:
            raise OSError(
                "缺少 openpyxl 库，无法导出 Excel。\n请改用 CSV / TXT / Markdown，"
                "或安装 openpyxl 后重试。")

        now = datetime.now()

        # 仅当导出的内容里含完成时间时才加「完成时间」列（如归档/已完成）
        has_done_time = any(t.get("completed_at") for t in tasks)
        headers = ["优先级", "任务", "子项", "截止时间", "状态"]
        if has_done_time:
            headers.append("完成时间")
        ncols = len(headers)
        last_col = get_column_letter(ncols)

        # 颜色
        NAVY = "1F3864"      # 主标题底色
        BLUE = "2E75B6"      # 表头底色
        STRIPE = "EAF1FB"    # 隔行浅蓝
        WHITE = "FFFFFF"
        PRIO_COLOR = {"高": "C00000", "中": "404040", "低": "1A7F37"}
        thin = Side(style="thin", color="BFC9D6")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        wb = Workbook()
        ws = wb.active
        ws.title = "工作事项清单"

        # —— 第 1 行：主标题（合并居中） ——
        title = self.XLSX_TITLE
        if scope and scope not in ("全部", "待办", "已完成", "选中项"):
            title = f"{self.XLSX_TITLE}　{scope}"
        ws.merge_cells(f"A1:{last_col}1")
        tcell = ws["A1"]
        tcell.value = title
        tcell.font = Font(name="宋体", size=16, bold=True, color=WHITE)
        tcell.alignment = Alignment(horizontal="center", vertical="center")
        tcell.fill = PatternFill("solid", fgColor=NAVY)
        ws.row_dimensions[1].height = 30

        # —— 第 2 行：表头 ——
        ws.append(headers)
        for c in ws[2]:
            c.font = Font(name="宋体", size=11, bold=True, color=WHITE)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.fill = PatternFill("solid", fgColor=BLUE)
            c.border = border
        ws.row_dimensions[2].height = 22

        # —— 数据行 ——
        base_font = Font(name="宋体", size=11)
        left_top = Alignment(horizontal="left", vertical="center", wrap_text=True)
        left_top_sub = Alignment(
            horizontal="left", vertical="top", wrap_text=True)
        center = Alignment(horizontal="center", vertical="center")

        DATE_FMT = "yyyy/m/d"
        row_idx = 2
        for i, t in enumerate(tasks):
            row_idx += 1
            due = self._parse_deadline(t)
            subs = t.get("subtasks") or []
            done_n = sum(1 for s in subs if s.get("completed"))
            title_text = t.get("title", "")
            if subs:
                title_text = f"{title_text} ({done_n}/{len(subs)} 子项)"
            sub_text = "\n".join(
                f"{j}. {s.get('title', '')}" for j, s in enumerate(subs, 1))
            status = self._task_status_text(t, now)
            prio = t.get("priority", "中")

            values = [prio, title_text, sub_text,
                      due.date() if due else "无效时间", status]
            if has_done_time:
                ca = None
                if t.get("completed_at"):
                    try:
                        ca = datetime.fromisoformat(t["completed_at"]).date()
                    except (ValueError, TypeError):
                        ca = None
                values.append(ca if ca else "")

            for col, val in enumerate(values, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.font = base_font
                cell.border = border
                if col == 1:        # 优先级
                    cell.font = Font(name="宋体", size=11, bold=True,
                                     color=PRIO_COLOR.get(prio, "404040"))
                    cell.alignment = center
                elif col == 2:      # 任务
                    cell.alignment = left_top
                elif col == 3:      # 子项
                    cell.alignment = left_top_sub
                elif col == 4:      # 截止时间（只到日）
                    cell.alignment = center
                    if due:
                        cell.number_format = DATE_FMT
                elif col == 5:      # 状态
                    cell.alignment = center
                    if status == "已过期":
                        cell.font = Font(name="宋体", size=11, color="C00000")
                    elif status == "已完成":
                        cell.font = Font(name="宋体", size=11, color="1A7F37")
                else:               # 完成时间
                    cell.alignment = center
                    if values[col - 1]:
                        cell.number_format = DATE_FMT

            # 隔行浅蓝
            if i % 2 == 1:
                for col in range(1, ncols + 1):
                    ws.cell(row=row_idx, column=col).fill = PatternFill(
                        "solid", fgColor=STRIPE)

            # 行高：子项多少行就给多高，避免内容被压成一行需要双击才展开
            n_lines = max(1, len(subs))
            ws.row_dimensions[row_idx].height = max(20, n_lines * 15.5 + 4)

        # 列宽（固定，整洁；含中文用较宽值，杜绝 ####）
        widths = {"优先级": 10, "任务": 34, "子项": 46,
                  "截止时间": 13, "状态": 10, "完成时间": 13}
        for col, name in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(col)].width = widths.get(
                name, 14)

        ws.freeze_panes = "A3"   # 冻结主标题 + 表头
        wb.save(path)

    def _reveal_in_folder(self, path):
        folder = str(Path(path).parent)
        try:
            if SYSTEM == "Windows":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif SYSTEM == "Darwin":
                subprocess.run(["open", folder], check=False)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except Exception as e:
            messagebox.showinfo(
                APP_NAME, f"文件已保存到：\n{path}\n\n自动打开失败：{e}",
                parent=self,
            )

    # ---------- 数据目录 ----------
    def _open_data_folder(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        try:
            if SYSTEM == "Windows":
                os.startfile(DATA_DIR)  # type: ignore[attr-defined]
            elif SYSTEM == "Darwin":
                subprocess.run(["open", str(DATA_DIR)], check=False)
            else:
                subprocess.run(["xdg-open", str(DATA_DIR)], check=False)
        except Exception as e:
            messagebox.showinfo(
                APP_NAME,
                f"数据目录：\n{DATA_DIR}\n\n自动打开失败：{e}",
            )

    def _save_ui_state(self):
        """把窗口大小/位置、筛选、排序记忆到 config.json。"""
        try:
            self.config["geometry"] = self.geometry()
            self.config["filter"] = self.filter_mode.get()
            self.config["sort"] = self.sort_mode.get()
            save_config(self.config)
        except Exception:
            pass

    def _on_close(self):
        try:
            self.reminder.stop()
        finally:
            self._save_ui_state()
            self.destroy()


def enable_dpi_awareness():
    """
    Windows 高分屏 / 缩放自适应：让 Tk 以真实像素渲染，配合 tk scaling 后
    文字更清晰、不同缩放比例下界面更一致。其它平台无需处理。
    """
    if SYSTEM != "Windows":
        return
    try:
        import ctypes
        try:
            # Per-Monitor v2（Win10 1703+）；失败则退回系统级 DPI 感知
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    enable_dpi_awareness()
    app = TodoApp()
    app.mainloop()


if __name__ == "__main__":
    main()
