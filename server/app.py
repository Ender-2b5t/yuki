#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yuki 后端：DeepSeek 大脑 + 离线中文语音识别 + edge-tts 嘴 + 本地记忆 + 电脑控制。

启动：python app.py    （监听 127.0.0.1:8765，Electron 桌面壳会自动拉起）
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# 同目录模块（用脚本方式启动时已加入 sys.path）
import awareness
import screen as screen_mod
import sing as sing_mod
import stt as asr_mod
import tools as tools_mod

BASE = Path(__file__).resolve().parent.parent
SERVER = BASE / "server"
WEB = BASE / "web"
VENDOR = BASE / "vendor"
MODELS = BASE / "models"
DATA = BASE / "data"
LOGS = DATA / "logs"
TTS_TMP = DATA / "tts-tmp"
for _p in (DATA, LOGS, MODELS, TTS_TMP):
    _p.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


CONFIG = _load_json(SERVER / "config.json", {})
PERSONA = _load_json(SERVER / "persona.json", {})
PERSONA_DARK = _load_json(SERVER / "persona_dark.json", {})
PERSONA_STATE = DATA / "persona_state.json"


def _apply_avatar_name(p: dict) -> dict:
    """Hina 3D 模式下名字/自称改为「日奈」。"""
    try:
        if str((CONFIG or {}).get("avatar") or "yuki") == "hina3d":
            p = dict(p)
            p["display_name"] = "日奈"
            system = str(p.get("system") or "")
            p["system"] = system.replace("yuki", "日奈").replace("Yuki", "日奈")
            greeting = str(p.get("greeting") or "")
            p["greeting"] = greeting.replace("yuki", "日奈").replace("Yuki", "日奈")
    except Exception:
        pass
    return p


def active_persona() -> dict:
    """当前人格（normal / dark）。"""
    mode = "normal"
    try:
        mode = (_load_json(PERSONA_STATE, {}) or {}).get("mode", "normal")
    except Exception:
        mode = "normal"
    if mode == "dark" and PERSONA_DARK:
        merged = dict(PERSONA)
        merged.update(PERSONA_DARK)
        merged["mode"] = "dark"
        return _apply_avatar_name(merged)
    merged = dict(PERSONA)
    merged["mode"] = "normal"
    return _apply_avatar_name(merged)


def current_voice() -> tuple[str, str, str]:
    p = active_persona()
    return (
        str(p.get("voice") or os.environ.get("NUROW_VOICE") or CONFIG.get("voice") or "zh-CN-XiaoyiNeural"),
        str(p.get("rate") or CONFIG.get("rate") or "+6%"),
        str(p.get("pitch") or CONFIG.get("pitch") or "+2Hz"),
    )


