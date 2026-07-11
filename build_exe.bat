@echo off
REM Builds a single portable CellStringer.exe (no installer, no external
REM dependencies needed on the target machine). Run this from a normal
REM Windows Python install; PyInstaller itself is only needed at build time.

setlocal
cd /d "%~dp0"

python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller build tool...
    python -m pip install --quiet pyinstaller
)

echo Building CellStringer.exe ...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name "CellStringer" ^
    --icon "app_icon.ico" ^
    --add-data "app_icon.ico;." ^
    --add-data "app_icon.png;." ^
    cell_stringer.py

echo.
echo Done. Portable exe is at dist\CellStringer.exe
echo You can copy that single file anywhere and run it directly.
endlocal
