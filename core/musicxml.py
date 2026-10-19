"""
Exporta la melodía como MusicXML (se abre en MuseScore, Finale, Sibelius...).
Cuantiza a semicorcheas, en compás de 4/4, con ligaduras cuando una nota cruza el compás.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

_SHARPS = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
           ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
_FLATS = [("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
          ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]

# (duración en semicorcheas, tipo, con puntillo)
_DURS = [(16, "whole", False), (12, "half", True), (8, "half", False), (6, "quarter", True),
         (4, "quarter", False), (3, "eighth", True), (2, "eighth", False), (1, "16th", False)]
_MEASURE = 16  # 4/4 con 4 divisiones por negra


def key_fifths(root: int, minor: bool) -> int:
    rel = (root + 3) % 12 if minor else root % 12
    f = (rel * 7) % 12
    return f - 12 if f > 6 else f


def _decompose(n: int) -> List[int]:
    out = []
    while n > 0:
        for d, _, _ in _DURS:
            if d <= n:
                out.append(d)
                n -= d
                break
    return out


def _note_xml(midi: Optional[int], dur: int, tie_stop: bool, tie_start: bool, names) -> str:
    _, typ, dot = next(x for x in _DURS if x[0] == dur)
    s = "<note>"
    if midi is None:
        s += "<rest/>"
    else:
        step, alter = names[midi % 12]
        s += f"<pitch><step>{step}</step>"
        if alter:
            s += f"<alter>{alter}</alter>"
        s += f"<octave>{midi // 12 - 1}</octave></pitch>"
    s += f"<duration>{dur}</duration>"
    if midi is not None:
        if tie_stop:
            s += '<tie type="stop"/>'
        if tie_start:
            s += '<tie type="start"/>'
    s += f"<type>{typ}</type>"
    if dot:
        s += "<dot/>"
    if midi is not None and (tie_stop or tie_start):
        s += "<notations>"
        if tie_stop:
            s += '<tied type="stop"/>'
        if tie_start:
            s += '<tied type="start"/>'
        s += "</notations>"
    return s + "</note>"


def notes_to_musicxml(
    notes: List[Dict[str, Any]],
    path: str,
    tempo: float = 120.0,
    time_offset: float = 0.0,
    key: Optional[Tuple[int, bool, float]] = None,
    title: str = "Melodía",
) -> str:
    tempo = float(tempo) if tempo and 30 <= tempo <= 240 else 120.0
    spu = 60.0 / tempo / 4.0  # segundos por semicorchea

    # 1) Notas cuantizadas, sin solaparse
    items: List[Tuple[int, int, int]] = []
    cursor = 0
    for n in sorted(notes, key=lambda x: x["start"]):
        s = max(cursor, int(round((n["start"] - time_offset) / spu)))
        e = max(s + 1, int(round((n["end"] - time_offset) / spu)))
        items.append((s, e, int(n["midi"])))
        cursor = e

    # 2) Eventos: notas y silencios
    events: List[Tuple[Optional[int], int]] = []
    pos = 0
    for s, e, m in items:
        if s > pos:
            events.append((None, s - pos))
        events.append((m, e - s))
        pos = e

    fifths = key_fifths(key[0], key[1]) if key else 0
    names = _FLATS if fifths < 0 else _SHARPS

    # 3) Repartir en compases, partiendo en duraciones escribibles
    measures: List[List[list]] = [[]]
    used = 0
    for m, dur in events:
        event_pieces: List[list] = []
        remaining = dur
        while remaining > 0:
            if used == _MEASURE:
                measures.append([])
                used = 0
            chunk = min(remaining, _MEASURE - used)
            for p in _decompose(chunk):
                piece = [m, p, False, False]
                measures[-1].append(piece)
                event_pieces.append(piece)
            used += chunk
            remaining -= chunk
        if m is not None:
            for k, piece in enumerate(event_pieces):
                piece[2] = k > 0
                piece[3] = k < len(event_pieces) - 1

    if used < _MEASURE:  # completar el último compás con silencio
        for p in _decompose(_MEASURE - used):
            measures[-1].append([None, p, False, False])

    # 4) XML
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="3.1">',
        f"<work><work-title>{escape(title)}</work-title></work>",
        "<identification><encoding><software>Music Helper</software></encoding></identification>",
        '<part-list><score-part id="P1"><part-name>Melodía</part-name></score-part></part-list>',
        '<part id="P1">',
    ]
    for i, meas in enumerate(measures, 1):
        out.append(f'<measure number="{i}">')
        if i == 1:
            out.append(
                f"<attributes><divisions>4</divisions><key><fifths>{fifths}</fifths></key>"
                "<time><beats>4</beats><beat-type>4</beat-type></time>"
                "<clef><sign>G</sign><line>2</line></clef></attributes>"
            )
            bpm = int(round(tempo))
            out.append(
                '<direction placement="above"><direction-type><metronome>'
                f"<beat-unit>quarter</beat-unit><per-minute>{bpm}</per-minute>"
                f'</metronome></direction-type><sound tempo="{bpm}"/></direction>'
            )
        for m, p, stop, start in meas:
            out.append(_note_xml(m, p, stop, start, names))
        out.append("</measure>")
    out.append("</part>")
    out.append("</score-partwise>")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path
