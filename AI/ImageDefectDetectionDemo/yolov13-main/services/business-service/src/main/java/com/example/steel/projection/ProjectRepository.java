package com.example.steel.projection;

import com.example.steel.projection.ProjectionModels.ProjectProjection;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProjectRepository extends JpaRepository<ProjectProjection, String> {}
