@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================================================
echo   VLOG-US-STUDIO-V1 : GOP VIDEO HOAN CHINH VA DON FILE NHAP TRUNG GIAN
echo ========================================================================
echo.

set "EP_ID=%~1"
if not "%EP_ID%"=="" goto RUN_CLEAN

echo Cac tap phim hien co trong thu muc episodes\:
for /d %%D in ("episodes\*") do (
    echo   - %%~nxD
)
echo.
set /p "EP_ID=Nhap ma tap phim can gop va don nhap (vi du EP001): "

if "%EP_ID%"=="" (
    echo [HUY] Ban chua nhap ma tap phim.
    pause
    exit /b 1
)

:RUN_CLEAN
if not exist "episodes\%EP_ID%" (
    echo [LOI] Khong tim thay thu muc episodes\%EP_ID% !
    if "%~1"=="" pause
    exit /b 1
)

echo.
echo [DANG XU LY] Gop video thanh pham va xoa cac file cat le tam thoi cho %EP_ID%...
python "engine\concat_master.py" --episode "%EP_ID%" --cleanup-temp
if errorlevel 1 (
    echo.
    echo [LOI] Qua trinh gop hoac don nhap gap su co. Vui long kiem tra thong bao phia tren.
    if "%~1"=="" pause
    exit /b 1
)

echo.
echo [HOAN TAT] Da luu an toan Video_Hoan_Chinh_%EP_ID%.mp4, Phu_De_YouTube_%EP_ID%.srt
echo            va giu nguyen du an CapCut_Draft\ trong episodes\%EP_ID%\3_pha_B_video\ !
if "%~1"=="" pause
exit /b 0
