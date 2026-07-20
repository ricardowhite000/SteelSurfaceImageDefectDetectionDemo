param(
  [string]$EnvironmentName = "steel-review",
  [string]$Config = "config\platform.portable.yaml"
)
$ErrorActionPreference = "Stop"
$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $PlatformRoot
try {
  conda run -n $EnvironmentName steel-platform doctor --config $Config
  conda run -n $EnvironmentName steel-platform serve --config $Config
} finally { Pop-Location }
