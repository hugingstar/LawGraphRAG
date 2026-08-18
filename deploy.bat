@echo off
REM Double-click launcher for deploy.ps1 (equivalent of running ./deploy.sh on Linux).
REM Kept ASCII-only on purpose: cmd.exe garbles non-ASCII text inside .bat files and
REM has, in testing, executed the corrupted fragments as commands. All Korean output
REM lives in deploy.ps1, which runs fine under PowerShell's UTF-8 handling.
REM
REM   deploy.bat                 double-click, or:  deploy.bat
REM   deploy.bat -Hub            pull prebuilt images from Docker Hub instead of building
REM   deploy.bat -ResetData      wipe volumes and start over
REM   deploy.bat -NoFirewall     skip opening the firewall
REM   deploy.bat -NoOps          app stack only, no monitoring
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
set EXITCODE=%ERRORLEVEL%
echo.
echo Exit code: %EXITCODE%
pause
exit /b %EXITCODE%
