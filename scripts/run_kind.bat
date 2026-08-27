@echo off
REM ETF 상관계수 미달 공시 체크 (KIND 원본 사용, 사내 PC용)
REM 사용법: 이 파일을 더블클릭하거나, 작업 스케줄러에 등록하세요.
chcp 65001 >nul
cd /d "%~dp0.."

where python >nul 2>&1
if errorlevel 1 (
  echo [오류] 파이썬을 찾을 수 없습니다. https://www.python.org/downloads/ 에서 설치하세요.
  echo        설치할 때 "Add python.exe to PATH" 체크를 꼭 켜 주세요.
  pause
  exit /b 1
)

if not exist "logs" mkdir "logs"
set "STAMP=%date:~0,4%%date:~5,2%%date:~8,2%"

python "scripts\check_etf_correlation.py" --source kind > "logs\%STAMP%.log" 2>&1
set "RC=%ERRORLEVEL%"

type "logs\%STAMP%.log"
echo.
if "%RC%"=="0" (
  echo 완료. 결과는 data\etf_correlation_checks.csv 에 누적됩니다.
) else (
  echo [실패] 위 메시지를 확인하세요. KIND 접속이 되는 망에서 실행해야 합니다.
)
echo.
pause
