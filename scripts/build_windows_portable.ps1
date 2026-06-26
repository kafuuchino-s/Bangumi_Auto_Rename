# 番剧自动重命名 · Windows 便携版组装脚本
# 在 GitHub Actions windows-latest runner 上执行，产出 BangumiAutoRename-win-x64.zip。
# 组装：python-embeddable + node + ffprobe + unrar + 依赖 + 源码 + 前端静态导出。
#
# 本地也可手动跑（需 PowerShell 7+ 与网络），用于验证：
#   pwsh scripts/build_windows_portable.ps1
#
# 关键不变量（决定便携版能否零改码工作）：
#   - REPO_ROOT = Path(__file__).resolve().parents[3]  → 便携版 app/src/... 向上 3 层 = app/
#   - Node cwd=REPO_ROOT 解 app/node_modules         → app/node_modules 必须就位
#   - shutil.which('ffprobe')                          → runtime/bin 必须在 PATH 前缀
#   - ['node', tools/pi_*.mjs]                         → runtime/node 必须在 PATH 前缀

#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12.9",
    [string]$NodeVersion = "22.19.0",   # Pi sidecar 要求 node >=22.19（Dockerfile 注释）
    [string]$OutDir = (Join-Path $PSScriptRoot "..", "dist"),
    [string]$Staging = (Join-Path $PSScriptRoot "..", "_portable_staging")
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "==> 仓库根: $repoRoot"

# --- 版本号：优先从 pyproject.toml 读，CI 可用 env RELEASE_VERSION 覆盖 ---
$releaseVersion = $env:RELEASE_VERSION
if (-not $releaseVersion) {
    $pyproject = Get-Content (Join-Path $repoRoot "pyproject.toml") -Raw
    if ($pyproject -match 'version\s*=\s*"([^"]+)"') {
        $releaseVersion = $Matches[1]
    } else {
        $releaseVersion = "0.0.0-dev"
    }
}
Write-Host "==> 版本: $releaseVersion"

# --- 清理 staging ---
$stagingAbs = (Resolve-Path $Staging -ErrorAction SilentlyContinue).Path
if ($stagingAbs) {
    Write-Host "==> 清理旧 staging: $stagingAbs"
    Remove-Item -Recurse -Force $stagingAbs
}
$distAbs = (New-Item -ItemType Directory -Force -Path $OutDir).FullName

# 便携包根目录名（解压后的顶层目录）
$pkgName = "BangumiAutoRename-win-x64-$releaseVersion"
$pkgRoot = Join-Path $Staging $pkgName
New-Item -ItemType Directory -Force -Path $pkgRoot | Out-Null

$appDir = Join-Path $pkgRoot "app"
$runtimeDir = Join-Path $pkgRoot "runtime"
$pythonEmbedDir = Join-Path $runtimeDir "python-embed"
$nodeDir = Join-Path $runtimeDir "node"
$binDir = Join-Path $runtimeDir "bin"
$dataDir = Join-Path $pkgRoot "data"

