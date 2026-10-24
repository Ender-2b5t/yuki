#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日奈唱歌：edge-tts 逐字念 + WORLD 变调/拉伸 = 有旋律的儿歌。

思路：每个字单独合成，用 WORLD 把音高改到乐谱的音、把时长拉到节拍长度，
加一点颤音，再拼起来。效果是"电子歌姬"风格，但真的有调、能跟着对口型。
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pyworld

SR = 24000
HOP = 5.0  # ms

# 歌单：(歌词单字, MIDI 音高, 时长秒)
SONGS: dict[str, list[tuple[str, int, float]]] = {
    "小星星": [
        ("一", 60, 0.42), ("闪", 60, 0.42), ("一", 67, 0.42), ("闪", 67, 0.42),
        ("亮", 69, 0.42), ("亮", 69, 0.42), ("晶", 67, 0.8),
        ("满", 65, 0.42), ("天", 65, 0.42), ("都", 64, 0.42), ("是", 64, 0.42),
        ("小", 62, 0.42), ("星", 62, 0.42), ("星", 60, 0.9),
    ],
    "生日快乐": [
        ("祝", 67, 0.38), ("你", 67, 0.37), ("生", 69, 0.38), ("日", 67, 0.38),
        ("快", 72, 0.38), ("乐", 71, 0.75),
        ("祝", 67, 0.38), ("你", 67, 0.37), ("生", 69, 0.38), ("日", 67, 0.38),
        ("快", 74, 0.38), ("乐", 72, 0.95),
    ],
    "两只老虎": [
        ("两", 60, 0.36), ("只", 62, 0.36), ("老", 64, 0.36), ("虎", 60, 0.5),
        ("两", 60, 0.36), ("只", 62, 0.36), ("老", 64, 0.36), ("虎", 60, 0.5),
        ("跑", 64, 0.36), ("得", 65, 0.36), ("快", 67, 0.6),
        ("跑", 64, 0.36), ("得", 65, 0.36), ("快", 67, 0.7),
    ],
}

ALIASES = {
    "星星": "小星星",
    "twinkle": "小星星",
    "生日": "生日快乐",
    "happy birthday": "生日快乐",
    "老虎": "两只老虎",
    "两只": "两只老虎",
}


def resolve_song(name: str) -> str:
    n = (name or "").strip()
    if n in SONGS:
        return n
    low = n.lower()
    for key, val in ALIASES.items():
        if key in low:
            return val
    for key in SONGS:
        if key in n:
            return key
    return "小星星"


async def _tts_pcm(text: str, voice: str) -> np.ndarray:
    """edge-tts 合成一个字 → 24k 单声道 float32。"""
    import edge_tts

    try:
        communicate = edge_tts.Communicate(text, voice, rate="+0%", pitch="+0Hz")
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk["data"])
        if not buf:
            return np.zeros(0, dtype=np.float32)
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as td:
            mp3 = Path(td) / "a.mp3"
            wav = Path(td) / "a.wav"
            mp3.write_bytes(bytes(buf))
            subprocess.run(
                [exe, "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), "-f", "wav", str(wav)],
                capture_output=True,
                timeout=60,
            )
            with wave.open(str(wav), "rb") as fh:
                data = fh.readframes(fh.getnframes())
        return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return np.zeros(0, dtype=np.float32)


def _vibrato(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    return 1.0 + 0.012 * np.sin(2 * np.pi * 5.2 * t)


def _resing(sample: np.ndarray, midi: float, duration: float) -> np.ndarray:
    """把一个字变成指定音高、指定时长的"唱腔"。"""
    need = int(duration * SR)
    if sample.size < int(0.025 * SR):
        return np.zeros(need, dtype=np.float32)
    x = sample.astype(np.float64)
    try:
        f0, t = pyworld.harvest(x, SR, f0_floor=70.0, f0_ceil=900.0, frame_period=HOP)
        sp = pyworld.cheaptrick(x, f0, t, SR)
        ap = pyworld.d4c(x, f0, t, SR)
        n_out = max(2, int(round(duration * 1000.0 / HOP)))
        src = np.arange(sp.shape[0])
        idx = np.linspace(0, sp.shape[0] - 1, n_out)
        sp2 = np.empty((n_out, sp.shape[1]), dtype=np.float64)
        ap2 = np.empty((n_out, ap.shape[1]), dtype=np.float64)
        for c in range(sp.shape[1]):
            sp2[:, c] = np.interp(idx, src, sp[:, c])
        for c in range(ap.shape[1]):
            ap2[:, c] = np.interp(idx, src, ap[:, c])
        target = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        f02 = target * _vibrato(n_out)
        y = pyworld.synthesize(f02, sp2, ap2, SR).astype(np.float32)
    except Exception:
        y = np.zeros(need, dtype=np.float32)
    if y.size < need:
        y = np.pad(y, (0, need - y.size))
    y = y[:need]
    fade = int(0.015 * SR)
    if y.size > 2 * fade:
        y[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        y[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return y


async def render_song(name: str, voice: str) -> tuple[np.ndarray, str, float]:
    """返回 (PCM float32, 歌词, 秒数)。"""
    song = resolve_song(name)
    notes = SONGS[song]
    tasks = [asyncio.create_task(_tts_pcm(ch, voice)) for ch, _m, _d in notes]
    pcm_list = await asyncio.gather(*tasks)
    parts: list[np.ndarray] = []
    for (_ch, midi, dur), pcm in zip(notes, pcm_list):
        parts.append(_resing(pcm, midi, dur))
        parts.append(np.zeros(int(0.02 * SR), dtype=np.float32))
    y = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0:
        y = (y * (0.9 / peak)).astype(np.float32)
    lyrics = "".join(ch for ch, _m, _d in notes)
    return y, lyrics, len(y) / SR


def save_wav(pcm: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(pcm, -1.0, 1.0)
    pcm16 = (data * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm16.tobytes())
    return path


def song_names() -> list[str]:
    return list(SONGS.keys())
