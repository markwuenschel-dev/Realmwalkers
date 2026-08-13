@echo off
setlocal
title Realmwalkers Manuscript Formatter

set "TOOLROOT=%~dp0"
set "PICKER=%TOOLROOT%shortcuts\pick_and_format.ps1"

if not exist "%PICKER%" goto missing

powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%PICKER%"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo   ---------------------------------------------------------------
    echo   The formatter stopped with error code %RC%.
    echo   The error should be printed above.
    echo   ---------------------------------------------------------------
    echo.
    pause
)
exit /b %RC%

:missing
echo.
echo   ---------------------------------------------------------------
echo   The formatter files are incomplete.
echo.
echo   This BAT must remain inside the extracted manuscript-format folder,
echo   beside the shortcuts and manuscript_format folders.
echo.
echo   Expected file:
echo       %PICKER%
echo   ---------------------------------------------------------------
echo.
pause
exit /b 1
