# YOLOv13 钢板表面缺陷检测：从标注到推理

这套教程不是只给出一条训练命令，而是带你完成一遍可以复现的目标检测闭环：

> 环境自检 → 准备60张种子图 → LabelImg画框 → 标签审计 → 构建数据集 → 冒烟训练 → 正式训练 → 评估 → 图片/视频推理 → 辅助标注

当前学习模型固定识别6类 NEU 钢材表面缺陷：

| 类别编号 | 缩写 | 英文含义 | 常见中文含义 |
| ---: | --- | --- | --- |
| 0 | Cr | crazing | 裂纹/龟裂 |
| 1 | In | inclusion | 夹杂 |
| 2 | Pa | patches | 斑块 |
| 3 | PS | pitted surface | 点蚀表面 |
| 4 | RS | rolled-in scale | 压入氧化皮 |
| 5 | Sc | scratches | 划痕 |

> 重要：60张图只用于学习和生成候选框，不能代表生产模型性能。

## 0. 先认识手里的数据

资料中有两组关键内容：

- `data/unmarked/NEU surface defect database`：1800张 BMP 图，六类各300张。文件名前缀已经表示整张图的缺陷类别，但没有框的位置。
- `data/marked/front`：10张 Cr 原图及标签；其中 `Cr_104`～`Cr_107` 的标签为空，但图中仍有可见异常，需要补框。
- `data/marked/back`：上述10张图的水平翻转版本。本教程不使用它们，防止原图和翻转图被分到训练集与验证集两边，造成数据泄漏。

目标检测标签不能只写“这张图是 Cr”，还必须告诉模型缺陷在哪里。YOLO 检测标签每一行表示一个框：

```text
class_id x_center y_center width height
```

后四个数字均除以图像宽高，范围是0～1。例如：

```text
0 0.500000 0.400000 0.200000 0.100000
```

表示类别0，框中心位于图像宽度50%、高度40%的位置，框宽20%、框高10%。一张图有多个缺陷时写多行。

## 1. 固定运行位置

在 PyCharm 中打开 `yolov13-main`，选择你之前已经配置正常的项目解释器 `yolov13`。以下命令都在项目根目录执行：

```text
G:\Desktop1\Documents\钢材表面异常视觉检测\AI\ImageDefectDetectionDemo\yolov13-main
```

不要在 Codex 当前的 Python 3.14 终端中运行训练；应使用你已安装 PyTorch/CUDA 的 PyCharm 解释器。

## 2. 检查环境

```powershell
python -m steel_tutorial.01_check_environment
```

通过时会显示 Python、PyTorch、Ultralytics、CUDA、GPU名称和 `yolov13n.pt` 路径。必须看到：

```text
CUDA available: True
环境检查通过。
```

若显示找不到 `torch`，说明终端没有使用 PyCharm 中的 `yolov13` 解释器；这不是数据问题，不要重新修改项目源码。

## 3. 准备60张种子图

命令如下：

```powershell
python -m steel_tutorial.02_prepare_seed
```

本工作区已经执行过一次，生成位置为：

```text
../data/tutorial_workspace/steel_seed_v1/
├── annotation_work/       # 用 LabelImg 打开的60张图
├── seed_manifest.csv      # 每张图的来源、类别和校验值
├── source_snapshot.json   # 原始1800张图的数量与SHA-256快照
└── reports/
```

为防止覆盖人工成果，目标目录非空时脚本会停止。不要反复运行或手工删除已有标注；确需重新实验时使用新的工作区：

```powershell
python -m steel_tutorial.02_prepare_seed --workspace "../data/tutorial_workspace/steel_seed_v2"
```

抽样规则：Cr 优先复用现有10张原图，其余五类分别按固定种子42选10张。相同文件和种子始终得到相同结果。

## 4. 使用 LabelImg 画框

LabelImg 已不再活跃开发，因此建议放在独立标注环境中，不要把 PyQt 依赖装进 YOLO 训练环境：

```powershell
conda create -n labelimg python=3.10 -y
conda activate labelimg
pip install labelImg
labelImg
```

### 4.1 打开与保存设置

