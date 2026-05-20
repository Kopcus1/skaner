@echo off
title System Wesola - FULL SUITE (Multicore Launcher)

:: Przejdz do folderu glownego skryptu
cd /d "%~dp0"

echo ========================================================
echo   START SYSTEMU "WESOLA" (Full Distributed Suite)
echo ========================================================

:: --- KROK 0: CZYSZCZENIE ---
echo [0/7] Konsolidacja procesow (czyszczenie portow)...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM electron.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1

:: --- KROK 1: MODULY LOKALNE (Root Venv) ---
echo [1/6] Startuje Skaner (login.py)...
start "1. LOGIN (Kamera)" ".venv\Scripts\python.exe" login.py

timeout /t 3 /nobreak >nul

echo [2/6] Startuje Mapper (wrapper.py)...
start "2. WRAPPER" ".venv\Scripts\python.exe" wrapper.py

echo [3/6] Startuje Uploader (uploader.py)...
start "3. UPLOADER" ".venv\Scripts\python.exe" uploader.py

:: --- KROK 2: MODULY ZEWNETRZNE (Dedykowane Venv) ---

:: DOWNLOADER
echo [4/6] Startuje Downloader (Sub-folder)...
if exist "downloader\main.py" (
    pushd downloader
    start "4. DOWNLOADER" ".venv\Scripts\python.exe" main.py
    popd
) else (
    echo [BLAD] Brak downloader/main.py!
)

:: DISPLAY SYSTEM
echo [5/6] Startuje Display System (Sub-folder)...
if exist "display_system\main.py" (
    pushd display_system
    start "5. DISPLAY" ".venv\Scripts\python.exe" main.py
    popd
) else (
    echo [BLAD] Brak display_system/main.py!
)


echo ========================================================
echo SYSTEM URUCHOMIONY: 6 MODULOW PRACUJE.
echo ========================================================
pause