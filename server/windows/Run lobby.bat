@echo off
rem Runs the MH4U lobby server (mh4u_lobby.py): the room list Azahar shows in Multiplayer >
rem Browse Public Rooms, and its check of names. Needs Python 3.10 or newer (python.org). Its
rem names and keys stay in %APPDATA%\MH4U Server\lobby. Players use http://<this PC's
rem address>:24872 as their Network Web API URL: forward TCP 24872 on the router to this PC,
rem and allow Python through the firewall when Windows asks. See docs/SERVER.md.
rem Options and commands go to mh4u_lobby.py, e.g.
rem   "Run lobby.bat" --mode invite
rem   "Run lobby.bat" add-user "Hunter One"       (prints that name's token)
rem   "Run lobby.bat" list-users
setlocal
set "LOBBY=%~dp0mh4u_lobby.py"
if not exist "%LOBBY%" set "LOBBY=%~dp0..\lobby\mh4u_lobby.py"
if not exist "%LOBBY%" (
    echo Can't find mh4u_lobby.py next to this script or in ..\lobby
    goto end
)
set "DATA=%MH4U_SERVER_DIR%"
if "%DATA%"=="" set "DATA=%APPDATA%\MH4U Server"
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python 3.10 or newer is needed: https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" when you install it, then run this again.
    goto end
)
%PY% "%LOBBY%" --data "%DATA%\lobby" %*
:end
if "%MH4U_NO_PAUSE%"=="" pause
