@echo off
REM Lance le dashboard meteo Streamlit en local (Windows) — double-clic.
REM Cherche Python dans l'ordre : Anaconda utilisateur, Anaconda partage, PATH.
setlocal

set "PY=%USERPROFILE%\anaconda3\python.exe"
if not exist "%PY%" set "PY=C:\Users\Public\Anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

REM Mode local force : active la page "Lancer le pipeline".
set WEATHER_LOCAL=1

cd /d "%~dp0"
"%PY%" -m streamlit run "%~dp0meteo_app.py"

pause
