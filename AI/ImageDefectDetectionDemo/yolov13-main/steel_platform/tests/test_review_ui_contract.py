from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "steel_platform" / "interfaces" / "static"
REVIEW_JS = STATIC / "js" / "review-workspace.js"
INDEX = STATIC / "index.html"
STATE_JS = STATIC / "js" / "state.js"
FILE_MANAGER_JS = STATIC / "js" / "file-manager.js"
STYLES = STATIC / "styles.css"


def test_review_workspace_is_scoped_and_keeps_editor_contract() -> None:
    source = REVIEW_JS.read_text(encoding="utf-8")

    assert "/api/v1/review/queues" not in source
    assert "review-rounds/${state.roundId}/items" in source
    assert "next_pending_item_id" in source
    assert "round_completed" in source
    route_state = STATE_JS.read_text(encoding="utf-8")
    assert 'query.get("project")' in route_state
    assert 'query.get("round")' in route_state
    assert "beforeunload" in source
    for key in ("KeyA", "KeyS", "KeyD", "KeyX", "KeyR", "Delete", "KeyQ", "KeyZ", "KeyY"):
        assert key in source


def test_review_workspace_is_loaded_from_the_modular_entrypoint() -> None:
    source = INDEX.read_text(encoding="utf-8")
    assert 'src="/static/js/main.js?v=__STATIC_VERSION__"' in source
    assert "reviewWorkspace" in (STATIC / "js" / "main.js").read_text(encoding="utf-8")


def test_review_route_loads_project_context_marks_review_active_and_clears_cross_project_round() -> None:
    source = (STATIC / "js" / "main.js").read_text(encoding="utf-8")

    assert "await loadProjects();" in source
    assert "reviewWorkspace();" in source
    assert 'setActiveNavigation("#annotation")' in source
    assert "roundId: null" in source
    assert "window.location.assign" not in source


def test_review_workspace_has_a_bounded_desktop_canvas_and_scrollable_queue() -> None:
    source = STYLES.read_text(encoding="utf-8")

    assert ".review-workspace{height:calc(100vh - 64px)" in source
    assert ".review-layout{flex:1;min-height:0" in source
    assert ".review-queue{min-height:0;overflow:auto" in source
    assert ".canvas-stage{position:relative;flex:1;min-height:0" in source
    assert "@media(max-width:680px){.review-workspace{height:auto" in source


def test_review_task_rows_offer_a_project_and_round_scoped_entry_link() -> None:
    source = FILE_MANAGER_JS.read_text(encoding="utf-8")

    assert "review-entry" in source
    assert "project=${encodeURIComponent(state.projectId)}" in source
    assert "round=${encodeURIComponent(item.id)}" in source


def test_directly_selected_review_task_renders_its_scoped_entry() -> None:
    source = FILE_MANAGER_JS.read_text(encoding="utf-8")

    assert 'type: node.type, id: node.id' in source
    assert 'item.type === "review_round"' in source


def test_no_pending_queue_renders_a_read_only_archive() -> None:
    source = REVIEW_JS.read_text(encoding="utf-8")

    assert "const nextPending = reviewState.queue.find" in source
    assert "reviewState.readOnly = !nextPending" in source
    assert '$("reviewTitle").textContent = "已完成工单档案"' in source


def test_review_workspace_escapes_imported_names_and_api_metadata() -> None:
    source = REVIEW_JS.read_text(encoding="utf-8")

    assert "const escapeHtml" in source
    assert "escapeHtml(item.filename)" in source
    assert "classLabels[item.expected_class_name]" in source
    assert 'escapeHtml(zh("status", item.state))' in source
    assert "escapeHtml(value ??" in source


def test_read_only_archive_blocks_canvas_keyboard_and_submission() -> None:
    source = REVIEW_JS.read_text(encoding="utf-8")

    assert "function canEdit()" in source
    assert "if (!canEdit()) return;" in source
    assert "if (reviewState.readOnly) return;" in source
    assert "if (reviewState.readOnly && !event.altKey) return;" in source


def test_multi_class_editor_preserves_box_classes_and_offers_selector() -> None:
    source = REVIEW_JS.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")

    assert 'id="boxClassSelector"' in index
    assert "item.boxes.map((box) => ({ ...box }))" in source
    assert 'reviewState.current.annotation_mode === "single_class_locked"' in source
    assert "box.class_id = Number" in source
