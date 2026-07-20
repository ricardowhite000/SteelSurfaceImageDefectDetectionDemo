package com.example.steel.projection;

import com.example.steel.projection.ProjectionModels.*;
import java.util.List;
import org.springframework.web.bind.annotation.*;

@RestController @RequestMapping("/api/v1")
public class ProjectionQueryController {
    private final ProjectRepository projects; private final WorkOrderRepository workOrders; private final EventReceiptRepository events;
    public ProjectionQueryController(ProjectRepository projects,WorkOrderRepository workOrders,EventReceiptRepository events){this.projects=projects;this.workOrders=workOrders;this.events=events;}
    @GetMapping("/projects") public List<ProjectProjection> projects(){return projects.findAll();}
    @GetMapping("/projects/{projectId}/annotation-work-orders") public List<WorkOrderProjection> workOrders(@PathVariable String projectId){return workOrders.findByProjectIdOrderByUpdatedAtDesc(projectId);}
    @GetMapping("/audit-events") public List<EventReceipt> auditEvents(){return events.findAll();}
}
