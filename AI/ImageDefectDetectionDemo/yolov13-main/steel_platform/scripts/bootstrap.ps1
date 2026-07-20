param(
  [string]$EnvironmentName = "steel-review",
  [string]$PythonVersion = "3.11"
)
$ErrorActionPreference = "Stop"
$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) { throw "未找到 conda，请先安装 Anaconda 或 Miniconda。" }
$Existing = conda env list --json | ConvertFrom-Json
if (-not ($Existing.envs | Where-Object { (Split-Path $_ -Leaf) -eq $EnvironmentName })) {
  conda create -n $EnvironmentName "python=$PythonVersion" -y
}
conda run -n $EnvironmentName python -m pip install --upgrade pip
conda run -n $EnvironmentName python -m pip install -e "$PlatformRoot[web,dev]"
Write-Host "平台环境已就绪：$EnvironmentName" -ForegroundColor Green
Write-Host "下一步运行：.\scripts\configure.ps1"
