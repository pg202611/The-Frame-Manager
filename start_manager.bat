@echo off
cd /d "%~dp0"
py -3.12 the_frame_manager.py
if errorlevel 1 pause
