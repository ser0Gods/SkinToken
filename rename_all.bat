@echo off
setlocal
cd /d "%~dp0"
set EXIT_CODE=0

if "%~1"=="" (
    echo Usage: rename_all.bat ^<convention^>
    echo.
    echo Available conventions:
    for %%f in ("mappings\*.json") do echo   %%~nf
    echo.
    echo   e.g.: rename_all.bat mesh2motion
    set EXIT_CODE=1
    goto :end
)

echo.
echo Renaming bone_ names in all *.glb under results\ using mapping "%~1"
echo Output: results\^<source^>__%~1.glb   ^(originals are not modified^)
echo.

docker compose run --rm rename "%~1"
set EXIT_CODE=%ERRORLEVEL%

if not %EXIT_CODE%==0 (
    echo.
    echo !!! Rename run reported errors - check output above.
) else (
    echo.
    echo Done. New GLBs are in results\ next to the originals.
)

:end
endlocal & exit /b %EXIT_CODE%
