#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看屏幕：截图 + 离线 OCR 读出屏幕上的文字 + 前台窗口信息。

只在你让她"看屏幕"（或被 osu! 解说模式触发）时截图，不会偷偷一直截。
为了速度：只裁前台窗口、缩到最长边 1280、关掉方向分类。
"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import ImageGrab

BASE = Path(__file__).resolve().parent.parent
SHOTS = BASE / "data" / "screenshots"
MAX_SIDE = 1280

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR(use_cls=False)
    return _ocr


def capture(save: bool = True, crop_to_window: bool = True, max_side: int = MAX_SIDE):
    """截主屏幕（可裁到前台窗口），返回 (PIL.Image, 保存路径或 None)。"""
    img = ImageGrab.grab(all_screens=False)
    if crop_to_window:
        try:
            import awareness

            rect = awareness.foreground_rect()
            if rect:
                left, top, right, bottom = rect
                w, h = img.size
                left, top = max(0, left), max(0, top)
                right, bottom = min(w, right), min(h, bottom)
                if right - left > 80 and bottom - top > 80:
                    img = img.crop((left, top, right, bottom))
        except Exception:
            pass
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
    path = None
    if save:
        SHOTS.mkdir(parents=True, exist_ok=True)
        path = SHOTS / f"screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
        img.save(path)
    return img, path


def ocr_image(img) -> list[str]:
    try:
        result, _elapsed = _get_ocr()(img)
    except Exception:
        return []
    lines: list[str] = []
    for item in result or []:
        try:
            text = str(item[1])
        except Exception:
            text = ""
        if text.strip():
            lines.append(text.strip())
    return lines


def look(max_lines: int = 90, save: bool = True, max_side: int = MAX_SIDE) -> dict:
    """看屏幕：前台窗口 + 截图 + OCR 文字。"""
    import awareness

    fg = awareness.foreground()
    img, path = capture(save=save, max_side=max_side)
    started = time.time()
    lines = ocr_image(img)
    return {
        "proc": fg.get("proc", ""),
        "title": fg.get("title", ""),
        "label": fg.get("label", ""),
        "size": list(img.size),
        "lines": len(lines),
        "text": " / ".join(lines[:max_lines])[:2600],
        "screenshot": str(path) if path else None,
        "ocr_seconds": round(time.time() - started, 2),
    }
