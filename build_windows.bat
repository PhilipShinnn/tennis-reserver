@echo off
chcp 65001 >nul
echo.
echo ====================================================
echo  세종시 테니스장 자동예약 — Windows EXE 빌드
echo ====================================================
echo.

echo [1/3] 패키지 설치 중...
pip install playwright pyinstaller --quiet
if %errorlevel% neq 0 (
    echo ❌ 패키지 설치 실패. pip 확인 후 재시도하세요.
    pause
    exit /b 1
)

echo [2/3] EXE 빌드 중... (약 1~2분 소요)
pyinstaller ^
    --noconsole ^
    --onefile ^
    --name "세종테니스예약" ^
    --collect-all playwright ^
    main_gui.py

if %errorlevel% neq 0 (
    echo ❌ 빌드 실패.
    pause
    exit /b 1
)

echo.
echo ====================================================
echo  ✅ 빌드 완료!
echo     dist\세종테니스예약.exe 를 실행하세요.
echo ====================================================
echo.
pause
