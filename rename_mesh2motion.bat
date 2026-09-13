@echo off
cd /d "%~dp0"
set EXIT_CODE=0

echo ============================================================
echo   SkinToken - rename bones ^(mesh2motion conversion^)
echo   Double-click wrapper for: rename_all.bat mesh2motion
echo ============================================================
echo.

echo [1/3] Checking that Docker is running...
docker info >nul 2>&1
if errorlevel 1 (
    echo.
    echo !!! Docker is not running or not installed.
    echo     Start Docker Desktop, then run this file again.
    set EXIT_CODE=1
    goto :fail
)
echo       Docker is running.
echo.

echo [2/3] Renaming bone_ names in every *.glb under results\ ...
echo       Progress: one "OK" line per GLB file. This can take a
echo       while for large files - please be patient.
echo.
call rename_all.bat mesh2motion
set EXIT_CODE=%ERRORLEVEL%
echo.

echo [3/3] Checking results...
set COUNT=0
for /f "delims=" %%f in ('dir /b /a:-d "results\*__mesh2motion.glb" 2^>nul') do set /a COUNT+=1
echo       Found %COUNT% renamed "__mesh2motion" GLB file(s) in results\.
echo.

if %EXIT_CODE%==0 (
    echo ============================================================
    echo   DONE - rename finished successfully.
    echo   New GLBs are in results\ next to the originals.
    echo ============================================================
    goto :end
)

:fail
echo ============================================================
echo   FAILED - error output is above, see it for details.
echo ============================================================

:end
endlocal
pause
exit /b %EXIT_CODE%
