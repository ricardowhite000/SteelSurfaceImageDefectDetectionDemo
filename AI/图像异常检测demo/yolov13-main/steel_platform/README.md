# 钢材表面异常视觉系统 Demo 平台

这不是另一个零散标注脚本，而是一套可在 Windows 本机运行的“模块化单体”演练平台。第一版把数据登记、候选框复核、数据集发布、训练任务、模型登记、流式推理、第二轮抽查和系统概览串成一条可追溯闭环。

> 学习目标不是用 240 张图宣称生产精度，而是理解并亲手跑通：**数据版本决定实验，实验产生模型，模型产生新候选，新证据再推动下一轮数据建设。**

## 1. 背景知识

### 1.1 为什么不能直接改 TXT

机器候选、人工接受和人工修正代表三种不同证据。若直接覆盖 TXT，就无法回答“这个框是谁、根据哪个模型、在何时改的”。平台因此把每次确认都保存为新的 `annotation_revision`，用 `parent_id` 连接旧版本；原图和旧标签永不覆盖。

### 1.2 为什么要区分资产、数据集、任务和模型

- **资产**是带 SHA256 的图片、标签、权重或清单。
- **数据集版本**是不可变成员清单；同一张图片必须明确引用某个标签版本和 train/val 角色。
- **任务**是可审阅命令，不等于已运行实验。状态依次为 `planned/running/succeeded/failed/cancelled`。
- **模型版本**必须能追溯训练数据集、父权重、配置、指标和权重哈希。
- **InferenceRun**属于某个模型版本；v2 结果写入新目录，绝不覆盖 v1 候选。

### 1.3 为什么仍采用 SQLite 和手动任务

Demo 的重点是业务闭环，不是运维复杂度。SQLite WAL、本地内容寻址资产和单进程服务足够学习；领域层同时定义 Repository、UnitOfWork、ArtifactStore、JobExecutor、PredictorAdapter、EventPublisher 和 Telemetry 接口，后续可替换 PostgreSQL、S3、Celery/Kubernetes、Triton 和 Prometheus。

## 2. 当前目标和数据配额

当前六类固定为：`0 Cr / 1 In / 2 Pa / 3 PS / 4 RS / 5 Sc`。

首轮从 1740 张候选中每类选择 30 张：18 张风险优先、6 张低置信度、6 张 dHash 多样性，共 180 张。每类前 10 张固定为验证角色，其余 20 张为训练角色。若选择“存疑”或“排除”，该图不占有效额度，系统自动追加同类别、同角色候补。

当每类有效完成 30 张后，与原种子集的 8 train / 2 val 合并：

| 来源 | train | val | 总计 |
|---|---:|---:|---:|
| v1 种子集 | 48 | 12 | 60 |
| 首轮人工确认 | 120 | 60 | 180 |
| `steel-dataset-v2` | **168** | **72** | **240** |

## 3. 架构和目录

```text
steel_platform/
├─ src/steel_platform/
│  ├─ domain/          # 纯领域对象、状态和可替换端口
│  ├─ application/     # 抽样、复核、发布、任务与维护用例
│  ├─ infrastructure/  # SQLAlchemy/Alembic、本地资产、YOLO格式
│  └─ interfaces/      # Typer CLI、FastAPI、原生Canvas前端
├─ config/platform.local.yaml
├─ tests/
└─ pyproject.toml
```

外部 1800 张图只登记为只读 SourceRoot。平台生成物写到 `数据/教程工作区/steel_platform_demo/`；内容资产使用 `sha256/前两位/完整哈希`，可浏览的数据集和运行目录位于 `artifacts/materialized/`。

## 4. 第一次启动

以下命令均在 `yolov13-main` 目录执行。Web 与数据库命令使用独立环境，不改变 Torch/CUDA：

```powershell
conda activate steel-review
python -m pip install -e ".\steel_platform[web,dev]"

steel-platform project check --config .\steel_platform\config\platform.local.yaml
steel-platform db upgrade --config .\steel_platform\config\platform.local.yaml
steel-platform project init --config .\steel_platform\config\platform.local.yaml
steel-platform review round-create --round 1 --config .\steel_platform\config\platform.local.yaml
steel-platform serve --config .\steel_platform\config\platform.local.yaml
```

浏览器打开 `http://127.0.0.1:8765`。服务只监听本机；同一资产工作区有单实例锁，第二个服务不会同时写 SQLite。

启动不会偷偷迁移数据库。如果版本落后，`/health/ready` 会返回 `database_upgrade_required`，并明确提示 `steel-platform db upgrade`。

## 5. 复核界面操作教程

界面由中央聚焦画布、左侧可折叠待复核队列和右侧证据信息组成。

1. 先在左侧按类别、状态或文件名筛选。风险来源和选择原因会显示在每个队列项中。
2. 滚轮以鼠标位置缩放；`Alt + 左键拖动`或中键拖动平移。
3. 单击框后可拖动；拖四角控制点可缩放。按 `R` 后拖出新框，按 `Delete` 删除选中框。
4. 一张图片只允许文件名前缀代表的类别，但可存在多个同类框。
5. `A` 接受候选并下一张；`S` 保存修正并下一张；`D` 存疑并保存草稿；`X` 排除。排除必须填写原因。
6. 看不出有效缺陷时必须排除，不能用空 TXT 伪装负样本。
7. `Ctrl+Z / Ctrl+Y` 撤销重做，`Q` 开关队列。切换图片或关闭页面前若有未保存内容，浏览器会警告。

只有服务端成功创建标签版本、幂等记录、领域事件和 Outbox 后，界面才会自动进入下一张。重复提交同一 `Idempotency-Key` 返回同一结果；旧 `expected_revision` 返回 409，绝不覆盖新编辑。

