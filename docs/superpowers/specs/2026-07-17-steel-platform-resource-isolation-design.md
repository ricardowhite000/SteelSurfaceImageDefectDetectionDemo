# 钢材视觉平台：项目、数据源与复核任务隔离设计

## 1. 背景与目标

当前平台已经具备资产登记、不可变标注版本、复核、数据集、实验、模型和推理等基础能力，但应用层仍隐式选择数据库中的第一个项目，复核队列接口也没有要求任务作用域。因此首轮复核的225项和第二轮抽查的60项会在同一队列中显示为285项；当前任务完成后，前端还可能继续从其他任务中寻找下一项。

本次迭代把平台改造成项目级隔离的模块化单体，并提供文件管理页面。用户可以创建或切换项目，从本机文件夹导入或挂载数据，并通过虚拟集合组织资产。复核任务从集合创建时冻结成员清单，此后的集合变化不会改变既有任务。

成功标准：

- 文件管理、复核工作台和结果模块职责分离。
- 首轮复核只显示225项，第二轮抽查只显示60项，默认界面不再出现285项混合队列。
- 项目、数据源、集合、任务、数据集、模型和推理运行均有明确归属。
- 现有原图、标签版本、复核决定和模型产物在迁移过程中不被重写。
- 新项目可以使用独立类别模式，不再依赖全局写死的六分类配置。

## 2. 用户界面与导航

平台采用“模块分屏”结构：

1. 顶部保留系统概览、文件管理、复核任务、实验与模型、监测看板五个一级入口，并提供项目选择器。
2. 文件管理页使用双栏布局。左侧资源树按数据源、复核任务、数据集版本、模型与推理分组；中间区域显示当前节点的表格、搜索、筛选和操作。资产详情以按需抽屉显示，不设置常驻第三栏。
3. 复核工作台是独立页面。顶部显示返回入口、任务名称和任务进度；左侧只显示当前任务队列；中央为标注画布；右侧为样本信息、标注血缘、备注和复核决定。
4. 当前任务没有待处理项时显示完成摘要和报告入口，不自动进入其他任务，也不显示空白图片区域。
5. 切换项目、任务或页面前存在未保存修改时，必须显示离开警告。

## 3. 领域边界与数据模型

### 3.1 项目与类别模式

`Project` 是所有业务资源的根边界。任何查询和写入都必须显式提供 `project_id`，禁止通过“取第一条项目记录”推断上下文。

新增不可变 `ClassSchema`：保存模式名称、版本和有序类别列表。项目引用当前类别模式；复核任务和数据集版本保存创建时的类别模式ID。现有项目迁移为 `steel-defects-v1`：`Cr / In / Pa / PS / RS / Sc`。新项目可在创建时定义类别模式，已被任务或数据集引用的模式不能原地修改，只能创建新版本。

### 3.2 数据源、资产与集合

保留数据库表 `source_roots` 以减少迁移风险，但领域名称统一为 `DataSource`。一个项目可以拥有多个同类型数据源。数据源包含名称、模式、状态、定位信息、清单哈希和最近校验时间。

支持两种模式：

- `managed`：浏览器选择目录并按文件上传，平台将内容写入内容寻址资产库。原目录移动或删除后，资产仍可使用。
- `external`：本机目录选择器返回路径，平台只读登记数据根、相对路径、大小和SHA256。目录移动后必须通过重新定位完成全量清单校验。

`Collection` 是项目内的虚拟文件夹，支持父子层级。集合可以来源于物理目录、保存的筛选条件或人工组合。`CollectionMember` 只保存集合与资产关系；增删、移动或重命名集合不得移动或修改原始文件。

`Asset` 始终保存项目ID、数据源ID、相对路径、SHA256、大小和媒体类型。受管资产通过 `storage_key` 读取，外部资产通过数据根和相对路径读取。相同哈希的不同来源文件保留独立资产记录，以保存数据血缘并支持重复/泄漏审计；内容寻址存储可以在物理层复用字节。

