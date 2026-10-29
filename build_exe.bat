@echo off
REM ============================================================
REM  Build DocSearch.exe
REM  Ruleaza din folderul proiectului, cu mediul conda activ.
REM ============================================================
cd /d "%~dp0"

echo.
echo [1/3] Instalez PyInstaller daca lipseste...
pip install pyinstaller pywin32 olefile
if errorlevel 1 goto :pip_error

echo.
echo [2/3] Construiesc executabilul - dureaza 1-3 minute...
rem Build curat, IN AFARA folderului Project (care urca pe GitHub):
rem rezultatul merge in ..\dist, fisierele temporare in ..\build
if exist "..\build" rmdir /s /q "..\build"
if exist "..\dist" rmdir /s /q "..\dist"
rem stergem si eventuale resturi vechi din interiorul Project
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller DocSearch.spec --noconfirm --distpath "..\dist" --workpath "..\build"
if errorlevel 1 goto :build_error

echo.
echo [3/3] Gata!
echo.
echo Executabilul este in:  ..\dist\DocSearch\DocSearch.exe
echo (langa folderul Project, NU in el - nu ajunge pe GitHub)
echo.
echo IMPORTANT: distribuie / muta INTREGUL folder dist\DocSearch,
echo nu doar DocSearch.exe - exe-ul are nevoie de fisierele de langa el.
echo.
pause
exit /b 0

:pip_error
echo.
echo EROARE: pip nu a functionat. Activeaza intai mediul conda:
echo     conda activate Search-index-doc-docx
echo apoi ruleaza din nou build_exe.bat
pause
exit /b 1

:build_error
echo.
echo EROARE la build. Citeste mesajele de mai sus.
pause
exit /b 1
