CREATE TABLE project_projection (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, updated_at TIMESTAMP(6) NOT NULL);
CREATE TABLE annotation_work_order_projection (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, name VARCHAR(200) NOT NULL, status VARCHAR(30) NOT NULL, updated_at TIMESTAMP(6) NOT NULL, INDEX ix_work_order_project (project_id, updated_at));
CREATE TABLE ai_event_receipt (id VARCHAR(100) PRIMARY KEY, event_type VARCHAR(100) NOT NULL, payload_json TEXT NOT NULL, received_at TIMESTAMP(6) NOT NULL, INDEX ix_event_type_time (event_type, received_at));
