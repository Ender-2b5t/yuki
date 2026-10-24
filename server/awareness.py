#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知觉：只读地看看主人在用什么软件 / 键鼠闲了多久。

只读窗口标题和进程名，不截图、不读窗口内容、不碰游戏内存。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

APP_LABELS = [
    ("bilibili", "B站"),
    ("msedge", "浏览器"),
    ("chrome", "浏览器"),
    ("firefox", "浏览器"),
    ("wechat", "微信"),
    ("weixin", "微信"),
    ("qq", "QQ"),
    ("cloudmusic", "网易云音乐"),
    ("qqlive", "腾讯视频"),
    ("potplayer", "看视频"),
    ("code", "VS Code"),
    ("explorer", "文件管理器"),
    ("notepad", "记事本"),
    ("photoshop", "画画"),
    ("blockbench", "Blockbench"),
]


def label_for(proc: str, title: str) -> str:
    low = (proc + " " + title).lower()
    for key, label in APP_LABELS:
        if key in low:
            return label
    return ""


def foreground() -> dict:
    """返回 {"proc", "title", "label"}。"""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"proc": "", "title": "", "label": ""}
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buf, max(1, length + 1))
        title = buf.value or ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            size = wintypes.DWORD(1024)
            pbuf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, pbuf, ctypes.byref(size)):
                proc = pbuf.value.rsplit("\\", 1)[-1]
            kernel32.CloseHandle(handle)
        return {"proc": proc, "title": title, "label": label_for(proc, title)}
    except Exception:
        return {"proc": "", "title": "", "label": ""}


def idle_seconds() -> float:
    """键鼠多久没动了（秒）。"""
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now = kernel32.GetTickCount64()
        return max(0.0, (now - info.dwTime) / 1000.0)
    except Exception:
        return 0.0


def foreground_rect() -> tuple[int, int, int, int] | None:
    """前台窗口的屏幕矩形 (left, top, right, bottom)。"""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        if rect.right - rect.left < 50 or rect.bottom - rect.top < 50:
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None
