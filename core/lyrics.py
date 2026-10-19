"""
Letras: búsqueda online en LRCLIB (gratis, sin clave) y transcripción opcional con Whisper.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

_UA = "MusicHelper/2.0 (aplicacion personal de practica musical)"
_LRC_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text: str) -> List[Tuple[float, str]]:
    """Convierte letra sincronizada [mm:ss.xx] en una lista [(segundos, línea)]."""
    out: List[Tuple[float, str]] = []
    for line in text.splitlines():
        stamps = _LRC_STAMP.findall(line)
        if not stamps:
            continue
        txt = _LRC_STAMP.sub("", line).strip()
        for mm, ss in stamps:
            out.append((int(mm) * 60 + float(ss), txt))
    out.sort(key=lambda x: x[0])
    return out


def search_lrclib(query: str, timeout: int = 15) -> List[Dict[str, Any]]:
    """Busca letras en lrclib.net. Devuelve una lista de resultados."""
    url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = []
    for d in data[:10]:
        plain = d.get("plainLyrics") or ""
        synced = d.get("syncedLyrics") or ""
        if not plain and not synced:
            continue
        results.append({
            "title": d.get("trackName") or "?",
            "artist": d.get("artistName") or "?",
            "album": d.get("albumName") or "",
            "duration": d.get("duration") or 0,
            "plain": plain,
            "synced": synced,
        })
    return results


def transcribe_whisper(path: str, model_size: str = "base") -> Tuple[List[Tuple[float, str]], str]:
    """
    Transcribe la letra con faster-whisper (opcional, solo al ejecutar desde el código).
    La primera vez descarga el modelo. Devuelve ([(segundos, línea)], idioma).
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper no está instalado.\n"
            "Instálalo con:  pip install faster-whisper\n"
            "(Solo funciona ejecutando desde el código; el .exe no lo incluye por tamaño.)"
        )
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(path, vad_filter=True)
    lines = [(float(s.start), s.text.strip()) for s in segments if s.text.strip()]
    return lines, info.language