foreach ($d in @($appDir, $runtimeDir, $pythonEmbedDir, $nodeDir, $binDir, $dataDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# ============================================================
# 1. Python embeddable + bootstrap pip + 装依赖
# ============================================================
Write-Host "==> [1/7] 下载 Python embeddable $PythonVersion"
$pyMajorMinor = ($PythonVersion -split '\.')[0..1] -join '.'
$pyEmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$pyZip = Join-Path $env:TEMP "python-embed.zip"
Invoke-WebRequest -Uri $pyEmbedUrl -OutFile $pyZip -UseBasicParsing
Expand-Archive -Path $pyZip -DestinationPath $pythonEmbedDir -Force
Remove-Item $pyZip

# embeddable 默认禁用 site-packages 与 site 模块：解开 ._pth 让 pip/第三方包能 import
$pthFile = Get-ChildItem $pythonEmbedDir -Filter "python*._pth" | Select-Object -First 1
if ($pthFile) {
    $pthContent = Get-Content $pthFile.FullName
    # 取消 import site 注释（embeddable 默认 #import site）
    $pthContent = $pthContent -replace '^#import site', 'import site'
    # 追便携 site-packages（app\Lib\site-packages 不在此，由 PYTHONPATH/start.bat 兜底，
    # 这里只确保 embeddable 自身能 import pip 装的包到 python-embed\Lib\site-packages）
    Set-Content -Path $pthFile.FullName -Value $pthContent -Encoding ASCII
}

# bootstrap pip：embeddable 不带 pip，下 get-pip.py
Write-Host "==> [1/7] bootstrap pip"
$getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$getPip = Join-Path $pythonEmbedDir "get-pip.py"
Invoke-WebRequest -Uri $getPipUrl -OutFile $getPip -UseBasicParsing
$pyExe = Join-Path $pythonEmbedDir "python.exe"
& $pyExe $getPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap 失败" }

# 装依赖到 app\Lib\site-packages（让程序体自包含，runtime/python-embed 只放解释器）
$appSitePackages = Join-Path $appDir "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $appSitePackages | Out-Null
Write-Host "==> [1/7] 装依赖到 $appSitePackages"
$reqFile = Join-Path $repoRoot "requirements_portable.txt"
& $pyExe -m pip install --no-warn-script-location -r $reqFile -t $appSitePackages
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
# pip 装到 -t 会带 *.dist-info 与 bin 脚本，清理 bin 脚本（Windows 用 .exe 入口在 site-packages 根）
Get-ChildItem $appSitePackages -Directory -Filter "bin" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ============================================================
# 2. Node.js win-x64
# ============================================================
Write-Host "==> [2/7] 下载 Node $NodeVersion win-x64"
$nodeUrl = "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip"
$nodeZip = Join-Path $env:TEMP "node-win-x64.zip"
Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeZip -UseBasicParsing
# node zip 内顶层是 node-vX.Y.Z-win-x64/，解压后取其内容到 runtime/node
$nodeExtract = Join-Path $env:TEMP "node-extract"
New-Item -ItemType Directory -Force -Path $nodeExtract | Out-Null
Expand-Archive -Path $nodeZip -DestinationPath $nodeExtract -Force
$nodeTopLevel = Get-ChildItem $nodeExtract -Directory | Select-Object -First 1
Copy-Item -Path (Join-Path $nodeTopLevel.FullName "*") -Destination $nodeDir -Recurse -Force
Remove-Item $nodeZip, $nodeExtract -Recurse -Force

# ============================================================
# 3. ffprobe + unrar（runtime/bin）
# ============================================================
# ffprobe：优先 BtbN 静态构建（windows gpl，含 ffprobe），回退说明。
# 用 BtbN/FFmpeg-Builds 的 master 最新 win64 gpl shared/static 里取 ffprobe.exe。
Write-Host "==> [3/7] 下载 ffprobe（BtbN 静态构建）"
$ffprobeUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
$ffZip = Join-Path $env:TEMP "ffmpeg-win64.zip"
try {
    Invoke-WebRequest -Uri $ffprobeUrl -OutFile $ffZip -UseBasicParsing
    $ffExtract = Join-Path $env:TEMP "ffmpeg-extract"
    New-Item -ItemType Directory -Force -Path $ffExtract | Out-Null
    Expand-Archive -Path $ffZip -DestinationPath $ffExtract -Force
    $ffprobeSrc = Get-ChildItem $ffExtract -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if (-not $ffprobeSrc) { throw "ffprobe.exe 未在包内找到" }
    Copy-Item $ffprobeSrc.FullName (Join-Path $binDir "ffprobe.exe") -Force
    Remove-Item $ffZip, $ffExtract -Recurse -Force
} catch {
    Write-Warning "BtbN ffprobe 下载失败（$($_.Exception.Message)）；便携版将缺 ffprobe，媒体元数据探测降级。"
    Remove-Item $ffZip -ErrorAction SilentlyContinue
}

# unrar：RARLAB 官方 Windows UnRAR.exe
Write-Host "==> [3/7] 下载 unrar"
$unrarUrl = "https://www.rarlab.com/rar/unrarw64.exe"  # 自解压安装器，含 UnRAR.exe
try {
    $unrarExe = Join-Path $env:TEMP "unrarw64.exe"
    Invoke-WebRequest -Uri $unrarUrl -OutFile $unrarExe -UseBasicParsing
    # unrarw64.exe 是 SFX 安装器，不好静默取文件；改用 7-zip 解或直接放说明。
    # 简化：RARLAB 也提供 unrar-x64-7xx.tar.gz，但 Windows 便携优先用现成 UnRAR.exe。
    # 这里尝试用系统 7z（windows-latest runner 自带 7z）解 SFX。
    $unrarExtract = Join-Path $env:TEMP "unrar-extract"
    New-Item -ItemType Directory -Force -Path $unrarExtract | Out-Null
    & 7z x $unrarExe -o"$unrarExtract" -y | Out-Null
    $unrarSrc = Get-ChildItem $unrarExtract -Recurse -Filter "UnRAR.exe" | Select-Object -First 1
    if (-not $unrarSrc) {
        $unrarSrc = Get-ChildItem $unrarExtract -Recurse -Filter "unrar.exe" | Select-Object -First 1
    }
    if ($unrarSrc) {
        Copy-Item $unrarSrc.FullName (Join-Path $binDir "unrar.exe") -Force
    } else {
        Write-Warning "UnRAR.exe 未在 SFX 内找到；.rar 字幕解压将不可用。"
    }
    Remove-Item $unrarExe, $unrarExtract -Recurse -Force
} catch {
    Write-Warning "unrar 下载失败（$($_.Exception.Message)）；.rar 字幕解压将不可用。"
}

# ============================================================
# 4. 复制程序主体（src / tools / .pi / package.json）
# ============================================================
Write-Host "==> [4/7] 复制程序主体到 app/"
Copy-Item -Path (Join-Path $repoRoot "src") -Destination $appDir -Recurse -Force
Copy-Item -Path (Join-Path $repoRoot "tools") -Destination $appDir -Recurse -Force
# .pi：合同 skills + extensions（agent/auth.json 经 .dockerignore 排除；本地组装不复制凭据）
if (Test-Path (Join-Path $repoRoot ".pi")) {
    Copy-Item -Path (Join-Path $repoRoot ".pi") -Destination $appDir -Recurse -Force
    # 清除可能存在的本地凭据/生成目录
    Remove-Item -Recurse -Force (Join-Path $appDir ".pi\agent") -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $appDir ".pi\agents") -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $appDir ".pi\prompts") -ErrorAction SilentlyContinue
}
Copy-Item -Path (Join-Path $repoRoot "package.json") -Destination $appDir -Force
Copy-Item -Path (Join-Path $repoRoot "package-lock.json") -Destination $appDir -Force

