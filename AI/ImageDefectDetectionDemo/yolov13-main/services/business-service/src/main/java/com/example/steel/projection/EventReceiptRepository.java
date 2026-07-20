package com.example.steel.projection;

import com.example.steel.projection.ProjectionModels.EventReceipt;
import org.springframework.data.jpa.repository.JpaRepository;

public interface EventReceiptRepository extends JpaRepository<EventReceipt, String> {}
