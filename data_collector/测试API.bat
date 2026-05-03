@echo off
chcp 65001 >nul
title 东方财富API测试

echo ============================================================
echo     东方财富API测试工具
echo ============================================================
echo.
echo 此工具将测试API连接并显示返回的数据
echo.

set PYTHON_EXE=C:\Users\56389\AppData\Local\ShadowBot\users\927346590034649090\apps\9a503e9e-1e90-4ba3-b318-cf5430b8a40f_Release\venv310\Scripts\python.exe

echo 运行测试...
"%PYTHON_EXE%" "%~dp0手动测试API.py"

echo.
echo ============================================================
echo 测试完成！请查看上面的结果。
echo 如果显示"✓ 请求成功"和数据，说明API正常。
echo ============================================================
echo.
pause
