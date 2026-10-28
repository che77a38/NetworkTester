@echo off
rem ===========================================================================
rem  NetWatch —— Windows 构建脚本
rem
rem  用法（在仓库根目录或本脚本所在目录执行均可）：
rem      packaging\build_windows.bat
rem
rem  产物：
rem      dist\netwatch.exe              单文件可执行程序
rem      dist\netwatch-windows-x64.zip  发布用压缩包
rem
rem  前置条件：已安装 Python 3.8+ 与 PyInstaller
rem      python -m pip install -r requirements-dev.txt
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "APP_NAME=netwatch"
set "SPEC_FILE=%~dp0netwatch.spec"

rem --- 切换到仓库根目录（本脚本位于 <root>\packaging\） ---
pushd "%~dp0.."
set "PROJECT_ROOT=%CD%"
set "DIST_DIR=%PROJECT_ROOT%\dist"
set "STAGE_DIR=%DIST_DIR%\NetWatch-windows-x64"

echo [build] 项目根目录：%PROJECT_ROOT%

rem --- 探测 Python ---
set "PY_CMD="
where python >nul 2>nul && set "PY_CMD=python"
if not defined PY_CMD (
    where py >nul 2>nul && set "PY_CMD=py -3"
)
if not defined PY_CMD (
    echo [build] 错误：未找到 Python，请先安装 Python 3.8+ 并加入 PATH
    popd
    exit /b 1
)

%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
if errorlevel 1 (
    echo [build] 错误：需要 Python 3.8 或更高版本
    popd
    exit /b 1
)

rem --- 检查 PyInstaller 是否已安装 ---
%PY_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [build] 错误：未安装 PyInstaller，请先执行：
    echo         %PY_CMD% -m pip install -r requirements-dev.txt
    popd
    exit /b 1
)
for /f "delims=" %%v in ('%PY_CMD% -m PyInstaller --version 2^>nul') do set "PYI_VERSION=%%v"
echo [build] PyInstaller 版本：!PYI_VERSION!

rem --- 清理旧的暂存目录 ---
if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"
mkdir "%STAGE_DIR%" 2>nul

echo [build] 开始构建 Windows 可执行程序...
%PY_CMD% -m PyInstaller "%SPEC_FILE%" --noconfirm --clean --distpath "%DIST_DIR%" --workpath "%PROJECT_ROOT%\build\packaging"
if errorlevel 1 (
    echo [build] 错误：PyInstaller 构建失败
    popd
    exit /b 1
)

if not exist "%DIST_DIR%\%APP_NAME%.exe" (
    echo [build] 错误：未生成 %DIST_DIR%\%APP_NAME%.exe
    popd
    exit /b 1
)

rem --- 收集发布文件 ---
copy /Y "%DIST_DIR%\%APP_NAME%.exe" "%STAGE_DIR%\" >nul
if exist "%PROJECT_ROOT%\README.md" copy /Y "%PROJECT_ROOT%\README.md" "%STAGE_DIR%\" >nul
if exist "%PROJECT_ROOT%\LICENSE" copy /Y "%PROJECT_ROOT%\LICENSE" "%STAGE_DIR%\" >nul

rem --- 生成 ZIP ---
echo [build] 生成 ZIP 压缩包...
if exist "%DIST_DIR%\netwatch-windows-x64.zip" del /q "%DIST_DIR%\netwatch-windows-x64.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%STAGE_DIR%' -DestinationPath '%DIST_DIR%\netwatch-windows-x64.zip' -Force"
if errorlevel 1 (
    echo [build] 错误：ZIP 打包失败
    popd
    exit /b 1
)

echo.
echo [build] 构建完成！
echo [build]   可执行程序：%DIST_DIR%\%APP_NAME%.exe
echo [build]   发布压缩包：%DIST_DIR%\netwatch-windows-x64.zip

popd
endlocal
exit /b 0
