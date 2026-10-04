@echo off
title AUDRIX Backend

cd /d "S:\Projects\SAT-SA Security Assessment\backend"

echo Starting AUDRIX Backend...
echo.

".venv\Scripts\uvicorn.exe" app.main:app --reload --port 8000

pause
