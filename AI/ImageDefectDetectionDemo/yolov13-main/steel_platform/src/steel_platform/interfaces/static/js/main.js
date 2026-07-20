import { clearAssetDetail, renderAssetDetail } from "./asset-detail.js?v=__STATIC_VERSION__";
import { initFileManager, loadExplorer, loadProjects, selectNode } from "./file-manager.js?v=__STATIC_VERSION__";
import { initImportWizard } from "./import-wizard.js?v=__STATIC_VERSION__";
import { renderReviewReport } from "./review-report.js?v=__STATIC_VERSION__";
import { setState, state, syncStateFromLocation } from "./state.js?v=__STATIC_VERSION__";
import { reviewWorkspace } from "./review-workspace.js?v=__STATIC_VERSION__";
import { initModelWorkbench, renderModelWorkbench, useAssetForInference } from "./model-workbench.js?v=__STATIC_VERSION__";
import { initAnnotationCenter, renderAnnotationCenter } from "./annotation-center.js?v=__STATIC_VERSION__";
import { initMonitoringCenter, renderMonitoringCenter } from "./monitoring-center.js?v=__STATIC_VERSION__";

window.__steelPlatformStarted = true;

const $ = (id) => document.getElementById(id);
const navigation = [...document.querySelectorAll(".nav")];
const pages = ["files", "annotationCenter", "assetDetailView", "reviewWorkspace", "reviewReportView", "modelWorkbench", "monitoringCenter"];
let routing = false;

function showOnly(id) { pages.forEach((pageId) => $(pageId)?.classList.toggle("hidden", pageId !== id)); }
function setActiveNavigation(hash) { navigation.forEach((nav) => nav.classList.toggle("active", nav.getAttribute("href") === hash)); }
function showWorkspaceError(error, { startup = false } = {}) {
  const message = error?.message || "工作区加载失败。";
  $("resourceTree").textContent = message; $("assetSummary").textContent = message;
  if (startup) { const selector = $("projectSelector"); selector.innerHTML = '<option value="">项目加载失败</option>'; selector.disabled = true; $("openImport").disabled = true; }
}

async function ensureExplorer() {
  if (!state.projectId) return;
  if (!state.explorer || state.explorer.project?.id !== state.projectId) await loadExplorer(state.projectId);
}

export async function renderRoute() {
  if (routing) return;
  routing = true;
  try {
    await ensureExplorer();
    if (state.report && state.roundId) {
      showOnly("reviewReportView"); setActiveNavigation("#annotation"); await renderReviewReport(); return;
    }
    if (state.roundId) {
      showOnly("reviewWorkspace"); setActiveNavigation("#annotation"); await reviewWorkspace(); return;
    }
    if (state.assetId) {
      showOnly("assetDetailView"); setActiveNavigation("#data"); await renderAssetDetail(); return;
    }
    if (["#model", "#workbench", "#runs"].includes(window.location.hash)) {
      clearAssetDetail(); showOnly("modelWorkbench"); setActiveNavigation("#model");
      await renderModelWorkbench(); return;
    }
    if (["#annotation", "#review"].includes(window.location.hash)) {
      clearAssetDetail(); showOnly("annotationCenter"); setActiveNavigation("#annotation");
      await renderAnnotationCenter(); return;
    }
    if (window.location.hash === "#monitoring") {
      clearAssetDetail(); showOnly("monitoringCenter"); setActiveNavigation("#monitoring");
      await renderMonitoringCenter(); return;
    }
    clearAssetDetail(); showOnly("files");
    const hash = ["#data", "#files"].includes(window.location.hash) ? "#data" : "#data";
    setActiveNavigation(hash);
    const target = navigation.find((nav) => nav.getAttribute("href") === hash)?.dataset.nodeTarget;
    const routeNode = state.node || target;
    if (routeNode && state.explorer) await selectNode(routeNode, { replace: true, hash });
  } catch (error) {
    if (state.report) $("reviewReportMessage").textContent = error.message;
    else if (state.assetId) $("assetDetailMessage").textContent = error.message;
    else showWorkspaceError(error);
  } finally { routing = false; }
}

async function activatePrimaryNavigation(nav) {
  const hash = nav.getAttribute("href");
  setState({ roundId: null, report: false, assetId: null }, { hash });
  await ensureExplorer();
  clearAssetDetail();
  if (hash === "#model") {
    showOnly("modelWorkbench");
    setActiveNavigation(hash);
    await renderModelWorkbench();
    return;
  }
  if (hash === "#annotation") { showOnly("annotationCenter"); setActiveNavigation(hash); await renderAnnotationCenter(); return; }
  if (hash === "#monitoring") { showOnly("monitoringCenter"); setActiveNavigation(hash); await renderMonitoringCenter(); return; }
  showOnly("files");
  setActiveNavigation(hash);
  if (nav.dataset.nodeTarget) await selectNode(nav.dataset.nodeTarget, { hash });
}

navigation.forEach((nav) => nav.addEventListener("click", async (event) => { event.preventDefault(); await activatePrimaryNavigation(nav); }));

$("projectSelector")?.addEventListener("change", async (event) => {
  state.explorer = null;
  setState({ projectId: event.target.value || null, roundId: null, node: null, assetId: null, report: false, importSession: null }, { hash: "#data" });
  try { await loadExplorer(state.projectId); await renderRoute(); } catch (error) { showWorkspaceError(error); }
});
$("refreshExplorer")?.addEventListener("click", async () => { try { await loadExplorer(state.projectId); await renderRoute(); } catch (error) { showWorkspaceError(error); } });
$("backToFiles")?.addEventListener("click", async () => { setState({ assetId: null }, { hash: "#data" }); await renderRoute(); });
$("backToReview")?.addEventListener("click", async () => { setState({ report: false }, { hash: "#annotation" }); await renderRoute(); });
$("sendAssetToWorkbench")?.addEventListener("click", async () => {
  const assetId = state.assetId;
  setState({ assetId: null, roundId: null, report: false }, { hash: "#model" });
  await renderRoute();
  if (assetId) useAssetForInference(assetId);
});
$("reviewReportLink")?.addEventListener("click", async (event) => { event.preventDefault(); setState({ report: true }, { hash: "#annotation" }); await renderRoute(); });

window.addEventListener("popstate", async () => { syncStateFromLocation(); await renderRoute(); });

async function boot() {
  try {
    initFileManager(renderRoute);
    initImportWizard(async () => { await loadExplorer(state.projectId); await renderRoute(); });
    initModelWorkbench();
    initAnnotationCenter(renderRoute);
    initMonitoringCenter();
    await loadProjects();
    await renderRoute();
  } catch (error) { showWorkspaceError(error, { startup: true }); }
}

boot();
