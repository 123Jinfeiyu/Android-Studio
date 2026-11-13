@echo off
rem Android Studio 一键启动（Windows）
rem 可用环境变量指定解释器：set PYTHON_EXE=python
if not defined PYTHON_EXE set PYTHON_EXE=python

cd /d %~dp0
%PYTHON_EXE% server.py --port 8777
pause
