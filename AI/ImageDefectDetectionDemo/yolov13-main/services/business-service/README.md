# Steel Business Service（学习骨架）

该服务是未来业务后端的学习与迁移边界，不与当前 Python 平台争夺写权限。Python 仍负责 YOLO、标注版本和本地任务；Java 通过 `POST /internal/v1/ai-events` 幂等消费领域事件，形成 MySQL 只读投影。

要求 Java 21、MySQL 8；项目自带 Maven Wrapper，不要求全局安装 Maven。首次先运行 `mvnw.cmd test` 验证 H2 测试环境和 Flyway 迁移。随后创建 MySQL 数据库与用户，设置 `STEEL_MYSQL_URL`、`STEEL_MYSQL_USER`、`STEEL_MYSQL_PASSWORD`，再执行 `mvnw.cmd spring-boot:run`。健康检查：`GET /actuator/health`。

当前事件类型包括标注工单、数据集、任务、模型和推理生命周期。第一版只物化标注工单投影；其余事件完整保存在审计表，便于后续逐步实现权限、报警和追溯，而无需改动 Python AI 内核。