### 3.3 导入会话

新增 `ImportSession` 和 `ImportEntry`，状态依次为：`planned`、`scanning`、`uploading`、`validating`、`ready`、`committing`、`succeeded`、`failed`、`cancelled`。

导入提交前，文件和扫描结果只属于导入会话，不出现在正式资源树中。受管导入按文件粒度恢复：客户端先提交相对路径、大小和修改时间清单，服务端返回需要上传的文件；已成功写入并校验的文件不会重复上传。外部挂载由服务端只读扫描并计算哈希。

### 3.4 复核任务

扩展 `ReviewRound`，增加任务名称、说明、来源集合、类别模式、目标数量和完成时间。`ReviewItem` 继续充当冻结后的任务成员清单。创建任务时将集合中选定资产复制为任务成员；集合后续变化不影响任务。

复核项目和任务必须同时匹配。读取或提交不属于当前项目/任务的条目时返回404；版本冲突返回409。复核提交成功后响应包含当前任务进度、下一待处理项ID和任务完成状态，前端不再重新查询全局队列。

## 4. 应用与基础设施架构

领域层新增 Project、ClassSchema、DataSource、Collection、ImportSession 和 ReviewTask 对象及其规则，不导入 FastAPI、SQLAlchemy、Windows接口、文件系统或YOLO。

应用层拆分为：

- `ProjectCatalogService`：项目创建、列表、切换所需查询。
- `DataSourceImportService`：导入会话、扫描、上传、校验、提交和重新定位。
- `ExplorerService`：资源树、集合、资产查询和筛选。
- `ReviewTaskQueryService`：任务列表、任务进度和任务内队列。
- `ReviewDecisionService`：幂等复核提交和不可变标注版本。

所有应用用例显式接收 `project_id`。Repository、Unit of Work、ArtifactStore 和 DirectoryPicker 通过接口注入；应用层不直接创建SQLAlchemy Engine或LocalArtifactStore。现有 `ReviewService` 中与本次功能相关的查询和提交逻辑迁入上述服务，不进行与本次目标无关的全平台重写。

基础设施层提供SQLAlchemy Repository、SQLite Unit of Work、本地ArtifactStore、Windows目录选择适配器和浏览器上传适配器。目录选择接口只在服务监听回环地址时启用，并使用同源会话令牌；不可用时前端允许手动输入路径。

## 5. HTTP API

所有接口继续使用 `/api/v1`、资源ID和统一错误结构 `code / message / details / request_id`。

项目与资源：

```text
GET  /api/v1/projects
POST /api/v1/projects
GET  /api/v1/projects/{project_id}/overview
GET  /api/v1/projects/{project_id}/explorer
GET  /api/v1/projects/{project_id}/sources
GET  /api/v1/projects/{project_id}/assets/{asset_id}/content
```

导入：

```text
POST /api/v1/local/folder-picker
POST /api/v1/projects/{project_id}/imports
GET  /api/v1/projects/{project_id}/imports/{import_id}
POST /api/v1/projects/{project_id}/imports/{import_id}/manifest
PUT  /api/v1/projects/{project_id}/imports/{import_id}/files/{entry_id}
POST /api/v1/projects/{project_id}/imports/{import_id}/commit
POST /api/v1/projects/{project_id}/imports/{import_id}/cancel
POST /api/v1/projects/{project_id}/sources/{source_id}/rebind
```

集合：

```text
POST /api/v1/projects/{project_id}/collections
PUT  /api/v1/projects/{project_id}/collections/{collection_id}
POST /api/v1/projects/{project_id}/collections/{collection_id}/members
DELETE /api/v1/projects/{project_id}/collections/{collection_id}/members/{asset_id}
```

复核：

```text
GET  /api/v1/projects/{project_id}/review-rounds
POST /api/v1/projects/{project_id}/review-rounds
GET  /api/v1/projects/{project_id}/review-rounds/{round_id}
GET  /api/v1/projects/{project_id}/review-rounds/{round_id}/items
GET  /api/v1/projects/{project_id}/review-rounds/{round_id}/items/{item_id}
PUT  /api/v1/projects/{project_id}/review-rounds/{round_id}/items/{item_id}/decision
```

