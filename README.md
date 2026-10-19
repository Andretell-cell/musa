# Music Helper

Aplicación de escritorio para **aprender canciones a tocar**. Uso personal.

- Abre **MP3 / WAV / FLAC / M4A** o descarga el audio de **YouTube**
- **Reproductor** con loop A-B, **velocidad sin cambiar el tono** (0.5x a 1.5x) y **transposición** en semitonos
- **Tempo, tonalidad y acordes** automáticos (el acorde actual se ve mientras suena)
- **Melodía a notas** con piano-roll, nombres en Do Re Mi o C D E, y exportación a **MIDI**
- **Letras** online (sincronizadas, tipo karaoke) o transcritas con Whisper (opcional)
- **Tarareo / silbido**: saca las notas, el código Parsons y **busca en qué parte de la canción suena tu melodía**

FFmpeg viene incluido en las dependencias: no hay que instalar nada aparte.

---

## Usar el .exe (sin instalar Python)

1. En GitHub, pestaña **Actions** → último workflow **Build Windows EXE** en verde.
2. Abajo, en **Artifacts**, descarga `MusicHelper-Windows`.
3. Descomprime en una carpeta nueva y abre `MusicHelper.exe`.

Si creas un **Release** en GitHub, el workflow también adjunta `MusicHelper-Windows.zip`.

> El .exe es una carpeta (no un solo archivo) a propósito: así abre en segundos. Windows puede mostrar
> un aviso de SmartScreen porque el programa no está firmado: "Más información" → "Ejecutar de todas formas".

## Ejecutar desde el código

Necesitas **Python 3.11 o 3.12** (con 3.13 o superior algunas librerías pueden no instalar).

Lo más fácil en Windows: doble clic en `INSTALAR_Y_EJECUTAR.bat`.

Manual:

```bash
python -m venv venv
venv\Scripts\activate        # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python main.py
```

Letras con IA (opcional, solo desde el código): `pip install -r requirements-letras-ia.txt`

## Crear el .exe en tu PC

Ejecuta `CREAR_EXE.bat`. Queda en `dist\MusicHelper\MusicHelper.exe`.

---

## Qué hace cada parte (y sus límites)

| Función | Notas |
|---|---|
| Velocidad sin cambiar el tono | La primera vez que eliges una velocidad tarda unos segundos en procesar |
| Loop A-B | «A aquí» y «B aquí» toman el punto donde estás escuchando |
| Notas de la melodía | Una sola línea melódica. Mejor con voz o un instrumento solo, y analizando un tramo corto |
| Acordes | Solo mayores y menores; son una guía para empezar |
| Letras online | Busca en LRCLIB. Si trae tiempos, se ve la línea actual arriba |
| Tarareo: buscar en la canción | Funciona aunque tarareaste en otro tono o a otra velocidad |
| ¿Qué canción es? (tarareo) | Compara tu tarareo con tu **biblioteca**: cada canción que abres, descargas o agregas desde una carpeta queda indexada en `~/MusicHelper/biblioteca`. Solo reconoce canciones que ya tienes |
| Canción que no tienes | Necesita una base de datos externa. Usa el código Parsons en Musipedia o la búsqueda por tarareo de Google / SoundHound |
| Diagnóstico | Pestaña Ayuda → «Diagnóstico del sistema»: revisa audio, micrófono, internet, YouTube y FFmpeg |

Los errores se guardan en `MusicHelper_errores.log`, en tu carpeta de usuario.

## Estructura

```
music/
├── main.py
├── requirements.txt
├── requirements-letras-ia.txt
├── INSTALAR_Y_EJECUTAR.bat
├── CREAR_EXE.bat
├── .gitignore
├── core/
│   ├── __init__.py
│   ├── audio_utils.py    # carga, YouTube, análisis, notas, acordes, MIDI
│   ├── player.py         # reproductor (velocidad, tono, loop)
│   └── lyrics.py         # letras online y Whisper
└── .github/workflows/
    └── build-windows.yml # genera el .exe automáticamente
```
