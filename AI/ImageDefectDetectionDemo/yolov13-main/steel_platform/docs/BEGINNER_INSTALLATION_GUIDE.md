# 新组员下载安装与启动指南

这是一份面向第一次接触本平台的安装说明。你不需要了解FastAPI、SQLite、PyTorch或YOLO的内部代码，只需要按照顺序完成下载、安装、配置和启动。

如果中间某一步失败，请先停在当前步骤处理，不要删除数据库、修改Python源码或复制其他人的工作区。

## 一、安装完成后会得到什么

平台由三个彼此分开的部分组成：

```text
平台源码
  ├─ 网页、接口、安装脚本
  └─ YOLOv13项目代码

Conda环境
  ├─ steel-review：运行网页、接口和SQLite
  └─ yolo-runtime-cpu / yolo-runtime-cuda：训练与推理

个人工作区
  ├─ state：数据库
  ├─ artifacts：图片、标签、模型等平台资产
  ├─ logs：日志
  ├─ machine：本机运行环境信息
  └─ tmp：临时文件
```

源码可以更新，Conda环境可以重建，但个人工作区保存着本机的项目、标签和任务记录，不要随意删除。

## 二、开始前准备

请确认电脑具备以下条件：

- Windows 10或Windows 11。
- 已安装Anaconda或Miniconda。
- 已安装GitHub Desktop，并拥有一个可以登录的GitHub账号。
- 至少15GB可用磁盘空间。
- 安装期间能够访问Conda、PyPI和PyTorch下载地址。
- 已获得团队提供的 `steel-platform-demo-1.0.0.zip`。

已有的YOLOv13 Conda环境可以保留。本指南会另外创建统一环境，避免不同成员的Python、Torch和Ultralytics版本互相影响。

### 1. 检查Conda

打开PowerShell，输入：

```powershell
conda --version
```

如果能看到类似 `conda 25.x.x` 的版本号，说明Conda可以使用。

如果提示“conda不是内部或外部命令”或“无法识别conda”，可以先在Anaconda Prompt中执行：

```powershell
conda init powershell
```

执行后关闭所有PowerShell窗口，再重新打开并检查 `conda --version`。

### 2. 判断使用CPU还是CUDA

在PowerShell中输入：

```powershell
nvidia-smi
```

- 能正常显示NVIDIA显卡和驱动信息：选择 `cuda`。
- 使用AMD显卡、核显、没有独立显卡，或者不确定：选择 `cpu`。
- `nvidia-smi`报错时不要选择CUDA，先使用CPU完成安装。

CPU模式也可以训练和推理，只是速度通常比NVIDIA GPU慢。

## 三、获取平台源码：GitHub Desktop主流程

平台仓库是Private仓库。Private表示只有被邀请并接受授权的GitHub账号才能查看或下载代码。

### 第1步：把GitHub用户名发给管理员

登录GitHub后，点击右上角头像即可看到账号用户名。把用户名发给仓库管理员，不要发送密码或访问令牌。

### 第2步：管理员发送邀请

管理员需要在仓库网页中打开：

```text
Settings → Collaborators and teams → Add people
```

然后使用组员的GitHub用户名或邮箱发送邀请。

### 第3步：接受邀请

组员需要在GitHub通知或邮件中点击接受。只收到仓库网址但没有接受邀请，仍然无法下载Private仓库。

如果打开仓库显示404，通常表示：

- 当前GitHub账号没有被邀请。
- 邀请尚未接受。
- GitHub Desktop登录了另一个账号。

Private仓库的权限错误经常表现为404，不代表仓库真的不存在。

### 第4步：使用GitHub Desktop克隆

1. 打开GitHub Desktop。
2. 登录已经接受邀请的GitHub账号。
3. 点击 `File → Clone repository`。
4. 切换到 `GitHub.com` 标签。
5. 在列表中选择团队提供的平台仓库。
6. 在 `Local path` 选择一个空间充足、自己有写权限的目录。
7. 点击 `Clone`。

如果发布者指定了分支，请在GitHub Desktop顶部的 `Current branch` 中切换；没有特别说明时使用默认分支。

克隆完成后，在GitHub Desktop中点击：

```text
Repository → Show in Explorer
```

然后依次进入：

```text
AI\ImageDefectDetectionDemo\yolov13-main\steel_platform
```

你应该能够看到：

```text
scripts
config
docs
requirements
src
README.md
pyproject.toml
```

