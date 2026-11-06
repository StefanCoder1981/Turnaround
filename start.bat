@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Erstelle virtuelle Umgebung ...
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m streamlit run app.py