# ============================================================
# 5. node_modules（npm ci --omit=dev）
# ============================================================
Write-Host "==> [5/7] npm ci --omit=dev（app/node_modules）"
Push-Location $appDir
try {
    & (Join-Path $nodeDir "npm.cmd") ci --omit=dev
    if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" }
    # 清冗余（同 Dockerfile）
    Get-ChildItem -Path "node_modules" -Recurse -File -Include "*.map","*.d.ts","*.md" | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path "node_modules" -Recurse -Directory -Filter "docs" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
} finally {
    Pop-Location
}

# ============================================================
# 6. 前端静态导出（npm run build → frontend/out）
# ============================================================
Write-Host "==> [6/7] 前端 next build（output: export → frontend/out）"
$frontendDir = Join-Path $repoRoot "frontend"
Push-Location $frontendDir
try {
    & (Join-Path $nodeDir "npm.cmd") ci
    if ($LASTEXITCODE -ne 0) { throw "frontend npm ci 失败" }
    & (Join-Path $nodeDir "npm.cmd") run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build 失败" }
} finally {
    Pop-Location
}
Copy-Item -Path (Join-Path $frontendDir "out") -Destination $appDir -Recurse -Force

# ============================================================
# 7. 启动脚本 + README + 打包
# ============================================================
Write-Host "==> [7/7] 复制 start.bat/README + 打包 zip"
Copy-Item -Path (Join-Path $repoRoot "dist_windows\start.bat") -Destination $pkgRoot -Force
Copy-Item -Path (Join-Path $repoRoot "dist_windows\README.txt") -Destination $pkgRoot -Force

# 清 __pycache__ / .pyc（便携版运行期自动重建）
Get-ChildItem -Path $appDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $appDir -Recurse -File -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue

$zipPath = Join-Path $distAbs "$pkgName.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $pkgRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "==> 完成: $zipPath"
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "==> 体积: $zipSize MB"

# CI 用：输出 zip 路径供 upload artifact / release
if ($env:GITHUB_ACTIONS -eq "true") {
    "zip_path=$zipPath" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding ASCII
    "zip_name=$pkgName.zip" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding ASCII
}
