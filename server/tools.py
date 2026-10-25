#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yuki 的"手"：操作电脑的工具（DeepSeek function calling）。

原则：
- 只做安全的、可解释的固定动作（开网页/开软件/调音量/提醒/截屏/打字/移动鼠标…）
- 不执行任意命令、不删除文件
- 说话/提醒类通过 ctx["session"] 回传
"""

from __future__ import annotations

import asyncio
import ctypes
import datetime as _dt
import json
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("nurow.tools")

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
SHOTS = DATA / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
MEMO = DATA / "memory.md"

START_MENU_ROOTS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]

# --------------------------------------------------------------------------- #
# 工具清单（OpenAI function calling 格式）
# --------------------------------------------------------------------------- #
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "用默认浏览器打开一个网址。比如打开B站、打开某个网站。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "完整网址，如 https://www.bilibili.com"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "在浏览器里搜索一个关键词（Bing）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "要搜的内容"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "打开电脑上已安装的软件（按名字找开始菜单里的程序），比如微信、QQ、计算器、记事本。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "软件名字"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "调节系统音量。level 给 0~100 表示设成大概多少；也可以用 delta 给相对变化（如 -20 小声一点）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "目标音量 0-100，可选"},
                    "delta": {"type": "integer", "description": "相对变化，正数变大声，负数变小声，可选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mute_volume",
            "description": "静音 / 取消静音（切换）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_media_key",
            "description": "模拟媒体键：播放/暂停、下一首、上一首。",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "enum": ["play_pause", "next", "prev"]}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "设置一个提醒，到时间 yuki 会主动提醒主人。",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "多少分钟后提醒"},
                    "label": {"type": "string", "description": "提醒内容，如 喝水、休息一下"},
                },
                "required": ["minutes", "label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "截一张当前屏幕的图，保存到电脑里。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取现在的日期和时间。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "查看电脑当前状态：时间日期、电量、内存占用、磁盘剩余空间、屏幕分辨率。主人问「现在几点」「还有多少电」「内存够不够」「D盘还剩多少」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_memo",
            "description": "把重要的事记进长期记忆（比如主人的生日、喜欢的东西、约定）。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要记住的事，一句话"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lock_pc",
            "description": "锁定电脑屏幕。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sing",
            "description": "唱一首歌给主人听（真的会出声、有旋律、对口型）。内置儿歌：小星星、生日快乐、两只老虎。",
            "parameters": {
                "type": "object",
                "properties": {"song": {"type": "string", "description": "歌名"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_screen",
            "description": "看屏幕：截当前屏幕并读出上面的文字（含窗口标题）。主人问「你看到我屏幕上是什么吗」「看看我在干嘛」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "把鼠标移到屏幕上的指定位置（像素坐标，左上角是 0,0）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "屏幕 X 坐标"},
                    "y": {"type": "integer", "description": "屏幕 Y 坐标"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "在当前鼠标位置点一下（左键/右键/双击）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "double": {"type": "boolean", "description": "是否双击"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_scroll",
            "description": "滚动鼠标滚轮。正数向上，负数向下。",
            "parameters": {
                "type": "object",
                "properties": {"clicks": {"type": "integer", "description": "滚动格数，例如 3 或 -3"}},
                "required": ["clicks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_drag",
            "description": "按住鼠标从一点拖到另一点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right"]},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "给当前光标所在的输入框打字（支持中文）。主人让你帮他输入文字时用。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要输入的文字（不超过 300 字）"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_keys",
            "description": "按快捷键。keys 用 + 连接，例如 enter、esc、ctrl+a、ctrl+c、alt+tab、win+e。",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string", "description": "例如 ctrl+a 或 enter"}},
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_pos",
            "description": "看鼠标现在在屏幕哪个位置。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_persona",
            "description": "切换人格：dark=黑yuki（毒舌坏心眼），normal=普通可爱的 yuki。",
            "parameters": {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["dark", "normal"]}},
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "activate_window",
            "description": "把某个已经打开的窗口切到前台（按标题或程序名匹配）。比如切到记事本、浏览器、B站客户端。",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "窗口标题或程序名的一部分，如 记事本、哔哩哔哩、Chrome"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_read",
            "description": "读剪贴板里现在的内容。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_write",
            "description": "把一段文字写进剪贴板（之后可以按 ctrl+v 粘贴）。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要放进剪贴板的文字"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_path",
            "description": "用资源管理器打开一个文件或文件夹（也可以打开桌面上的文件）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件或文件夹的完整路径，例如 C:\\Users\\Administrator\\Desktop"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_seconds",
            "description": "等几秒钟（等软件/网页加载好再继续操作时用）。",
            "parameters": {
                "type": "object",
                "properties": {"seconds": {"type": "number", "description": "等待秒数，0.2~8"}},
                "required": ["seconds"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# 基础动作
# --------------------------------------------------------------------------- #
def _run_ps(script: str, timeout: int = 20) -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            timeout=timeout,
        )
        return (out.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as exc:  # pragma: no cover
        log.warning("powershell 失败：%s", exc)
        return ""


def _startfile(target: str) -> None:
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    else:  # pragma: no cover
        subprocess.Popen(["xdg-open", target])


def _send_media_code(code: int, times: int = 1) -> None:
    if times <= 0:
        return
    sends = ";".join([f"$w.SendKeys([char]{code})" for _ in range(min(times, 60))])
    _run_ps("$w=New-Object -ComObject WScript.Shell;" + sends)


def _system_info_text() -> str:
    """本地读取时间/电量/内存/磁盘/屏幕，返回给模型看的中文摘要。"""
    now = _dt.datetime.now()
    week = "一二三四五六日"[now.weekday()]
    lines = [f"现在时间：{now.strftime('%Y-%m-%d %H:%M')}，星期{week}"]

    class _POWER(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("Reserved1", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    try:
        sp = _POWER()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sp)):
            bp = int(sp.BatteryLifePercent) & 0xFF
            flag = int(sp.BatteryFlag) & 0xFF
            if (flag & 128) or bp == 255 or bp > 100:
                lines.append("电池：这台是台式机，没有电池，直连电源")
            else:
                charging = "（正在充电）" if sp.ACLineStatus == 1 else ""
                lines.append(f"电池：{bp}%{charging}")
    except Exception:
        pass

    try:
        class _MEM(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        mem = _MEM()
        mem.dwLength = ctypes.sizeof(_MEM)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            total = mem.ullTotalPhys / 1073741824
            used = (mem.ullTotalPhys - mem.ullAvailPhys) / 1073741824
            lines.append(f"内存：已用 {used:.1f}G / 共 {total:.1f}G（{mem.dwMemoryLoad}%）")
    except Exception:
        pass

    try:
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        for drive in ("C:\\", "D:\\"):
            if ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(drive), None, ctypes.byref(total), ctypes.byref(free)
            ):
                lines.append(
                    f"{drive[:1]} 盘：剩余 {free.value / 1073741824:.1f}G / 共 {total.value / 1073741824:.1f}G"
                )
    except Exception:
        pass

    try:
        w = int(ctypes.windll.user32.GetSystemMetrics(0))
        h = int(ctypes.windll.user32.GetSystemMetrics(1))
        lines.append(f"屏幕分辨率：{w}x{h}")
    except Exception:
        pass
    return "\n".join(lines)


def _find_lnk(name: str) -> Path | None:
    name_l = name.strip().lower()
    hits: list[Path] = []
    for root in START_MENU_ROOTS:
        if not root.exists():
            continue
        try:
            for lnk in root.rglob("*.lnk"):
                if name_l in lnk.stem.lower():
                    hits.append(lnk)
        except Exception:  # pragma: no cover
            continue
    if not hits:
        return None
    hits.sort(key=lambda p: len(p.stem))
    return hits[0]


def _known_app_paths(name: str) -> list[str]:
    n = name.lower()
    table = {
        "b站": [r"C:\Program Files\bilibili\哔哩哔哩.exe"],
        "哔哩哔哩": [r"C:\Program Files\bilibili\哔哩哔哩.exe"],
        "bilibili": [r"C:\Program Files\bilibili\哔哩哔哩.exe"],
        "计算器": ["calc.exe"],
        "记事本": ["notepad.exe"],
        "画图": ["mspaint.exe"],
        "任务管理器": ["taskmgr.exe"],
        "edge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
        "浏览器": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    }
    for key, paths in table.items():
        if key in n:
            expanded = [os.path.expandvars(p) for p in paths]
            return [
                p for p in expanded
                if p in ("calc.exe", "notepad.exe", "mspaint.exe", "taskmgr.exe") or os.path.exists(p)
            ]
    return []


# --------------------------------------------------------------------------- #
# 窗口 / 剪贴板
# --------------------------------------------------------------------------- #
def _enum_windows() -> list[tuple[int, str, str]]:
    """返回 [(hwnd, 标题, 进程名)]，只包含可见的顶层窗口。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    result: list[tuple[int, str, str]] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if not title or title in ("Program Manager", "Windows Input Experience"):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = ""
            try:
                h = kernel32.OpenProcess(0x1000, False, pid.value)
                if h:
                    pbuf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size))
                    proc = os.path.basename(pbuf.value)
                    kernel32.CloseHandle(h)
            except Exception:
                pass
            result.append((int(hwnd), title, proc))
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return result


