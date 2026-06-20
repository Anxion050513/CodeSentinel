@echo off
title AI Code Review Bot

echo ============================================
echo   AI Code Review Bot - Quick Start
echo ============================================
echo.

cd /d "%~dp0"

:: 1. Check .env
if not exist ".env" (
    echo [ERROR] .env file not found!
    echo First run: copy .env.example .env
    echo Then edit .env with your settings.
    pause
    exit /b 1
)
echo [OK] .env found

:: 2. Check MySQL
echo [*] Checking MySQL...
mysqladmin ping -h localhost -u root -p123456 >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] MySQL not running - DB features will not work
) else (
    echo [OK] MySQL is running
    mysql -u root -p123456 -e "CREATE DATABASE IF NOT EXISTS code_review_bot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>nul
    echo [OK] Database code_review_bot ready
)

:: 3. Setup virtualenv
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    python -m venv .venv
    echo [OK] venv created
) else (
    echo [OK] venv exists
)

:: 4. Install dependencies
call .venv\Scripts\activate.bat
echo [*] Installing dependencies...
pip install -q fastapi "uvicorn[standard]" pydantic pydantic-settings sqlalchemy aiomysql chromadb langchain langchain-openai pluggy httpx python-dotenv 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Some packages failed, retrying...
    pip install fastapi uvicorn pydantic pydantic-settings sqlalchemy aiomysql chromadb langchain langchain-openai pluggy httpx python-dotenv
)
echo [OK] Dependencies ready

:: 5. Start server
echo.
echo ============================================
echo   Starting at http://localhost:8000
echo   Health : http://localhost:8000/api/v1/health
echo   Swagger: http://localhost:8000/docs
echo   Press Ctrl+C to stop
echo ============================================
echo.
.venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

pause
