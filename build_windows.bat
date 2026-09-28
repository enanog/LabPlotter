@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo   LabPlotter - generador de EXE e instalador para Windows
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 -m venv .build-venv
) else (
    where python >nul 2>nul
    if errorlevel 1 goto :no_python
    python -m venv .build-venv
)
if errorlevel 1 goto :failed

set "BUILD_PYTHON=%CD%\.build-venv\Scripts\python.exe"
"%BUILD_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%BUILD_PYTHON%" -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto :failed

echo.
echo [1/2] Generando dist\LabPlotter.exe...
"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean LabPlotter.spec
if errorlevel 1 goto :failed
if not exist "%CD%\dist\LabPlotter.exe" goto :failed

echo.
echo [2/2] Buscando Inno Setup para generar el instalador...
set "ISCC_EXE="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not defined ISCC_EXE goto :portable_only
"%ISCC_EXE%" "%CD%\packaging\windows_installer.iss"
if errorlevel 1 goto :failed

echo.
echo Listo:
echo   Portable:   %CD%\dist\LabPlotter.exe
echo   Instalador: %CD%\dist\LabPlotter-Setup.exe
echo.
pause
exit /b 0

:portable_only
echo.
echo Se genero correctamente el ejecutable portable:
echo   %CD%\dist\LabPlotter.exe
echo.
echo Para obtener tambien LabPlotter-Setup.exe, instala Inno Setup 6
echo y vuelve a ejecutar este archivo. El proyecto del instalador ya
echo esta incluido en packaging\windows_installer.iss.
echo.
pause
exit /b 0

:no_python
echo ERROR: no se encontro Python 3 en el sistema.
echo Instalalo desde https://www.python.org/downloads/windows/ y reintenta.
pause
exit /b 1

:failed
echo.
echo ERROR: la compilacion no pudo completarse. Revisa el mensaje anterior.
pause
exit /b 1