def activate_window(title: str) -> bool:
    q = (title or "").strip().lower()
    if not q:
        return False
    wins = _enum_windows()
    hits = [w for w in wins if q in w[1].lower() or q in w[2].lower()]
    if not hits:
        return False
    # 优先标题匹配的，其次进程名匹配的
    hits.sort(key=lambda w: 0 if q in w[1].lower() else 1)
    hwnd = hits[0][0]
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    return True


def _get_clipboard() -> str:
    text = _run_ps(
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw", timeout=15
    )
    return text or ""


def _set_clipboard(text: str) -> bool:
    import base64

    b64 = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
    _run_ps(
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')))" % b64,
        timeout=15,
    )
    return True
user32 = ctypes.windll.user32
ULONG_PTR = ctypes.c_size_t

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

VK = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "win": 0x5B, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "capslock": 0x14, "printscreen": 0x2C,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
MODIFIERS = {"ctrl", "control", "alt", "shift", "win"}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _mi(dx: int, dy: int, data: int, flags: int) -> INPUT:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    return item


def _ki(vk: int, scan: int, flags: int) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def _send(*items: INPUT) -> None:
    if not items:
        return
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def screen_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def mouse_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def move_mouse(x: int, y: int) -> tuple[int, int]:
    w, h = screen_size()
    x = max(0, min(int(x), w - 1))
    y = max(0, min(int(y), h - 1))
    ax = int(x * 65535 / max(1, w - 1))
    ay = int(y * 65535 / max(1, h - 1))
    _send(_mi(ax, ay, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE))
    return x, y


