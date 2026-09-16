@echo off
rem 작업 스케줄러가 이 파일을 실행한다. main.py 안의 loguru 파일 로그(logs\run_YYYY-MM-DD.log)가
rem 주된 기록이고, 이 리다이렉트는 파이썬이 뜨기도 전에(venv 없음 등) 실패하는 경우를 대비한
rem 안전망이다.

cd /d "%~dp0"
if not exist logs mkdir logs

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

python main.py run --review >> logs\wrapper.log 2>&1
set EXITCODE=%ERRORLEVEL%

echo %date% %time% exit=%EXITCODE% >> logs\wrapper.log
exit /b %EXITCODE%