1. 点击 **Open Dir**，打开 `data/tutorial_workspace/steel_seed_v1/annotation_work`。
2. 点击 **Change Save Dir**，仍选择同一个 `annotation_work` 目录。
3. 将工具栏上的 `PascalVOC` 切换为 **YOLO**。
4. 类别文件 `classes.txt` 中只有一个临时类 `defect`，所以画框时不需要判断六类编号。
5. 每画完一张按 `Ctrl+S` 保存，按 `D` 下一张、`A` 上一张、`W` 新建框、`Delete` 删除选中框。

### 4.2 统一的画框原则

- 框住可见异常区域，不要因为整张图属于某类就框整张图。
- 一个图中有多个彼此分离的异常区域时分别画框。
- 裂纹和划痕使用能包住连续异常的最小矩形；明显断开的区域分别标。
- 斑块、点蚀、夹杂和氧化皮按连通或视觉上连续的异常区域画框。
- 框边缘贴近异常，但不要切掉异常，也不要包含过多正常纹理。
- 同一种视觉情况始终采用相同规则；一致性比追求“绝对完美的边界”更重要。
- 不确定的图先记录文件名，完成一轮后集中复核。

### 4.3 必须修正的现有图片

以下四个标签当前为空，但图中存在异常：

```text
Cr_104.bmp
Cr_105.bmp
Cr_106.bmp
Cr_107.bmp
```

必须在 LabelImg 中补框。其余50张非 Cr 图片当前没有 TXT，也都需要画框并保存。

## 5. 审计标签

完成60张后运行：

```powershell
python -m steel_tutorial.03_audit_labels
```

脚本检查：

- 60张图片是否都有同名 TXT；
- 是否存在空标签；
- 每行是否恰好5列；
- 类别是否为临时类0；
- 中心点、宽高和框边界是否有效；
- 是否存在没有对应图片的孤立标签。

当前第一次审计的实际状态是：

```text
图片: 60，标签: 10，框: 15
审计未通过，共54个问题
```

其中4个为空标签、50个为缺失标签，这是正常的人工标注检查点。画完后反复运行审计，直到看到：

```text
标签审计通过，可以构建训练数据集。
```

报告和可视化抽查图位于：

```text
../data/tutorial_workspace/steel_seed_v1/reports/
├── audit_report.json
└── label_preview/*.jpg
```

不要只看“审计通过”；还要打开预览图检查框是否真的贴合异常。

## 6. 构建标准 YOLO 数据集

审计通过后运行：

```powershell
python -m steel_tutorial.04_build_dataset
```

该步骤会根据文件名前缀把临时 `defect=0` 转换成正式六类编号，并按每类8张训练、2张验证进行稳定划分：

```text
../data/tutorial_workspace/steel_seed_v1/dataset/
├── images/
│   ├── train/             # 48张
│   └── val/               # 12张
├── labels/
│   ├── train/
│   └── val/
├── data.yaml
├── split_manifest.csv
└── audit_report.json
```

`data.yaml` 不写死盘符，内容类似：

```yaml
train: images/train
val: images/val

names:
  0: Cr
  1: In
  2: Pa
  3: PS
  4: RS
  5: Sc
```

## 7. 先做1轮冒烟训练

冒烟训练的目的不是提高精度，而是用最短时间确认路径、标签、GPU、显存和训练代码全部连通：

```powershell
python -m steel_tutorial.05_train --smoke
```

成功后检查：

```text
runs/steel_tutorial/train/seed_smoke/
├── args.yaml
├── results.csv
└── weights/
    ├── best.pt
    └── last.pt
```

若发生 CUDA out of memory，先减小批量：

```powershell
python -m steel_tutorial.05_train --smoke --batch 2
```

## 8. 进行100轮学习训练

冒烟训练成功后运行：

```powershell
python -m steel_tutorial.05_train
```

默认参数及作用：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `imgsz` | 640 | 输入尺寸；原图会缩放到训练尺寸 |
| `batch` | 4 | 每次送入GPU的图片数 |
| `epochs` | 100 | 最大训练轮数 |
| `patience` | 20 | 指标连续20轮不改善则提前停止 |
| `device` | 0 | 使用第1块 NVIDIA GPU |
| `workers` | 0 | Windows 下最稳妥的数据加载设置 |
| `amp` | True | 自动混合精度，降低显存并加速 |
| `seed` | 42 | 固定随机性，便于复现实验 |

