param(
  [string]$YoloPython,
  [string]$WorkspaceRoot
)
$ErrorActionPreference = "Stop"
$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $YoloPython) { $YoloPython = Read-Host "请输入YOLO环境 python.exe 的完整路径" }
if (-not (Test-Path -LiteralPath $YoloPython -PathType Leaf)) { throw "Python解释器不存在：$YoloPython" }
if (-not $WorkspaceRoot) { $WorkspaceRoot = Read-Host "请输入平台数据工作区目录" }
New-Item -ItemType Directory -Force -Path $WorkspaceRoot | Out-Null
$MachineConfig = Join-Path $PlatformRoot "config\machine.local.yaml"
$Template = Get-Content -LiteralPath (Join-Path $PlatformRoot "config\machine.example.yaml") -Raw -Encoding UTF8
$Template = $Template -replace '(?m)^yolo_python:.*$', ('yolo_python: "' + ($YoloPython -replace '\\','/') + '"')
Set-Content -LiteralPath $MachineConfig -Value $Template -Encoding UTF8
$PortableConfig = Join-Path $PlatformRoot "config\platform.portable.yaml"
Copy-Item -LiteralPath (Join-Path $PlatformRoot "config\platform.portable.example.yaml") -Destination $PortableConfig -Force
Write-Host "已生成本机配置：$MachineConfig" -ForegroundColor Green
Write-Host "请按实际目录修改 machine.local.yaml 中的数据路径，然后运行 doctor.ps1。"
