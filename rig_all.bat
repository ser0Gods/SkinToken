@echo off
setlocal
cd /d "%~dp0"

echo.
echo === SkinTokens batch rig ===
echo Input folder:  %~dp0input
echo Output folder: %~dp0results
echo.
echo Press Ctrl+C to abort.
echo.

docker compose run --rm rig

if errorlevel 1 (
    echo.
    echo !!! Conversion run reported errors - check output above.
) else (
    echo.
    echo === Done. Check results\ folder.
)

endlocal
pause
