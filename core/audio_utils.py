"""
Utilidades de audio de Music Helper.

Carga de audio, descarga de YouTube, análisis (tempo, tonalidad, acordes),
extracción de melodía a notas, exportación a MIDI y búsqueda de un tarareo
dentro de una canción.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import librosa
from scipy.ndimage import maximum_filter1d, median_filter
from scipy.signal import find_peaks

PLAY_SR = 44100       # frecuencia para reproducir
ANALYSIS_SR = 22050   # frecuencia para analizar (más rápido)

NOTE_NAMES_EN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_NAMES_ES = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]

# Rangos de frecuencia (Hz) según lo que se quiere detectar
RANGES: Dict[str, Tuple[float, float]] = {
    "Voz / melodía": (65.0, 1000.0),
    "Bajo": (35.0, 330.0),
    "Agudo (flauta, silbido)": (200.0, 2200.0),
}
HUM_RANGES: Dict[str, Tuple[float, float]] = {
    "Voz": (70.0, 900.0),
    "Silbido": (400.0, 2600.0),
}


# ----------------------------------------------------------------------
# Nombres de notas y acordes
# ----------------------------------------------------------------------
def midi_to_name(m: int, solfege: bool = True, octave: bool = True) -> str:
    names = NOTE_NAMES_ES if solfege else NOTE_NAMES_EN
    name = names[int(m) % 12]
    return f"{name}{int(m) // 12 - 1}" if octave else name


def chord_name(root: int, minor: bool, solfege: bool = True) -> str:
    names = NOTE_NAMES_ES if solfege else NOTE_NAMES_EN
    return names[root % 12] + ("m" if minor else "")


def key_name(root: int, minor: bool, solfege: bool = True) -> str:
    names = NOTE_NAMES_ES if solfege else NOTE_NAMES_EN
    return f"{names[root % 12]} {'menor' if minor else 'mayor'}"


# ----------------------------------------------------------------------
# FFmpeg (viene incluido con el paquete imageio-ffmpeg: no hay que instalarlo)
# ----------------------------------------------------------------------
_FFMPEG_DIR: Optional[str] = None


def get_ffmpeg_dir() -> Optional[str]:
    """Devuelve la carpeta que contiene ffmpeg(.exe), o None si no hay."""
    global _FFMPEG_DIR
    if _FFMPEG_DIR and os.path.isdir(_FFMPEG_DIR):
        return _FFMPEG_DIR

    exe = None
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
    if not exe or not os.path.exists(exe):
        return None

    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    target_dir = os.path.join(tempfile.gettempdir(), "music_helper_ffmpeg")
    target = os.path.join(target_dir, name)
    try:
        if not os.path.exists(target):
            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(exe, target)
        _FFMPEG_DIR = target_dir
    except Exception:
        _FFMPEG_DIR = os.path.dirname(exe)
    return _FFMPEG_DIR


def _ffmpeg_exe() -> str:
    d = get_ffmpeg_dir()
    if d is None:
        raise RuntimeError(
            "No encontré FFmpeg. Instala las dependencias con "
            "'pip install -r requirements.txt' (incluye imageio-ffmpeg)."
        )
    for candidate in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(d, candidate)
        if os.path.exists(p):
            return p
    # Si imageio-ffmpeg dejó el binario con otro nombre
    for f in os.listdir(d):
        if f.lower().startswith("ffmpeg"):
            return os.path.join(d, f)
    raise RuntimeError("No encontré el ejecutable de FFmpeg.")


def _convert_to_wav(path: str, sr: int = PLAY_SR) -> str:
    out_dir = tempfile.mkdtemp(prefix="music_helper_")
    out = os.path.join(out_dir, "convertido.wav")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run(
        [_ffmpeg_exe(), "-y", "-i", path, "-ac", "1", "-ar", str(sr), out],
        capture_output=True,
        creationflags=flags,
    )
    if r.returncode != 0 or not os.path.exists(out):
        detail = r.stderr.decode(errors="ignore")[-300:]
        raise RuntimeError(f"No pude leer ese archivo de audio.\n{detail}")
    return out


# ----------------------------------------------------------------------
# Carga y descarga
# ----------------------------------------------------------------------
def load_audio(path: str, sr: int = PLAY_SR) -> Tuple[np.ndarray, int]:
    """Carga un audio en mono. Si el formato no es legible (m4a, aac...), lo convierte con FFmpeg."""
    try:
        y, _ = librosa.load(path, sr=sr, mono=True)
        if len(y) == 0:
            raise ValueError("audio vacío")
    except Exception:
        wav = _convert_to_wav(path, sr)
        y, _ = librosa.load(wav, sr=sr, mono=True)
    return np.ascontiguousarray(y, dtype=np.float32), sr


def to_analysis(y_play: np.ndarray) -> np.ndarray:
    """Pasa el audio de reproducción (44.1 kHz) a la frecuencia de análisis (22.05 kHz)."""
    return librosa.resample(y_play, orig_sr=PLAY_SR, target_sr=ANALYSIS_SR).astype(np.float32)


def download_youtube_audio(url: str, output_dir: Optional[str] = None) -> Tuple[str, str]:
    """Descarga el audio de YouTube como MP3. Devuelve (ruta, título)."""
    import yt_dlp

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="music_helper_")

    opts: Dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(title).120s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    ffdir = get_ffmpeg_dir()
    if ffdir:
        opts["ffmpeg_location"] = ffdir

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title") or "audio"
        vid = info.get("id") or ""
        expected = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"

    if os.path.exists(expected):
        return expected, title
    candidates = [p for p in Path(output_dir).glob("*.mp3") if vid and vid in p.name]
    if not candidates:
        candidates = list(Path(output_dir).glob("*.mp3"))
    if not candidates:
        raise RuntimeError("No se pudo descargar el audio de YouTube.")
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(latest), title


def guess_artist_title(name: str) -> Tuple[str, str]:
    """Intenta sacar (artista, título) del nombre de un archivo o video de YouTube."""
    s = Path(name).stem if ("." in name and len(Path(name).suffix) <= 5) else name
    s = re.sub(r"\[[^\]]*\]", "", s)  # [id de YouTube]
    s = re.sub(
        r"[\(\[][^\)\]]*(official|video|audio|lyric|letra|oficial|hd|4k|visualizer|live|en vivo)[^\)\]]*[\)\]]",
        "", s, flags=re.I,
    )
    s = re.sub(r"\s+", " ", s).strip(" -_")
    if " - " in s:
        artist, title = s.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", s


# ----------------------------------------------------------------------
# Análisis de la canción: tempo, tonalidad, acordes
# ----------------------------------------------------------------------
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def key_from_chroma(chroma: np.ndarray) -> Tuple[int, bool, float]:
    """Tonalidad por perfiles de Krumhansl-Schmuckler. Devuelve (nota raíz, es_menor, correlación)."""
    prof = chroma.mean(axis=1)
    best = (-2.0, 0, False)
    if np.std(prof) < 1e-9:
        return 0, False, 0.0
    for i in range(12):
        for minor, tmpl in ((False, _MAJOR), (True, _MINOR)):
            c = float(np.corrcoef(prof, np.roll(tmpl, i))[0, 1])
            if c > best[0]:
                best = (c, i, minor)
    return best[1], best[2], best[0]


def _chord_templates() -> Tuple[np.ndarray, List[Tuple[int, bool]]]:
    temps, meta = [], []
    for root in range(12):
        for minor, intervals in ((False, (0, 4, 7)), (True, (0, 3, 7))):
            t = np.zeros(12)
            for i in intervals:
                t[(root + i) % 12] = 1.0
            temps.append(t / np.linalg.norm(t))
            meta.append((root, minor))
    return np.array(temps), meta


def chords_from_chroma(
    chroma: np.ndarray, sr: int, hop: int, beat_times: np.ndarray, duration: float
) -> List[Dict[str, Any]]:
    """Estima acordes (mayores y menores) agrupando de a dos tiempos."""
    temps, meta = _chord_templates()
    if len(beat_times) >= 4:
        edges = [0.0] + [float(b) for b in beat_times if b > 0.05]
    else:
        edges = [float(x) for x in np.arange(0.0, duration, 1.0)]
    edges = edges[::2]
    edges = [e for e in edges if e < duration] + [duration]

    frames_t = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr, hop_length=hop)
    out: List[Dict[str, Any]] = []
    for a, b in zip(edges[:-1], edges[1:]):
        i0 = int(np.searchsorted(frames_t, a))
        i1 = max(int(np.searchsorted(frames_t, b)), i0 + 1)
        seg = chroma[:, i0:i1]
        if seg.size == 0:
            continue
        v = seg.mean(axis=1)
        nv = float(np.linalg.norm(v))
        if nv < 1e-6:
            continue
        k = int(np.argmax(temps @ (v / nv)))
        root, minor = meta[k]
        if out and out[-1]["root"] == root and out[-1]["minor"] == minor and abs(out[-1]["end"] - a) < 1e-6:
            out[-1]["end"] = float(b)
        else:
            out.append({"start": float(a), "end": float(b), "root": root, "minor": minor})
    return out


def analyze_song(y: np.ndarray, sr: int = ANALYSIS_SR) -> Dict[str, Any]:
    """Tempo, tonalidad y acordes de una canción (audio a 22.05 kHz)."""
    duration = len(y) / sr
    try:
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.atleast_1d(tempo)[0])
        beat_times = librosa.frames_to_time(beats, sr=sr)
    except Exception:
        tempo, beat_times = 120.0, np.array([])

    hop = 1024
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    root, minor, corr = key_from_chroma(chroma)
    chords = chords_from_chroma(chroma, sr, hop, beat_times, duration)
    return {
        "duration": duration,
        "tempo": tempo,
        "key": (root, minor, corr),
        "chords": chords,
        "beats": beat_times,
    }


# ----------------------------------------------------------------------
# Melodía -> notas
# ----------------------------------------------------------------------
def extract_pitch_track(
    y: np.ndarray, sr: int, fmin: float, fmax: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Pitch monofónico con pyin. Devuelve (tiempos, f0 en Hz; NaN donde no hay tono)."""
    hop = 512
    frame_length = 4096 if fmin < 60 else 2048
    f0, _, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=frame_length, hop_length=hop)
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    return times, f0


