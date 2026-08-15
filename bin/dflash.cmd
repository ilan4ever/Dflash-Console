@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%"
if not defined DFLASH_ROOT set "DFLASH_ROOT=%ROOT%"
python -m dflash_cli %*
exit /b %ERRORLEVEL%