训练结果默认保存在：

```text
runs/steel_tutorial/train/seed_v1/
```

最重要的文件是 `weights/best.pt`。`last.pt` 只是最后一轮，未必是验证表现最好的一轮。

## 9. 评估并理解指标

```powershell
python -m steel_tutorial.06_evaluate
```

输出包括混淆矩阵、PR曲线和 `metrics_summary.json`。

- **Precision**：模型报出的缺陷中有多少是真的；低说明误检多。
- **Recall**：真实缺陷中有多少被模型找到；工业质检通常更重视漏检，因此特别关注 Recall。
- **mAP50**：IoU阈值0.5时的综合检测指标。
- **mAP50-95**：在更严格的一系列IoU阈值下取平均，更能反映定位质量。
- **混淆矩阵**：观察哪些缺陷类型互相混淆，以及背景误检/漏检。

当前验证集只有12张，指标波动会非常大。此处只能用来学习读图和排查明显错误，不能写成企业项目最终精度。

## 10. 推理

### 10.1 单张钢板图片

```powershell
python -m steel_tutorial.07_infer --source "../data/tutorial_workspace/steel_seed_v1/annotation_work/Cr_1.bmp"
```

### 10.2 整个图片文件夹

```powershell
python -m steel_tutorial.07_infer --source "../data/tutorial_workspace/steel_seed_v1/annotation_work" --name seed_folder
```

### 10.3 视频

```powershell
python -m steel_tutorial.07_infer --source "11.mp4" --name video_demo
```

`11.mp4` 不是钢板视频，只用于证明视频逐帧管线能运行，出现0个钢板缺陷框是允许的。真实效果必须换成钢板视频。

推理目录中会得到标注媒体、YOLO TXT 和 `detections.csv`。CSV字段为：

```text
source_file,frame_index,time_seconds,class_id,class_name,confidence,x1,y1,x2,y2
```

这份 CSV 可以直接作为后续数据看板或数据库入库的接口原型。调整阈值示例：

```powershell
python -m steel_tutorial.07_infer --source "钢板视频.mp4" --conf 0.15 --name recall_first
```

降低阈值通常提高召回率，也会增加误检，必须结合验证数据决定，不能只凭视觉挑一个好看的结果。

## 11. 用种子模型辅助标注剩余1740张

完成种子训练后运行：

```powershell
python -m steel_tutorial.08_pseudo_label
```

脚本默认使用 `batch=1`，并通过临时TXT清单逐批从磁盘读取图片，适合8GB显存。
不要把全部图片路径作为Python列表直接交给 `model.predict()`；在当前YOLOv13加载器中，
这会把整份列表合并成一个GPU批次，即使设置 `stream=True` 也可能显存溢出。

如果只是机器候选标签生成中断，可以直接重新运行，已有候选不会被覆盖。只有在确认目标目录
尚未开始人工复核、并且需要重建所有机器候选时，才使用：

```powershell
python -m steel_tutorial.08_pseudo_label --batch 1 --overwrite
```

默认行为：

- 排除 `seed_manifest.csv` 中的60张种子图；
- 对其余1740张生成候选框；
- 候选框类别由文件名前缀确定，模型提供位置；
- 阈值为0.20；
- GPU批次默认为1；
- 已存在的候选标签绝不覆盖；
- 无框、类别预测不一致、置信度低于0.40的图片优先复核；
- 无框图片不会自动创建空 TXT，避免被误当成无缺陷负样本。

输出：

```text
../data/tutorial_workspace/steel_seed_v1/pseudo_labels/
├── classes.txt
├── *.txt
└── pseudo_review.csv
```

候选标签不是正确答案。用 LabelImg 打开未标注图片目录，将保存目录切换到 `pseudo_labels`，逐张移动、删除、补充候选框。只有人工复核后的标签才能进入正式训练集。

## 12. 常见问题

