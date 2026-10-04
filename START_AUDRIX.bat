@echo off
title AUDRIX Launcher

echo ========================================
echo            AUDRIX Launcher
echo ========================================
echo.

echo Starting Backend...
start "AUDRIX Backend" cmd /k "cd /d "S:\Projects\SAT-SA Security Assessment\backend" && ".venv\Scripts\uvicorn.exe" app.main:app --reload --port 8000"

timeout /t 3 /nobreak >nul

echo Starting Frontend...
start "AUDRIX Frontend" cmd /k "cd /d "S:\Projects\SAT-SA Security Assessment\frontend" && npm run dev"

echo.
echo ========================================
echo AUDRIX is starting...
echo.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:5173
echo ========================================
echo.

pause
