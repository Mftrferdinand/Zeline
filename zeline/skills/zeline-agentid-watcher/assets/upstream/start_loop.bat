@echo off
cd /d "%~dp0"
if not exist data mkdir data
set FAILS=0
:loop
if exist WATCH_STOP goto end
node watch.mjs >> data\watch_console.log 2>&1
set RC=%errorlevel%
if exist WATCH_STOP goto end
if "%RC%"=="0" set FAILS=0
if "%RC%"=="1" if exist data\update_backup.mjs set /a FAILS+=1
if %FAILS% GEQ 3 call :rollback
timeout /t 5 /nobreak >nul
goto loop
:rollback
copy /y data\update_backup.mjs watch.mjs >nul
del data\update_backup.mjs
echo [%date% %time%] new version failed to start 3 times, rolled back to the previous version>> data\watch_console.log
set FAILS=0
exit /b
:end
