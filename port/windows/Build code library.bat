@echo off
rem Builds the game's code library from your own MH4U v1.3.0 update (see build-code-library.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-code-library.ps1" %*