## 四、无法使用GitHub时的备用源码ZIP

如果暂时无法获得Private仓库权限，团队网盘应提供：

```text
steel-platform-source-1.0.0.zip
steel-platform-demo-1.0.0.zip
SHA256SUMS.txt
```

其中：

- `source` ZIP是平台源码。
- `demo` ZIP是60张图片、标签和模型组成的演示数据包。
- `SHA256SUMS.txt`记录两个ZIP的校验值。

### 第1步：检查下载是否完整

在下载文件所在文件夹打开PowerShell，分别执行：

```powershell
Get-FileHash -Algorithm SHA256 .\steel-platform-source-1.0.0.zip
Get-FileHash -Algorithm SHA256 .\steel-platform-demo-1.0.0.zip
```

把显示的Hash与 `SHA256SUMS.txt`逐字比较。只要有一个字符不同，就不要继续安装，应重新下载。

当前Demo包 `steel-platform-demo-1.0.0.zip` 的SHA256是：

```text
563013d37eacfa1a85fdb331eb93f3561886b54a56b827396d219efbe2da34fa
```

### 第2步：解压源码ZIP

只解压 `steel-platform-source-1.0.0.zip`，不要解压Demo ZIP。

建议把源码解压到路径简单、可写且空间充足的位置。解压后检查目录层级，不要出现多层重复目录，例如：

```text
错误：platform-source\platform-source\platform-source\AI\...
正确：platform-source\AI\ImageDefectDetectionDemo\...
```

进入源码中的：

```text
AI\ImageDefectDetectionDemo\yolov13-main\steel_platform
```

后面的安装步骤与GitHub Desktop方式完全相同。

备用ZIP可以正常运行平台，但无法像GitHub Desktop那样方便地获取后续代码更新。

## 五、在正确目录打开PowerShell

在文件资源管理器中打开 `steel_platform` 文件夹，然后使用以下任一方式：

- 在文件夹空白处按住Shift并点击鼠标右键，选择“在终端中打开”。
- 点击资源管理器地址栏，输入 `powershell` 后按Enter。

在PowerShell中执行：

```powershell
Get-Location
Get-ChildItem .\scripts
```

`Get-ChildItem`应显示：

```text
bootstrap.ps1
configure.ps1
doctor.ps1
start.ps1
```

如果找不到 `scripts`，说明当前目录不正确，请不要继续。

### PowerShell禁止运行脚本怎么办

如果看到“系统禁止运行脚本”等提示，只对当前窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

关闭该PowerShell窗口后，这个临时设置会自动失效。

## 六、四步完成安装

下面四步必须按顺序执行。安装命令可以重复运行，第一次中断后通常不需要删除已经创建的环境。

### 第一步：安装统一环境

#### CPU电脑

```powershell
.\scripts\bootstrap.ps1 -Runtime cpu
```

脚本会创建：

```text
steel-review
yolo-runtime-cpu
```

#### NVIDIA电脑

```powershell
.\scripts\bootstrap.ps1 -Runtime cuda
```

脚本会创建：

```text
steel-review
yolo-runtime-cuda
```

这一过程会安装Python 3.11、平台依赖、PyTorch和YOLO依赖，第一次运行可能需要较长时间。请保持网络连接，不要关闭PowerShell。

成功时，末尾会出现类似：

```text
平台环境已就绪：steel-review
YOLO环境已就绪：yolo-runtime-cpu（cpu）
```

或者：

```text
YOLO环境已就绪：yolo-runtime-cuda（cuda）
```

如果安装中断，网络恢复后重新执行同一条命令即可。

### 第二步：创建个人工作区并安装Demo

CPU电脑执行：

```powershell
.\scripts\configure.ps1 -Runtime cpu
```

NVIDIA电脑执行：

```powershell
.\scripts\configure.ps1 -Runtime cuda
```

脚本会依次询问两个问题。

#### 问题1：平台数据工作区目录

工作区应放在源码目录之外，并且具有足够空间。例如可以新建一个名为 `SteelPlatformWorkspace` 的普通文件夹，然后输入它的完整路径。

工作区用于保存数据库、标签、模型和任务记录。以后更新源码时不要删除这个目录。

#### 问题2：Demo ZIP完整路径

输入团队提供的：

```text
steel-platform-demo-1.0.0.zip
```

的完整路径。

注意：

- Demo ZIP不要提前解压。
- 路径必须指向ZIP文件本身，而不是下载文件夹。
- 如果复制出来的路径前后带双引号，请删除最外层双引号后再粘贴。
- 文件名和扩展名必须完整。

