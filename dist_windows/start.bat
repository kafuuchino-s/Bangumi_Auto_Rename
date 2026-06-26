@echo off
REM 番剧自动重命名 · Windows 便携版启动脚本
REM 解压即用：双击本脚本即可启动 Web 服务（默认端口 5999）。
REM 运行时（python-embed / node / ffprobe / unrar）已内置在 runtime/，无需预装环境。

setlocal
set "HERE=%~dp0"

REM 把便携运行时前缀到 PATH，使 shutil.which('ffprobe') 与 ['node', ...] 命中便携副本
set "PATH=%HERE%runtime\python-embed;%HERE%runtime\node;%HERE%runtime\bin;%PATH%"

REM 让 embeddable python 能 import app/src 下的模块
set "PYTHONPATH=%HERE%app"

cd /d "%HERE%app"

echo.
echo ============================================================
echo   番剧自动重命名 · 便携版
echo   浏览器访问: http://localhost:5999
echo   首次使用请先在网页 设置 页填写 AI / 路径 / 通知配置
echo   配置与数据保存在: %HERE%data\
echo ============================================================
echo.

REM 首启自动建立 data/ 目录树（config/record/cache/log 等）
if not exist "%HERE%data" mkdir "%HERE%data"

python -m src.start

REM 异常退出时留住窗口便于看报错
if errorlevel 1 (
    echo.
    echo [启动失败] 请将上方报错截图反馈。日志见 %HERE%data\log\BAR.log
    pause
)

endlocal
