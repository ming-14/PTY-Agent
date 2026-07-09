@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

for /r /d %%d in (__pycache__) do if exist "%%d" (
    set "skip="
    set "has_dot="
    for %%p in ("%%d:\=" "%") do (
        set "part=%%~p"
        if "!part:~0,1!"=="." set "has_dot=1"
    )
    if defined has_dot set "skip=1"
    if not defined skip (attrib "%%d" | findstr /r /c:" [HS]" >nul && set "skip=1")
    if not defined skip (dir /ad /b "%%d" 2>nul | findstr /r . >nul && set "skip=1")
    if not defined skip (
        set "valid=1"
        for %%f in ("%%d\*") do (
            set "ext=%%~xf"
            if /i not "!ext!"==".pyc" set "valid="
        )
        if defined valid (rd /s /q "%%d" && echo 已删除: %%d)
    )
)

if exist "%~dp0pty-agent" rd /s /q "%~dp0pty-agent"
mkdir "%~dp0pty-agent"
xcopy "%~dp0src" "%~dp0pty-agent\src\" /e /i /q
copy "%~dp0app.py" "%~dp0pty-agent\" >nul
copy "%~dp0SKILL.md" "%~dp0pty-agent\" >nul
mkdir "%~dp0pty-agent\doc"
xcopy "%~dp0doc\Skill文档\*" "%~dp0pty-agent\doc\" /q >nul
echo 构建完成: %~dp0pty-agent