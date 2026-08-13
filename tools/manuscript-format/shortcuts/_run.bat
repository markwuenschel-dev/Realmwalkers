@echo off
rem Shared launcher for the drag-and-drop shortcuts.
rem Arg 1 is the target format (reader, shunn or doc); arg 2 is the dropped file.
rem NOTE: keep this file pure ASCII with no percent-signs in comments. cmd.exe reads .bat in the
rem OEM codepage and parses comment lines too, so a stray UTF-8 dash or a percent-expansion in a
rem rem-line will corrupt the command that follows it.
setlocal

set "FORMAT=%~1"
set "INPUT=%~2"

if "%INPUT%"=="" (
    echo.
    echo   Nothing dropped.
    echo.
    echo   Drag a manuscript file onto this icon to format it.
    echo   Accepts:  .md  .markdown  .txt  .docx
    echo.
    pause
    exit /b 1
)

rem A drive+path expansion always ends in a backslash, and a quoted path ending in a backslash
rem escapes its own closing quote, which silently swallows the next argument. Strip it.
set "OUTDIR=%~dp2"
if "%OUTDIR:~-1%"=="\" set "OUTDIR=%OUTDIR:~0,-1%"

pushd "%~dp0.."

echo.
echo   Formatting:  %~nx2
echo   As:          %FORMAT%
echo   Saving to:   %OUTDIR%
echo.

python -m manuscript_format "%INPUT%" --to %FORMAT% -o "%OUTDIR%"
if errorlevel 1 goto failed

echo.
echo   ---------------------------------------------------------------
echo   Done. The new file is in:
echo.
echo       %OUTDIR%
echo   ---------------------------------------------------------------
echo.
pause
popd
exit /b 0

:failed
echo.
echo   ---------------------------------------------------------------
echo   That did not work. The error is printed above.
echo.
echo   Most common cause: Python or python-docx is not installed.
echo   Fix it by running this once:
echo.
echo       pip install -r requirements.txt
echo   ---------------------------------------------------------------
echo.
pause
popd
exit /b 1
