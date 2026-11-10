@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Agent ID Sniper one-click install
echo.
echo ==============================================
echo   Agent ID Sniper one-click install
echo   Official TG group: t.me/nancy_c0813
echo ==============================================
echo.
echo Before starting, make sure Agentic Wallet is enabled in the Binance App, a .ai Agent ID is claimed, and "developer mode" is on
echo.
pause

REM ---- 1. Node.js ----
where node >nul 2>nul
if errorlevel 1 (
  echo [1/5] Node.js not found, installing...
  winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
  echo.
  echo Node.js installed. Please [close this window and double-click install.bat again] to continue.
  echo If the install failed, download the LTS build from https://nodejs.org, install it by hand and run this again.
  pause
  exit /b
)
echo [1/5] Node.js is installed

REM ---- 2. Binance baw ----
where baw >nul 2>nul
if errorlevel 1 (
  echo [2/5] Installing the Binance Agentic Wallet tool (baw)...
  call npm install -g @binance/agentic-wallet
)
where baw >nul 2>nul
if errorlevel 1 (
  echo Installing baw failed; check your network and run it again, or run it by hand: npm install -g @binance/agentic-wallet
  pause
  exit /b
)
echo [2/5] baw is installed

REM ---- 3. Settings ----
if not exist data mkdir data
echo [3/5] Set up the wallet and notifications
node setup.mjs
if not exist config.json (
  echo Setup was not completed, please run it again
  pause
  exit /b
)

REM ---- 4. Log the wallet in ----
echo.
echo [4/5] Logging in wallet 1: a link appears in a moment, open it with this wallet's Binance App and confirm (within 5 minutes)
node login.mjs 1
set /p NW=<data_wallets.txt
if "%NW%"=="2" (
  echo.
  echo Logging in wallet 2: open the link and confirm with the 2nd wallet's Binance account
  call setup_wallet2.bat
)
del data_wallets.txt >nul 2>nul

REM ---- 5. Autostart at boot + start now ----
echo.
echo [5/5] Setting up autostart at boot
node -e "const p=require('path'),fs=require('fs');const f=p.join(process.env.APPDATA,'Microsoft','Windows','Start Menu','Programs','Startup','agentid-sniper.vbs');const t='CreateObject(\"WScript.Shell\").Run \"\"\"'+p.join(process.cwd(),'start_hidden.vbs')+'\"\"\", 0, False\r\n';fs.writeFileSync(f,Buffer.concat([Buffer.from([0xff,0xfe]),Buffer.from(t,'utf16le')]));console.log('autostart at boot is set up')"
start "" wscript "%~dp0start_hidden.vbs"
echo.
echo ==============================================
echo   ✅ Install finished, the tool is running in the background
echo   - Log: data\watch.log
echo   - Stop: create a file named WATCH_STOP in this folder
echo   - New versions are announced on TG, reply /update to update
echo   - Join the official TG group: t.me/nancy_c0813
echo ==============================================
pause
