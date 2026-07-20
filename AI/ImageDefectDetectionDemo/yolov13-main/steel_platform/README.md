# 钢材表面异常视觉检测平台

这是一个 Windows 本地可运行的多项目机器视觉数据闭环。当前页面按业务顺序组织为“数据中心 → 标注中心 → 模型中心 → 监测中心”，覆盖登记图片、初始标注、候选复核、不可变数据集、训练/推理与状态追踪。它是教学与流程验证平台，不以小样本指标替代生产验收。

## 组员四步启动（推荐入口）

先从团队网盘下载 `steel-platform-demo-1.0.0.zip`，再进入本仓库的 `steel_platform` 目录。AMD或无独立显卡电脑使用 `cpu`；NVIDIA电脑可以使用 `cuda`：

```powershell
.\scripts\bootstrap.ps1 -Runtime cpu
.\scripts\configure.ps1 -Runtime cpu
.\scripts\doctor.ps1
.\scripts\start.ps1
```

脚本依次完成“双Conda环境安装 → 本机工作区配置与Demo导入 → 严格诊断 → 启动”。首次部署、SHA256校验、CPU/GPU差异和故障恢复见[《Windows跨电脑交付与四步启动指南》](docs/PORTABLE_DELIVERY_GUIDE.md)，实机测试请填写[《可复现验收记录模板》](docs/REPRODUCIBILITY_ACCEPTANCE_TEMPLATE.md)。

下一阶段的人工操作、指标解读、误差分析和v3迭代路线见[《钢材缺陷模型下一阶段迭代：学习与操作手册》](docs/NEXT_STAGE_MODEL_ITERATION_GUIDE.md)。通用条件筛选工单现已进入标注中心，可绑定一次推理运行并按类别、风险、置信度和数量创建可复现队列。

## 当前模块边界

- **数据中心**：项目、数据源、集合、图片与不可变数据集。
- **标注中心**：初始人工标注、机器候选复核、条件筛选、已完成档案与修订工单。
- **模型中心**：训练、评估、流式推理、任务日志、模型库与结果视图。
- **监测中心**：真实读取标注、任务、模型、数据集和推理状态；相机与报警留待后续。

Python平台仍是当前唯一业务写入端。`services/business-service/` 是 Java 21 + Spring Boot + MySQL 学习骨架，只通过幂等领域事件建立只读投影，不与 SQLite 双写。

标签历史精度审计与安全修复：

```powershell
steel-platform annotations audit --config config\platform.local.yaml
steel-platform annotations repair-rounding --config config\platform.local.yaml --dry-run
steel-platform annotations repair-rounding --config config\platform.local.yaml --apply
```

可移植配置推荐使用 `config/platform.portable.example.yaml`、`project.example.yaml` 与本机私有的 `machine.local.yaml`。Windows组员可从 `scripts/bootstrap.ps1`、`configure.ps1`、`doctor.ps1`、`start.ps1` 开始。

## 1. 背景知识

原图、机器候选和人工修订是不同证据。平台不覆盖原文件：图片用 SHA256 登记，人工操作创建新的 `annotation_revision`，数据集版本固定其图片、标签版本和 train/val 角色。这样才能追溯一次模型使用了什么数据、一个框何时被谁修改。

平台按项目隔离资源：`project → source / collection / review round / dataset / job / model / inference run`。任何队列、资产内容和集合操作都必须同时携带项目标识；复核条目还必须携带复核任务标识。

## 2. 当前目标

当前类别固定为 `Cr、In、Pa、PS、RS、Sc`。目标是完成可审计闭环，而不是宣称生产精度：

1. 在项目内导入或挂载图片；
2. 从集合创建有范围的复核任务；
3. 复核并导出完成报告；
4. 发布数据集、登记训练与推理结果；
5. 用推理差异创建下一轮抽查。

首轮与审计轮的数量由任务自身保存，页面和接口不会把不同轮次相加显示成一个“当前队列”。

## 3. 架构

