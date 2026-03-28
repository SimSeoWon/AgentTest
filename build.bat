@echo off
echo ===================================
echo  AgentTest - 전체 빌드
echo ===================================

pip install pyinstaller mcp >nul 2>&1

echo.
echo [1/4] watch.exe 빌드 중...
pyinstaller -y ^
    --onefile ^
    --name "watch" ^
    --paths "watcher" ^
    watcher/watch.py

echo.
echo [2/4] MCP - context_search 빌드 중...
if not exist "dist\mcp" mkdir dist\mcp

pyinstaller -y ^
    --onefile ^
    --distpath "dist\mcp" ^
    --name "context_search" ^
    mcp/context_search/server.py

echo.
echo [3/4] MCP - log_analyzer 빌드 중...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\mcp" ^
    --name "log_analyzer" ^
    mcp/log_analyzer/server.py

echo.
echo [4/4] MCP - crash_analyzer 빌드 중...
pyinstaller -y ^
    --onefile ^
    --distpath "dist\mcp" ^
    --name "crash_analyzer" ^
    mcp/crash_analyzer/server.py

echo.
echo ===================================
echo  빌드 완료
echo.
echo  배포 방법:
echo    dist\ 폴더 전체를 Unreal 프로젝트 루트에 복사
echo    (Source\.git 이 있는 폴더의 상위)
echo.
echo    배포 구조:
echo      [프로젝트 루트]\
echo        watch.exe
echo        mcp\
echo          context_search.exe
echo          log_analyzer.exe
echo          crash_analyzer.exe
echo.
echo    최초 실행(watch.exe) 시 자동으로:
echo      - 감시 브랜치 / 폴링 간격 설정
echo      - .claude\context\ 도메인 폴더 생성
echo      - .claude\agents\ 에이전트 폴더 9개 + settings.json 생성
echo ===================================
pause
