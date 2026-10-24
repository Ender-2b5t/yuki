#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音识别（STT）：离线中文流式识别。

优先用 sherpa-onnx 的 streaming zipformer 模型，找不到就退回 Vosk。
模型统一放在 nurow/models/asr/ 下面，程序自动发现。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("nurow.asr")

BASE = Path(__file__).resolve().parent.parent
ASR_DIR = BASE / "models" / "asr"
SAMPLE_RATE = 16000


def _find_sherpa_model():
    """返回 (目录, encoder, decoder, joiner) 或 None。优先非 int8（更准）。"""
    for d in sorted(ASR_DIR.glob("*")):
        if not d.is_dir() or not (d / "tokens.txt").exists():
            continue
        pick = lambda pat: sorted(d.glob(pat), key=lambda p: ("int8" in p.name, p.name))
        enc = pick("encoder*.onnx")
        dec = pick("decoder*.onnx")
        joi = pick("joiner*.onnx")
        if enc and dec and joi:
            return d, enc[0], dec[0], joi[0]
    return None


def _find_vosk_model():
    for d in sorted(ASR_DIR.glob("*")):
        if d.is_dir() and (d / "am").is_dir() and (d / "conf").is_dir():
            return d
    return None


class ASREngine:
    """全局共享的识别器（模型只加载一次）。"""

    def __init__(self) -> None:
        self.kind = "none"
        self.recognizer = None
        self.vosk_model = None
        self.model_name = ""
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        sherpa = _find_sherpa_model()
        if sherpa is not None:
            try:
                import sherpa_onnx  # noqa: F401

                d, enc, dec, joi = sherpa
                self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=str(d / "tokens.txt"),
                    encoder=str(enc),
                    decoder=str(dec),
                    joiner=str(joi),
                    num_threads=4,
                    sample_rate=SAMPLE_RATE,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    enable_endpoint_detection=True,
                )
                self.kind = "sherpa"
                self.model_name = d.name
                log.info("ASR 就绪：sherpa-onnx / %s", d.name)
                return
            except Exception as exc:  # pragma: no cover
                log.warning("sherpa 加载失败：%s", exc)

        vd = _find_vosk_model()
        if vd is not None:
            try:
                from vosk import Model

                self.vosk_model = Model(str(vd), log_level=-1)
                self.kind = "vosk"
                self.model_name = vd.name
                log.info("ASR 就绪：vosk / %s", vd.name)
                return
            except Exception as exc:  # pragma: no cover
                log.warning("vosk 加载失败：%s", exc)

        log.warning("ASR 未就绪：%s 下没有可用模型", ASR_DIR)

    # --------------------------------------------------------------- stream
    def new_stream(self) -> "ASRStream":
        return ASRStream(self)


class ASRStream:
    def __init__(self, engine: ASREngine) -> None:
        self.engine = engine
        self.last_partial = ""
        self.stream = None
        self.rec = None
        if engine.kind == "sherpa":
            self.stream = engine.recognizer.create_stream()
        elif engine.kind == "vosk":
            from vosk import KaldiRecognizer

            self.rec = KaldiRecognizer(engine.vosk_model, SAMPLE_RATE)

    @property
    def ready(self) -> bool:
        return self.engine.kind != "none"

    def feed(self, pcm: bytes) -> list[tuple[str, str]]:
        """喂 16k/16bit/mono PCM，返回 [("partial"|"final", 文本)]。"""
        events: list[tuple[str, str]] = []
        if not pcm:
            return events
        if self.engine.kind == "sherpa":
            import numpy as np

            samples = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
            self.stream.accept_waveform(SAMPLE_RATE, samples)
            while self.engine.recognizer.is_ready(self.stream):
                self.engine.recognizer.decode_stream(self.stream)
            text = (self.engine.recognizer.get_result(self.stream) or "").strip()
            if text and text != self.last_partial:
                self.last_partial = text
                events.append(("partial", text))
            if self.engine.recognizer.is_endpoint(self.stream):
                self.engine.recognizer.reset(self.stream)
                if text:
                    events.append(("final", text))
                self.last_partial = ""
        elif self.engine.kind == "vosk":
            if self.rec.AcceptWaveform(pcm):
                text = json.loads(self.rec.Result()).get("text", "").replace(" ", "")
                if text:
                    events.append(("final", text))
                self.last_partial = ""
            else:
                text = json.loads(self.rec.PartialResult()).get("partial", "").replace(" ", "")
                if text and text != self.last_partial:
                    self.last_partial = text
                    events.append(("partial", text))
        return events

    def finish(self) -> list[tuple[str, str]]:
        """说完了：强制收尾，吐出 final。"""
        events: list[tuple[str, str]] = []
        if self.engine.kind == "sherpa":
            try:
                import numpy as np

                self.stream.accept_waveform(
                    SAMPLE_RATE, np.zeros(int(SAMPLE_RATE * 0.7), dtype="float32")
                )
                while self.engine.recognizer.is_ready(self.stream):
                    self.engine.recognizer.decode_stream(self.stream)
                text = (self.engine.recognizer.get_result(self.stream) or "").strip()
                if text:
                    events.append(("final", text))
                self.engine.recognizer.reset(self.stream)
            except Exception as exc:  # pragma: no cover
                log.debug("sherpa finish 出错：%s", exc)
            self.last_partial = ""
        elif self.engine.kind == "vosk":
            try:
                text = json.loads(self.rec.FinalResult()).get("text", "").replace(" ", "")
                if text:
                    events.append(("final", text))
            except Exception as exc:  # pragma: no cover
                log.debug("vosk finish 出错：%s", exc)
            self.last_partial = ""
        return events
