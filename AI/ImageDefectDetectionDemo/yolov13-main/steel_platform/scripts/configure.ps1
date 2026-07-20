param(
  [ValidateSet("cpu", "cuda")]
  [string]$Runtime = "cpu",
  [string]$WorkspaceRoot,
  [string]$DemoPackage,
  [string]$PlatformEnvironmentName = "steel-review"
)
$ErrorActionPreference = "Stop"
$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$YoloRoot = (Resolve-Path (Join-Path $PlatformRoot "..")).Path
$YoloEnvironmentName = if ($Runtime -eq "cuda") { "yolo-runtime-cuda" } else { "yolo-runtime-cpu" }
$CondaCommand = Get-Command conda -ErrorAction SilentlyContinue
if (-not $CondaCommand) { throw "未找到conda，请先运行bootstrap.ps1。" }
$CondaExe = $CondaCommand.Source

function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "命令执行失败（退出码 $LASTEXITCODE）：$FilePath $($Arguments -join ' ')"
  }
}

function Resolve-CondaPython([string]$Name) {
  $Json = & $CondaExe env list --json
  if ($LASTEXITCODE -ne 0) { throw "读取Conda环境列表失败。" }
  $Environment = ($Json | ConvertFrom-Json).envs | Where-Object { (Split-Path $_ -Leaf) -eq $Name } | Select-Object -First 1
  if (-not $Environment) { throw "Conda环境不存在：$Name，请先运行bootstrap.ps1。" }
  $Python = Join-Path $Environment "python.exe"
  if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python解释器不存在：$Python" }
  return $Python
}

if (-not $WorkspaceRoot) { $WorkspaceRoot = Read-Host "请输入平台数据工作区目录" }
$WorkspaceRoot = [System.IO.Path]::GetFullPath($WorkspaceRoot)
foreach ($Relative in @("state", "artifacts", "logs", "tmp", "machine", "packages")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $WorkspaceRoot $Relative) | Out-Null
}

$PlatformPython = Resolve-CondaPython $PlatformEnvironmentName
$YoloPython = Resolve-CondaPython $YoloEnvironmentName

function Quote-Yaml([string]$Value) {
  return '"' + (($Value -replace '\\','/') -replace '"','\"') + '"'
}

$MachineConfig = Join-Path $PlatformRoot "config\machine.local.yaml"
$Lines = @(
  "workspace_root: $(Quote-Yaml $WorkspaceRoot)",
  "host: 127.0.0.1",
  "port: 8765",
  "yolo_python: $(Quote-Yaml $YoloPython)",
  "yolo_project_root: $(Quote-Yaml $YoloRoot)",
  ('device: "' + $(if ($Runtime -eq "cuda") { "0" } else { "cpu" }) + '"')
)
[System.IO.File]::WriteAllLines($MachineConfig, $Lines, [System.Text.UTF8Encoding]::new($false))
$PortableConfig = Join-Path $PlatformRoot "config\platform.portable.yaml"
Copy-Item -LiteralPath (Join-Path $PlatformRoot "config\platform.portable.example.yaml") -Destination $PortableConfig -Force

Invoke-Native $PlatformPython @("-m", "steel_platform.interfaces.cli", "db", "upgrade", "--config", $PortableConfig)
$Devices = if ($Runtime -eq "cuda") { @("--device", "0", "--device", "cpu") } else { @("--device", "cpu") }
$RuntimeArguments = @("-m", "steel_platform.interfaces.cli", "runtime", "upsert", "--name", "本机YOLO-$Runtime", "--python", $YoloPython, "--project-root", $YoloRoot, "--backend", $Runtime) + $Devices + @("--config", $PortableConfig)
Invoke-Native $PlatformPython $RuntimeArguments

if (-not $DemoPackage) { $DemoPackage = Read-Host "请输入Demo ZIP完整路径（暂不安装可直接按Enter）" }
if ($DemoPackage) {
  $DemoPackage = [System.IO.Path]::GetFullPath($DemoPackage)
  Invoke-Native $PlatformPython @("-m", "steel_platform.interfaces.cli", "package", "verify", "--file", $DemoPackage, "--config", $PortableConfig)
  Invoke-Native $PlatformPython @("-m", "steel_platform.interfaces.cli", "package", "install", "--file", $DemoPackage, "--config", $PortableConfig)
}

Write-Host "已生成本机配置：$MachineConfig" -ForegroundColor Green
Write-Host "工作区：$WorkspaceRoot" -ForegroundColor Green
Write-Host "下一步运行：.\scripts\doctor.ps1" -ForegroundColor Cyan
