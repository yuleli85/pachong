@echo off
chcp 936 >nul
set PYTHON_EXE=C:\Users\56389\AppData\Local\ShadowBot\users\927346590034649090\apps\9a503e9e-1e90-4ba3-b318-cf5430b8a40f_Release\venv310\Scripts\python.exe

echo Starting Data Collector...
"%PYTHON_EXE%" "%~dp0gui.py"
