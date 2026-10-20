@echo off
setlocal
pushd "%~dp0.."
uv run --locked --script kindle.py send output/pdf/pilot --recursive %*
set "kindle_result=%ERRORLEVEL%"
popd
exit /b %kindle_result%