脚本会自动完成：

1. 创建工作区目录。
2. 创建或升级SQLite数据库。
3. 登记本机CPU或CUDA运行环境。
4. 验证Demo ZIP的文件清单和SHA256。
5. 安装Demo项目、数据集和模型。

安装成功后，Demo中应有：

```text
60张图片
60份标签
48张训练图片
12张验证图片
1个基础权重
1个种子检测器
```

重复安装同一个Demo包不会创建多份相同项目。

### 第三步：运行环境诊断

执行：

```powershell
.\scripts\doctor.ps1
```

Doctor会检查：

- Conda和Python是否可用。
- 平台和YOLO依赖能否导入。
- CPU或CUDA能力是否匹配。
- 数据库版本是否正确。
- 工作区是否可写。
- Demo图片、标签和模型是否完整。
- 8765端口是否可用。

成功时输出中应包含：

```json
"ready": true
```

如果输出中是 `"ready": false`，请查看 `failed_checks`，并先按照本文“常见故障”处理。

### 第四步：启动平台

执行：

```powershell
.\scripts\start.ps1
```

启动脚本会再次运行Doctor。检查通过后，终端会显示类似：

```text
Uvicorn running on http://127.0.0.1:8765
```

这个PowerShell窗口必须保持打开。关闭窗口或按 `Ctrl+C` 会停止平台。

## 七、第一次打开平台

打开Chrome或Edge，在地址栏输入：

```text
http://127.0.0.1:8765
```

也可以先检查：

```text
http://127.0.0.1:8765/health/ready
```

正常结果为：

```json
{"status":"ready"}
```

第一次使用只需完成以下基本检查，不要求运行pytest或正式模型训练：

- [ ] 页面可以正常打开。
- [ ] 顶部能够选择Demo项目。
- [ ] 数据中心显示60张图片。
- [ ] 打开图片详情能够看到标注框。
- [ ] 数据集显示48张训练图片和12张验证图片。
- [ ] 模型库显示基础权重和种子检测器。
- [ ] 能进入标注中心创建修订工单，或者在模型中心创建单图推理任务。

完成这些项目即可认为平台已经基本安装成功。

## 八、以后如何启动和停止

首次安装完成后，不需要每天重复运行 `bootstrap.ps1` 和 `configure.ps1`。

以后启动时：

1. 进入源码中的 `steel_platform` 文件夹。
2. 在该文件夹打开PowerShell。
3. 执行：

```powershell
.\scripts\start.ps1
```

停止平台：

```text
回到启动平台的PowerShell窗口，按Ctrl+C。
```

请不要在平台运行期间复制、移动或覆盖SQLite数据库。

以下工作区目录不要删除：

```text
state
artifacts
machine
```

工作区是个人运行数据，不应该放进Git仓库，也不应该与其他成员共用同一个工作区。

## 九、通过GitHub Desktop获取更新

更新前先在平台终端按 `Ctrl+C` 停止服务，然后：

1. 打开GitHub Desktop。
2. 选择平台仓库。
3. 点击 `Fetch origin`。
4. 如果有更新，点击 `Pull origin`。
5. 回到 `steel_platform` 目录。
6. 重新执行一次对应的安装命令，以便补充可能新增的依赖：

```powershell
.\scripts\bootstrap.ps1 -Runtime cpu
```

或：

```powershell
.\scripts\bootstrap.ps1 -Runtime cuda
```

然后正常执行：

```powershell
.\scripts\start.ps1
```

不要用新源码覆盖或删除个人工作区。

## 十、常见故障

### 1. Private仓库显示404

这是最常见的权限问题。依次确认：

1. 管理员已经邀请当前GitHub账号。
2. 当前账号已经接受邀请。
3. 浏览器和GitHub Desktop登录的是同一个账号。
4. 仓库管理员没有撤销权限。

无法及时解决时，使用团队网盘的源码ZIP备用方案。

### 2. GitHub Desktop中找不到仓库

先在浏览器中确认已经接受邀请，再在GitHub Desktop中退出账号并重新登录，然后重新打开：

```text
File → Clone repository → GitHub.com
```

### 3. `conda`无法识别

在Anaconda Prompt中执行：

```powershell
conda init powershell
```

关闭并重新打开PowerShell。如果仍然失败，检查Anaconda或Miniconda是否完整安装。

### 4. PowerShell禁止脚本