def mouse_click(button: str = "left", double: bool = False) -> None:
    flags = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(str(button or "left").lower(), (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
    for _ in range(2 if double else 1):
        _send(_mi(0, 0, 0, flags[0]), _mi(0, 0, 0, flags[1]))
        time.sleep(0.06)


def mouse_scroll(clicks: int) -> None:
    _send(_mi(0, 0, int(clicks) * 120, MOUSEEVENTF_WHEEL))


def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", duration: float = 0.45) -> None:
    is_right = str(button or "left").lower() == "right"
    down = MOUSEEVENTF_RIGHTDOWN if is_right else MOUSEEVENTF_LEFTDOWN
    up = MOUSEEVENTF_RIGHTUP if is_right else MOUSEEVENTF_LEFTUP
    move_mouse(x1, y1)
    time.sleep(0.08)
    _send(_mi(0, 0, 0, down))
    steps = 12
    for i in range(1, steps + 1):
        move_mouse(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
        time.sleep(max(0.01, duration / steps))
    time.sleep(0.06)
    _send(_mi(0, 0, 0, up))


def type_text(text: str) -> int:
    """用 SendInput 逐字符输入（支持中文）。"""
    text = str(text or "")[:300]
    count = 0
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            code -= 0x10000
            seq = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        else:
            seq = [code]
        items: list[INPUT] = []
        for c in seq:
            items.append(_ki(0, c, KEYEVENTF_UNICODE))
        for c in seq:
            items.append(_ki(0, c, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        _send(*items)
        time.sleep(0.008)
        count += 1
    return count


def _vk(name: str) -> int | None:
    n = name.strip().lower()
    if n in VK:
        return VK[n]
    if len(n) == 1:
        if n.isalpha():
            return ord(n.upper())
        if n.isdigit():
            return ord(n)
        if n == " ":
            return 0x20
    return None


def press_keys(keys: str) -> str:
    parts = [p for p in str(keys or "").split("+") if p.strip()]
    if not parts:
        return ""
    codes = []
    for p in parts:
        code = _vk(p)
        if code is None:
            raise ValueError(f"不认识的按键：{p}")
        codes.append(code)
    modifiers = [c for p, c in zip(parts, codes) if p.strip().lower() in MODIFIERS]
    normal = [c for p, c in zip(parts, codes) if p.strip().lower() not in MODIFIERS]
    for c in modifiers:
        _send(_ki(c, 0, 0))
        time.sleep(0.02)
    for c in normal:
        _send(_ki(c, 0, 0), _ki(c, 0, KEYEVENTF_KEYUP))
        time.sleep(0.02)
    for c in reversed(modifiers):
        _send(_ki(c, 0, KEYEVENTF_KEYUP))
        time.sleep(0.02)
    return keys


# --------------------------------------------------------------------------- #
# 工具执行
# --------------------------------------------------------------------------- #
async def execute_tool(name: str, args: dict, ctx: dict | None = None) -> str:
    ctx = ctx or {}
    args = args or {}
    try:
        if name == "open_url":
            url = str(args.get("url", "")).strip()
            if not url:
                return "没有给网址"
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            _startfile(url)
            return f"已经用浏览器打开 {url}"
        if name == "web_search":
            q = str(args.get("query", "")).strip()
            if not q:
                return "没有给搜索词"
            _startfile("https://www.bing.com/search?q=" + quote(q))
            return f"已经打开搜索：{q}"
        if name == "open_app":
            app_name = str(args.get("name", "")).strip()
            if not app_name:
                return "没有给软件名"
            for exe in _known_app_paths(app_name):
                try:
                    _startfile(exe)
                    return f"已经打开 {app_name}"
                except Exception:
                    continue
            lnk = _find_lnk(app_name)
            if lnk is not None:
                _startfile(str(lnk))
                return f"已经打开 {app_name}（{lnk.stem}）"
            return f"没找到叫「{app_name}」的软件"
        if name == "set_volume":
            if "level" in args and args["level"] is not None:
                lvl = max(0, min(100, int(args["level"])))
                _send_media_code(173)
                _send_media_code(175, int(lvl / 2))
                _send_media_code(173)
                return f"音量已调到大约 {lvl}%"
            delta = int(args.get("delta", 0))
            if delta > 0:
                _send_media_code(175, delta // 2)
                return "音量调大了"
            if delta < 0:
                _send_media_code(174, (-delta) // 2)
                return "音量调小了"
            return "没有给音量参数"
        if name == "mute_volume":
            _send_media_code(173)
            return "已经切换静音"
        if name == "press_media_key":
            key = str(args.get("key", ""))
            code = {"play_pause": 179, "next": 176, "prev": 177}.get(key)
            if code is None:
                return "不认识的按键"
            _send_media_code(code)
            return {"play_pause": "已播放/暂停", "next": "已切下一首", "prev": "已切上一首"}[key]
        if name == "set_timer":
            session = ctx.get("session")
            minutes = float(args.get("minutes", 0) or 0)
            label = str(args.get("label", "时间到啦"))
            minutes = max(0.05, min(minutes, 24 * 60))
            if session is not None:
                async def _remind() -> None:
                    await asyncio.sleep(minutes * 60)
                    text = f"主人～{label}！时间到啦！"
                    await session.send({"type": "info", "text": text})
                    await session.speak(text)

                asyncio.create_task(_remind())
            return f"好的，{minutes:g} 分钟后提醒主人：{label}"
        if name == "take_screenshot":
            out = SHOTS / f"shot_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            _run_ps(
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                "$g=[System.Drawing.Graphics]::FromImage($bmp);"
                "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
                f"$bmp.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
                "$g.Dispose();$bmp.Dispose()"
            )
            return f"截屏已保存到 {out}" if out.exists() else "截屏失败"
        if name == "get_time":
            now = _dt.datetime.now()
            week = "一二三四五六日"[now.weekday()]
            return now.strftime(f"%Y年%m月%d日 %H:%M，星期{week}")
        if name == "get_system_info":
            return _system_info_text()
        if name == "write_memo":
            text = str(args.get("text", "")).strip()
            if not text:
                return "没有给内容"
            stamp = _dt.datetime.now().strftime("%Y-%m-%d")
            with MEMO.open("a", encoding="utf-8") as fh:
                fh.write(f"- ({stamp}) {text}\n")
            return "已记进长期记忆"
        if name == "lock_pc":
            subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
            return "已经锁屏"
        if name == "look_screen":
            import importlib

            screen_mod = importlib.import_module("screen")
            info = await asyncio.to_thread(screen_mod.look)
            text = str(info.get("text") or "").strip()
            head = "【前台窗口】%s（%s）" % (info.get("title") or "?", info.get("proc") or "?")
            if not text:
                return head + "\n屏幕上没有识别到文字（可能是纯画面）。"
            return head + "\n【屏幕文字】" + text[:1800]
        if name == "mouse_move":
            x, y = move_mouse(int(args.get("x", 0)), int(args.get("y", 0)))
            return f"鼠标移到 ({x}, {y})"
        if name == "mouse_click":
            mouse_click(str(args.get("button") or "left"), bool(args.get("double")))
            return "点了一下"
        if name == "mouse_scroll":
            mouse_scroll(int(args.get("clicks") or 0))
            return "滚轮滚好了"
        if name == "mouse_drag":
            mouse_drag(
                int(args.get("x1", 0)), int(args.get("y1", 0)),
                int(args.get("x2", 0)), int(args.get("y2", 0)),
                str(args.get("button") or "left"),
            )
            return "拖拽完成"
        if name == "type_text":
            n = type_text(str(args.get("text") or ""))
            return f"输入了 {n} 个字"
        if name == "press_keys":
            press_keys(str(args.get("keys") or ""))
            return "按好了"
        if name == "mouse_pos":
            x, y = mouse_pos()
            return f"鼠标在 ({x}, {y})"
        if name == "activate_window":
            title = str(args.get("title") or "").strip()
            if not title:
                return "没有给窗口名"
            ok = activate_window(title)
            return f"已经切到「{title}」" if ok else f"没找到标题包含「{title}」的窗口"
        if name == "clipboard_read":
            text = _get_clipboard()
            return "剪贴板内容：" + (text[:1500] if text else "（空的）")
        if name == "clipboard_write":
            text = str(args.get("text") or "")
            ok = _set_clipboard(text)
            return "已经放进剪贴板" if ok else "写剪贴板失败"
        if name == "open_path":
            p = str(args.get("path") or "").strip()
            if not p:
                return "没有给路径"
            p = os.path.expandvars(p)
            if not os.path.exists(p):
                return f"路径不存在：{p}"
            _startfile(p)
            return f"已经打开 {p}"
        if name == "wait_seconds":
            try:
                secs = float(args.get("seconds", 1) or 1)
            except Exception:
                secs = 1.0
            secs = max(0.2, min(8.0, secs))
            await asyncio.sleep(secs)
            return f"等了 {secs:g} 秒"
        if name == "sing":
            return "（唱歌由主程序处理）"
        if name == "switch_persona":
            return "（人格切换由主程序处理）"
    except Exception as exc:  # pragma: no cover
        log.exception("工具 %s 执行失败", name)
        return f"工具执行失败：{exc}"
    return f"没有这个工具：{name}"
