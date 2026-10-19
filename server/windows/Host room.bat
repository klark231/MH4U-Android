@echo off
rem Hosts an MH4U network room with Azahar's dedicated room server (see host-room.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0host-room.ps1" %*