```text
src/steel_platform/
├─ domain/          # 领域对象、状态、端口
├─ application/     # 项目、导入、集合、复核、维护用例
├─ infrastructure/  # SQLite/Alembic、资产存储、YOLO 编解码
└─ interfaces/      # Typer CLI、FastAPI、浏览器界面
```

SQLite 保存资源关系和版本元数据；`artifact_root` 保存平台托管的内容寻址对象与物化数据集。外部挂载源保持只读，平台只记录路径、清单和哈希；managed 导入则复制到平台资产目录。

### 配置和本地路径

复制 `config/platform.example.yaml` 为 `config/platform.local.yaml` 后再填写本机路径。配置中所有相对路径都以**配置文件所在目录**解析，例如 `artifacts` 表示 `config/artifacts`，`sqlite:///platform.db` 表示 `config/platform.db`。因此请使用相对路径或你自己的本机路径，切勿把盘符、用户名、共享目录或真实数据路径提交到 Git。

旧工作区的手工安装方式（新组员应优先使用上面的四步脚本）：

```powershell
conda run -n steel-review python -m pip install -e ".\steel_platform[web,dev]"
```

## 4. 初始化和切换项目

先升级空白数据库，再从配置初始化项目。命令中的 `<CONFIG>` 是你的 `config/platform.local.yaml`；不要把真实盘符写进可提交配置。

```powershell
steel-platform db upgrade --config <CONFIG>
steel-platform project init --config <CONFIG>
steel-platform project list --json --config <CONFIG>
steel-platform project check --config <CONFIG>
```

在浏览器顶部项目选择器中切换项目。需要项目范围的CLI会显式要求 `--project <PROJECT_ID>`；`project check` 则检查当前配置关联的全部来源。切换后，概览、资源树、集合和复核任务只显示该项目的数据。

## 5. Managed 导入

managed 模式把选择的图片写入平台托管存储，之后删除原始临时目录也不影响打开资产。浏览器中选择项目后使用“导入文件夹”，选择 **Managed**，检查预览和校验结果，再提交。浏览器受到安全限制，不能把本机文件夹的真实绝对路径交给服务端，因此网页只支持 managed 导入；external 必须使用下面的 CLI 命令。

当前managed导入只通过浏览器向导开放，CLI没有 `import managed` 命令。导入前后可对临时源目录计算SHA256；managed导入只读取源文件，不会改写源文件。不要把真实生产图片复制到仓库中。

## 6. External 挂载和重新绑定

external模式只登记文件夹、相对路径和哈希，图片仍保留在原位置，适合受保护的大型原始图片库。当前发布版CLI只开放已有来源的核验和重新绑定：

```powershell
steel-platform source verify --project <PROJECT_ID> --source <SOURCE_ID> --config <CONFIG>
steel-platform source rebind --project <PROJECT_ID> --source <SOURCE_ID> --path <MOVED_FOLDER> --config <CONFIG>
```

重新绑定会比较已登记资产清单；任何已登记文件缺失或哈希改变都会失败，而不会悄悄把新文件当成旧资产。当前没有新建external来源的公共CLI入口，不要使用旧文档中的 `import external` 示例，也不能直接改数据库路径。

### 页面一直显示“加载项目”

平台会为入口脚本生成内容版本号，并对 HTML/静态模块发送 `Cache-Control: no-store`，正常重启后不再命中旧脚本。如果浏览器仍保留修复前页面，请先确认终端中服务没有退出，然后按 `Ctrl+F5` 强制刷新一次。`GET /favicon.ico 204` 是正常响应；旧版本出现的 favicon 404 不会影响业务功能。

如果仍无法加载，依次检查：

```powershell
steel-platform project check --config config\platform.local.yaml
steel-platform artifacts verify --config config\platform.local.yaml
```

然后打开 `http://127.0.0.1:8765/health/ready`。返回 `{"status":"ready"}` 才表示数据库版本、资产目录与服务均已就绪。

## 7. 创建集合

集合是在单个项目内组织资产的视图。managed导入成功后会创建对应集合，可在数据中心查看。当前CLI没有 `collection create/add` 命令；标注中心可从数据源或集合创建初始标注工单，也可从推理运行创建条件筛选工单。集合不能引用另一个项目的资产。

