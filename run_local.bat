@echo off
REM Lance le dashboard en mode local : active l'option "Lancer un run".
set WEATHER_LOCAL=1
streamlit run meteo_app.py
