@echo off
REM Lance le dashboard meteo Streamlit
setlocal

set "PY=C:\Users\Public\Anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%~dp0"
"%PY%" -m streamlit run "%~dp0meteo_app.py"

pause