## 8. 进入有范围的复核任务

现有复核轮次可以在浏览器中按项目和轮次独立进入。CLI可列出轮次和导出进度：

```powershell
steel-platform review round-list --project <PROJECT_ID> --json --config <CONFIG>
steel-platform review export --project <PROJECT_ID> --round-id <ROUND_ID> --output .\review-progress.csv --config <CONFIG>
```

`review round-create --round <NUMBER>` 是当前六类主动学习流程的配置驱动命令，不支持任意集合、类别和阈值；不要把它当作通用队列创建器。

在浏览器中先选择项目，再选择复核任务。滚轮缩放，`Alt+左键` 或中键平移，`R` 绘框，`Delete` 删除框，`Ctrl+Z/Ctrl+Y` 撤销/重做；`A` 接受、`S` 修正、`D` 存疑、`X` 排除。排除必须写原因，存疑会保存草稿。接口使用版本号和幂等键，过期编辑不会覆盖较新的人工修订。

## 9. 完成报告

每个复核任务独立导出进度；不要用项目内其他轮次的数量替代本轮结果：

```powershell
steel-platform review export-progress --project <PROJECT_ID> --round-id <ROUND_ID> --output .\round-report.csv --config <CONFIG>
steel-platform project check --config <CONFIG>
steel-platform artifacts verify --config <CONFIG>
```

报告应记录任务 ID、目标数、已接受/修正/存疑/排除数、导出时间和配置版本。对于受保护的外部源，保留导入前后哈希清单；若两次不同，先调查来源变更，再继续发布。

## 10. 迁移和恢复

先停止服务，再升级；升级前始终创建备份。不要在运行服务时复制或覆盖 SQLite 文件。

```powershell
steel-platform backup create --config <CONFIG>
steel-platform db upgrade --config <CONFIG>
steel-platform project check --config <CONFIG>
steel-platform artifacts verify --config <CONFIG>
```

恢复演练应在备份副本或新的测试目录进行：恢复后运行 `project check`、`artifacts verify` 和各轮 `export-progress`，确认数据库版本、资产哈希和任务数量正确。遇到 `database_upgrade_required` 时，先备份再升级；不要删除表或手改迁移版本。

### GitHub Desktop 提交流程

1. 在 GitHub Desktop 打开仓库，确认只勾选代码、测试、`README.md` 和 `platform.example.yaml`。
2. 明确取消勾选 `config/platform.local.yaml`、SQLite、`artifacts/`、图片、模型与任何导出的 CSV。
3. 在 Changes 中检查 diff；通过测试后填写简短摘要并 Commit to 当前分支。
4. Push branch 后再创建 Pull Request。提交前不要把本机盘符、用户名或生产路径写进配置和文档。

## 11. 模型工作台：人工完成训练与推理

模型工作台把原先由脚本或大模型代为执行的步骤暴露为可检查、可确认、可追溯的人工流程。平台不接收任意 Shell 字符串；浏览器只提交数据集、模型、来源、预设和白名单参数，后端再生成参数数组与不可变命令快照。

### 11.1 启动与进入

```powershell
conda activate steel-review
cd <YOLO_PROJECT>\steel_platform
steel-platform serve --config config\platform.local.yaml
```

打开 `http://127.0.0.1:8765`，选择项目，再点击顶部“模型中心”。第一次升级旧工作区时必须先执行：

```powershell
steel-platform backup create --config config\platform.local.yaml
steel-platform db upgrade --config config\platform.local.yaml
steel-platform project check --config config\platform.local.yaml
```

### 11.2 训练操作顺序

1. 在“新建训练”选择不可变数据集和父模型。
2. 先选“冒烟训练（1轮）”，检查 `imgsz=640`、`batch=4`、`workers=0` 和设备。
3. 创建草稿后进入“任务中心”，点击“生成并冻结命令”。
4. 阅读命令预览；确认数据集、权重、输出目录和参数无误。
5. 点击“打开 PowerShell”。外部终端会再次显示任务和原始YOLO命令，等待人工按 Enter；输入 `C` 可取消。
6. 回到任务中心查看状态、进度和UTF-8日志。成功后结果区展示训练曲线、图表、`results.csv`、`best.pt` 和 `last.pt`。
7. 冒烟任务成功后再新建“正式训练（100轮）”。冒烟模型只验证流程，不作为正式性能结论。

