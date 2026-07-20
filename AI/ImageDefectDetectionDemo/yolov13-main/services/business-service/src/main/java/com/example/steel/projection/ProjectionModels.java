package com.example.steel.projection;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

public final class ProjectionModels {
    private ProjectionModels() {}

    @Entity @Table(name = "project_projection")
    public static class ProjectProjection {
        @Id public String id;
        @Column(nullable = false) public String name;
        @Column(nullable = false) public Instant updatedAt;
        protected ProjectProjection() {}
        public ProjectProjection(String id, String name, Instant updatedAt) { this.id=id; this.name=name; this.updatedAt=updatedAt; }
    }

    @Entity @Table(name = "annotation_work_order_projection")
    public static class WorkOrderProjection {
        @Id public String id;
        @Column(nullable = false) public String projectId;
        @Column(nullable = false) public String name;
        @Column(nullable = false) public String status;
        @Column(nullable = false) public Instant updatedAt;
        protected WorkOrderProjection() {}
        public WorkOrderProjection(String id,String projectId,String name,String status,Instant updatedAt){this.id=id;this.projectId=projectId;this.name=name;this.status=status;this.updatedAt=updatedAt;}
    }

    @Entity @Table(name = "ai_event_receipt")
    public static class EventReceipt {
        @Id public String id;
        @Column(nullable = false) public String eventType;
        @Column(nullable = false, columnDefinition = "TEXT") public String payloadJson;
        @Column(nullable = false) public Instant receivedAt;
        protected EventReceipt() {}
        public EventReceipt(String id,String eventType,String payloadJson,Instant receivedAt){this.id=id;this.eventType=eventType;this.payloadJson=payloadJson;this.receivedAt=receivedAt;}
    }
}