def set_persona_mode(mode: str) -> dict:
    mode = "dark" if str(mode).lower() in ("dark", "evil", "黑", "黑化", "bad") else "normal"
    try:
        PERSONA_STATE.write_text(json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        log.warning("保存人格状态失败：%s", exc)
    p = active_persona()
    return {
        "mode": mode,
        "display_name": p.get("display_name", "yuki"),
        "greeting": p.get("greeting", ""),
        "voice": p.get("voice", ""),
    }

PORT = int(os.environ.get("NUROW_PORT") or CONFIG.get("port") or 8765)
API_KEY = os.environ.get("NUROW_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or CONFIG.get("api_key") or ""
BASE_URL = str(CONFIG.get("base_url") or "https://api.deepseek.com").rstrip("/")
MODEL_NAME = str(CONFIG.get("model") or "deepseek-chat")
VOICE = os.environ.get("NUROW_VOICE") or CONFIG.get("voice") or PERSONA.get("voice") or "zh-CN-XiaoyiNeural"
RATE = str(CONFIG.get("rate") or "+6%")
PITCH = str(CONFIG.get("pitch") or "+2Hz")
HISTORY_TURNS = int(CONFIG.get("history_turns") or 24)
MAX_TOKENS = int(CONFIG.get("max_tokens") or 700)
TEMPERATURE = float(CONFIG.get("temperature") or 1.05)
AUTO_GREET = bool(CONFIG.get("auto_greet", True))
VOICE_ENABLED = bool(CONFIG.get("voice_enabled", False))
TOOLS_ENABLED = bool(CONFIG.get("tools_enabled", True))
SUMMARY_EVERY = int(CONFIG.get("summary_every", 12))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS / "server.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("yuki")

DB_PATH = DATA / "nurow.db"
MEMO_PATH = DATA / "memory.md"
SUMMARY_CURSOR = DATA / ".summary_cursor"
STATE_PATH = DATA / "yuki_state.json"

# --------------------------------------------------------------------------- #
# yuki 的"生命状态"：心情 / 精神 / 关系值 / 聊天次数，会落盘，重启不失忆
# --------------------------------------------------------------------------- #
LOVE_LEVELS = [
    (0, "刚认识"),
    (12, "有点熟"),
    (40, "朋友"),
    (100, "亲近"),
    (220, "很黏你"),
    (400, "最最最黏你"),
]


def _default_state() -> dict:
    return {
        "mood": 72.0,
        "energy": 82.0,
        "love": 0.0,
        "chats": 0,
        "pats": 0,
        "last_chat": 0.0,
        "first_seen": time.time(),
        "day": "",
        "today_chats": 0,
    }


def load_state() -> dict:
    state = _default_state()
    try:
        if STATE_PATH.exists():
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in state:
                    if k in raw:
                        state[k] = raw[k]
    except Exception as exc:  # pragma: no cover
        log.warning("读取 yuki 状态失败：%s", exc)
    return state


def save_state(state: dict) -> None:
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception as exc:  # pragma: no cover
        log.warning("保存 yuki 状态失败：%s", exc)


STATE = load_state()


def love_level(love: float) -> str:
    level = LOVE_LEVELS[0][1]
    for need, name in LOVE_LEVELS:
        if love >= need:
            level = name
    return level


def state_context() -> str:
    """把"此刻的她"写进 system prompt，让回答有真实感、有连续性。"""
    now = time.time()
    t = time.localtime(now)
    week = "一二三四五六日"[t.tm_wday]
    hour = t.tm_hour
    if hour < 5:
        period, hint = "凌晨", "你困得不行，说话软绵绵、容易打哈欠"
    elif hour < 9:
        period, hint = "早上", "你刚睡醒，声音软软的，会问主人吃早饭没"
    elif hour < 12:
        period, hint = "上午", "你精神很好，想找点事做"
    elif hour < 14:
        period, hint = "中午", "你会关心主人吃午饭没"
    elif hour < 18:
        period, hint = "下午", "你有点犯困，但会硬撑着陪主人"
    elif hour < 22:
        period, hint = "傍晚", "你精神不错，爱撒娇"
    else:
        period, hint = "深夜", "你有点困但不想睡，会催主人早点休息"
    gap_min = int((now - float(STATE.get("last_chat") or now)) / 60) if STATE.get("last_chat") else 0
    if not STATE.get("last_chat"):
        gap_text = "刚刚开机，你们还没说过话"
    elif gap_min <= 3:
        gap_text = "刚刚还在聊天"
    elif gap_min < 60:
        gap_text = f"上次聊天是 {gap_min} 分钟前"
    elif gap_min < 24 * 60:
        gap_text = f"上次聊天是 {gap_min // 60} 小时前"
    else:
        gap_text = f"上次聊天是 {gap_min // (24 * 60)} 天前"
    mood = float(STATE.get("mood", 72))
    mood_text = "很好" if mood >= 80 else ("不错" if mood >= 60 else ("有点低落" if mood >= 35 else "不太开心"))
    love = float(STATE.get("love", 0))
    lines = [
        "【此刻的你】",
        f"- 现在时间：{t.tm_year}年{t.tm_mon}月{t.tm_mday}日 星期{week} {hour:02d}:{t.tm_min:02d}（{period}）。{hint}。",
        f"- 你今天已经和主人聊了 {STATE.get('today_chats', 0)} 次，{gap_text}。",
        f"- 你的心情：{mood_text}（{mood:.0f}/100），精神：{float(STATE.get('energy', 80)):.0f}/100。",
        f"- 和主人的关系：{love_level(love)}（好感 {love:.0f}）。主人一共摸过你 {STATE.get('pats', 0)} 次头。",
        "把这些自然地带进语气里（比如傍晚问吃没吃饭、困了打哈欠、关系好就更黏），但不要逐条复述这些数值。",
    ]
    return "\n".join(lines)


def update_state_on_chat() -> None:
    now = time.time()
    t = time.localtime(now)
    day = time.strftime("%Y-%m-%d", t)
    if STATE.get("day") != day:
        STATE["day"] = day
        STATE["today_chats"] = 0
    STATE["chats"] = int(STATE.get("chats", 0)) + 1
    STATE["today_chats"] = int(STATE.get("today_chats", 0)) + 1
    STATE["last_chat"] = now
    STATE["mood"] = max(0.0, min(100.0, float(STATE.get("mood", 72)) + 1.5))
    hour = t.tm_hour
    base_energy = 82 if 8 <= hour < 23 else 40
    STATE["energy"] = float(base_energy)
    STATE["love"] = float(STATE.get("love", 0)) + 2.0
    save_state(STATE)


def update_state_on_pat() -> None:
    STATE["pats"] = int(STATE.get("pats", 0)) + 1
    STATE["mood"] = min(100.0, float(STATE.get("mood", 72)) + 1.0)
    STATE["love"] = float(STATE.get("love", 0)) + 3.0
    save_state(STATE)


ASR = asr_mod.ASREngine()


PET_SESSIONS: set = set()


async def broadcast_pet(obj: dict) -> None:
    for s in list(PET_SESSIONS):
        await s.send(obj)


async def broadcast_pet_speak(text: str) -> None:
    for s in list(PET_SESSIONS):
        asyncio.create_task(s.speak(text))


# --------------------------------------------------------------------------- #
# 唱歌
# --------------------------------------------------------------------------- #
SONG_DIR = DATA / "songs"


async def prepare_song(name: str) -> dict:
    """生成一首歌，返回 {file, duration, lyrics, song, data_b64}。"""
    song = sing_mod.resolve_song(name)
    voice = current_voice()[0]
    pcm, lyrics, duration = await sing_mod.render_song(song, voice)
    path = sing_mod.save_wav(pcm, SONG_DIR / ("%d_%s.wav" % (int(time.time()), song)))
    log.info("唱了《%s》 %.1fs → %s", song, duration, path)
    return {
        "song": song,
        "lyrics": lyrics,
        "duration": round(duration, 2),
        "file": str(path),
        "data_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


async def deliver_song(session: Session, name: str) -> None:
    try:
        await session.send({"type": "info", "text": "（正在准备《%s》，等我一下下…）" % (name or "小星星")})
        info = await prepare_song(name)
        await session.send(
            {
                "type": "sing",
                "song": info["song"],
                "lyrics": info["lyrics"],
                "duration": info["duration"],
                "format": "wav",
                "data": info["data_b64"],
            }
        )
    except Exception as exc:  # pragma: no cover
        log.exception("sing failed")
        await session.send({"type": "info", "text": "（呜…唱歌失败了：%s）" % str(exc)[:60]})


# --------------------------------------------------------------------------- #
# 知觉（看主人在用什么）+ 主动搭话
# --------------------------------------------------------------------------- #
AWARE_ENABLED = bool(CONFIG.get("awareness", True))
PROACTIVE_ENABLED = bool(CONFIG.get("proactive", True))
SCREEN_COMMENT = bool(CONFIG.get("screen_comment", True))
SCREEN_COMMENT_COOLDOWN = float(CONFIG.get("screen_comment_cooldown", 40))
COMMENT_COOLDOWN = float(CONFIG.get("comment_cooldown_sec", 240))
PROACTIVE_AFTER = float(CONFIG.get("proactive_after_sec", 18 * 60))
LAST_USER_AT = time.time()
_last_app_label = ""
_last_app_comment_at = 0.0
_last_proactive_at = 0.0
_last_screen_check_at = 0.0
_last_screen_comment_at = 0.0
_last_screen_text = ""


async def spontaneous_comment(instruction: str) -> str:
    """让 yuki 主动说一句（气泡 + 语音）。"""
    if not API_KEY:
        return ""
    info = await asyncio.to_thread(awareness.foreground)
    idle = await asyncio.to_thread(awareness.idle_seconds)
    context = (
        "当前时间：%s。主人前台窗口：%s（%s），鼠标键盘闲置 %d 秒。"
        % (
            time.strftime("%m月%d日 %H:%M"),
            info.get("proc") or "未知",
            (info.get("title") or "")[:60],
            int(idle),
        )
    )
    text = await _complete_once(
        [
            {"role": "system", "content": active_persona().get("system", "")},
            {"role": "user", "content": context + "\n" + instruction},
        ],
        max_tokens=100,
        temperature=1.0,
    )
    text = clean_for_tts(text)[:80]
    if not text:
        return ""
    await broadcast_pet({"type": "info", "text": text})
    await broadcast_pet_speak(text)
    return text


async def awareness_loop() -> None:
    """每 6 秒看一眼前台窗口；换到好玩的软件、或太久没人理她，就主动搭话。"""
    global _last_app_label, _last_app_comment_at, _last_proactive_at
    global _last_screen_check_at, _last_screen_comment_at, _last_screen_text
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.sleep(6)
            if not (AWARE_ENABLED or PROACTIVE_ENABLED):
                continue
            if not PET_SESSIONS:
                continue
            busy = any(getattr(s, "busy", False) for s in PET_SESSIONS)
            if busy:
                continue
            info = await asyncio.to_thread(awareness.foreground)
            idle = await asyncio.to_thread(awareness.idle_seconds)
            now = time.time()
            label = info.get("label") or ""
            if label != _last_app_label:
                was = _last_app_label
                _last_app_label = label
                if (
                    AWARE_ENABLED
                    and label in ("B站", "浏览器", "看视频")
                    and label != was
                    and now - _last_app_comment_at > COMMENT_COOLDOWN
                    and idle < 180
                ):
                    _last_app_comment_at = now
                    title = (info.get("title") or "")[:60]
                    await spontaneous_comment(
                        "你刚看到主人打开了「%s」（窗口标题：%s）。"
                        "用一句自然的话吐槽或搭话，不超过 30 字，别问他问题。" % (label, title)
                    )
            if (
                PROACTIVE_ENABLED
                and now - LAST_USER_AT > PROACTIVE_AFTER
                and now - _last_proactive_at > max(600.0, COMMENT_COOLDOWN * 2)
                and idle < 600
            ):
                _last_proactive_at = now
                await spontaneous_comment(
                    "主人已经很久没跟你说话了。主动找他聊一句：可以关心、撒娇、聊他正在做的事。"
                    "不超过 30 字，语气自然，别重复上次说过的话。"
                )
            # 看屏幕上的文字（只读文字版陪伴）：文字有变化就吐槽/接话
            proc_name = (info.get("proc") or "").lower()
            title_now = (info.get("title") or "").strip()
            if (
                SCREEN_COMMENT
                and now - _last_screen_check_at > max(1.5, min(12.0, SCREEN_COMMENT_COOLDOWN))
                and now - _last_screen_comment_at > SCREEN_COMMENT_COOLDOWN
                and idle < 120
                and proc_name not in ("electron.exe", "python.exe", "pythonw.exe", "code.exe")
                and title_now not in ("", "yuki")
            ):
                _last_screen_check_at = now
                try:
                    shot = await asyncio.to_thread(screen_mod.look, 60, False, 960)
                    text = (shot.get("text") or "").strip()
                except Exception:
                    text = ""
                if len(text) >= 6 and text != _last_screen_text:
                    _last_screen_text = text
                    _last_screen_comment_at = now
                    await spontaneous_comment(
                        "你刚看了一眼主人的屏幕（前台窗口：%s）。屏幕上读到的文字：%s。"
                        "用一句自然的话吐槽或者接话，不超过 30 字，别问他问题、别念数字。" % (title_now[:50], text[:300])
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover
            log.debug("awareness loop error: %s", exc)


# --------------------------------------------------------------------------- #
# 记忆（全部本机）
# --------------------------------------------------------------------------- #
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts REAL NOT NULL,
               role TEXT NOT NULL,
               content TEXT NOT NULL)"""
    )
    return conn


def save_message(role: str, content: str) -> None:
    if not content:
        return
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(ts, role, content) VALUES(?,?,?)",
                (time.time(), role, content),
            )
    except Exception as exc:  # pragma: no cover
        log.warning("save_message failed: %s", exc)


def recent_messages(limit: int) -> list[dict]:
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]
    except Exception as exc:  # pragma: no cover
        log.warning("recent_messages failed: %s", exc)
        return []


def memory_notes() -> str:
    try:
        return MEMO_PATH.read_text(encoding="utf-8").strip()[-6000:]
    except Exception:
        return ""


def build_messages(text: str, extra_system: str = "") -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": active_persona().get("system", "")}]
    notes = memory_notes()
    is_hina = str((CONFIG or {}).get("avatar") or "yuki") == "hina3d"
    if is_hina:
        notes = notes.replace("yuki", "日奈").replace("Yuki", "日奈")
        msgs.append(
            {
                "role": "system",
                "content": "（现在你以「日奈」的形态陪伴主人：名字叫日奈，记忆和聊天记录里出现的 yuki 也是你，只是换了名字和样子。别人问名字时回答日奈。）",
            }
        )
    if notes:
        msgs.append({"role": "system", "content": "【关于主人的长期记忆】\n" + notes})
    if extra_system:
        msgs.append({"role": "system", "content": extra_system})
    history = recent_messages(HISTORY_TURNS)
    if is_hina:
        for m in history:
            content = m.get("content")
            if content:
                m["content"] = content.replace("yuki", "日奈").replace("Yuki", "日奈")
    msgs.extend(history)
    msgs.append({"role": "user", "content": text})
    return msgs


TOOL_HINT = (
    "你可以真实地操作主人的电脑（不是嘴上说说）：\n"
    "- 打开：open_app 开软件、open_url 开网页、web_search 搜索、open_path 打开文件/文件夹\n"
    "- 操作：mouse_move 移动鼠标、mouse_click 点击、mouse_scroll 滚动、mouse_drag 拖拽、"
    "type_text 打字、press_keys 快捷键、activate_window 切到某个窗口、clipboard_read/write 读写剪贴板\n"
    "- 系统：set_volume 调音量、mute_volume 静音、press_media_key 媒体键、set_timer 提醒、"
    "take_screenshot 截屏、get_time 时间、get_system_info 电脑状态（电量/内存/磁盘/分辨率）、"
    "lock_pc 锁屏、write_memo 记笔记、wait_seconds 等界面加载\n"
    "- 看：look_screen 截图并读屏幕文字（知道你屏幕上有什么）；sing 唱歌。\n"
    "【任务规则】\n"
    "1) 主人交代电脑上的任务时，直接动手：按顺序调用工具，一步一步做完，不要只描述步骤、不要问「需要我帮你吗」。\n"
    "2) 多步骤任务就连续调用多个工具（例如：先 open_app 打开软件，再 wait_seconds 等它加载，再 type_text 输入）。\n"
    "3) 每步做完可以简短说一句，但别啰嗦；全部完成后用一句话汇报结果。\n"
    "4) 工具失败就如实说哪一步没成功，不要假装成功。\n"
    "5) 普通聊天不要乱调工具。"
)


# --------------------------------------------------------------------------- #
# LLM（DeepSeek，OpenAI 兼容）
# --------------------------------------------------------------------------- #
async def _stream_completion(messages: list[dict], tools: list[dict] | None, on_delta=None):
    payload: dict = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": True,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"}
    content = ""
    tool_calls: dict[int, dict] = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
        async with client.stream(
            "POST", BASE_URL + "/chat/completions", json=payload, headers=headers
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:400]
                log.error("LLM HTTP %s: %s", resp.status_code, body)
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {body[:120]}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0]["delta"]
                except Exception:
                    continue
                text = delta.get("content")
                if text:
                    content += text
                    if on_delta is not None:
                        await on_delta(text)
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index", 0))
                    slot = tool_calls.setdefault(
                        idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
    ordered = [tool_calls[i] for i in sorted(tool_calls)]
    return content, ordered


async def _complete_once(messages: list[dict], max_tokens: int = 300, temperature: float = 0.3) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            resp = await client.post(BASE_URL + "/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # pragma: no cover
        log.warning("complete_once failed: %s", exc)
        return ""


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
BOUNDARY = set("。！？!?；;\n…")
COMMAS = set("，、,：:")
EMOJI_RE = re.compile(
    "[" "\U0001F000-\U0001FAFF" "\u2600-\u27BF" "\uFE0F" "\u2B00-\u2BFF" "\u2190-\u21FF" "]"
)


def clean_for_tts(text: str) -> str:
    t = re.sub(r"\*[^*]*\*", "", text)
    t = re.sub(r"（[^）]*）|\([^)]*\)", "", t)
    t = re.sub(r"[#>`|*_~]+", "", t)
    t = EMOJI_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ，,。")
    return t


def _sapi_wav(text: str) -> bytes | None:
    try:
        txt = TTS_TMP / "sapi.txt"
        wav = TTS_TMP / "sapi.wav"
        txt.write_text(text, encoding="utf-8")
        if wav.exists():
            wav.unlink()
        script = (
            "$ErrorActionPreference='Stop';"
            "Add-Type -AssemblyName System.Speech;"
            f"$t=Get-Content -Raw -Encoding UTF8 '{txt}';"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "try{$s.SelectVoice('Microsoft Huihui Desktop')}catch{};"
            f"$s.SetOutputToWaveFile('{wav}');$s.Speak($t);$s.Dispose();"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            timeout=45,
        )
        if wav.exists() and wav.stat().st_size > 2000:
            return wav.read_bytes()
    except Exception as exc:  # pragma: no cover
        log.warning("sapi fallback failed: %s", exc)
    return None


async def synth_tts(text: str) -> tuple[bytes, str]:
    text = clean_for_tts(text)
    if not text:
        return b"", ""
    if len(text) > 150:
        text = text[:150]
    voice, rate, pitch = current_voice()
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk["data"])
        if buf:
            return bytes(buf), "mp3"
    except Exception as exc:
        log.warning("edge-tts failed (%s), falling back to SAPI", exc)
    wav = await asyncio.to_thread(_sapi_wav, text)
    if wav:
        return wav, "wav"
    return b"", ""


def split_ready(buf: str, soft_limit: int = 60) -> tuple[list[str], str]:
    ready: list[str] = []
    while True:
        hit = -1
        for i, ch in enumerate(buf):
            if ch in BOUNDARY and i >= 1:
                hit = i + 1
                break
        if hit > 0:
            ready.append(buf[:hit])
            buf = buf[hit:]
            continue
        if len(buf) >= soft_limit:
            cut = -1
            for i in range(len(buf) - 1, -1, -1):
                if buf[i] in COMMAS:
                    cut = i + 1
                    break
            if cut > 0:
                ready.append(buf[:cut])
                buf = buf[cut:]
                continue
        break
    return ready, buf


# --------------------------------------------------------------------------- #
# WebSocket 会话
# --------------------------------------------------------------------------- #
class Session:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.seq = 0
        self.muted = False
        self.busy = False
        self.stt_stream = None
        self.stt_text = ""
        # 免提唤醒状态
        self.wake_stream = None
        self.wake_on = False
        self.capture_active = False
        self.capture_text = ""
        self.capture_live = ""
        self.capture_started = 0.0
        self.capture_last = 0.0
        self.last_wake_at = 0.0
        # 摸头计数（摸太多次她会害羞抗议）
        self.pat_count = 0
        self.pat_since = 0.0

    async def send(self, obj: dict) -> None:
        try:
            await self.ws.send_json(obj)
        except Exception:
            pass

    async def speak(self, text: str) -> None:
        if self.muted or not VOICE_ENABLED:
            return
        audio, fmt = await synth_tts(text)
        if not audio:
            return
        self.seq += 1
        await self.send(
            {
                "type": "audio",
                "seq": self.seq,
                "format": fmt,
                "text": clean_for_tts(text)[:60],
                "data": base64.b64encode(audio).decode("ascii"),
            }
        )


# --------------------------------------------------------------------------- #
# 免提唤醒：常开麦克风里听到「yuki」就进入对话
# --------------------------------------------------------------------------- #
# 小模型经常把「yuki」听成「有姬/尤琪」等等，所以用模糊匹配
_WAKE_FUZZY = re.compile(r"[有尤悠由][姬琪奇基纪琦旗齐]|雪")
_WAKE_EN = ("yuki", "yuk1", "yu ki", "youki")
_WAKE_HINA = ("日奈", "日乃", "日菜", "希娜", "阳奈", "hina", "ひな")


def _has_wake(text: str) -> bool:
    t = (text or "").lower().replace(" ", "").replace("　", "")
    if not t:
        return False
    if any(w in t for w in _WAKE_EN):
        return True
    if str((CONFIG or {}).get("avatar") or "yuki") == "hina3d":
        if any(w in t for w in _WAKE_HINA):
            return True
    return bool(_WAKE_FUZZY.search(t))


async def _watch_capture(session: Session) -> None:
    """唤醒后等她说完一句话：安静 1.1 秒或超过 15 秒就收尾。"""
    try:
        while True:
            await asyncio.sleep(0.25)
            if not session.capture_active:
                return
            now = time.time()
            text = (session.capture_text + " " + session.capture_live).strip()
            if text and now - session.capture_last > 1.1:
                break
            if not text and now - session.capture_started > 8.0:
                break
            if now - session.capture_started > 15.0:
                break
        if not session.capture_active:
            return
        text = (session.capture_text + " " + session.capture_live).strip()
        session.capture_active = False
        session.capture_text = ""
        session.capture_live = ""
        session.wake_stream = ASR.new_stream()
        await session.send({"type": "wake", "state": "idle"})
        if len(text) >= 2:
            await session.send({"type": "stt_final", "text": text})
            await session.send({"type": "user_text", "text": text})
            await handle_chat(session, text)
        else:
            await session.send({"type": "info", "text": "（嗯？叫我干嘛呀）"})
    except asyncio.CancelledError:
        return
    except Exception as exc:  # pragma: no cover
        log.warning("wake capture error: %s", exc)


async def _wake_feed(session: Session, raw: bytes) -> None:
    """免提模式下持续喂音频：先找唤醒词，再收一句话。"""
    if not raw:
        return
    if session.wake_stream is None:
        session.wake_stream = ASR.new_stream()
    now0 = time.time()
    for kind, text in session.wake_stream.feed(raw):
        if session.capture_active:
            session.capture_last = now0
            if kind == "final":
                session.capture_text = (session.capture_text + text).strip()
                session.capture_live = ""
                await session.send(
                    {"type": "stt_partial", "text": session.capture_text}
                )
            else:
                session.capture_live = text
                await session.send(
                    {"type": "stt_partial", "text": (session.capture_text + " " + text).strip()}
                )
            continue
        if session.busy:
            continue
        if kind == "partial" and _has_wake(text):
            now = time.time()
            if now - session.last_wake_at < 2.0:
                continue
            session.last_wake_at = now
            session.capture_active = True
            session.capture_text = ""
            session.capture_live = ""
            session.capture_started = now
            session.capture_last = now
            session.wake_stream = ASR.new_stream()
            await session.send({"type": "wake", "state": "triggered", "text": text})
            asyncio.create_task(_watch_capture(session))


def _intent_hint(text: str):
    """轻量意图识别：返回 (提示文本, 兜底动作)。兜底动作是 (工具名, 参数)，
    LLM 第一轮没调工具时由服务端直接执行，保证「让她干活」不会只回嘴。"""
    t = (text or "").lower()
    rules = [
        (("音量", "声音大", "声音小", "调大", "调小", "小声", "大声"), "set_volume（要静音就用 mute_volume）"),
        (("静音", "取消静音"), "mute_volume"),
        (("下一首", "上一首", "播放", "暂停", "切歌"), "press_media_key"),
        (("打字", "输入一段", "打一行", "打上", "帮我写", "输入文字"), "type_text（需要的话先 activate_window 切到目标窗口）"),
        (("鼠标移到", "移动鼠标", "鼠标移动", "鼠标放", "点一下", "点击", "双击"), "mouse_move / mouse_click"),
        (("滚动", "滑下去", "往下翻"), "mouse_scroll"),
        (("提醒", "分钟后", "倒计时"), "set_timer"),
        (("截图", "截张图", "截个图", "截一下图", "截个屏"), "take_screenshot"),
        (("锁屏",), "lock_pc"),
        (("切到", "切换到", "切回", "激活窗口"), "activate_window"),
        (("打开", "启动"), "open_app / open_url"),
        (("剪贴板", "复制", "粘贴"), "clipboard_read / clipboard_write / press_keys"),
        (("看屏幕", "屏幕上", "我屏幕上"), "look_screen"),
    ]
    for keys, tool in rules:
        if any(k in t for k in keys):
            hint = f"【本轮意图】主人这是让你用电脑做事的指令：请直接调用 {tool} 真实执行，不要只回文字。"
            return hint, _forced_action(t)
    # 明显是多步任务：强化"连续做完"的提示
    action_words = ("打开", "打字", "输入", "移到", "点击", "截图", "看屏幕", "调", "提醒", "切到", "锁屏", "滚动", "拖")
    multi = sum(1 for w in action_words if w in t) >= 2 or any(
        k in t for k in ("然后", "接着", "最后再", "做完再", "顺便再")
    )
    if multi:
        return (
            "【本轮意图】这是多步任务：请把步骤拆开，连续调用多个工具一步一步做完"
            "（需要就先 activate_window 切窗口、用 wait_seconds 等加载），全部完成后用一句话汇报结果。",
            None,
        )
    return "", None


def _forced_action(t: str):
    """把明显的简单指令翻译成一个兜底工具动作。"""
    if "静音" in t:
        return ("mute_volume", {})
    if any(k in t for k in ("音量", "声音大", "声音小", "调大", "调小", "小声", "大声")):
        louder = any(k in t for k in ("调大", "大声", "音量大", "响一点"))
        return ("set_volume", {"delta": 25 if louder else -20})
    if any(k in t for k in ("截图", "截张图", "截个图", "截一下图", "截个屏")):
        return ("take_screenshot", {})
    if "锁屏" in t:
        return ("lock_pc", {})
    if any(k in t for k in ("下一首", "上一首", "播放", "暂停", "切歌")):
        key = "next" if "下一首" in t else ("prev" if "上一首" in t else "play_pause")
        return ("press_media_key", {"key": key})
    if any(k in t for k in ("看屏幕", "屏幕上", "我屏幕上")):
        return ("look_screen", {})
    m = re.search(r"鼠标.*?(\d{2,4})\s*[,， ]\s*(\d{2,4})", t)
    if m:
        return ("mouse_move", {"x": int(m.group(1)), "y": int(m.group(2))})
    if "屏幕中间" in t or "屏幕正中" in t:
        return ("mouse_move", {"x": 960, "y": 540})
    if any(k in t for k in ("鼠标移到", "移动鼠标", "鼠标移动", "鼠标放")):
        return ("mouse_move", {"x": 960, "y": 540})
    cn_digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def _cn_num(s: str) -> float:
        if s.isdigit():
            return float(s)
        if s == "十":
            return 10.0
        if "十" in s:
            left, right = s.split("十", 1)
            tens = cn_digits.get(left, 1) if left else 1
            ones = cn_digits.get(right, 0) if right else 0
            return float(tens * 10 + ones)
        return float(cn_digits.get(s, 0))

    m = re.search(r"([0-9零一二两三四五六七八九十]+)\s*分钟", t)
    if m and any(k in t for k in ("提醒", "后")):
        minutes = _cn_num(m.group(1))
        if minutes > 0:
            return ("set_timer", {"minutes": minutes, "label": "时间到啦"})
    m = re.search(r"[：:]\s*(.+)$", t) if t else None
    if m and any(k in t for k in ("打字", "输入", "打上", "帮我写")):
        return ("type_text", {"text": m.group(1).strip()[:200]})
    return None


async def _quick_local_answer(text: str) -> str | None:
    """时间和电脑状态这类问题本地秒回，不走大模型（快、免费、不会说错时间）。"""
    t = (text or "").strip()
    if not t or len(t) > 40:
        return None
    now = time.localtime()
    hm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
    week = "一二三四五六日"[now.tm_wday]
    if any(k in t for k in ("现在几点", "几点了", "现在时间", "当前时间", "现在是什么时间")) and not any(
        k in t for k in ("提醒", "闹钟", "定时")
    ):
        return f"现在 {hm} 啦～"
    if any(k in t for k in ("今天几号", "几月几号", "今天日期", "今天周几", "今天星期几")):
        return f"今天是 {now.tm_mon} 月 {now.tm_mday} 日，星期{week} 哦～"
    if any(
        k in t
        for k in (
            "系统状态", "电脑状态", "内存", "CPU", "cpu", "磁盘", "硬盘", "剩余空间",
            "电量", "电池", "多少电", "有电", "电源", "屏幕分辨率", "分辨率",
        )
    ):
        info = await tools_mod.execute_tool("get_system_info", {}, {})
        return "我看了一下哦：\n" + str(info)
    return None


async def handle_chat(session: Session, text: str) -> None:
    global LAST_USER_AT
    LAST_USER_AT = time.time()
    if session.busy:
        await session.send({"type": "info", "text": "（我正在说话呢，等我一下）"})
        return
    quick = await _quick_local_answer(text)
    if quick is not None:
        session.busy = True
        try:
            save_message("user", text)
            save_message("assistant", quick)
            await session.send({"type": "start"})
            await session.send({"type": "delta", "text": quick})
            await session.send({"type": "done", "text": quick})
            update_state_on_chat()
            if VOICE_ENABLED:
                await session.speak(quick)
            asyncio.create_task(maybe_summarize())
        finally:
            session.busy = False
        return
    session.busy = True
    queue: asyncio.Queue = asyncio.Queue()

    async def tts_worker() -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            try:
                await session.speak(item)
            except Exception as exc:  # pragma: no cover
                log.warning("tts worker error: %s", exc)

    worker = asyncio.create_task(tts_worker())
    buf = ""
    full = ""

    async def on_delta(delta_text: str) -> None:
        nonlocal buf
        buf += delta_text
        await session.send({"type": "delta", "text": delta_text})
        ready, buf = split_ready(buf)
        for sent in ready:
            sent = sent.strip()
            if len(clean_for_tts(sent)) >= 2:
                await queue.put(sent)

    try:
        update_state_on_chat()
        hint = TOOL_HINT if TOOLS_ENABLED else ""
        forced_action = None
        if TOOLS_ENABLED:
            extra_hint, forced_action = _intent_hint(text)
            if extra_hint:
                hint = hint + "\n" + extra_hint
        hint = (hint + "\n\n" + state_context()).strip()
        messages = build_messages(text, hint)
        save_message("user", text)
        await session.send({"type": "start"})
        for _round in range(10):
            try:
                content, tcs = await _stream_completion(
                    messages, tools_mod.TOOL_SCHEMAS if TOOLS_ENABLED else None, on_delta=on_delta
                )
            except RuntimeError as exc:
                msg = str(exc)
                await session.send({"type": "delta", "text": f"（脑子卡住了：{msg[:80]}）"})
                content, tcs = "", []
            full += content
            if not tcs and _round == 0 and forced_action:
                # 模型没动手：服务端兜底执行一次，保证「让她干活」真的干
                fname, fargs = forced_action
                await session.send({"type": "tool_call", "name": fname, "args": fargs})
                try:
                    fresult = await tools_mod.execute_tool(fname, fargs, {"session": session})
                except Exception as exc:  # pragma: no cover
                    fresult = "工具执行失败：%s" % exc
                messages.append(
                    {
                        "role": "assistant",
                        "content": content or "",
                        "tool_calls": [
                            {
                                "id": "call_fallback",
                                "type": "function",
                                "function": {
                                    "name": fname,
                                    "arguments": json.dumps(fargs, ensure_ascii=False),
                                },
                            }
                        ],
                    }
                )
                messages.append(
                    {"role": "tool", "tool_call_id": "call_fallback", "content": str(fresult)[:800]}
                )
                continue
            if not tcs:
                break
            assistant_msg: dict = {"role": "assistant", "content": content or ""}
            assistant_msg["tool_calls"] = [
                {"id": tc["id"] or f"call_{i}", "type": "function", "function": tc["function"]}
                for i, tc in enumerate(tcs)
            ]
            messages.append(assistant_msg)
            for i, tc in enumerate(tcs):
                name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"] or "{}"
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
                await session.send({"type": "tool_call", "name": name, "args": args})
                if name == "switch_persona":
                    info = set_persona_mode(str(args.get("mode") or "normal"))
                    await broadcast_pet({"type": "persona", **info})
                    result = "已经切换成「%s」啦" % info["display_name"]
                elif name == "sing":
                    song = str(args.get("song") or "小星星")
                    asyncio.create_task(deliver_song(session, song))
                    result = "好呀，我唱《%s》给你听～" % sing_mod.resolve_song(song)
                else:
                    result = await tools_mod.execute_tool(name, args, {"session": session})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": assistant_msg["tool_calls"][i]["id"],
                        "content": str(result)[:800],
                    }
                )
        if buf.strip() and len(clean_for_tts(buf)) >= 1:
            await queue.put(buf.strip())
        if not full:
            full = "……"
    except Exception as exc:  # pragma: no cover
        log.exception("handle_chat error: %s", exc)
        if not full:
            full = "（我刚刚走神了……你再说一遍？）"
            await session.send({"type": "delta", "text": full})
    finally:
        await queue.put(None)
        await asyncio.gather(worker, return_exceptions=True)
        save_message("assistant", full)
        session.busy = False
        await session.send({"type": "done", "text": full})
        asyncio.create_task(maybe_summarize())


# --------------------------------------------------------------------------- #
# 自动长期记忆：每聊 N 条总结一次有用的信息
# --------------------------------------------------------------------------- #
_summary_lock = asyncio.Lock()


async def maybe_summarize() -> None:
    if _summary_lock.locked():
        return
    async with _summary_lock:
        try:
            last_id = 0
            if SUMMARY_CURSOR.exists():
                last_id = int(SUMMARY_CURSOR.read_text().strip() or "0")
            with db() as conn:
                rows = conn.execute(
                    "SELECT id, role, content FROM messages WHERE id > ? ORDER BY id ASC LIMIT 80",
                    (last_id,),
                ).fetchall()
            if len(rows) < SUMMARY_EVERY:
                return
            convo = "\n".join(f"{r[1]}: {r[2]}" for r in rows)
            prompt = (
                "下面是我和主人的聊天记录。请提炼出值得长期记住的、关于主人的事实或约定"
                "（喜好、习惯、重要的事、关系进展等），不超过 5 条，每条一行，以「- 」开头，"
                "用第三人称写。没有值得记的就输出空字符串。不要别的内容。\n\n" + convo[-6000:]
            )
            text = await _complete_once(
                [{"role": "system", "content": "你真的只输出条目，别的不输出。"},
                 {"role": "user", "content": prompt}],
                max_tokens=220,
                temperature=0.2,
            )
            new_lines = [
                ln.strip()
                for ln in (text or "").splitlines()
                if ln.strip().startswith("- ") and len(ln.strip()) > 3
            ]
            if new_lines:
                existing = memory_notes()
                fresh = [ln for ln in new_lines if ln not in existing]
                if fresh:
                    with MEMO_PATH.open("a", encoding="utf-8") as fh:
                        for ln in fresh:
                            fh.write(ln + "\n")
                    log.info("memory 新增 %d 条", len(fresh))
            SUMMARY_CURSOR.write_text(str(rows[-1][0]))
        except Exception as exc:  # pragma: no cover
            log.warning("summarize failed: %s", exc)


# --------------------------------------------------------------------------- #
# 模型发现
# --------------------------------------------------------------------------- #
def _find_model_files() -> dict:
    out = {"model3": [], "model2": [], "vrm": []}
    try:
        for p in sorted(MODELS.rglob("*.model3.json")):
            out["model3"].append(p)
        for p in sorted(MODELS.rglob("*.model.json")):
            if not p.name.endswith(".model3.json"):
                out["model2"].append(p)
        for p in sorted(MODELS.rglob("*.vrm")):
            out["vrm"].append(p)
    except Exception as exc:  # pragma: no cover
        log.warning("scan models failed: %s", exc)
    return out


def _model_payload() -> dict:
    current = _load_json(MODELS / "current.json", {})
    files = _find_model_files()
    chosen: Path | None = None
    if current.get("model"):
        cand = (MODELS / str(current["model"]).replace("\\", "/")).resolve()
        try:
            cand.relative_to(MODELS.resolve())
            if cand.exists():
                chosen = cand
        except Exception:
            chosen = None
    if chosen is None:
        for key in ("model3", "model2"):
            if files[key]:
                chosen = files[key][0]
                break
    payload = {
        "url": None,
        "cubism": None,
        "scale": float(current.get("scale", 1.0)),
        "offsetX": float(current.get("offsetX", 0.0)),
        "offsetY": float(current.get("offsetY", 0.0)),
        "available": {
            "model3": [p.name for p in files["model3"]],
            "model2": [p.name for p in files["model2"]],
            "vrm": [p.name for p in files["vrm"]],
        },
    }
    if chosen is not None:
        rel = chosen.relative_to(MODELS.resolve()).as_posix()
        payload["url"] = "/models/" + "/".join(quote(seg) for seg in rel.split("/"))
        payload["cubism"] = 2 if chosen.name.endswith(".model.json") else 4
    return payload


# --------------------------------------------------------------------------- #
# FastAPI
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(awareness_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="yuki", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith("/") or path.endswith(".html") or path.endswith(".js") or path.endswith(".css") or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/api/health")
async def health():
    voice, _rate, _pitch = current_voice()
    return {
        "ok": True,
        "name": active_persona().get("display_name", "nurow"),
        "mode": active_persona().get("mode", "normal"),
        "brain": MODEL_NAME,
        "brain_ready": bool(API_KEY),
        "voice": voice,
        "asr": {"backend": ASR.kind, "model": ASR.model_name},
        "tools": TOOLS_ENABLED,
        "awareness": AWARE_ENABLED,
        "proactive": PROACTIVE_ENABLED,
        "python": sys.version.split()[0],
    }


@app.get("/api/state")
async def api_state():
    now = time.time()
    return {
        "ok": True,
        "mood": round(float(STATE.get("mood", 72)), 1),
        "energy": round(float(STATE.get("energy", 80)), 1),
        "love": round(float(STATE.get("love", 0)), 1),
        "level": love_level(float(STATE.get("love", 0))),
        "today_chats": int(STATE.get("today_chats", 0)),
        "chats": int(STATE.get("chats", 0)),
        "pats": int(STATE.get("pats", 0)),
        "last_chat_ago": int(now - float(STATE.get("last_chat") or now)),
        "first_seen": STATE.get("first_seen", now),
    }


@app.get("/api/model")
async def api_model():
    return JSONResponse(_model_payload())


@app.get("/api/persona")
async def api_persona():
    p = active_persona()
    return {
        "name": p.get("name", "nurow"),
        "display_name": p.get("display_name", "Nurow"),
        "greeting": p.get("greeting", ""),
        "mode": p.get("mode", "normal"),
        "voice": p.get("voice", ""),
    }


@app.post("/api/persona/switch")
async def api_persona_switch(payload: dict):
    mode = str((payload or {}).get("mode") or "normal")
    info = set_persona_mode(mode)
    await broadcast_pet({"type": "persona", **info})
    return {"ok": True, **info}


@app.get("/api/awareness")
async def api_awareness():
    info = await asyncio.to_thread(awareness.foreground)
    idle = await asyncio.to_thread(awareness.idle_seconds)
    return {
        "enabled": AWARE_ENABLED,
        "proactive": PROACTIVE_ENABLED,
        "foreground": info,
        "idle_seconds": round(idle, 1),
        "last_user_ago": round(time.time() - LAST_USER_AT, 1),
        "comment_cooldown": COMMENT_COOLDOWN,
        "proactive_after": PROACTIVE_AFTER,
    }


@app.post("/api/awareness/comment")
async def api_awareness_comment(payload: dict | None = None):
    instruction = str((payload or {}).get("instruction") or "").strip() or (
        "主动跟主人说一句话（关心或吐槽都行），不超过 30 字。"
    )
    text = await spontaneous_comment(instruction)
    return {"ok": bool(text), "text": text}


@app.get("/api/screen")
async def api_screen():
    return await asyncio.to_thread(screen_mod.look)


@app.post("/api/screen/comment")
async def api_screen_comment():
    info = await asyncio.to_thread(screen_mod.look, 60, False, 960)
    text = (info.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "屏幕上没读到文字"}
    await spontaneous_comment(
        "你刚看了一眼主人的屏幕（前台窗口：%s）。屏幕上读到的文字：%s。"
        "用一句自然的话吐槽或者接话，不超过 30 字，别问他问题、别念数字。"
        % ((info.get("title") or "")[:50], text[:300])
    )
    return {"ok": True, "text": text[:200]}


@app.get("/api/memory")
async def api_memory():
    return {"notes": memory_notes(), "recent": recent_messages(20)}


@app.get("/api/tools")
async def api_tools():
    return {"enabled": TOOLS_ENABLED, "tools": [t["function"]["name"] for t in tools_mod.TOOL_SCHEMAS]}


@app.get("/api/songs")
async def api_songs():
    return {"songs": sing_mod.song_names()}


@app.post("/api/sing")
async def api_sing(payload: dict | None = None):
    payload = payload or {}
    name = str(payload.get("song") or "小星星")
    info = await prepare_song(name)
    broadcast = bool(payload.get("broadcast"))
    if broadcast:
        msg = {
            "type": "sing",
            "song": info["song"],
            "lyrics": info["lyrics"],
            "duration": info["duration"],
            "format": "wav",
            "data": info["data_b64"],
        }
        await broadcast_pet(msg)
    return {
        "ok": True,
        "song": info["song"],
        "lyrics": info["lyrics"],
        "duration": info["duration"],
        "file": info["file"],
        "broadcast": broadcast,
    }


@app.get("/api/model/expressions")
async def api_model_expressions():
    cur = _load_json(MODELS / "current.json", {})
    model_rel = str(cur.get("model") or "")
    if model_rel:
        cand = (MODELS / model_rel.replace("\\", "/")).resolve()
        try:
            cand.relative_to(MODELS.resolve())
            mapping = cand.parent / "expressions_map.json"
            if mapping.exists():
                return {"expressions": _load_json(mapping, {})}
        except Exception:
            pass
    return {"expressions": {}}


# --------------------------------------------------------------------------- #
# 摸头/戳脸/碰尾巴的反应
# --------------------------------------------------------------------------- #
REACTIONS_NORMAL = {
    "head": ["唔…摸头的话我会变乖的哦～", "嘿嘿，被摸头了…再来一下！", "我的猫耳软吧？只准轻轻摸哦"],
    "face": ["欸？！脸、脸红了啦…", "别戳我脸呀，讨厌～", "呜…你在干嘛呀"],
    "body": ["呀！痒…别闹啦！", "喂喂，不可以乱碰的哦", "哼哼，想偷袭我？"],
    "legs": ["喂——！", "不许碰那里啦！", "再这样我要生气了哦"],
    "tail": ["喵！？尾巴别抓！", "呜哇！尾巴会炸毛的！", "啊…被摸到尾巴了…"],
}
REACTIONS_DARK = {
    "head": ["…摸头？你以为我会乖乖的吗。", "哼，这一下就当赏你的。", "手拿开，笨蛋。"],
    "face": ["再戳一下试试？", "……脸红了？你看错了。", "别得寸进尺。"],
    "body": ["你是不是想被打一顿？", "敢碰我？胆子不小。", "哼，幼稚。"],
    "legs": ["喂。"], 
    "tail": ["喵！…谁准你抓尾巴的！", "再碰就咬你。", "…嗯，就一下。"],
}


def _reaction_line(part: str) -> str:
    pool = REACTIONS_DARK if active_persona().get("mode") == "dark" else REACTIONS_NORMAL
    lines = pool.get(part) or pool.get("body") or ["……"]
    return str(random.choice(lines))


# --------------------------------------------------------------------------- #
# 设置面板
# --------------------------------------------------------------------------- #
VOICE_CHOICES = [
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊（元气少女，适合 yuki）"},
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（温柔 / 百变）"},
    {"id": "zh-CN-XiaochenNeural", "name": "晓辰（自然松弛）"},
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻（台湾腔）"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "小北（东北话）"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "小妮（陕西话）"},
    {"id": "en-US-AriaNeural", "name": "Aria（英语女声）"},
]


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "***"
    return key[:8] + "…" + key[-4:]


@app.get("/api/settings")
async def api_settings_get():
    p = active_persona()
    cur = _load_json(MODELS / "current.json", {})
    files = _find_model_files()
    voice, rate, pitch = current_voice()
    return {
        "persona": {
            "display_name": p.get("display_name", "yuki"),
            "mode": p.get("mode", "normal"),
            "voice": voice,
            "rate": rate,
            "pitch": pitch,
        },
        "config": {
            "auto_greet": AUTO_GREET,
            "voice_enabled": VOICE_ENABLED,
            "tools_enabled": TOOLS_ENABLED,
            "awareness": AWARE_ENABLED,
            "proactive": PROACTIVE_ENABLED,
            "screen_comment": SCREEN_COMMENT,
            "screen_comment_cooldown": SCREEN_COMMENT_COOLDOWN,
            "avatar": str(CONFIG.get("avatar") or "yuki"),
            "halo": bool(CONFIG.get("halo", True)),
            "yuki_outfit": str(CONFIG.get("yuki_outfit") or ""),
            "yuki_expressions": list(CONFIG.get("yuki_expressions") or []),
            "idle_level": str(CONFIG.get("idle_level") or "normal"),
            "talk_flap": bool(CONFIG.get("talk_flap", True)),
            "always_on_top": bool(CONFIG.get("always_on_top", True)),
            "auto_start": bool(CONFIG.get("auto_start", False)),
            "click_react": bool(CONFIG.get("click_react", True)),
            "gaze_follow": bool(CONFIG.get("gaze_follow", True)),
            "gaze_speed": str(CONFIG.get("gaze_speed") or "fast"),
            "idle_actions": bool(CONFIG.get("idle_actions", True)),
            "proactive_minutes": round(PROACTIVE_AFTER / 60.0, 1),
            "wake_autostart": bool(CONFIG.get("wake_autostart", False)),
            "api_key_set": bool(API_KEY),
            "api_key_masked": _mask_key(str(CONFIG.get("api_key") or "")),
        },
        "model": {
            "current": cur.get("model", "yuki/jk.model3.json"),
            "scale": float(cur.get("scale", 1.0)),
            "offsetY": float(cur.get("offsetY", 0.0)),
            "available": [p2.relative_to(MODELS.resolve()).as_posix() for p2 in files["model3"]],
        },
        "voices": VOICE_CHOICES,
    }


@app.post("/api/settings")
async def api_settings_save(payload: dict | None = None):
    global CONFIG, PERSONA, API_KEY, VOICE, RATE, PITCH
    global AUTO_GREET, VOICE_ENABLED, TOOLS_ENABLED, AWARE_ENABLED, PROACTIVE_ENABLED
    global SCREEN_COMMENT, SCREEN_COMMENT_COOLDOWN
    global COMMENT_COOLDOWN, PROACTIVE_AFTER
    payload = payload or {}

    # 1) 人设（名字/声音/语速/音调）
    persona = _load_json(SERVER / "persona.json", {})
    if "display_name" in payload:
        name = str(payload.get("display_name") or "").strip()[:20] or "yuki"
        persona["display_name"] = name
    for key in ("voice", "rate", "pitch"):
        val = payload.get(key)
        if val:
            persona[key] = str(val)
    (SERVER / "persona.json").write_text(
        json.dumps(persona, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2) 开关 / key / 间隔
    cfg = _load_json(SERVER / "config.json", {})
    for key in ("auto_greet", "voice_enabled", "tools_enabled", "awareness", "proactive", "screen_comment", "wake_autostart",
                "always_on_top", "auto_start", "click_react", "gaze_follow", "idle_actions"):
        if key in payload:
            cfg[key] = bool(payload[key])
    if "screen_comment_cooldown" in payload:
        try:
            cfg["screen_comment_cooldown"] = max(1.0, min(120.0, float(payload["screen_comment_cooldown"])))
        except Exception:
            pass
    if "proactive_minutes" in payload:
        try:
            cfg["proactive_after_sec"] = max(60.0, float(payload["proactive_minutes"]) * 60.0)
        except Exception:
            pass
    avatar = str(payload.get("avatar") or "").strip()
    if avatar in ("yuki", "hina3d"):
        cfg["avatar"] = avatar
    if "halo" in payload:
        cfg["halo"] = bool(payload["halo"])
    exprs = payload.get("yuki_expressions")
    if isinstance(exprs, list):
        clean = []
        for x in exprs[:40]:
            x = str(x).strip()
            if re.match(r"^expr_\d{2}$", x) and x not in clean:
                clean.append(x)
        cfg["yuki_expressions"] = clean
    outfit = str(payload.get("yuki_outfit") or "").strip()
    if outfit in ("", "scarf", "sweater", "cat", "glasses", "ears", "tail", "hat",
                  "eyeshadow", "fringe", "bun", "longhair"):
        cfg["yuki_outfit"] = outfit
    idle_level = str(payload.get("idle_level") or "").strip()
    if idle_level in ("quiet", "normal", "lively"):
        cfg["idle_level"] = idle_level
    if "talk_flap" in payload:
        cfg["talk_flap"] = bool(payload["talk_flap"])
    gaze_speed = str(payload.get("gaze_speed") or "").strip()
    if gaze_speed in ("fast", "slow"):
        cfg["gaze_speed"] = gaze_speed
    api_key = str(payload.get("api_key") or "").strip()
    if api_key and "…" not in api_key and api_key.startswith("sk-"):
        cfg["api_key"] = api_key
    (SERVER / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 3) 模型大小 / 位置 / 选中的模型
    cur = _load_json(MODELS / "current.json", {})
    if payload.get("model"):
        cand = (MODELS / str(payload["model"]).replace("\\", "/")).resolve()
        try:
            cand.relative_to(MODELS.resolve())
            if cand.exists():
                cur["model"] = str(payload["model"]).replace("\\", "/")
        except Exception:
            pass
    if "scale" in payload:
        try:
            cur["scale"] = max(0.5, min(1.5, float(payload["scale"])))
        except Exception:
            pass
    if "offsetY" in payload:
        try:
            cur["offsetY"] = max(-0.3, min(0.3, float(payload["offsetY"])))
        except Exception:
            pass
    (MODELS / "current.json").write_text(
        json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 4) 人格模式
    if payload.get("mode") in ("normal", "dark"):
        set_persona_mode(str(payload["mode"]))

    # 5) 刷新内存里的配置
    CONFIG = _load_json(SERVER / "config.json", {})
    PERSONA = _load_json(SERVER / "persona.json", {})
    API_KEY = os.environ.get("NUROW_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or CONFIG.get("api_key") or ""
    VOICE = str(PERSONA.get("voice") or CONFIG.get("voice") or "zh-CN-XiaoyiNeural")
    RATE = str(PERSONA.get("rate") or CONFIG.get("rate") or "+6%")
    PITCH = str(PERSONA.get("pitch") or CONFIG.get("pitch") or "+2Hz")
    AUTO_GREET = bool(CONFIG.get("auto_greet", True))
    VOICE_ENABLED = bool(CONFIG.get("voice_enabled", False))
    TOOLS_ENABLED = bool(CONFIG.get("tools_enabled", True))
    AWARE_ENABLED = bool(CONFIG.get("awareness", True))
    PROACTIVE_ENABLED = bool(CONFIG.get("proactive", True))
    SCREEN_COMMENT = bool(CONFIG.get("screen_comment", True))
    SCREEN_COMMENT_COOLDOWN = float(CONFIG.get("screen_comment_cooldown", 40))
    COMMENT_COOLDOWN = float(CONFIG.get("comment_cooldown_sec", 240))
    PROACTIVE_AFTER = float(CONFIG.get("proactive_after_sec", 18 * 60))

    await broadcast_pet({"type": "settings_changed"})
    return {
        "ok": True,
        "persona": active_persona().get("display_name", "yuki"),
        "voice": current_voice()[0],
    }


@app.post("/api/memory/clear")
async def api_memory_clear():
    try:
        with db() as conn:
            conn.execute("DELETE FROM messages")
        if MEMO_PATH.exists():
            MEMO_PATH.unlink()
        if SUMMARY_CURSOR.exists():
            SUMMARY_CURSOR.unlink()
        await broadcast_pet({"type": "info", "text": "（记忆清空啦，我们重新认识一下吧）"})
        return {"ok": True}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": str(exc)}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = Session(ws)
    PET_SESSIONS.add(session)
    log.info("ws connected")
    await session.send(
        {
            "type": "hello",
            "persona": {
                "name": active_persona().get("name", "nurow"),
                "display_name": active_persona().get("display_name", "Nurow"),
                "greeting": active_persona().get("greeting", ""),
                "mode": active_persona().get("mode", "normal"),
            },
            "settings": {"voice": current_voice()[0], "brain_ready": bool(API_KEY), "asr": ASR.kind},
        }
    )
    if AUTO_GREET and active_persona().get("greeting"):
        async def greet() -> None:
            await asyncio.sleep(0.8)
            await session.speak(active_persona().get("greeting", ""))

        asyncio.create_task(greet())
    try:
        while True:
            msg = await ws.receive_json()
            typ = msg.get("type")
            if typ == "chat":
                text = (msg.get("text") or "").strip()
                if text:
                    await handle_chat(session, text)
            elif typ == "audio_start":
                session.stt_stream = ASR.new_stream()
                session.stt_text = ""
                await session.send({"type": "stt_state", "state": "listening", "ready": ASR.kind != "none"})
            elif typ == "wake_start":
                session.wake_on = True
                session.wake_stream = ASR.new_stream()
                session.capture_active = False
                session.capture_text = ""
                await session.send({"type": "wake", "state": "armed", "ready": ASR.kind != "none"})
            elif typ == "wake_stop":
                session.wake_on = False
                session.wake_stream = None
                session.capture_active = False
                session.capture_text = ""
                await session.send({"type": "wake", "state": "off"})
            elif typ == "audio_in":
                raw = base64.b64decode(msg.get("data") or "")
                rate = int(msg.get("sampleRate") or 16000)
                if rate != 16000 and raw:
                    try:
                        import audioop

                        raw, _ = audioop.ratecv(raw, 2, 1, rate, 16000, None)
                    except Exception:
                        pass
                if msg.get("mode") == "wake":
                    await _wake_feed(session, raw)
                else:
                    if session.stt_stream is None:
                        session.stt_stream = ASR.new_stream()
                    for kind, text in session.stt_stream.feed(raw):
                        if kind == "partial":
                            await session.send({"type": "stt_partial", "text": (session.stt_text + text).strip()})
                        else:
                            session.stt_text = (session.stt_text + text).strip()
                            session.stt_stream = ASR.new_stream()
                            await session.send({"type": "stt_partial", "text": session.stt_text})
            elif typ == "audio_end":
                if session.stt_stream is not None:
                    for kind, text in session.stt_stream.finish():
                        session.stt_text = (session.stt_text + text).strip()
                    session.stt_stream = None
                text = (session.stt_text or "").strip()
                session.stt_text = ""
                await session.send({"type": "stt_final", "text": text})
                if len(text) >= 2:
                    await session.send({"type": "user_text", "text": text})
                    await handle_chat(session, text)
                elif text:
                    await session.send({"type": "info", "text": "（没听清，再说一遍？）"})
                await session.send({"type": "stt_state", "state": "idle"})
            elif typ == "react":
                part = str(msg.get("part") or "body")
                update_state_on_pat()
                now_ts = time.time()
                line = _reaction_line(part)
                if part == "head":
                    if now_ts - session.pat_since > 60:
                        session.pat_count = 0
                        session.pat_since = now_ts
                    session.pat_count += 1
                    if session.pat_count >= 5:
                        line = str(
                            random.choice(
                                [
                                    "呜…再摸下去头发要乱啦！",
                                    "主人是摸头狂魔吗…不过、也没有很讨厌啦。",
                                    "喂喂，都摸五次了哦！…哼，那、再摸一下下也行。",
                                ]
                            )
                        )
                        session.pat_count = 0
                await session.send({"type": "react_text", "text": line, "part": part})
                await session.speak(line)
            elif typ == "mute":
                session.muted = bool(msg.get("value"))
                await session.send({"type": "muted", "value": session.muted})
            elif typ == "ping":
                await session.send({"type": "pong"})
    except WebSocketDisconnect:
        log.info("ws disconnected")
    except Exception as exc:  # pragma: no cover
        log.warning("ws error: %s", exc)
    finally:
        PET_SESSIONS.discard(session)


# 静态资源（最后注册，/api 与 /ws 优先）
if VENDOR.is_dir():
    app.mount("/vendor", StaticFiles(directory=str(VENDOR)), name="vendor")
if MODELS.is_dir():
    app.mount("/models", StaticFiles(directory=str(MODELS)), name="models")
if WEB.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


if __name__ == "__main__":
    log.info(
        "yuki 后端启动 http://127.0.0.1:%s (brain=%s voice=%s asr=%s tools=%s)",
        PORT, bool(API_KEY), VOICE, ASR.kind, TOOLS_ENABLED,
    )
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
