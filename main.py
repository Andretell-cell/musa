#!/usr/bin/env python3
"""
Music Helper - app de escritorio para aprender canciones.

- Abre MP3/WAV/FLAC/M4A o descarga el audio de YouTube
- Reproductor con loop A-B, velocidad sin cambiar el tono y transposición
- Tempo, tonalidad y acordes (el acorde actual se ve mientras suena)
- Melodía a notas con piano-roll, exportar a MIDI
- Letras online (sincronizadas, tipo karaoke) o con Whisper
- Tarareo / silbido: notas, código Parsons y búsqueda dentro de la canción
"""
import bisect
import os
import queue
import sys
import tempfile
import textwrap
import threading
import traceback
import webbrowser
from pathlib import Path

if sys.stdout is None:  # app sin consola (.exe con --windowed)
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, str(Path(__file__).parent))

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import audio_utils as au
from core import guitar
from core import musicxml as mxl
from core import lyrics as lyr
from core.player import AudioPlayer

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

LOG_PATH = Path.home() / "MusicHelper_errores.log"
SPEEDS = ["0.5x", "0.6x", "0.7x", "0.75x", "0.8x", "0.9x", "1.0x", "1.1x", "1.25x", "1.5x"]
SEMIS = [f"{i:+d}" if i else "0" for i in range(-6, 7)]


def draw_chord(canvas, x, y, frets, title, color="#4aa3ff", text_color="#ffffff"):
    """Dibuja un diagrama de acorde de guitarra (cuerda 6 a la izquierda)."""
    sp, fh = 14, 15
    gx, gy = x + 18, y + 40
    played = [f for f in frets if f > 0]
    base = 1 if (not played or max(played) <= 5) else min(played)
    canvas.create_text(x + 53, y + 9, text=title, fill=text_color, font=("Segoe UI", 11, "bold"))
    for st in range(6):
        canvas.create_line(gx + st * sp, gy, gx + st * sp, gy + 5 * fh, fill="#8a8a8a")
    for fr in range(6):
        w = 3 if (fr == 0 and base == 1) else 1
        canvas.create_line(gx, gy + fr * fh, gx + 5 * sp, gy + fr * fh, fill="#8a8a8a", width=w)
    if base > 1:
        canvas.create_text(x + 6, gy + fh / 2, text=str(base), fill="#aaaaaa", font=("Segoe UI", 8))
    for st, f in enumerate(frets):
        cx = gx + st * sp
        if f < 0:
            canvas.create_text(cx, gy - 9, text="x", fill="#aaaaaa", font=("Segoe UI", 9))
        elif f == 0:
            canvas.create_oval(cx - 4, gy - 13, cx + 4, gy - 5, outline="#aaaaaa")
    if played:
        m = min(played)
        idx = [st for st, f in enumerate(frets) if f == m]
        if len(idx) >= 2 and idx[-1] - idx[0] >= 2 and all(f >= m for f in frets[idx[0]:idx[-1] + 1]):
            cy = gy + (m - base) * fh + fh / 2
            canvas.create_line(gx + idx[0] * sp, cy, gx + idx[-1] * sp, cy, fill=color, width=9,
                               capstyle="round")
    for st, f in enumerate(frets):
        if f > 0:
            cx = gx + st * sp
            cy = gy + (f - base) * fh + fh / 2
            canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=color, outline="")


def fmt_time(s: float) -> str:
    s = max(0.0, float(s))
    return f"{int(s // 60)}:{int(s % 60):02d}"


class MusicHelperApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Music Helper - Aprende canciones")
        h = min(880, max(620, self.winfo_screenheight() - 90))
        self.geometry(f"1040x{h}")
        self.minsize(860, 520)

        self.player = AudioPlayer()
        self.temp_dir = tempfile.mkdtemp(prefix="music_helper_")

        self.current_path = None
        self.y_play = None
        self.y_ana = None
        self.duration = 0.0
        self.harmony = None
        self.chords = []
        self.chord_starts = []
        self.notes = []
        self.notes_range = (0.0, 0.0)
        self.lyric_lines = []
        self.lyric_times = []
        self.lyric_results = []
        self.hum_y = None
        self.hum_notes = []
        self.hum_matches = []
        self.id_results = []
        self._pending_seek = None
        self.solfege = True

        self._q = queue.Queue()
        self._load_id = 0
        self._dragging = False
        self._rec_buf = None
        self._playhead = None
        self._last_chord = None
        self._last_chord_i = None
        self._last_lyric = None

        self._build_ui()
        self._refresh_lib_label()
        self.bind("<space>", self._on_space)
        self.bind("<Left>", lambda e: self._nudge(-5))
        self.bind("<Right>", lambda e: self._nudge(5))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._tick)

    # ==================================================================
    # Utilidades internas
    # ==================================================================
    def _log(self, text: str):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        detail = "".join(traceback.format_exception(exc, val, tb))
        self._log(detail)
        messagebox.showerror("Error inesperado", f"{val}\n\nDetalle guardado en:\n{LOG_PATH}")

    def _post(self, fn):
        """Pone una función en la cola para ejecutarla en el hilo de la interfaz."""
        self._q.put(fn)

    def _bg(self, work, done, msg=None, on_error=None):
        """Ejecuta work() en segundo plano y luego done(resultado) en la interfaz."""
        if msg:
            self.status.configure(text=msg)

        def runner():
            try:
                res = work()
            except Exception as exc:
                err = str(exc) or exc.__class__.__name__
                tb = traceback.format_exc()
                self._post(lambda: self._bg_error(err, tb, on_error))
                return
            self._post(lambda: done(res))

        threading.Thread(target=runner, daemon=True).start()

    def _bg_error(self, err, tb, on_error):
        self._log(tb)
        self.status.configure(text="Hubo un error")
        if on_error:
            on_error(err)
        else:
            messagebox.showerror("Error", err)

    def _tick(self):
        while True:
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                self._log(traceback.format_exc())
        try:
            self._update_playback_ui()
        except Exception:
            self._log(traceback.format_exc())
        self.after(80, self._tick)

    def _on_close(self):
        try:
            self.player.close()
        except Exception:
            pass
        self._stop_sound()
        self.destroy()

    def _typing(self) -> bool:
        w = self.focus_get()
        return w is not None and w.winfo_class() in ("Entry", "Text", "TEntry")

    def _on_space(self, event):
        if not self._typing():
            self.toggle_play()

    def _nudge(self, delta):
        if not self._typing() and self.player.loaded:
            self.player.seek(self.player.position + delta)

    # ==================================================================
    # Construcción de la interfaz
    # ==================================================================
    def _build_ui(self):
        self.status = ctk.CTkLabel(self, text="Listo", anchor="w", text_color="gray")
        self.status.pack(side="bottom", fill="x", padx=16, pady=(0, 8))
        self.main = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.main.pack(fill="both", expand=True)

        top = ctk.CTkFrame(self.main)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Music Helper", font=ctk.CTkFont(size=20, weight="bold")).pack(
            side="left", padx=10, pady=8)
        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right", padx=8, pady=6)
        ctk.CTkButton(right, text="Abrir audio", width=110, command=self.open_file).pack(side="left", padx=4)
        ctk.CTkButton(right, text="YouTube", width=100, command=self.open_youtube).pack(side="left", padx=4)
        self.notation = ctk.CTkSegmentedButton(right, values=["Do Re Mi", "C D E"], command=self._on_notation)
        self.notation.set("Do Re Mi")
        self.notation.pack(side="left", padx=(14, 4))

        info = ctk.CTkFrame(self.main)
        info.pack(fill="x", padx=12, pady=6)
        self.lbl_title = ctk.CTkLabel(info, text="Ningún archivo cargado", font=ctk.CTkFont(size=14))
        self.lbl_title.pack(anchor="w", padx=12, pady=(8, 0))
        self.lbl_info = ctk.CTkLabel(info, text="Duración: — | Tempo: — | Tonalidad: —", text_color="gray")
        self.lbl_info.pack(anchor="w", padx=12, pady=(0, 4))
        row = ctk.CTkFrame(info, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(row, text="Acorde:", text_color="gray").pack(side="left")
        self.lbl_chord = ctk.CTkLabel(row, text="—", width=90, anchor="w",
                                      font=ctk.CTkFont(size=26, weight="bold"))
        self.lbl_chord.pack(side="left", padx=(6, 8))
        self.chord_canvas = tk.Canvas(row, width=215, height=118, bg="#2b2b2b", highlightthickness=0)
        self.chord_canvas.pack(side="left", padx=(0, 14))
        self.lbl_lyric = ctk.CTkLabel(row, text="", anchor="w", justify="left", font=ctk.CTkFont(size=14))
        self.lbl_lyric.pack(side="left", fill="x", expand=True)

        # --- Reproductor ---
        pf = ctk.CTkFrame(self.main)
        pf.pack(fill="x", padx=12, pady=6)
        seek_row = ctk.CTkFrame(pf, fg_color="transparent")
        seek_row.pack(fill="x", padx=12, pady=(10, 2))
        self.seek = ctk.CTkSlider(seek_row, from_=0, to=1, command=self._on_seek)
        self.seek.set(0)
        self.seek.pack(side="left", fill="x", expand=True)
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "_dragging", True))
        self.seek.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging", False))
        self.lbl_time = ctk.CTkLabel(seek_row, text="0:00 / 0:00", width=100)
        self.lbl_time.pack(side="left", padx=(10, 0))

        ctrl = ctk.CTkFrame(pf, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=4)
        self.btn_play = ctk.CTkButton(ctrl, text="Play", width=80, command=self.toggle_play)
        self.btn_play.pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="Stop", width=70, command=self.stop_playback).pack(side="left", padx=4)
        ctk.CTkLabel(ctrl, text="Velocidad:").pack(side="left", padx=(16, 4))
        self.speed_var = ctk.StringVar(value="1.0x")
        ctk.CTkOptionMenu(ctrl, values=SPEEDS, variable=self.speed_var, width=80,
                          command=lambda v: self._apply_effects()).pack(side="left", padx=4)
        ctk.CTkLabel(ctrl, text="Tono (semitonos):").pack(side="left", padx=(16, 4))
        self.semi_var = ctk.StringVar(value="0")
        ctk.CTkOptionMenu(ctrl, values=SEMIS, variable=self.semi_var, width=70,
                          command=lambda v: self._apply_effects()).pack(side="left", padx=4)
        ctk.CTkLabel(ctrl, text="La velocidad no cambia el tono", text_color="gray").pack(side="left", padx=12)

        lp = ctk.CTkFrame(pf, fg_color="transparent")
        lp.pack(fill="x", padx=12, pady=(4, 10))
        ctk.CTkLabel(lp, text="Loop (seg):").pack(side="left", padx=4)
        ctk.CTkButton(lp, text="A aquí", width=70, command=self.mark_a).pack(side="left", padx=2)
        self.loop_start = ctk.CTkEntry(lp, width=70, placeholder_text="inicio")
        self.loop_start.pack(side="left", padx=4)
        ctk.CTkButton(lp, text="B aquí", width=70, command=self.mark_b).pack(side="left", padx=2)
        self.loop_end = ctk.CTkEntry(lp, width=70, placeholder_text="fin")
        self.loop_end.pack(side="left", padx=4)
        ctk.CTkButton(lp, text="Aplicar", width=70, command=self.apply_loop).pack(side="left", padx=6)
        ctk.CTkButton(lp, text="Quitar loop", width=90, command=self.clear_loop).pack(side="left", padx=4)
        ctk.CTkLabel(lp, text="Espacio = play/pausa, flechas = ±5 s", text_color="gray").pack(side="right", padx=6)

        # --- Pestañas ---
        self.tabs = ctk.CTkTabview(self.main, height=470)
        self.tabs.pack(fill="x", padx=12, pady=8)
        self.tab_notes = self.tabs.add("Notas / MIDI")
        self.tab_chords = self.tabs.add("Acordes")
        self.tab_tab = self.tabs.add("Tablatura")
        self.tab_lyrics = self.tabs.add("Letras")
        self.tab_hum = self.tabs.add("Tarareo / Silbido")
        self.tab_help = self.tabs.add("Ayuda")
        self._build_notes_tab()
        self._build_chords_tab()
        self._build_tab_tab()
        self._build_lyrics_tab()
        self._build_hum_tab()
        self._build_help_tab()

    def _build_notes_tab(self):
        t = ctk.CTkFrame(self.tab_notes, fg_color="transparent")
        t.pack(fill="x", padx=6, pady=(6, 2))
        ctk.CTkLabel(t, text="Instrumento:").pack(side="left", padx=(2, 4))
        self.range_var = ctk.StringVar(value="Voz / melodía")
        ctk.CTkOptionMenu(t, values=list(au.RANGES.keys()), variable=self.range_var, width=170).pack(
            side="left", padx=4)
        ctk.CTkButton(t, text="Analizar canción completa", command=lambda: self.run_melody(False)).pack(
            side="left", padx=4)
        ctk.CTkButton(t, text="Analizar solo el loop A-B", command=lambda: self.run_melody(True)).pack(
            side="left", padx=4)
        t2 = ctk.CTkFrame(self.tab_notes, fg_color="transparent")
        t2.pack(fill="x", padx=6, pady=(2, 4))
        ctk.CTkButton(t2, text="Escuchar notas", width=120, command=self.listen_notes).pack(side="left", padx=4)
        ctk.CTkButton(t2, text="Parar sonido", width=100, command=self._stop_sound).pack(side="left", padx=4)
        ctk.CTkButton(t2, text="Exportar MIDI", width=110, command=self.export_midi).pack(side="left", padx=4)
        ctk.CTkButton(t2, text="Exportar MusicXML", width=140, command=self.export_musicxml).pack(
            side="left", padx=4)
        ctk.CTkButton(t2, text="Copiar notas", width=100, command=self.copy_notes).pack(side="left", padx=4)

        self.roll = tk.Canvas(self.tab_notes, height=170, bg="#1e1e1e", highlightthickness=0)
        self.roll.pack(fill="x", padx=8, pady=(2, 4))
        self.roll.bind("<Configure>", lambda e: self._draw_roll())
        self.roll.bind("<Button-1>", self._on_roll_click)

        self.notes_text = ctk.CTkTextbox(self.tab_notes, font=ctk.CTkFont(family="Consolas", size=13))
        self.notes_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.notes_text.insert(
            "1.0",
            "Carga una canción y pulsa «Analizar».\n\n"
            "Consejo: analiza solo un tramo (marca A y B y usa «Analizar solo el loop»).\n"
            "Es mucho más rápido y más preciso que la canción completa.\n"
            "La detección sigue una sola línea melódica: funciona mejor con voz o un solo instrumento.")

    def _build_chords_tab(self):
        ctk.CTkLabel(
            self.tab_chords,
            text="Acordes estimados (mayores y menores). Son una guía, no una verdad absoluta.\n"
                 "Diagramas de guitarra de los más usados (cuerda grave a la izquierda; x = no se toca, o = al aire).",
            text_color="gray", justify="left").pack(anchor="w", padx=10, pady=(8, 2))
        self.chord_legend = tk.Canvas(self.tab_chords, height=120, bg="#2b2b2b", highlightthickness=0)
        self.chord_legend.pack(fill="x", padx=8, pady=(2, 4))
        self.chords_text = ctk.CTkTextbox(self.tab_chords, font=ctk.CTkFont(family="Consolas", size=14))
        self.chords_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.chords_text.insert("1.0", "Los acordes aparecen aquí al cargar una canción.")

    def _build_tab_tab(self):
        r = ctk.CTkFrame(self.tab_tab, fg_color="transparent")
        r.pack(fill="x", padx=6, pady=(8, 4))
        ctk.CTkButton(r, text="Copiar tablatura", width=130, command=self.copy_tab).pack(side="left", padx=4)
        ctk.CTkLabel(r, text="Se genera sola al analizar la melodía en «Notas / MIDI».",
                     text_color="gray").pack(side="left", padx=10)
        self.tab_box = ctk.CTkTextbox(self.tab_tab, font=ctk.CTkFont(family="Consolas", size=14), wrap="none")
        self.tab_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tab_box.insert("1.0", "Analiza la melodía en «Notas / MIDI» y aquí aparece la tablatura para guitarra.")

    def _build_lyrics_tab(self):
        s = ctk.CTkFrame(self.tab_lyrics, fg_color="transparent")
        s.pack(fill="x", padx=6, pady=(8, 4))
        ctk.CTkLabel(s, text="Buscar:").pack(side="left", padx=(2, 4))
        self.lyric_query = ctk.CTkEntry(s, width=330, placeholder_text="artista y título")
        self.lyric_query.pack(side="left", padx=4)
        ctk.CTkButton(s, text="Buscar letra online", width=140, command=self.search_lyrics).pack(
            side="left", padx=4)
        self.lyric_pick = ctk.CTkOptionMenu(s, values=["(sin resultados)"], width=300,
                                            command=self._on_pick_lyric)
        self.lyric_pick.pack(side="left", padx=4)

        w = ctk.CTkFrame(self.tab_lyrics, fg_color="transparent")
        w.pack(fill="x", padx=6, pady=(0, 4))
        ctk.CTkLabel(w, text="Si no la encuentras, transcribir con IA (Whisper):").pack(side="left", padx=(2, 6))
        self.whisper_size = ctk.StringVar(value="base")
        ctk.CTkOptionMenu(w, values=["tiny", "base", "small"], variable=self.whisper_size, width=80).pack(
            side="left", padx=4)
        ctk.CTkButton(w, text="Transcribir", width=100, command=self.run_whisper).pack(side="left", padx=4)

        self.lyrics_text = ctk.CTkTextbox(self.tab_lyrics, font=ctk.CTkFont(size=14))
        self.lyrics_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.lyrics_text.insert(
            "1.0",
            "Busca la letra arriba, o pega aquí la tuya.\n\n"
            "Si la letra viene sincronizada, la línea actual se muestra arriba, junto al acorde, mientras suena.")

    def _build_hum_tab(self):
        ctk.CTkLabel(
            self.tab_hum, justify="left",
            text=("1) Graba tu tarareo o silbido (o carga un archivo).   2) Pulsa «¿Qué canción es?».\n"
                  "La app compara con TUS canciones: cada canción que abres o agregas a la biblioteca queda indexada.\n"
                  "Para una canción que no tienes, usa el código Parsons en Musipedia o la búsqueda por tarareo de Google.")
        ).pack(anchor="w", padx=10, pady=(8, 4))

        r = ctk.CTkFrame(self.tab_hum, fg_color="transparent")
        r.pack(fill="x", padx=6, pady=4)
        self.hum_mode = ctk.CTkSegmentedButton(r, values=["Voz", "Silbido"])
        self.hum_mode.set("Voz")
        self.hum_mode.pack(side="left", padx=4)
        self.hum_dur = ctk.StringVar(value="8 s")
        ctk.CTkOptionMenu(r, values=["8 s", "15 s", "30 s"], variable=self.hum_dur, width=80).pack(
            side="left", padx=4)
        self.btn_rec = ctk.CTkButton(r, text="Grabar", width=90, command=self.record_hum)
        self.btn_rec.pack(side="left", padx=4)
        ctk.CTkButton(r, text="Analizar archivo...", width=140, command=self.analyze_hum_file).pack(
            side="left", padx=4)

        r2 = ctk.CTkFrame(self.tab_hum, fg_color="transparent")
        r2.pack(fill="x", padx=6, pady=4)
        ctk.CTkButton(r2, text="Escuchar notas", width=120, command=self.listen_hum).pack(side="left", padx=4)
        ctk.CTkButton(r2, text="Parar sonido", width=100, command=self._stop_sound).pack(side="left", padx=4)
        ctk.CTkButton(r2, text="Buscar en la canción cargada", width=190, command=self.find_in_song).pack(
            side="left", padx=4)
        ctk.CTkButton(r2, text="Ir al mejor resultado", width=150, command=self.goto_best_match).pack(
            side="left", padx=4)
        ctk.CTkButton(r2, text="Código Parsons + Musipedia", width=200, command=self.open_musipedia).pack(
            side="left", padx=4)

        r3 = ctk.CTkFrame(self.tab_hum, fg_color="transparent")
        r3.pack(fill="x", padx=6, pady=4)
        ctk.CTkButton(r3, text="¿Qué canción es?", width=150, command=self.identify_in_library).pack(
            side="left", padx=4)
        ctk.CTkButton(r3, text="Abrir mejor resultado", width=160, command=self.open_best_id).pack(
            side="left", padx=4)
        ctk.CTkButton(r3, text="Agregar carpeta de música", width=190, command=self.add_folder_to_library).pack(
            side="left", padx=4)
        self.lbl_lib = ctk.CTkLabel(r3, text="Biblioteca: 0 canciones", text_color="gray")
        self.lbl_lib.pack(side="left", padx=10)

        self.hum_text = ctk.CTkTextbox(self.tab_hum, font=ctk.CTkFont(family="Consolas", size=13))
        self.hum_text.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _build_help_tab(self):
        text = """MUSIC HELPER - Guía rápida

1. Cargar una canción
   «Abrir audio»: MP3, WAV, FLAC, OGG, M4A...
   «YouTube»: pega el enlace del video (baja solo el audio).

2. Practicar
   Play / Pausa (también con la barra espaciadora). Arrastra la barra para saltar.
   Velocidad: de 0.5x a 1.5x SIN cambiar el tono (la primera vez tarda unos segundos).
   Tono: sube o baja la canción en semitonos para cantarla en tu rango.
   Loop A-B: reproduce el punto donde estás con «A aquí» y «B aquí» y se repite esa parte.

3. Tonalidad, tempo y acordes
   Se calculan solos al cargar la canción. El acorde actual se ve arriba mientras suena.

4. Notas y MIDI
   Elige el tipo de instrumento y pulsa «Analizar». Mejor con un tramo corto (loop A-B).
   «Escuchar notas» reproduce lo detectado para que compruebes si salió bien.
   «Exportar MIDI» lo abre en MuseScore, Guitar Pro, etc.

5. Guitarra: acordes, tablatura y partitura
   Junto al acorde grande verás el diagrama de dedos (ahora y el siguiente).
   En «Acordes» están los diagramas de los más usados y la digitación de cada uno.
   En «Tablatura» sale la melodía para guitarra (se genera al analizar las notas).
   «Exportar MusicXML» crea la partitura para abrirla en MuseScore.

6. Letras
   Busca por artista y título (LRCLIB, gratis). Si trae tiempos, funciona como karaoke.
   Whisper (opcional) transcribe del audio; solo disponible al ejecutar desde el código.

7. Tarareo / silbido
   Graba (8, 15 o 30 s) o carga un archivo. Obtienes las notas y el código Parsons.
   «Buscar en la canción cargada» te dice en qué parte suena tu melodía, aunque la
   hayas tarareado en otro tono o a otra velocidad.

Límites
- La transcripción sigue una sola línea melódica; con mezclas completas es aproximada.
- Los acordes son solo mayores y menores. Son una guía para empezar a sacar la canción.
- Identificar una canción desconocida por tarareo necesita una base de datos externa.
  Aquí no hay una: por eso se ofrece el código Parsons y el buscador de la canción cargada.
- Si algo no funciona, usa el botón «Diagnóstico del sistema» (arriba).
- Los errores se guardan en el archivo MusicHelper_errores.log de tu carpeta de usuario.
"""
        bar = ctk.CTkFrame(self.tab_help, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkButton(bar, text="Diagnóstico del sistema", width=190, command=self.run_diagnostics).pack(side="left")
        ctk.CTkLabel(bar, text="Si algo no funciona (micrófono, YouTube, audio), pulsa aquí.",
                     text_color="gray").pack(side="left", padx=10)
        box = ctk.CTkTextbox(self.tab_help, font=ctk.CTkFont(size=13))
        box.pack(fill="both", expand=True, padx=8, pady=8)
        box.insert("1.0", text)
        box.configure(state="disabled")

    def run_diagnostics(self):
        self._bg(au.diagnostics, lambda lines: messagebox.showinfo("Diagnóstico", "\n".join(lines)),
                 "Revisando el sistema...")

    # ==================================================================
    # Cargar audio
    # ==================================================================
    def open_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar audio",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.opus"), ("Todos", "*.*")],
        )
        if path:
            self._load_path(path)

    def open_youtube(self):
        dialog = ctk.CTkInputDialog(text="Pega el enlace de YouTube:", title="Descargar de YouTube")
        url = dialog.get_input()
        if not url or not url.strip():
            return

        def work():
            return au.download_youtube_audio(url.strip(), self.temp_dir)

        def done(res):
            path, title = res
            try:
                path = au.persist_download(path)
            except Exception:
                self._log(traceback.format_exc())
            self._load_path(path, title)

        self._bg(work, done, "Descargando audio de YouTube... (puede tardar)")

    def _load_path(self, path, title=None):
        self._load_id += 1
        lid = self._load_id
        self.player.stop()
        self.btn_play.configure(text="Play")

        def work():
            y, _ = au.load_audio(path)
            return y, au.to_analysis(y)

        def done(res):
            if lid != self._load_id:
                return
            y, y22 = res
            self.current_path = path
            self.y_play, self.y_ana = y, y22
            self.duration = len(y) / au.PLAY_SR
            self.player.load(y)
            self.speed_var.set("1.0x")
            self.semi_var.set("0")
            name = title or Path(path).stem
            self.lbl_title.configure(text=name)
            self.harmony, self.chords, self.chord_starts = None, [], []
            self.notes, self.notes_range = [], (0.0, self.duration)
            self.lyric_lines, self.lyric_times = [], []
            self._last_chord = self._last_lyric = None
            self._last_chord_i = None
            self.chord_canvas.delete("all")
            self.chord_legend.delete("all")
            self.tab_box.delete("1.0", "end")
            self.tab_box.insert("1.0", "Analiza la melodía en «Notas / MIDI» y aquí aparece la tablatura para guitarra.")
            self.lbl_chord.configure(text="—")
            self.lbl_lyric.configure(text="")
            self.loop_start.delete(0, "end")
            self.loop_end.delete(0, "end")
            self.chords_text.delete("1.0", "end")
            self.chords_text.insert("1.0", "Calculando acordes...")
            self.notes_text.delete("1.0", "end")
            self.notes_text.insert("1.0", "Archivo listo. Pulsa «Analizar» cuando quieras.")
            self._draw_roll()
            artist, song = au.guess_artist_title(name)
            self.lyric_query.delete(0, "end")
            self.lyric_query.insert(0, f"{artist} {song}".strip())
            self._refresh_info()
            self.status.configure(text=f"Cargado: {name}")
            self._start_harmony(y22, lid)
            if self._pending_seek is not None:
                self.player.seek(self._pending_seek)
                self.player.play()
                self.btn_play.configure(text="Pausa")
                self._pending_seek = None

        self._bg(work, done, f"Cargando {Path(path).name}...")

    def _start_harmony(self, y22, lid):
        def done(res):
            if lid != self._load_id:
                return
            self.harmony = res
            self.chords = res["chords"]
            self.chord_starts = [c["start"] for c in self.chords]
            self._refresh_info()
            self._refresh_chords()
            try:
                au.save_to_library(self.current_path, self.lbl_title.cget("text"), res["chroma"], res["duration"])
                self._refresh_lib_label()
            except Exception:
                self._log(traceback.format_exc())
            self.status.configure(text="Listo: tempo, tonalidad y acordes calculados")

        def fail(err):
            self.chords_text.delete("1.0", "end")
            self.chords_text.insert("1.0", f"No se pudieron calcular los acordes:\n{err}")

        self._bg(lambda: au.analyze_song(y22), done, None, on_error=fail)

    # ==================================================================
    # Reproductor
    # ==================================================================
    def toggle_play(self):
        if not self.player.loaded:
            messagebox.showinfo("Aviso", "Primero carga un archivo de audio.")
            return
        if self.player.playing:
            self.player.pause()
            self.btn_play.configure(text="Play")
        else:
            try:
                self.player.play()
            except Exception as exc:
                messagebox.showerror("Error de audio", f"No pude abrir la salida de audio:\n{exc}")
                return
            self.btn_play.configure(text="Pausa")

    def stop_playback(self):
        self.player.stop()
        self.btn_play.configure(text="Play")

    def _on_seek(self, value):
        if self.player.loaded:
            self.player.seek(float(value) * self.duration)

    def _apply_effects(self):
        if not self.player.loaded:
            return
        speed = float(self.speed_var.get().rstrip("x"))
        semis = int(self.semi_var.get())
        self.status.configure(text="Procesando audio (velocidad/tono)... la primera vez tarda unos segundos")
        self.player.set_effects(
            speed, semis,
            on_done=lambda: self._post(lambda: self.status.configure(
                text=f"Velocidad {speed}x, tono {semis:+d}")),
            on_error=lambda msg: self._post(lambda: self._bg_error(msg, msg, None)),
        )

    def mark_a(self):
        if self.player.loaded:
            self.loop_start.delete(0, "end")
            self.loop_start.insert(0, f"{self.player.position:.1f}")

    def mark_b(self):
        if self.player.loaded:
            self.loop_end.delete(0, "end")
            self.loop_end.insert(0, f"{self.player.position:.1f}")
            if self.loop_start.get().strip():
                self.apply_loop()

    def apply_loop(self):
        if not self.player.loaded:
            return
        try:
            start = float(self.loop_start.get().strip() or "0")
            end_s = self.loop_end.get().strip()
            end = float(end_s) if end_s else None
            self.player.set_loop(start, end)
            self.status.configure(text=f"Loop: {start:.1f}s → {end if end else 'fin'}")
        except ValueError as exc:
            messagebox.showerror("Loop", str(exc) if "loop" in str(exc).lower() else
                                 "Escribe números válidos (segundos) para el loop.")

    def clear_loop(self):
        self.player.clear_loop()
        self.loop_start.delete(0, "end")
        self.loop_end.delete(0, "end")
        self.status.configure(text="Loop eliminado")

    def _update_playback_ui(self):
        p = self.player
        if not p.loaded:
            return
        pos = p.position
        if not self._dragging and self.duration > 0:
            self.seek.set(min(pos / self.duration, 1.0))
        self.lbl_time.configure(text=f"{fmt_time(pos)} / {fmt_time(self.duration)}")

        if self.chord_starts:
            i = bisect.bisect_right(self.chord_starts, pos) - 1
            if i >= 0 and pos > self.chords[i]["end"] + 0.5:
                i = -1
            if i != self._last_chord_i:
                self._last_chord_i = i
                self._show_chord_pair(i)

        if self.lyric_times:
            i = bisect.bisect_right(self.lyric_times, pos) - 1
            cur = self.lyric_lines[i][1] if i >= 0 else ""
            nxt = self.lyric_lines[i + 1][1] if i + 1 < len(self.lyric_lines) else ""
            txt = f"{cur}\n{nxt}"
            if txt != self._last_lyric:
                self._last_lyric = txt
                self.lbl_lyric.configure(text=txt)

        if self._playhead is not None and self.notes:
            t0, t1 = self.notes_range
            W = max(self.roll.winfo_width(), 200)
            H = max(self.roll.winfo_height(), 100)
            x = (pos - t0) / max(t1 - t0, 0.1) * W
            x = x if 0 <= x <= W else -5
            self.roll.coords(self._playhead, x, 0, x, H)

        if p.pop_finished():
            self.btn_play.configure(text="Play")

    def _show_chord_pair(self, i):
        cv = self.chord_canvas
        cv.delete("all")
        if i < 0 or i >= len(self.chords):
            self.lbl_chord.configure(text="—")
            return
        c = self.chords[i]
        name = au.chord_name(c["root"], c["minor"], self.solfege)
        self.lbl_chord.configure(text=name)
        draw_chord(cv, 0, 0, guitar.chord_frets(c["root"], c["minor"]), name)
        if i + 1 < len(self.chords):
            n = self.chords[i + 1]
            nname = au.chord_name(n["root"], n["minor"], self.solfege)
            draw_chord(cv, 112, 0, guitar.chord_frets(n["root"], n["minor"]), "→ " + nname,
                       color="#6b7a8f", text_color="#9aa5b5")

    # ==================================================================
    # Notación y textos
    # ==================================================================
    def _on_notation(self, value):
        self.solfege = value == "Do Re Mi"
        self._last_chord = None
        self._last_chord_i = None
        self._refresh_info()
        self._refresh_chords()
        if self.notes:
            self._show_notes()
        if self.hum_notes:
            self._show_hum()
        self._draw_roll()

    def _refresh_info(self):
        if not self.player.loaded:
            return
        txt = f"Duración: {fmt_time(self.duration)}"
        if self.harmony:
            root, minor, _ = self.harmony["key"]
            tempo = self.harmony["tempo"]
            tempo_txt = f"{tempo:.0f} BPM" if tempo >= 30 else "—"
            txt += (f" | Tempo ≈ {tempo_txt}"
                    f" | Tonalidad aprox: {au.key_name(root, minor, self.solfege)}")
        else:
            txt += " | Analizando tempo, tonalidad y acordes..."
        self.lbl_info.configure(text=txt)

    def _refresh_chords(self):
        if not self.chords:
            return
        lines = ["  Tiempo   Acorde   Digitación (cuerda 6 → 1)", "-" * 44]
        for c in self.chords:
            name = au.chord_name(c["root"], c["minor"], self.solfege)
            fing = guitar.fingering_text(guitar.chord_frets(c["root"], c["minor"]))
            lines.append(f"  {fmt_time(c['start']):>5}    {name:<6}   {fing}")
        self.chords_text.delete("1.0", "end")
        self.chords_text.insert("1.0", "\n".join(lines))

        # diagramas de los acordes más usados
        totals = {}
        for c in self.chords:
            k = (c["root"], c["minor"])
            totals[k] = totals.get(k, 0.0) + (c["end"] - c["start"])
        top = sorted(totals, key=lambda k: -totals[k])[:8]
        self.chord_legend.delete("all")
        for n, (root, minor) in enumerate(top):
            draw_chord(self.chord_legend, n * 108, 0, guitar.chord_frets(root, minor),
                       au.chord_name(root, minor, self.solfege))

    # ==================================================================
    # Melodía → notas
    # ==================================================================
    def run_melody(self, only_loop):
        if self.y_ana is None:
            messagebox.showinfo("Aviso", "Primero carga una canción.")
            return
        if only_loop:
            loop = self.player.loop
            if not loop:
                messagebox.showinfo("Aviso", "Primero define un loop con los botones «A aquí» y «B aquí».")
                return
            t0, t1 = loop
        else:
            t0, t1 = 0.0, self.duration
            if t1 > 90 and not messagebox.askyesno(
                    "Canción larga",
                    "Analizar la canción completa puede tardar varios minutos.\n"
                    "Es más rápido y preciso analizar solo un tramo (loop A-B).\n\n¿Continuar con la completa?"):
                return
        fmin, fmax = au.RANGES[self.range_var.get()]
        sr = au.ANALYSIS_SR
        seg = self.y_ana[int(t0 * sr):int(t1 * sr)]
        lid = self._load_id

        def work():
            return au.analyze_melody(seg, sr, fmin, fmax, t_offset=t0)

        def done(notes):
            if lid != self._load_id:
                return
            self.notes = notes
            self.notes_range = (t0, t1)
            self._show_notes()
            self.status.configure(text=f"Análisis listo: {len(notes)} notas")

        self._bg(work, done, "Analizando melodía... (puede tardar)")

    def _notes_report(self, notes, t0=None, t1=None):
        if not notes:
            return "No se detectaron notas claras. Prueba con otro tramo o con otro tipo de instrumento."
        seq = " ".join(au.midi_to_name(n["midi"], self.solfege, octave=False) for n in notes)
        head = f"Notas detectadas: {len(notes)}"
        if t0 is not None:
            head += f"   (tramo {fmt_time(t0)} - {fmt_time(t1)})"
        lines = [head, "", "Secuencia de la melodía:", textwrap.fill(seq, 90), "",
                 f"{'Inicio':>8}  {'Fin':>8}  {'MIDI':>5}  Nota", "-" * 36]
        for n in notes[:400]:
            lines.append(f"{n['start']:8.2f}  {n['end']:8.2f}  {n['midi']:5d}  "
                         f"{au.midi_to_name(n['midi'], self.solfege)}")
        if len(notes) > 400:
            lines.append(f"... y {len(notes) - 400} notas más (están todas en el MIDI exportado)")
        return "\n".join(lines)

    def _show_notes(self):
        t0, t1 = self.notes_range
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", self._notes_report(self.notes, t0, t1))
        self._draw_roll()
        self.tab_box.delete("1.0", "end")
        self.tab_box.insert("1.0", guitar.notes_to_tab(self.notes))

    def _draw_roll(self):
        c = self.roll
        c.delete("all")
        self._playhead = None
        W = max(c.winfo_width(), 200)
        H = max(c.winfo_height(), 100)
        if not self.notes:
            c.create_text(W // 2, H // 2, fill="#888888",
                          text="Aquí aparecen las notas cuando analices la melodía.")
            return
        t0, t1 = self.notes_range
        span = max(t1 - t0, 0.1)
        lo = min(n["midi"] for n in self.notes) - 1
        hi = max(n["midi"] for n in self.notes) + 1
        rows = hi - lo + 1
        rh = H / rows
        for m in range(lo, hi + 1):
            y = H - (m - lo + 1) * rh
            if m % 12 in (1, 3, 6, 8, 10):
                c.create_rectangle(0, y, W, y + rh, fill="#262626", outline="")
            if m % 12 == 0 or rh >= 12:
                c.create_text(3, y + rh / 2, anchor="w", fill="#777777", font=("Segoe UI", 7),
                              text=au.midi_to_name(m, self.solfege))
        for n in self.notes:
            x0 = (n["start"] - t0) / span * W
            x1 = (n["end"] - t0) / span * W
            y = H - (n["midi"] - lo + 1) * rh
            c.create_rectangle(x0, y + 1, max(x1, x0 + 2), y + rh - 1, fill="#4aa3ff", outline="")
        self._playhead = c.create_line(-5, 0, -5, H, fill="#ff5a5a", width=2)

    def _on_roll_click(self, event):
        if not self.notes or not self.player.loaded:
            return
        t0, t1 = self.notes_range
        W = max(self.roll.winfo_width(), 200)
        self.player.seek(t0 + event.x / W * (t1 - t0))

    def export_midi(self):
        if not self.notes:
            messagebox.showinfo("Aviso", "Primero analiza la melodía.")
            return
        base = Path(self.current_path).stem if self.current_path else "melodia"
        path = filedialog.asksaveasfilename(defaultextension=".mid", initialfile=base + ".mid",
                                            filetypes=[("MIDI", "*.mid"), ("Todos", "*.*")])
        if not path:
            return
        try:
            tempo = self.harmony["tempo"] if self.harmony else 120.0
            au.notes_to_midi_file(self.notes, path, tempo=tempo, time_offset=self.notes_range[0])
            self.status.configure(text=f"MIDI guardado: {path}")
            messagebox.showinfo("Listo", f"MIDI guardado:\n{path}\n\nÁbrelo en MuseScore, Guitar Pro, etc.")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def export_musicxml(self):
        if not self.notes:
            messagebox.showinfo("Aviso", "Primero analiza la melodía.")
            return
        base = Path(self.current_path).stem if self.current_path else "melodia"
        path = filedialog.asksaveasfilename(
            defaultextension=".musicxml", initialfile=base + ".musicxml",
            filetypes=[("MusicXML", "*.musicxml *.xml"), ("Todos", "*.*")])
        if not path:
            return
        try:
            tempo = self.harmony["tempo"] if self.harmony else 120.0
            key = self.harmony["key"] if self.harmony else None
            mxl.notes_to_musicxml(self.notes, path, tempo=tempo, time_offset=self.notes_range[0],
                                  key=key, title=base)
            self.status.configure(text=f"MusicXML guardado: {path}")
            messagebox.showinfo(
                "Listo",
                f"Partitura guardada:\n{path}\n\nÁbrela en MuseScore (gratis). El ritmo es aproximado: "
                "revísalo y corrígelo ahí si hace falta.")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def copy_tab(self):
        self.clipboard_clear()
        self.clipboard_append(self.tab_box.get("1.0", "end"))
        self.status.configure(text="Tablatura copiada al portapapeles")

    def copy_notes(self):
        self.clipboard_clear()
        self.clipboard_append(self.notes_text.get("1.0", "end"))
        self.status.configure(text="Notas copiadas al portapapeles")

    # ==================================================================
    # Sonido de las notas detectadas
    # ==================================================================
    def _play_notes(self, notes):
        if not notes:
            messagebox.showinfo("Aviso", "No hay notas para escuchar todavía.")
            return
        try:
            import sounddevice as sd
            audio = au.synth_notes(notes)
            sd.stop()
            sd.play(audio, au.ANALYSIS_SR)
        except Exception as exc:
            messagebox.showerror("Error de audio", str(exc))

    def _stop_sound(self):
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def listen_notes(self):
        self._play_notes(self.notes)

    def listen_hum(self):
        self._play_notes(self.hum_notes)

    # ==================================================================
    # Letras
    # ==================================================================
    def search_lyrics(self):
        query = self.lyric_query.get().strip()
        if not query:
            messagebox.showinfo("Aviso", "Escribe artista y título para buscar.")
            return

        def done(results):
            self.lyric_results = results
            if not results:
                self.lyric_pick.configure(values=["(sin resultados)"])
                self.lyric_pick.set("(sin resultados)")
                self.status.configure(text="No encontré esa letra. Prueba otro nombre o usa Whisper.")
                return
            labels = []
            for r in results:
                tag = "con tiempos" if r["synced"] else "sin tiempos"
                labels.append(f"{r['artist']} - {r['title']} ({fmt_time(r['duration'])}, {tag})"[:60])
            self.lyric_labels = labels
            self.lyric_pick.configure(values=labels)
            self.lyric_pick.set(labels[0])
            self._apply_lyric_result(results[0])
            self.status.configure(text=f"{len(results)} resultado(s). Elige el correcto en la lista.")

        def fail(err):
            messagebox.showerror("Letras", f"No pude buscar la letra (¿hay internet?):\n{err}")

        self._bg(lambda: lyr.search_lrclib(query), done, "Buscando letra...", on_error=fail)

    def _on_pick_lyric(self, label):
        labels = getattr(self, "lyric_labels", [])
        if label in labels:
            self._apply_lyric_result(self.lyric_results[labels.index(label)])

    def _apply_lyric_result(self, r):
        if r["synced"]:
            self._set_synced_lyrics(lyr.parse_lrc(r["synced"]))
        else:
            self.lyric_lines, self.lyric_times = [], []
            self._last_lyric = None
            self.lbl_lyric.configure(text="")
            self.lyrics_text.delete("1.0", "end")
            self.lyrics_text.insert("1.0", r["plain"])

    def _set_synced_lyrics(self, lines):
        self.lyric_lines = lines
        self.lyric_times = [t for t, _ in lines]
        self._last_lyric = None
        self.lyrics_text.delete("1.0", "end")
        self.lyrics_text.insert("1.0", "\n".join(f"[{fmt_time(t)}] {txt}" for t, txt in lines))

    def run_whisper(self):
        if not self.current_path:
            messagebox.showinfo("Aviso", "Primero carga una canción.")
            return
        path, size = self.current_path, self.whisper_size.get()

        def done(res):
            lines, language = res
            self._set_synced_lyrics(lines)
            self.status.configure(text=f"Transcripción lista (idioma detectado: {language}). Revísala: puede tener errores.")

        def fail(err):
            messagebox.showerror("Whisper", err)

        self._bg(lambda: lyr.transcribe_whisper(path, size), done,
                 "Transcribiendo con Whisper... (la primera vez descarga el modelo)", on_error=fail)

    # ==================================================================
    # Tarareo / silbido
    # ==================================================================
    def record_hum(self):
        try:
            import sounddevice as sd
        except Exception as exc:
            messagebox.showerror("Error", f"sounddevice no está disponible:\n{exc}")
            return
        secs = int(self.hum_dur.get().split()[0])
        sr = au.ANALYSIS_SR
        try:
            self._rec_buf = sd.rec(int(secs * sr), samplerate=sr, channels=1, dtype="float32")
        except Exception as exc:
            messagebox.showerror("Micrófono", f"No pude usar el micrófono:\n{exc}")
            return
        self.btn_rec.configure(state="disabled")
        self._rec_tick(secs)

    def _rec_tick(self, remaining):
        if remaining > 0:
            self.status.configure(text=f"Grabando... {remaining} s. ¡Tararea o silba ahora!")
            self.after(1000, lambda: self._rec_tick(remaining - 1))
            return
        import sounddevice as sd
        sd.wait()
        self.btn_rec.configure(state="normal")
        y = self._rec_buf[:, 0].copy()
        self._rec_buf = None
        peak = float(abs(y).max()) if len(y) else 0.0
        if peak < 0.01:
            messagebox.showwarning("Micrófono", "Casi no se escuchó nada. Revisa que el micrófono esté activo.")
            self.status.configure(text="Grabación vacía")
            return
        self._analyze_hum_audio(y / peak)

    def analyze_hum_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar grabación de tarareo",
            filetypes=[("Audio", "*.wav *.mp3 *.ogg *.flac *.m4a"), ("Todos", "*.*")])
        if not path:
            return

        def done(y):
            self._analyze_hum_audio(y)

        self._bg(lambda: au.to_analysis(au.load_audio(path)[0]), done, "Cargando grabación...")

    def _analyze_hum_audio(self, y):
        self.hum_y = y
        fmin, fmax = au.HUM_RANGES[self.hum_mode.get()]
        sr = au.ANALYSIS_SR

        def done(notes):
            self.hum_notes = notes
            self.hum_matches = []
            self.id_results = []
            self._show_hum()
            self.status.configure(text=f"Tarareo analizado: {len(notes)} notas")

        self._bg(lambda: au.analyze_melody(y, sr, fmin, fmax, min_duration=0.07), done, "Analizando tarareo...")

    def _show_hum(self):
        notes = self.hum_notes
        text = self._notes_report(notes)
        if notes:
            text += ("\n\nCódigo Parsons (contorno): " + au.parsons_code(notes) +
                     "\n(* = inicio, U = sube, D = baja, R = repite)")
        if self.id_results:
            text += "\n\n¿Qué canción podría ser? (entre tu biblioteca)\n"
            for i, r in enumerate(self.id_results, 1):
                text += f"  {i}. {r['title'][:50]}  —  {r['score'] * 100:.0f}%  (cerca de {fmt_time(r['start'])})\n"
            if self.id_results[0]["score"] < 0.25:
                text += "Ninguna coincide con claridad: puede que esa canción no esté en tu biblioteca.\n"
            else:
                text += "Es orientativo: compara de oído con el primer resultado («Abrir mejor resultado»).\n"
        if self.hum_matches:
            text += "\n\nDónde suena en la canción cargada:\n"
            for i, m in enumerate(self.hum_matches, 1):
                if m["semitones"] == 0:
                    tono = "mismo tono"
                else:
                    tono = f"tu tarareo está {abs(m['semitones'])} semitonos " + \
                           ("más grave" if m["semitones"] > 0 else "más agudo")
                text += (f"  {i}. cerca de {fmt_time(m['start'])} - {fmt_time(m['end'])}"
                         f"   (coincidencia {m['score'] * 100:.0f}%, {tono})\n")
            text += "La coincidencia es relativa: si es baja, probablemente esa melodía no está en esta canción."
        self.hum_text.delete("1.0", "end")
        self.hum_text.insert("1.0", text)

    def find_in_song(self):
        if self.hum_y is None:
            messagebox.showinfo("Aviso", "Primero graba o carga un tarareo.")
            return
        if self.y_ana is None:
            messagebox.showinfo("Aviso", "Primero carga la canción donde quieres buscar.")
            return
        hum, song = self.hum_y, self.y_ana

        def done(matches):
            self.hum_matches = matches
            self._show_hum()
            self.status.configure(text="Búsqueda lista" if matches else "El tarareo es muy corto para buscar")

        self._bg(lambda: au.find_melody_in_song(hum, song), done, "Buscando tu melodía en la canción...")

    def goto_best_match(self):
        if not self.hum_matches or not self.player.loaded:
            messagebox.showinfo("Aviso", "Primero usa «Buscar en la canción cargada».")
            return
        self.player.seek(self.hum_matches[0]["start"])
        self.player.play()
        self.btn_play.configure(text="Pausa")

    def _refresh_lib_label(self):
        try:
            self.lbl_lib.configure(text=f"Biblioteca: {len(au.library_files())} canciones")
        except Exception:
            pass

    def add_folder_to_library(self):
        folder = filedialog.askdirectory(title="Carpeta con tu música")
        if not folder:
            return
        exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
        files = [str(f) for f in Path(folder).rglob("*") if f.suffix.lower() in exts][:500]
        if not files:
            messagebox.showinfo("Biblioteca", "No encontré audios en esa carpeta.")
            return

        def work():
            added = 0
            for i, f in enumerate(files, 1):
                try:
                    added += int(au.index_audio_file(f))
                except Exception:
                    self._log(f"No se pudo indexar {f}\n" + traceback.format_exc())
                self._post(lambda i=i: self.status.configure(text=f"Indexando música... {i}/{len(files)}"))
            return added

        def done(added):
            self._refresh_lib_label()
            self.status.configure(text=f"Biblioteca lista: {added} canciones nuevas")

        self._bg(work, done, f"Indexando {len(files)} archivos (tarda un poco)...")

    def identify_in_library(self):
        if self.hum_y is None:
            messagebox.showinfo("Aviso", "Primero graba o carga un tarareo.")
            return
        if not au.library_files():
            messagebox.showinfo(
                "Biblioteca vacía",
                "Aún no hay canciones para comparar.\nAbre canciones con «Abrir audio» o «YouTube», "
                "o usa «Agregar carpeta de música».")
            return
        hum = self.hum_y

        def progress(i, n):
            self._post(lambda: self.status.configure(text=f"Comparando con tus canciones... {i}/{n}"))

        def done(res):
            self.id_results = res
            self._show_hum()
            self.status.configure(text="Comparación lista" if res else "El tarareo es muy corto")

        self._bg(lambda: au.identify_hum(hum, progress), done, "Comparando con tus canciones...")

    def open_best_id(self):
        if not self.id_results:
            messagebox.showinfo("Aviso", "Primero pulsa «¿Qué canción es?».")
            return
        r = self.id_results[0]
        if not os.path.exists(r["path"]):
            messagebox.showerror("Archivo no encontrado", f"Ya no existe:\n{r['path']}")
            return
        if self.current_path and os.path.abspath(self.current_path) == r["path"] and self.player.loaded:
            self.player.seek(r["start"])
            self.player.play()
            self.btn_play.configure(text="Pausa")
        else:
            self._pending_seek = r["start"]
            self._load_path(r["path"], r["title"])

    def open_musipedia(self):
        if not self.hum_notes:
            messagebox.showinfo("Aviso", "Primero graba o carga un tarareo.")
            return
        code = au.parsons_code(self.hum_notes)
        self.clipboard_clear()
        self.clipboard_append(code)
        self.status.configure(text="Código Parsons copiado. Pégalo en Musipedia (búsqueda por contorno).")
        webbrowser.open("https://www.musipedia.org/")


def main():
    app = MusicHelperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
