param(
  [ValidateSet("cpu", "cuda")]
  [string]$Runtime = "cpu",
  [string]$PlatformEnvironmentName = "steel-review",
  [string]$PythonVersion = "3.11.15"
)
$ErrorActionPreference = "Stop"
$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$YoloRoot = (Resolve-Path (Join-Path $PlatformRoot "..")).Path
$YoloEnvironmentName = if ($Runtime -eq "cuda") { "yolo-runtime-cuda" } else { "yolo-runtime-cpu" }

$CondaCommand = Get-Command conda -ErrorAction SilentlyContinue
if (-not $CondaCommand) {
  throw "未找到conda，请先安装Anaconda或Miniconda并重新打开PowerShell。"
}
$CondaExe = $CondaCommand.Source

function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "命令执行失败（退出码 $LASTEXITCODE）：$FilePath $($Arguments -join ' ')"
  }
}

function Get-CondaEnvironments {
  $Json = & $CondaExe env list --json
  if ($LASTEXITCODE -ne 0) { throw "读取Conda环境列表失败。" }
  return ($Json | ConvertFrom-Json).envs
}

function Resolve-CondaPython([string]$Name) {
  $Environment = Get-CondaEnvironments | Where-Object { (Split-Path $_ -Leaf) -eq $Name } | Select-Object -First 1
  if (-not $Environment) { throw "Conda环境不存在：$Name" }
  $Python = Join-Path $Environment "python.exe"
  if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python解释器不存在：$Python" }
  return $Python
}

function Ensure-CondaEnvironment([string]$Name) {
  if (-not (Get-CondaEnvironments | Where-Object { (Split-Path $_ -Leaf) -eq $Name })) {
    Invoke-Native $CondaExe @("create", "-n", $Name, "python=$PythonVersion", "pip", "-y")
  }
}

function Assert-LockIntegrity {
  $ManifestPath = Join-Path $PlatformRoot "requirements\locks.sha256.json"
  $Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  foreach ($Property in $Manifest.files.PSObject.Properties) {
    $LockPath = Join-Path $PlatformRoot ("requirements\" + $Property.Name)
    if (-not (Test-Path -LiteralPath $LockPath -PathType Leaf)) { throw "依赖锁不存在：$LockPath" }
    $Actual = (Get-FileHash -LiteralPath $LockPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne ([string]$Property.Value).ToLowerInvariant()) {
      throw "依赖锁校验失败：$($Property.Name)"
    }
  }
}

Assert-LockIntegrity
Ensure-CondaEnvironment $PlatformEnvironmentName
Ensure-CondaEnvironment $YoloEnvironmentName
$PlatformPython = Resolve-CondaPython $PlatformEnvironmentName
$YoloPython = Resolve-CondaPython $YoloEnvironmentName

$PlatformLock = Join-Path $PlatformRoot "requirements\platform-win-py311.lock.txt"
$YoloLock = Join-Path $PlatformRoot "requirements\yolo-common-win-py311.lock.txt"
$TorchLock = if ($Runtime -eq "cuda") {
  Join-Path $PlatformRoot "requirements\yolo-cuda121-win-py311.lock.txt"
} else {
  Join-Path $PlatformRoot "requirements\yolo-cpu-win-py311.lock.txt"
}
Invoke-Native $PlatformPython @("-m", "pip", "install", "--upgrade", "pip")
if (Test-Path -LiteralPath $PlatformLock) {
  Invoke-Native $PlatformPython @("-m", "pip", "install", "--no-deps", "-r", $PlatformLock)
}
Invoke-Native $PlatformPython @("-m", "pip", "install", "-e", "$PlatformRoot[web,dev]", "--no-deps")

Invoke-Native $YoloPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Native $YoloPython @("-m", "pip", "install", "--no-deps", "-r", $TorchLock)
if (Test-Path -LiteralPath $YoloLock) {
  Invoke-Native $YoloPython @("-m", "pip", "install", "--no-deps", "-r", $YoloLock)
}
Invoke-Native $YoloPython @("-m", "pip", "install", "-e", $YoloRoot, "--no-deps")
Invoke-Native $YoloPython @("-m", "pip", "install", "-e", $PlatformRoot, "--no-deps")
Invoke-Native $YoloPython @("-c", "import torch, ultralytics, steel_platform; print(torch.__version__, ultralytics.__version__)")

Write-Host "平台环境已就绪：$PlatformEnvironmentName" -ForegroundColor Green
Write-Host "YOLO环境已就绪：$YoloEnvironmentName（$Runtime）" -ForegroundColor Green
Write-Host "下一步运行：.\scripts\configure.ps1 -Runtime $Runtime"
