@echo off
chcp 65001 >nul

echo ===================================
echo  AgentTest - Full Build
echo ===================================

pip install pyinstaller mcp >nul 2>&1

echo.
echo [1/4] Building watch.exe ...
pyinstaller -y ^
    --onefile ^
    --name "watch" ^
    --paths "watcher" ^
    watcher/watch.py

echo.
echo [2/4] Building MCP - context_search ...
if not exist "dist\.claude\mcp" mkdir dist\.claude\mcp

pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "context_search" ^
    mcp/context_search/server.py

echo.
echo [3/4] Building MCP - log_analyzer ...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "log_analyzer" ^
    mcp/log_analyzer/server.py

echo.
echo [4/4] Building MCP - crash_analyzer ...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "crash_analyzer" ^
    mcp/crash_analyzer/server.py

echo.
echo Packaging AgentWatch.zip ...
if exist "AgentWatch.zip" del "AgentWatch.zip"
powershell -Command "Compress-Archive -Path 'dist\*' -DestinationPath 'AgentWatch.zip'"

echo.
echo ===================================
echo  Build Complete!
echo.
echo  Output: AgentWatch.zip
echo  Send this file to team members.
echo  See INSTALL.md for setup instructions.
echo ===================================
pause
