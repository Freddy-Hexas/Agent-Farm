@echo off
setlocal EnableExtensions

rem One-click source launcher for Agent Farm. Keep the repository as the
rem working directory so the native app opens this checkout by default.
set "ROOT=%~dp0"
set "REPO=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "AGENT_FARM_SOURCE_ROOT=%REPO%"
set "AGENT_FARM_REPO=%REPO%"

set "PYTHON_EXE="
set "PYTHON_ARGS="
if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%ROOT%venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYTHON_EXE where py >nul 2>&1 && set "PYTHON_EXE=py" && set "PYTHON_ARGS=-3"
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        echo(%%P | findstr /I /C:"\WindowsApps\" >nul || if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
    )
)

if not defined PYTHON_EXE (
    echo Agent Farm could not find Python 3.11 or newer.
    echo Install Python, or activate the environment that contains this checkout.
    pause
    exit /b 1
)

if not exist "%ROOT%AgentFarm.Desktop\AgentFarm.Desktop.csproj" (
    echo AgentFarm.Desktop\AgentFarm.Desktop.csproj was not found.
    echo Run this file from the Agent Farm repository folder.
    pause
    exit /b 1
)

where dotnet >nul 2>&1
if errorlevel 1 (
    echo Agent Farm could not find the .NET SDK.
    echo Install the .NET 8 SDK or newer, plus the WinUI 3 build dependencies.
    pause
    exit /b 1
)

if /I "%~1"=="--check" (
    echo Checking Agent Farm source prerequisites...
    "%PYTHON_EXE%" %PYTHON_ARGS% -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'; import agent_farm; print('Python runtime: OK')"
    if errorlevel 1 (
        echo Python can be found, but the Agent Farm package could not be imported.
        echo Install the source environment with: python -m pip install -e .
        exit /b 1
    )
    dotnet --version
    echo Agent Farm source prerequisites: OK
    exit /b 0
)

echo Starting Agent Farm...
echo Close the Agent Farm window to hide it; the local desktop session stays running.
echo.
dotnet run --project "%ROOT%AgentFarm.Desktop\AgentFarm.Desktop.csproj" --configuration Debug -p:Platform=x64 --no-launch-profile
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Agent Farm could not start. Exit code: %EXIT_CODE%.
    echo Run Start-AgentFarm.cmd --check to verify the local prerequisites.
    pause
)
exit /b %EXIT_CODE%