项目创建、导入提交、任务创建、集合成员变更和复核提交要求 `Idempotency-Key`。集合修改和复核提交要求 `expected_revision`。旧 `/api/v1/review/queues` 返回410和 `scope_required`，不再返回混合队列；旧条目接口同步停止被前端调用。

## 6. 数据迁移与兼容

新增显式Alembic迁移 `0002_resource_scoping`，禁止在服务启动时自动升级。升级命令执行前创建SQLite数据库和配置文件备份，并记录项目、资产、标注版本、复核任务、复核条目和决定状态数量。

迁移内容：

1. 创建类别模式、集合、集合成员、导入会话和导入条目表。
2. 扩展项目、数据源、资产和复核任务字段。
3. 移除数据源 `(project_id, kind)` 唯一限制，改为 `(project_id, name)`。
4. 为现有项目创建 `steel-defects-v1` 并建立引用。
5. 将现有数据根回填为外部只读数据源。
6. 将历史轮次命名为独立任务；首轮保留225项，第二轮保留60项。
7. 保持所有现有主键、资产路径、哈希、标注版本和复核状态不变。

升级后再次统计并逐项核对；不一致时升级失败并提示从备份恢复。降级只允许在数据库副本上演练，平台不会自动执行降级。

## 7. 一致性、失败与安全

- 受管文件先写临时文件、刷新磁盘并原子重命名，再在数据库事务中登记。数据库失败产生的无引用资产由GC预览识别。
- 导入会话失败后保留已验证条目，可从文件级断点继续；取消不会删除已被其他资产引用的内容。
- 外部数据源离线时保留历史任务、标签和数据集元数据；资产内容请求返回明确的 `source_offline`。
- 重新定位在全部相对路径、大小和哈希通过前不得更新数据根。
- 目录选择接口只允许回环请求、同源会话令牌和单个活动对话框；远程监听时禁用。
- 所有资源访问同时校验资源ID和项目归属，资产接口不接受客户端文件路径。
- 原图与外部候选标签始终只读；标注修正继续生成不可变版本。

## 8. 测试与验收

自动化测试覆盖：

- 领域规则：项目归属、类别模式不可变、集合层级、任务成员冻结和状态机。
- Repository契约：SQLite实现与接口行为一致。
- API：项目/任务跨界访问、幂等重试、版本冲突、旧队列410和统一错误结构。
- 导入：受管导入、外部挂载、中断恢复、重复文件、非法相对路径、重新定位和源离线。
- 迁移：全新升级、现有数据库 `0001→0002`、备份恢复、计数与主键保持。
- 浏览器：项目切换、文件管理、导入向导、任务切换、完成摘要、脏数据警告和现有框编辑快捷键。
- 源数据保护：迁移和测试前后原图、候选标签及模型产物SHA256一致。

验收场景：

1. 现有项目中首轮任务显示225项，第二轮任务显示60项；任何任务内页面都不显示285项。
2. 新建第二个临时项目并导入六张图片后，两个项目的概览、资产、任务和模型完全隔离。
3. 受管导入成功后移走原目录，平台仍能读取资产。
4. 外部挂载目录移动后先显示离线，重新定位并校验后恢复。
5. 迁移后现有60项抽查结果、225项历史条目及全部标注版本保持不变。
6. 原始数据哈希执行前后完全一致。

## 9. 实施边界

本次实现项目目录、类别模式、双模式数据源、集合、任务隔离、迁移、文件管理页和独立复核工作台。训练、模型、推理和监测模块只改为项目作用域并接入新资源树，不改变训练算法、模型格式或工业相机接入方式。权限系统、对象存储、PostgreSQL、分布式任务和实时视频流不在本次范围内，但接口边界不得阻碍后续替换。
