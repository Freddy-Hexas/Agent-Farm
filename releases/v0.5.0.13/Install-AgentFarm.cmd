@echo off
setlocal

rem Keep the script path relative to the elevated process working directory.
rem This avoids losing the space in paths such as "A:\Agent Farm" when
rem Start-Process serializes its argument list.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$process = Start-Process -FilePath powershell.exe -Verb RunAs -WorkingDirectory '%~dp0' -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','Install-AgentFarm.ps1'); exit $process.ExitCode"
set "exitCode=%ERRORLEVEL%"

if not "%exitCode%"=="0" (
  echo Agent Farm installation did not complete (exit code %exitCode%).
  echo Check Install-AgentFarm.log in this folder for the detailed error.
  pause
)

exit /b %exitCode%
