package com.example.steel.projection;

import com.example.steel.projection.ProjectionModels.WorkOrderProjection;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface WorkOrderRepository extends JpaRepository<WorkOrderProjection, String> {
    List<WorkOrderProjection> findByProjectIdOrderByUpdatedAtDesc(String projectId);
}
