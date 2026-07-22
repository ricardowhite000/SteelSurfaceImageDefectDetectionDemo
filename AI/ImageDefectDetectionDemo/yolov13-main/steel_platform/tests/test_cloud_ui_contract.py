from __future__ import annotations

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src" / "steel_platform" / "interfaces" / "static"


def test_cloud_browser_has_search_sort_view_and_lazy_thumbnails() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    browser = (STATIC / "js" / "file-manager.js").read_text(encoding="utf-8")

    for element_id in ("resourceSearch", "resourceSort", "gridView", "listView", "resourcePager"):
        assert f'id="{element_id}"' in index
    assert "/resources/${resource.type}/${encodeURIComponent(resource.id)}/items" in browser
    assert 'loading="lazy"' in browser
    assert "localStorage" in browser


def test_asset_detail_and_report_are_application_routes() -> None:
    state = (STATIC / "js" / "state.js").read_text(encoding="utf-8")
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    review = (STATIC / "js" / "review-workspace.js").read_text(encoding="utf-8")

    assert 'query.get("asset")' in state
    assert 'query.get("report")' in state
    assert "renderAssetDetail" in main
    assert "renderReviewReport" in main
    assert "report=1" in review
    assert "window.location.assign" not in main


def test_asset_detail_uses_project_class_schema_instead_of_steel_constants() -> None:
    detail = (STATIC / "js" / "asset-detail.js").read_text(encoding="utf-8")

    assert "detailState.detail?.class_names" in detail
    assert '["Cr", "In", "Pa", "PS", "RS", "Sc"]' not in detail


def test_long_names_are_ellipsized_and_user_visible_shell_is_chinese() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    index = (STATIC / "index.html").read_text(encoding="utf-8")

    assert ".tree-button span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" in css
    assert ".resource-pane{min-width:0" in css
    for text in ("文件", "复核", "运行", "导入文件", "搜索文件", "钢材视觉平台"):
        assert text in index


def test_visible_source_has_no_replacement_character() -> None:
    files = [STATIC / "index.html", STATIC / "styles.css", *sorted((STATIC / "js").glob("*.js"))]
    for path in files:
        assert "�" not in path.read_text(encoding="utf-8"), path


def test_report_risk_and_sampling_enums_have_chinese_labels() -> None:
    locale = (STATIC / "js" / "locale-zh.js").read_text(encoding="utf-8")

    for token in ("人工抽查", "低置信度", "类别冲突", "模型版本差异优先", "质量抽查"):
        assert token in locale
    assert 'String(value).includes(";")' in locale
