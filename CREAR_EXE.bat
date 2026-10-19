@echo off
chcp 65001 >nul
title Crear MusicHelper.exe
cd /d "%~dp0"

echo ========================================
echo   Crear MusicHelper con PyInstaller
echo ========================================
echo.

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo Primero ejecuta INSTALAR_Y_EJECUTAR.bat al menos una vez.
    pause
    exit /b 1
)

pip install pyinstaller

pyinstaller --noconfirm --clean --onedir --windowed --name MusicHelper ^
    --collect-all customtkinter ^
    --collect-all librosa ^
    --collect-all numba ^
    --collect-all llvmlite ^
    --collect-all soundfile ^
    --collect-all sounddevice ^
    --collect-all imageio_ffmpeg ^
    --collect-submodules yt_dlp ^
    --collect-all yt_dlp_ejs ^
    main.py

if exist "dist\MusicHelper\MusicHelper.exe" (
    echo.
    echo LISTO. Esta en: dist\MusicHelper\MusicHelper.exe
    echo Copia la carpeta completa dist\MusicHelper donde quieras.
) else (
    echo.
    echo Hubo un problema al crear el .exe. Revisa los mensajes de arriba.
)
pause
