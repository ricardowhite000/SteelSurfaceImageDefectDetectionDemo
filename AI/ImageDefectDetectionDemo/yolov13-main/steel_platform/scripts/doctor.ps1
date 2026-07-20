param(
  [string]$EnvironmentName = "steel-review",
  [string]$Config = "config\platform.portable.yaml"
)
$ErrorActionPreference = "Stop"
$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CondaCommand = Get-Command conda -ErrorAction SilentlyContinue
if (-not $CondaCommand) { throw "未找到conda。" }
$Json = & $CondaCommand.Source env list --json
if ($LASTEXITCODE -ne 0) { throw "读取Conda环境列表失败。" }
$Environment = ($Json | ConvertFrom-Json).envs | Where-Object { (Split-Path $_ -Leaf) -eq $EnvironmentName } | Select-Object -First 1
if (-not $Environment) { throw "Conda环境不存在：$EnvironmentName" }
$Python = Join-Path $Environment "python.exe"
Push-Location $PlatformRoot
try {
  & $Python -m steel_platform.interfaces.cli doctor --strict --json --config $Config
  if ($LASTEXITCODE -ne 0) { throw "严格诊断失败（退出码 $LASTEXITCODE）。" }
} finally { Pop-Location }