### LabelImg 画框时在 `canvas.py` 的 `drawLine` 闪退

这是 LabelImg 1.8.6 与较新 PyQt5 的参数类型兼容问题。若堆栈明确指向
`libs/canvas.py`，并提示 `drawLine` 收到了 `float`，在项目根目录运行：

```powershell
conda activate labelimg
python -m steel_tutorial.fix_labelimg_pyqt
```

脚本会完整修正 LabelImg 1.8.6 在新版 PyQt5 下的已确认类型边界，包括十字线、
矩形预览、标签文字、滚轮滚动、Ctrl+滚轮缩放和拖动画布。首次运行时会分别创建
`canvas.py.bak`、`labelImg.py.bak` 和 `shape.py.bak`；重复运行不会重复修改。
修复后关闭旧的 LabelImg 进程，重新执行 `labelImg`。

### `ModuleNotFoundError: No module named 'torch'`

当前终端选错了解释器。回到 PyCharm 的 Python Interpreter 设置，选择已经可以训练 COCO8 的 `yolov13` 环境。

### `CUDA available: False`

先运行 `nvidia-smi` 确认驱动，再检查 PyTorch 是否为 CUDA 版本。不要因为系统显示 CUDA 版本较新就随意重装；以此前已经成功运行的环境为准。

### `标签审计未通过`

打开 `reports/audit_report.json`。缺失标签表示图片没有同名TXT；空标签表示TXT存在但没有任何框；越界框需要回到 LabelImg 调整。

### `目标目录不为空`

这是防覆盖保护。不要删除已经画好的标注。使用 `--workspace` 或 `--name` 创建新实验。

### 训练指标很高但推理不好

60张数据太少，且训练图与验证图纹理相近。检查数据泄漏、漏标和框的一致性，再逐批增加经人工复核的数据。

### 视频没有检测框

先用钢板图片验证 `best.pt`。若图片可以、普通视频不可以，通常是视频内容不属于钢板域；若钢板视频也不行，再检查分辨率、运动模糊、光照和阈值。

## 13. 学习完成标准

满足以下条件，说明你已经跑通第一阶段：

- [ ] 60张种子图全部标注并通过审计；
- [ ] 构建出48张训练、12张验证的六类数据集；
- [ ] 1轮冒烟训练成功；
- [ ] 100轮学习训练产生 `best.pt`；
- [ ] 能解释 Precision、Recall、mAP 和混淆矩阵；
- [ ] 单图、文件夹和视频接口均能完成推理；
- [ ] 能找到并读懂 `detections.csv`；
- [ ] 知道自动候选框必须人工复核，不能直接当作真值。

完成这些以后，再逐批扩展到完整数据，并重新建立70%训练、20%验证、10%测试的正式划分。

## 附录：如何看待 `formal_best.onnx`

资料中还有：

```text
../saved_models/yolov13/formal_best.onnx
```

它可能是已经导出的检测模型，但当前资料没有同时给出训练数据版本、类别顺序、输入尺寸、阈值和评估报告。因此主教程不依赖它，也不能因为文件名包含 `best` 就把它当作正确答案。

如果已在独立环境安装 `onnxruntime`，可以先新建一个临时 Python 文件运行以下只读检查：

```python
from pathlib import Path
import onnxruntime as ort

model_path = Path("../saved_models/yolov13/formal_best.onnx")
session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

print("metadata:", session.get_modelmeta().custom_metadata_map)
for item in session.get_inputs():
    print("input:", item.name, item.shape, item.type)
for item in session.get_outputs():
    print("output:", item.name, item.shape, item.type)
```

只有同时满足以下条件才进入对比实验：

1. 元数据或提供者明确说明类别顺序；
2. 类别能够无歧义地映射到本教程的 `Cr/In/Pa/PS/RS/Sc`；
3. 在60张人工标注集上重新计算过 Precision、Recall 和 mAP；
4. 推理预处理与后处理已确认，包括颜色顺序、归一化、输入尺寸和 NMS；
5. 输出结果经过人工可视化抽查。

ONNX 用于部署或对比推理，不用于继续执行本教程的 Ultralytics `.pt` 迁移训练。
