@echo off
rem Drop one or more manuscript files, or a manuscript folder, onto this file.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0drag_and_format.ps1" -Format doc %*
