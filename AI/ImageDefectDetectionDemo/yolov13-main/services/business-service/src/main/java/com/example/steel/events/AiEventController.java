package com.example.steel.events;

import com.example.steel.projection.ProjectionModels.EventReceipt;
import com.example.steel.projection.ProjectionModels.WorkOrderProjection;
import com.example.steel.projection.EventReceiptRepository;
import com.example.steel.projection.WorkOrderRepository;
import tools.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.time.Instant;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

@RestController
public class AiEventController {
    private final EventReceiptRepository receipts; private final WorkOrderRepository workOrders;
    public AiEventController(EventReceiptRepository receipts, WorkOrderRepository workOrders){this.receipts=receipts;this.workOrders=workOrders;}
    public record AiEvent(@NotBlank String eventType, @Valid JsonNode payload, Instant occurredAt) {}

    @PostMapping("/internal/v1/ai-events") @Transactional
    public ResponseEntity<Map<String,Object>> receive(@RequestHeader("Idempotency-Key") String id, @RequestBody @Valid AiEvent event){
        if(receipts.existsById(id)) return ResponseEntity.ok(Map.of("eventId",id,"replayed",true));
        Instant when=event.occurredAt()==null?Instant.now():event.occurredAt(); JsonNode p=event.payload();
        if(event.eventType().startsWith("annotation.work_order.")){
            String orderId=p.path("work_order_id").asText(); String projectId=p.path("project_id").asText();
            if(!orderId.isBlank()&&!projectId.isBlank()) workOrders.save(new WorkOrderProjection(orderId,projectId,p.path("name").asText("标注工单"),p.path("status").asText("active"),when));
        }
        receipts.save(new EventReceipt(id,event.eventType(),p.toString(),Instant.now()));
        return ResponseEntity.accepted().body(Map.of("eventId",id,"replayed",false));
    }
}
