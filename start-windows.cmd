@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 docs/ONPREM.md의 설치 절차를 실행해 주세요.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m src.onprem
if errorlevel 1 pause