def pitch_track_to_notes(
    times: np.ndarray,
    f0: np.ndarray,
    min_duration: float = 0.08,
    merge_gap: float = 0.03,
    jump: float = 0.75,
    t_offset: float = 0.0,
    rms: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    """
    Convierte la curva de pitch en notas: {"start", "end", "midi"}.
    Si se da la energía (rms), también separa notas repetidas por las caídas de volumen
    (pyin mira ventanas largas y no ve huecos cortos entre notas iguales).
    """
    f0 = np.asarray(f0, dtype=float)
    if len(times) < 2:
        return []
    hop = float(np.median(np.diff(times)))

    voiced = np.isfinite(f0) & (f0 > 0)
    if rms is not None and len(rms) > 0:
        r = np.asarray(rms, dtype=float)
        r = r[: len(f0)] if len(r) >= len(f0) else np.pad(r, (0, len(f0) - len(r)), mode="edge")
        peak = maximum_filter1d(r, size=17)
        voiced &= ~(r < 0.35 * peak)
    midi = np.full(len(f0), np.nan)
    midi[voiced] = librosa.hz_to_midi(f0[voiced])

    # Suavizado: quita saltos de octava sueltos y pequeñas oscilaciones
    filled = np.where(voiced, midi, 0.0)
    smooth = median_filter(filled, size=5, mode="nearest")
    midi = np.where(voiced & (smooth > 0), smooth, np.nan)

    segs: List[Tuple[int, int, List[float]]] = []
    cur: List[float] = []
    start = 0
    for i, m in enumerate(midi):
        if np.isnan(m):
            if cur:
                segs.append((start, i, cur))
                cur = []
            continue
        if cur and abs(m - float(np.mean(cur))) > jump:
            segs.append((start, i, cur))
            cur = []
        if not cur:
            start = i
        cur.append(float(m))
    if cur:
        segs.append((start, len(midi), cur))

    notes: List[Dict[str, Any]] = []
    for s, e, vals in segs:
        pitch = int(round(float(np.median(vals))))
        if not 21 <= pitch <= 108:
            continue
        t0 = float(times[s])
        t1 = float(times[e - 1]) + hop
        if notes and notes[-1]["midi"] == pitch and t0 - notes[-1]["end"] <= merge_gap:
            notes[-1]["end"] = t1
        else:
            notes.append({"start": t0, "end": t1, "midi": pitch})

    notes = [n for n in notes if n["end"] - n["start"] >= min_duration]
    for n in notes:
        n["start"] += t_offset
        n["end"] += t_offset
    return notes


def analyze_melody(
    y: np.ndarray, sr: int, fmin: float, fmax: float, t_offset: float = 0.0, min_duration: float = 0.08
) -> List[Dict[str, Any]]:
    times, f0 = extract_pitch_track(y, sr, fmin, fmax)
    rms = librosa.feature.rms(y=y, frame_length=512, hop_length=512)[0]
    return pitch_track_to_notes(times, f0, min_duration=min_duration, t_offset=t_offset, rms=rms)


def parsons_code(notes: List[Dict[str, Any]]) -> str:
    """Código Parsons (contorno): U = sube, D = baja, R = repite. Sirve para buscar en Musipedia."""
    code = "*"
    for a, b in zip(notes, notes[1:]):
        d = b["midi"] - a["midi"]
        code += "R" if d == 0 else ("U" if d > 0 else "D")
    return code


# ----------------------------------------------------------------------
# Sonido de las notas detectadas (para comprobar si salieron bien)
# ----------------------------------------------------------------------
def synth_notes(notes: List[Dict[str, Any]], sr: int = ANALYSIS_SR, time_offset: float = 0.0) -> np.ndarray:
    if not notes:
        return np.zeros(1, dtype=np.float32)
    t0 = min(n["start"] for n in notes) if time_offset == 0.0 else time_offset
    total = max(n["end"] for n in notes) - t0 + 0.4
    out = np.zeros(int(total * sr) + 1, dtype=np.float32)
    for n in notes:
        f = float(librosa.midi_to_hz(n["midi"]))
        s = int((n["start"] - t0) * sr)
        length = max(int((n["end"] - n["start"]) * sr), int(0.06 * sr))
        t = np.arange(length) / sr
        dur = length / sr
        env = np.clip(np.minimum(t / 0.01, (dur - t) / 0.03), 0, 1)
        tone = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * env * 0.25
        seg = out[s:s + length]
        seg += tone[: len(seg)].astype(np.float32)
    return out


# ----------------------------------------------------------------------
# Exportar MIDI (con mido, sin depender de pretty_midi)
# ----------------------------------------------------------------------
def notes_to_midi_file(
    notes: List[Dict[str, Any]], output_path: str, tempo: float = 120.0, time_offset: float = 0.0
) -> str:
    import mido

    tempo = float(tempo) if tempo and tempo > 20 else 120.0
    tpb = 480
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0))
    track.append(mido.Message("program_change", program=0, time=0))

    def to_ticks(sec: float) -> int:
        return max(0, int(round((sec - time_offset) * (tempo / 60.0) * tpb)))

    events = []
    for n in notes:
        on = to_ticks(n["start"])
        off = max(to_ticks(n["end"]), on + 1)
        events.append((on, 1, mido.Message("note_on", note=int(n["midi"]), velocity=90)))
        events.append((off, 0, mido.Message("note_off", note=int(n["midi"]), velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))  # los note_off primero en el mismo tick

    last = 0
    for tick, _, msg in events:
        track.append(msg.copy(time=tick - last))
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.save(output_path)
    return output_path


# ----------------------------------------------------------------------
# Buscar un tarareo dentro de una canción
# ----------------------------------------------------------------------
def find_melody_in_song(
    hum_y: np.ndarray, song_y: np.ndarray, sr: int = ANALYSIS_SR, top: int = 3
) -> List[Dict[str, Any]]:
    """
    Busca en qué parte de la canción suena una melodía tarareada.
    Compara el contorno armónico (chroma) con DTW, probando las 12 transposiciones,
    así que funciona aunque tarareaste en otro tono o a otra velocidad.
    """
    hum_y, _ = librosa.effects.trim(hum_y, top_db=30)
    if len(hum_y) < sr * 1.0:
        return []
    hop = 1024
    hc = librosa.feature.chroma_cqt(y=hum_y, sr=sr, hop_length=hop) + 1e-6
    sc = librosa.feature.chroma_cqt(y=song_y, sr=sr, hop_length=hop) + 1e-6
    n = hc.shape[1]
    hum_dur = len(hum_y) / sr

    best_cost = np.full(sc.shape[1], np.inf)
    best_rot = np.zeros(sc.shape[1], dtype=int)
    for k in range(12):
        D = librosa.sequence.dtw(X=np.roll(hc, k, axis=0), Y=sc, metric="cosine", subseq=True, backtrack=False)
        cost = D[-1, :] / n
        better = cost < best_cost
        best_cost[better] = cost[better]
        best_rot[better] = k

    spacing = max(1, int(8.0 * sr / hop))
    peaks, _ = find_peaks(-best_cost, distance=spacing)
    if len(peaks) == 0:
        peaks = np.array([int(np.argmin(best_cost))])
    peaks = sorted(peaks, key=lambda j: best_cost[j])[:top]

    med = float(np.median(best_cost[np.isfinite(best_cost)])) + 1e-9
    results = []
    for j in peaks:
        end = float(j * hop / sr)
        k = int(best_rot[j])
        shift = k if k <= 6 else k - 12  # semitonos que hay que subir el tarareo para encajar
        results.append({
            "start": max(0.0, end - hum_dur),
            "end": end,
            "score": float(max(0.0, 1.0 - best_cost[j] / med)),
            "semitones": shift,
        })
    return results
