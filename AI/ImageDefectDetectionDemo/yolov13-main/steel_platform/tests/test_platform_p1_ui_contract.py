from __future__ import annotations

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src" / "steel_platform" / "interfaces" / "static" / "js"


def test_model_center_polls_current_route_and_keeps_empty_log_out_of_real_log() -> None:
    source = (STATIC / "model-workbench.js").read_text(encoding="utf-8")

    assert 'window.location.hash === "#model"' in source
    assert 'window.location.hash === "#workbench"' not in source
    assert 'const EMPTY_LOG = "暂无日志。"' in source
    assert '$(&quot;workbenchLog&quot;).textContent === EMPTY_LOG' not in source
    assert '$("workbenchLog").textContent === EMPTY_LOG' in source


def test_monitoring_center_reports_partial_failures_instead_of_zeroing_them() -> None:
    source = (STATIC / "monitoring-center.js").read_text(encoding="utf-8")

    assert "Promise.allSettled" in source
    assert "数据加载失败" in source
    assert "失败模块" in source
    assert "catch (_) { return fallback; }" not in source


def test_annotation_center_escapes_api_controlled_names_and_notes() -> None:
    source = (STATIC / "annotation-center.js").read_text(encoding="utf-8")

    assert "function escapeHtml" in source
    for expression in (
        "escapeHtml(item.name)",
        "escapeHtml(order.name)",
        "escapeHtml(item.filename)",
    ):
        assert expression in source
    assert 'textContent = error.message' in source