平台为每个任务创建独立 `workbench/jobs/<JOB_ID>/`，并把无扩展名的对象存储权重原子物化为任务专用 `inputs/model.pt`；物化前后SHA256必须一致。GPU任务使用单设备锁，避免多个训练或推理任务同时占满显存。

### 11.3 推理操作顺序

1. 在“新建推理”选择通过六类模式校验的缺陷检测器。
2. 来源可以是已登记数据源，也可以是单张图片或视频：文件详情页可点击“使用此图片创建推理任务”，也可以在工作台上传图片/视频或填写已有资产编号。
3. 常规推理默认 `conf=0.25`；辅助标注默认 `conf=0.20`、复核阈值 `0.40`；视频预设逐帧处理。
4. 所有推理固定 `batch=1`、`stream=True`。嵌套数据源会在任务目录生成经过哈希校验的扁平输入视图和 `source-map.json`，原目录不被修改。
5. 任务成功后，结果区可查看标注图片、播放输出视频并下载 `detections.csv`；静态图片预测会登记为机器标注版本，可从推理运行进入图片详情查看检测框。

Windows 下 Ultralytics 默认把视频结果写成 AVI，而 Chrome/Edge 通常不能直接解码 AVI。平台的视频脚本因此使用逐帧流式推理并输出 VP8 WebM，便于浏览器直接播放；历史 AVI 产物仍保留，并在结果区提供下载提示，不会被覆盖。

`11.mp4` 仅用于验证视频管线，不是钢板视频。模型在该视频上的框没有业务含义，也不能用于评价钢材缺陷准确率。

### 11.4 失败恢复

- 状态为失败或中断时先读日志，不要删除任务目录。
- 若YOLO已经生成完整产物但自动登记失败，点击“重新导入结果”；接口使用幂等键，不会重复创建资产。
- 训练或推理进程无响应时可点击“取消任务”。运行中的任务在心跳检查点终止，日志和已有产物保留。
- `device_busy` 表示同一GPU已有任务；等待前一任务结束后再执行。
- `model_hash_missing` 或 `artifact_hash_mismatch` 表示模型血缘不完整或内容变化，应重新登记/校验模型，不能绕过。
- PowerShell显示YOLO设置文件无写权限的警告通常不影响训练；真正的任务状态以退出码、预期产物和平台登记结果共同判断。

### 11.5 模型库

模型库支持上传并登记 `.pt` 与 `.onnx`。`.pt` 会自动创建可加载性校验任务；基础权重可用于迁移学习，不要求预先是钢材六类。缺陷检测器只有在类别数和顺序严格等于 `Cr/In/Pa/PS/RS/Sc` 后才能推理。`.onnx` 第一版仅归档和校验哈希，不直接执行推理。

## 12. 后续生产化的训练、推理和监测

完成复核并满足配额后，可以发布不可变数据集、准备训练、登记模型，再准备流式推理和审计轮。下面是当前配置驱动的兼容CLI；新任务优先使用浏览器模型工作台：

```powershell
steel-platform dataset publish --round <ROUND_NUMBER> --config <CONFIG>
steel-platform jobs prepare-training --dataset <DATASET_ID> --config <CONFIG>
steel-platform inference prepare --model <MODEL_ID> --config <CONFIG>
steel-platform review audit-create --inference <INFERENCE_ID> --per-class 10 --config <CONFIG>
```

后续生产化可将 SQLite 替换为 PostgreSQL、托管资产替换为对象存储、任务执行替换为队列/容器，并接入相机流、告警和指标系统；项目、资产、版本和复核任务的隔离边界保持不变。

## 自动化验证

```powershell
conda run -n steel-review python -m pytest -q
```

测试覆盖资源隔离、跨项目访问拒绝、managed/external 导入、任务队列范围、外部源哈希校验和迁移约束。