在当前PowerShell执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

然后重新运行脚本。

### 5. 不知道选择CPU还是CUDA

执行：

```powershell
nvidia-smi
```

只有NVIDIA显卡且该命令正常时才选择CUDA。其他情况选择CPU。

### 6. CUDA诊断失败

可能原因包括NVIDIA驱动异常、显卡不支持或安装时选择错误。为了先把平台跑起来，可以切换到CPU流程：

```powershell
.\scripts\bootstrap.ps1 -Runtime cpu
.\scripts\configure.ps1 -Runtime cpu
```

原来的CUDA环境可以保留，不需要手动删除。

### 7. Demo ZIP路径错误

确认输入的是ZIP文件的完整路径，而不是目录。可以先执行：

```powershell
Test-Path "<Demo ZIP完整路径>"
```

返回 `True` 才表示文件存在。`<Demo ZIP完整路径>`需要替换为实际路径。

### 8. Demo包SHA256不一致

不要绕过校验，也不要修改ZIP内部文件。删除当前下载，重新从团队网盘获取。

### 9. Doctor显示 `ready: false`

先查找输出中的：

```text
failed_checks
```

常见项目：

- `database`：配置步骤未完成，重新运行 `configure.ps1`。
- `runtime_profiles`：CPU/CUDA环境不可用，重新运行 `bootstrap.ps1`和 `configure.ps1`。
- `demo_package`：没有正确安装Demo ZIP，重新运行 `configure.ps1`并输入ZIP路径。
- `port`：8765端口已被其他程序或旧平台占用。
- `artifact_integrity`：资产缺失或内容变化，应重新下载并安装Demo，不要手动修改资产文件。

### 10. 8765端口被占用

先检查是否已经有一个平台PowerShell窗口正在运行。如果有，直接使用已经启动的平台，或者在旧窗口按 `Ctrl+C` 后重新启动。

不要同时启动多个平台进程使用同一个工作区。

### 11. 浏览器打不开平台

确认：

- `start.ps1`所在PowerShell窗口仍然打开。
- 终端中出现了 `Uvicorn running`。
- 访问的是 `http://127.0.0.1:8765`，不是HTTPS。
- Doctor显示 `ready: true`。

可以先访问健康检查地址判断服务是否运行。

### 12. 页面打开但没有项目

通常表示配置时没有安装Demo包。停止平台后重新运行：

```powershell
.\scripts\configure.ps1 -Runtime cpu
```

或CUDA版本，并在提示时输入未解压的Demo ZIP完整路径。

### 13. 不小心解压了Demo ZIP

平台需要原始ZIP。保留或重新下载 `steel-platform-demo-1.0.0.zip`，配置时选择ZIP文件，不要选择解压后的目录。

### 14. 源码目录重复嵌套

如果命令提示找不到 `scripts`，使用文件资源管理器搜索 `bootstrap.ps1`，找到后退回其上一级 `steel_platform` 文件夹，再打开PowerShell。

### 15. 安装中断或网络断开

恢复网络后重新执行失败的那条命令。安装脚本会复用已经创建的Conda环境和已经下载的依赖。

不要因为一次失败就删除整个源码或个人工作区。

## 十一、需要向负责人反馈什么

如果仍无法解决，请把以下信息发给负责人：

- 使用CPU还是CUDA。
- Windows版本。
- 显卡型号。
- 失败的是四步中的哪一步。
- PowerShell中从错误开始到结束的完整文本。
- `doctor.ps1`输出中的 `failed_checks`。

不要只发送“打不开”或“报错了”，也不要发送GitHub密码、访问令牌或其他个人敏感信息。

## 十二、一页式安装检查表

- [ ] Anaconda或Miniconda已安装。
- [ ] `conda --version`能够运行。
- [ ] 已判断使用CPU还是CUDA。
- [ ] 已接受Private仓库邀请并通过GitHub Desktop克隆；或已下载并校验源码ZIP。
- [ ] 已下载且未解压Demo ZIP。
- [ ] PowerShell当前目录是 `steel_platform`。
- [ ] `bootstrap.ps1`执行成功。
- [ ] `configure.ps1`执行成功。
- [ ] `doctor.ps1`显示 `ready: true`。
- [ ] `start.ps1`显示Uvicorn运行地址。
- [ ] 浏览器能够打开平台。
- [ ] 能看到60张图片和检测框。
- [ ] 已记住以后只需运行 `start.ps1`。
