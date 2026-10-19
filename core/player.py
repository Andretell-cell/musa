"""
Reproductor de Music Helper.

Usa sounddevice con un stream propio, así que:
- la posición es exacta,
- el loop A-B corta de verdad en B,
- la velocidad cambia SIN alterar el tono (time-stretch),
- se puede transponer el tono SIN alterar la velocidad.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import librosa
import sounddevice as sd

from .audio_utils import PLAY_SR


class AudioPlayer:
    def __init__(self, sr: int = PLAY_SR):
        self.sr = sr
        self._lock = threading.RLock()
        self._orig: Optional[np.ndarray] = None
        self._data: Optional[np.ndarray] = None
        self._cache: Dict[Tuple[float, int], np.ndarray] = {}
        self._speed = 1.0
        self._semitones = 0
        self._pos = 0
        self._playing = False
        self._finished = False
        self._volume = 0.9
        self._loop: Optional[Tuple[float, Optional[float]]] = None  # segundos del audio original
        self._ls = 0
        self._le = 0
        self._loop_on = False
        self._stream: Optional[sd.OutputStream] = None
        self._token = 0
        self.duration = 0.0

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._orig is not None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> float:
        """Posición actual en segundos del audio ORIGINAL (sin importar la velocidad)."""
        with self._lock:
            return min(self._pos / self.sr * self._speed, self.duration)

    @property
    def loop(self) -> Optional[Tuple[float, float]]:
        with self._lock:
            if self._loop is None:
                return None
            s, e = self._loop
            return s, (e if e is not None else self.duration)

    def pop_finished(self) -> bool:
        with self._lock:
            f = self._finished
            self._finished = False
            return f

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------
    def load(self, y: np.ndarray) -> None:
        """Carga audio mono float32 a self.sr."""
        with self._lock:
            self._token += 1
            self._playing = False
            y = np.ascontiguousarray(y, dtype=np.float32)
            peak = float(np.max(np.abs(y))) if len(y) else 0.0
            if peak > 0:
                y = y * (0.95 / peak)
            self._orig = y
            self._data = y
            self._cache = {}
            self._speed = 1.0
            self._semitones = 0
            self._pos = 0
            self._finished = False
            self._loop = None
            self._loop_on = False
            self.duration = len(y) / self.sr
        self._ensure_stream()

    def _ensure_stream(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(
            samplerate=self.sr, channels=1, dtype="float32", callback=self._callback
        )
        self._stream.start()

    def close(self) -> None:
        self._playing = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def play(self) -> None:
        if not self.loaded:
            return
        self._ensure_stream()
        with self._lock:
            n = len(self._data)
            if self._finished or self._pos >= n:
                self._pos = self._ls if self._loop_on else 0
                self._finished = False
            self._playing = True

    def pause(self) -> None:
        self._playing = False

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._pos = 0
            self._finished = False

    def seek(self, seconds: float) -> None:
        with self._lock:
            if not self.loaded:
                return
            seconds = max(0.0, min(float(seconds), self.duration))
            self._pos = min(int(seconds / self._speed * self.sr), len(self._data))
            self._finished = False

    def set_volume(self, vol: float) -> None:
        self._volume = max(0.0, min(1.0, float(vol)))

    # ------------------------------------------------------------------
    # Loop A-B
    # ------------------------------------------------------------------
    def set_loop(self, start: float, end: Optional[float] = None) -> None:
        with self._lock:
            if not self.loaded:
                return
            end_v = self.duration if end is None else float(end)
            if start < 0 or end_v <= start:
                raise ValueError("El fin del loop debe ser mayor que el inicio.")
            if end_v - start < 0.1:
                raise ValueError("El loop debe durar al menos 0.1 segundos.")
            self._loop = (float(start), end_v)
            self._recalc_loop()

    def clear_loop(self) -> None:
        with self._lock:
            self._loop = None
            self._loop_on = False

    def _recalc_loop(self) -> None:
        if self._loop is None or self._data is None:
            self._loop_on = False
            return
        s, e = self._loop
        n = len(self._data)
        self._ls = min(int(s / self._speed * self.sr), n)
        self._le = min(int(e / self._speed * self.sr), n)
        self._loop_on = self._le - self._ls > int(0.05 * self.sr)

    # ------------------------------------------------------------------
    # Velocidad y tono
    # ------------------------------------------------------------------
    def _render(self, speed: float, semitones: int) -> np.ndarray:
        key = (round(float(speed), 3), int(semitones))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            orig = self._orig
        y = orig
        if key[0] != 1.0:
            y = librosa.effects.time_stretch(y, rate=key[0])
        if key[1] != 0:
            y = librosa.effects.pitch_shift(y, sr=self.sr, n_steps=key[1])
        y = np.ascontiguousarray(y, dtype=np.float32)
        with self._lock:
            if len(self._cache) >= 4:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = y
        return y

    def set_effects(
        self,
        speed: float,
        semitones: int,
        on_done: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Cambia velocidad (0.5-1.5) y transposición (semitonos). Se procesa en segundo plano."""
        if not self.loaded:
            return
        speed = max(0.25, min(2.0, float(speed)))
        semitones = int(semitones)
        with self._lock:
            if speed == self._speed and semitones == self._semitones:
                if on_done:
                    on_done()
                return
            self._token += 1
            token = self._token

        def worker():
            try:
                if speed == 1.0 and semitones == 0:
                    data = self._orig
                else:
                    data = self._render(speed, semitones)
            except Exception as exc:
                if on_error:
                    on_error(str(exc) or exc.__class__.__name__)
                return
            with self._lock:
                if token != self._token:
                    return  # llegó otro cambio más nuevo
                t_orig = self._pos / self.sr * self._speed
                self._data = data
                self._speed = speed
                self._semitones = semitones
                self._pos = min(int(t_orig / speed * self.sr), len(data))
                self._recalc_loop()
            if on_done:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Callback de audio (hilo de sounddevice)
    # ------------------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            data = self._data
            if not self._playing or data is None:
                outdata.fill(0)
                return
            n = len(data)
            out = np.zeros(frames, dtype=np.float32)
            filled = 0
            wraps = 0
            while filled < frames:
                end = self._le if self._loop_on else n
                if self._pos >= end:
                    if self._loop_on and wraps < 8:
                        self._pos = self._ls
                        wraps += 1
                        continue
                    self._playing = False
                    self._finished = True
                    break
                take = min(frames - filled, end - self._pos)
                out[filled:filled + take] = data[self._pos:self._pos + take]
                self._pos += take
                filled += take
            outdata[:, 0] = out * self._volume
