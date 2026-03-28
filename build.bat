@echo off
chcp 65001 >nul

echo ===================================
echo  AgentTest - Full Build
echo ===================================

pip install pyinstaller mcp chromadb >nul 2>&1

echo.
echo [1/6] Building watch.exe ...
pyinstaller -y ^
    --onefile ^
    --name "watch" ^
    --paths "watcher" ^
    watcher/watch.py

echo.
echo [2/6] Building MCP - context_search ...
if not exist "dist\.claude\mcp" mkdir dist\.claude\mcp

pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "context_search" ^
    --collect-all chromadb ^
    --collect-all onnxruntime ^
    --hidden-import=tokenizers ^
    mcp/context_search/server.py

echo.
echo [3/6] Building MCP - log_analyzer ...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "log_analyzer" ^
    mcp/log_analyzer/server.py

echo.
echo [4/6] Building MCP - crash_analyzer ...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "crash_analyzer" ^
    mcp/crash_analyzer/server.py

echo.
echo [5/6] Building MCP - commandlet_runner ...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "commandlet_runner" ^
    mcp/commandlet_runner/server.py

echo.
echo [6/6] Building MCP - gemini_query ...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\.claude\mcp" ^
    --name "gemini_query" ^
    mcp/gemini_query/server.py

echo.
echo Bundling ONNX embedding model ...
set "ONNX_SRC=%USERPROFILE%\.cache\chroma\onnx_models\all-MiniLM-L6-v2\onnx"
set "ONNX_DST=dist\.claude\mcp\onnx_model"
if exist "%ONNX_SRC%\model.onnx" (
    if not exist "%ONNX_DST%" mkdir "%ONNX_DST%"
    xcopy /Y /Q "%ONNX_SRC%\*" "%ONNX_DST%\" >nul
    echo   ONNX model bundled from cache.
) else (
    echo   [WARNING] ONNX model not found in cache. Run context_search.exe once to download.
)

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
