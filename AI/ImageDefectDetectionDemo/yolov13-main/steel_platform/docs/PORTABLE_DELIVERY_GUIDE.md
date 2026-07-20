# Windows跨电脑交付与四步启动指南

更新时间：2026-07-20

## 1. 这份交付物解决什么问题

平台源码、个人工作区和模型数据不再绑在同一个文件夹里。GitHub私有仓库保存源码、脚本和依赖锁；团队网盘保存标准Demo ZIP；每台电脑单独创建自己的数据库、资产目录和运行环境。组员不需要复制开发者的SQLite、Conda环境或盘符路径。

交付后的数据关系如下：

```text
GitHub源码 + 网盘Demo包
          ↓
本机steel-review环境（网页、API、SQLite）
          ↓
本机yolo-runtime-cpu/cuda环境（PyTorch、YOLO）
          ↓
本机工作区（state、artifacts、logs、tmp、machine）
```

## 2. 前置条件

- Windows 10或Windows 11，PowerShell 5.1及以上。
- 已安装Git、GitHub Desktop和Miniconda/Anaconda；`conda`可在新PowerShell中执行。
- 至少15GB可用磁盘空间，建议工作区放在空间充足的非系统盘。
- 从GitHub私有仓库全新克隆源码。
- 从团队网盘下载 `steel-platform-demo-1.0.0.zip`，不要自行解压。

运行模式选择：

| 电脑 | 参数 | 说明 |
|---|---|---|
| NVIDIA显卡且驱动正常 | `cuda` | 安装CUDA 12.1版PyTorch，训练和推理使用GPU |
| AMD显卡、核显或无独显 | `cpu` | 不使用DirectML/ROCm，训练和推理都走CPU |

## 3. 先验证Demo包

团队发布包的固定信息：

- 文件名：`steel-platform-demo-1.0.0.zip`
- 大小：12,538,699字节
- SHA256：`563013d37eacfa1a85fdb331eb93f3561886b54a56b827396d219efbe2da34fa`

在下载目录执行：

```powershell
Get-FileHash -Algorithm SHA256 .\steel-platform-demo-1.0.0.zip
```

结果必须与上面的SHA256完全一致。不一致时不要安装，应重新下载并联系发布者。SHA256相同说明文件内容一致，不代表来源自动可信，因此包只从组内约定的网盘位置获取。

## 4. 四步安装

打开PowerShell，进入克隆仓库中的：

```text
AI\ImageDefectDetectionDemo\yolov13-main\steel_platform
```

若系统阻止本次脚本执行，可只为当前PowerShell会话临时放行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### 第一步：安装双环境

CPU电脑：

```powershell
.\scripts\bootstrap.ps1 -Runtime cpu
```

NVIDIA电脑：

```powershell
.\scripts\bootstrap.ps1 -Runtime cuda
```

脚本会幂等创建 `steel-review` 和对应的YOLO环境，核验依赖锁文件，并安装平台、PyTorch、仓库内Ultralytics和YOLO代码。已有同名环境时脚本复用环境并重新核对安装。

### 第二步：配置工作区并安装Demo

交互式运行：

```powershell
.\scripts\configure.ps1 -Runtime cpu
```

脚本会询问工作区目录和Demo ZIP完整路径。也可以一次传入：

```powershell
.\scripts\configure.ps1 -Runtime cpu `
  -WorkspaceRoot "D:\SteelPlatformWorkspace" `
  -DemoPackage "D:\TeamShare\steel-platform-demo-1.0.0.zip"
```

NVIDIA电脑把两个 `cpu` 改为 `cuda`。配置结果写入被Git忽略的 `config/machine.local.yaml` 和 `config/platform.portable.yaml`。工作区包含：

```text
state/       SQLite数据库
artifacts/   内容寻址资产与物化数据集
logs/        平台日志
tmp/         临时文件
machine/     Runtime Profile注册表
packages/    本机包记录
```

Demo安装完成后应登记60张图片、60份标签、48/12不可变数据集、一个基础权重和一个种子检测器。重复安装同一个包不会创建第二份项目。

### 第三步：严格诊断

```powershell
.\scripts\doctor.ps1
```

严格诊断检查Windows、PowerShell、Conda、Python、Torch、Ultralytics、CUDA/CPU能力、数据库迁移、磁盘空间、工作区权限、Demo资产哈希、端口和Runtime Profile。任何必需项失败都会返回非零退出码和修复线索。

### 第四步：启动

```powershell
.\scripts\start.ps1
```

脚本会再次运行严格诊断，通过后才启动 `http://127.0.0.1:8765`。浏览器访问 `/health/ready` 应返回 `ready`。停止服务请在运行终端按 `Ctrl+C`。

## 5. 首次功能验收

1. 数据中心能看到60张图片，并能打开图片和标注框。
2. 标注中心创建一个修订工单，修改一张图片并保存新标签版本。
3. 模型中心选择种子检测器，对单张图执行推理。
4. CPU电脑选择“CPU冒烟训练”，NVIDIA电脑选择GPU冒烟训练，均只运行1轮。
5. 任务结束后能查看日志、曲线、结果图片和模型文件。
6. 重启平台后项目、标签、模型和任务仍存在。

1轮冒烟训练只证明工作流可用，不能用来评价模型精度。CPU训练的30分钟目标必须以每台验收电脑的实际记录为准。

## 6. 常见故障

### 找不到conda

安装Miniconda/Anaconda后重新打开PowerShell；先确认 `conda --version` 可执行。不要把某位开发者的Anaconda绝对路径写入脚本。

### CUDA诊断失败

确认电脑是NVIDIA显卡、驱动正常，并运行：

```powershell
conda run -n yolo-runtime-cuda python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

若电脑不是NVIDIA显卡，重新按 `-Runtime cpu` 配置。

### Demo包哈希不一致

删除损坏下载，重新从团队网盘获取。不要修改ZIP内部文件，也不要绕过 `package verify`。

### 端口8765被占用

先关闭旧平台进程；若需换端口，在本机 `config/machine.local.yaml` 修改 `port` 后重新运行Doctor。不要提交此本机文件。

### 数据库需要升级

先备份，再执行：

```powershell
conda run -n steel-review steel-platform backup create --config config\platform.portable.yaml
conda run -n steel-review steel-platform db upgrade --config config\platform.portable.yaml
```

不要删除数据库、手改Alembic版本或复制别人的SQLite来“修复”。

### CPU任务时间过长

确认选择 `smoke_cpu`、`imgsz=320`、`batch=1`、`workers=0`、`amp=False`、`device=cpu`。任务超过30分钟会被平台终止，但日志和中间产物会保留。

## 7. 发布者更新Demo包

源码仓库只提交：

- `delivery/demo-package-index.json`
- `delivery/steel-platform-demo-1.0.0.zip.sha256`

真实ZIP放入仓库根的 `deliverables/` 后上传团队网盘；该目录已被Git忽略。每次变更图片、标签或模型都必须提高包版本、重新构建、重新计算SHA256并更新索引，不能用新内容覆盖同版本文件。

## 8. 交付边界

- 本次交付支持Windows本机单用户，不包含局域网多人协作。
- Java/Spring Boot骨架不是Python平台启动的前置条件。
- 不迁移开发者的私人数据库、历史任务、日志和1800张完整数据。
- Demo数据和模型仅在团队私有范围内使用；公开发布前必须复核数据、模型和代码许可证。
