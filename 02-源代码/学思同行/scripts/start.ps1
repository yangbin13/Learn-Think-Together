param([int]$Port = 8765, [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host '已生成 .env，请填写 OpenAI 兼容模型服务的 URL、Key 和模型 ID。无有效凭证时仍可体验资料、练习和计划；聊天会提示配置问题。'
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "端口 $Port 已被占用。可指定 -Port 8766，不会终止已有服务。"
}
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败' }
}
if (-not $SkipInstall) {
    $dependencyFile = if (Test-Path -LiteralPath 'backend/requirements.lock.txt') { 'backend/requirements.lock.txt' } else { 'backend/requirements.txt' }
    & '.venv/Scripts/python.exe' -m pip install -r $dependencyFile
    if ($LASTEXITCODE -ne 0) { throw '安装后端依赖失败' }
    Push-Location -LiteralPath 'frontend'
    try {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw '缺少前端构建，请首次运行不要加 -SkipInstall' }
Write-Host "学思同行已准备好：http://127.0.0.1:$Port （Ctrl+C停止本次服务）"
& '.venv/Scripts/python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port