随时导出进度：

```powershell
steel-platform review export-progress --round 1 --output .\review_progress.csv --config .\steel_platform\config\platform.local.yaml
```

## 6. 发布 v2 数据集

首轮有效配额完成后执行：

```powershell
steel-platform dataset publish --round 1 --config .\steel_platform\config\platform.local.yaml
```

发布前会拒绝空框、缺失人工版本、类别不足和重复文件名。成功后输出 Dataset ID，并生成 `manifest.json`、`data.yaml`、标准 `images/train|val` 和 `labels/train|val`。重复执行返回同一版本，不创建副本。

检查清单中的 `counts` 必须是 `train=168 / val=72`。`manifest.json` 逐项记录图片哈希、标签版本、标签哈希、划分和来源轮次。

## 7. 生成并运行训练任务

```powershell
steel-platform jobs prepare-training --dataset <DATASET_ID> --config .\steel_platform\config\platform.local.yaml
steel-platform jobs show --job <JOB_ID> --config .\steel_platform\config\platform.local.yaml
steel-platform jobs run --job <JOB_ID> --config .\steel_platform\config\platform.local.yaml
```

一次生成三个任务：1 轮冒烟训练、100 轮正式训练、固定 72 张验证集评估。先运行冒烟任务；确认 `best.pt / last.pt / results.csv` 正常后再运行正式任务和评估任务。v2 默认以 v1 `best.pt` 为父权重，不从随机结构初始化。

评估工具不再写死“12 张验证图”，会从 `data.yaml` 读取实际数量并写入 `metrics_summary.json`。阶段指标仅用于同一固定验证集上的相对比较。

完成正式训练与评估后登记：

```powershell
steel-platform runs ingest-training --job <FORMAL_JOB_ID> --path <FORMAL_RUN_DIR> --config .\steel_platform\config\platform.local.yaml
```

导入要求 `best.pt、last.pt、results.csv、evaluation/v2_fixed_val/metrics_summary.json` 完整；重复导入不会注册第二个模型。

## 8. v2 流式推理和第二轮抽查

```powershell
steel-platform inference prepare --model <MODEL_ID> --config .\steel_platform\config\platform.local.yaml
steel-platform jobs show --job <INFERENCE_JOB_ID> --config .\steel_platform\config\platform.local.yaml
steel-platform jobs run --job <INFERENCE_JOB_ID> --config .\steel_platform\config\platform.local.yaml
steel-platform runs ingest-inference --job <INFERENCE_JOB_ID> --path <PREDICTION_DIR> --config .\steel_platform\config\platform.local.yaml
steel-platform review audit-create --inference <INFERENCE_RUN_ID> --per-class 10 --config .\steel_platform\config\platform.local.yaml
```

平台在准备任务时生成临时 `sources.txt`，排除 60 张种子图和首轮所有已复核图。执行器固定 `batch=1、stream=True`，每张结果原子写入，并用 `processed.jsonl` 断点续跑；每 25 张刷新 `pseudo_review.csv`，不会把全部结果留在显存。

第二轮按无框、类别冲突、低置信度、框数差、v1/v2 IoU 差异和 dHash 多样性综合排序，每类取 10 张、共 60 张。它是抽查，不要求再复核全部剩余图片。

## 9. 故障恢复与维护演练

### 训练失败

`jobs run` 会把状态写为 `failed` 并保留退出码。修复环境或参数后可重新运行同一任务；已发布数据集不会变化。不要删除失败目录来伪装成功。

### 推理中断或显存不足

先确认命令含 `--batch 1`。重新运行同一任务时，执行器读取 `processed.jsonl`，只处理剩余文件。若仍不足，先关闭其他占用 GPU 的程序；不要提高 batch。

### 数据库与资产检查

```powershell
steel-platform artifacts verify --config .\steel_platform\config\platform.local.yaml
steel-platform artifacts gc --dry-run --config .\steel_platform\config\platform.local.yaml
steel-platform backup create --config .\steel_platform\config\platform.local.yaml
```

`gc` 第一版永远只报告不删除。备份使用 SQLite 在线备份 API，并保存配置快照、数据库哈希和资产校验摘要。恢复演练必须先停止服务，把当前数据库另存，再从备份复制到新的测试目录并运行 `project check` 与 `artifacts verify`；不要在服务运行时覆盖数据库。

结构化日志位于 `artifacts/logs/platform.jsonl`，滚动保留 5 份，包含 request_id 以及可用的 project_id/job_id/run_id。

## 10. 测试

```powershell
conda activate steel-review
python -m pytest .\steel_platform\tests -q
python -m pytest .\steel_tutorial\tests -q
```

测试覆盖领域边界、YOLO 坐标属性、稳定抽样、Alembic 新库升级、SQLite Repository 行为、幂等/409、不可变标签、补位、数据集发布、任务生成、模型登记、v2 推理导入、抽查队列、备份和 GC 只读语义。

## 11. 后续正式化路线

1. 先把数据库 URL 切换 PostgreSQL、ArtifactStore 切换 MinIO/S3，保持用例接口不变。
2. 再把 ManualCommandExecutor 换成 Celery/Kubernetes，把 Outbox 分发到 RabbitMQ/Kafka。
3. 在线推理可替换 ONNX Runtime、Triton 或边缘设备适配器。
4. 工业相机、实时流和告警独立成为新边界；概览继续复用指标查询与 Telemetry，而不是扫描目录。
5. 最后增加用户权限、审计签名、OpenTelemetry/Prometheus 和生产级备份恢复。

在这些步骤之前，先完整跑通一次 v2 闭环并核对 168/72 清单。这才是当前 Demo 的完成标准。
