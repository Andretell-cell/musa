"""
Guitarra: posiciones de acordes (mayores y menores) y tablatura de una melodía.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Afinación estándar, de la cuerda 6 (grave) a la 1 (aguda): E2 A2 D3 G3 B3 E4
TUNING = [40, 45, 50, 55, 59, 64]
STRING_LABELS = ["E", "A", "D", "G", "B", "e"]
MAX_FRET = 15

# Acordes abiertos: trastes de la cuerda 6 a la 1. -1 = no se toca, 0 = al aire.
_OPEN: Dict[Tuple[int, bool], List[int]] = {
    (0, False): [-1, 3, 2, 0, 1, 0],    # Do
    (2, False): [-1, -1, 0, 2, 3, 2],   # Re
    (4, False): [0, 2, 2, 1, 0, 0],     # Mi
    (7, False): [3, 2, 0, 0, 0, 3],     # Sol
    (9, False): [-1, 0, 2, 2, 2, 0],    # La
    (9, True): [-1, 0, 2, 2, 1, 0],     # Lam
    (2, True): [-1, -1, 0, 2, 3, 1],    # Rem
    (4, True): [0, 2, 2, 0, 0, 0],      # Mim
}


def chord_frets(root: int, minor: bool) -> List[int]:
    """Posición de guitarra para un acorde mayor o menor. Lista de 6 trastes (cuerda 6 -> 1)."""
    key = (root % 12, bool(minor))
    if key in _OPEN:
        return list(_OPEN[key])

    candidates = []
    f6 = (root - 4) % 12  # traste de la raíz en la cuerda 6
    if f6 == 0:
        f6 = 12
    e_shape = [f6, f6 + 2, f6 + 2, f6, f6, f6] if minor else [f6, f6 + 2, f6 + 2, f6 + 1, f6, f6]
    candidates.append(e_shape)

    f5 = (root - 9) % 12  # traste de la raíz en la cuerda 5
    if f5 == 0:
        f5 = 12
    a_shape = [-1, f5, f5 + 2, f5 + 2, f5, f5] if minor else [-1, f5, f5 + 2, f5 + 2, f5 + 2, f5]
    if minor:
        a_shape = [-1, f5, f5 + 2, f5 + 2, f5 + 1, f5]
    candidates.append(a_shape)

    return min(candidates, key=lambda c: max(c))


def fingering_text(frets: List[int]) -> str:
    """Por ejemplo x-3-2-0-1-0 (de la cuerda grave a la aguda)."""
    return "-".join("x" if f < 0 else str(f) for f in frets)


# ----------------------------------------------------------------------
# Tablatura
# ----------------------------------------------------------------------
def _fit_octave(m: int) -> int:
    top = TUNING[-1] + MAX_FRET
    while m < TUNING[0]:
        m += 12
    while m > top:
        m -= 12
    return m


def _positions(m: int) -> List[Tuple[int, int]]:
    return [(s, m - open_) for s, open_ in enumerate(TUNING) if 0 <= m - open_ <= MAX_FRET]


def choose_positions(pitches: List[int]) -> List[Tuple[int, int]]:
    """Elige (cuerda, traste) para cada nota minimizando los saltos de mano (Viterbi)."""
    cands = [_positions(p) for p in pitches]
    if not cands:
        return []

    def trans(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return abs(max(a[1], 1) - max(b[1], 1)) + 0.4 * abs(a[0] - b[0])

    costs = [{c: 0.08 * c[1] for c in cands[0]}]
    backs: List[Dict[Tuple[int, int], Tuple[int, int]]] = [{}]
    for i in range(1, len(cands)):
        cur: Dict[Tuple[int, int], float] = {}
        back: Dict[Tuple[int, int], Tuple[int, int]] = {}
        for c in cands[i]:
            best_prev, best_cost = None, float("inf")
            for p, pc in costs[i - 1].items():
                v = pc + trans(p, c)
                if v < best_cost:
                    best_prev, best_cost = p, v
            cur[c] = best_cost + 0.08 * c[1]
            back[c] = best_prev
        costs.append(cur)
        backs.append(back)

    last = min(costs[-1], key=lambda k: costs[-1][k])
    path = [last]
    for i in range(len(cands) - 1, 0, -1):
        last = backs[i][last]
        path.append(last)
    path.reverse()
    return path


def notes_to_tab(notes: List[Dict[str, Any]], per_line: int = 12) -> str:
    """Tablatura ASCII de la melodía (afinación estándar)."""
    if not notes:
        return "No hay notas para la tablatura."
    raw = [int(n["midi"]) for n in notes]
    pitches = [_fit_octave(m) for m in raw]
    moved = sum(1 for a, b in zip(raw, pitches) if a != b)
    path = choose_positions(pitches)

    lines = [
        "TABLATURA DE LA MELODÍA (guitarra, afinación estándar E A D G B e)",
        "Los números son los trastes (0 = cuerda al aire). Muestra el orden de las notas, no el ritmo.",
    ]
    if moved:
        lines.append(f"{moved} nota(s) se movieron de octava para caber en la guitarra.")
    lines.append("")

    for block in range(0, len(path), per_line):
        chunk = path[block:block + per_line]
        rows = {s: "" for s in range(6)}
        for s_play, fret in chunk:
            cell = (str(fret) + "---")[:4]
            for s in range(6):
                rows[s] += cell if s == s_play else "-" * 4
        for s in range(5, -1, -1):  # primero la cuerda aguda (e)
            lines.append(f"{STRING_LABELS[s]}|{rows[s]}|")
        lines.append("")
    return "\n".join(lines)
