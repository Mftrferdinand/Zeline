@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM The 2nd wallet uses a separate baw copy: the system keychain (keytar) is disabled and the session lives in the baw2 folder, so it does not kick the 1st wallet offline
if not exist baw2pkg mkdir baw2pkg
call npm install @binance/agentic-wallet@1.12.0 --prefix baw2pkg --no-audit --no-fund
if exist baw2pkg\node_modules\@binance\agentic-wallet\node_modules\@github move baw2pkg\node_modules\@binance\agentic-wallet\node_modules\@github baw2pkg\keytar_disabled_1
if exist baw2pkg\node_modules\@github move baw2pkg\node_modules\@github baw2pkg\keytar_disabled_2
if not exist baw2 mkdir baw2
node login.mjs 2
pause
