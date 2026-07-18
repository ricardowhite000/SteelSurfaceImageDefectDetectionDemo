import { clearAssetDetail, renderAssetDetail } from "./asset-detail.js?v=__STATIC_VERSION__";
import { initFileManager, loadExplorer, loadProjects, selectNode } from "./file-manager.js?v=__STATIC_VERSION__";
import { initImportWizard } from "./import-wizard.js?v=__STATIC_VERSION__";
import { renderReviewReport } from "./review-report.js?v=__STATIC_VERSION__";
import { setState, state, syncStateFromLocation } from "./state.js?v=__STATIC_VERSION__";
import { reviewWorkspace } from "./review-workspace.js?v=__STATIC_VERSION__";
import { initModelWorkbench, renderModelWorkbench, useAssetForInference } from "./model-workbench.js?v=__STATIC_VERSION__";

window.__steelPlatformStarted = true;

const $ = (id) => document.getElementById(id);
const navigation = [...document.querySelectorAll(".nav")];
const pages = ["files", "assetDetailView", "reviewWorkspace", "reviewReportView", "modelWorkbench"];
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
      showOnly("reviewReportView"); setActiveNavigation("#review"); await renderReviewReport(); return;
    }
    if (state.roundId) {
      showOnly("reviewWorkspace"); setActiveNavigation("#review"); await reviewWorkspace(); return;
    }
    if (state.assetId) {
      showOnly("assetDetailView"); setActiveNavigation("#files"); await renderAssetDetail(); return;
    }
    if (window.location.hash === "#workbench") {
      clearAssetDetail(); showOnly("modelWorkbench"); setActiveNavigation("#workbench");
      await renderModelWorkbench(); return;
    }
    clearAssetDetail(); showOnly("files");
    const hash = ["#files", "#review", "#runs"].includes(window.location.hash) ? window.location.hash : "#files";
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
  if (hash === "#workbench") {
    showOnly("modelWorkbench");
    setActiveNavigation(hash);
    await renderModelWorkbench();
    return;
  }
  showOnly("files");
  setActiveNavigation(hash);
  if (nav.dataset.nodeTarget) await selectNode(nav.dataset.nodeTarget, { hash });
}

navigation.forEach((nav) => nav.addEventListener("click", async (event) => { event.preventDefault(); await activatePrimaryNavigation(nav); }));

$("projectSelector")?.addEventListener("change", async (event) => {
  state.explorer = null;
  setState({ projectId: event.target.value || null, roundId: null, node: null, assetId: null, report: false, importSession: null }, { hash: "#files" });
  try { await loadExplorer(state.projectId); await renderRoute(); } catch (error) { showWorkspaceError(error); }
});
$("refreshExplorer")?.addEventListener("click", async () => { try { await loadExplorer(state.projectId); await renderRoute(); } catch (error) { showWorkspaceError(error); } });
$("backToFiles")?.addEventListener("click", async () => { setState({ assetId: null }, { hash: "#files" }); await renderRoute(); });
$("backToReview")?.addEventListener("click", async () => { setState({ report: false }, { hash: "#review" }); await renderRoute(); });
$("sendAssetToWorkbench")?.addEventListener("click", async () => {
  const assetId = state.assetId;
  setState({ assetId: null, roundId: null, report: false }, { hash: "#workbench" });
  await renderRoute();
  if (assetId) useAssetForInference(assetId);
});
$("reviewReportLink")?.addEventListener("click", async (event) => { event.preventDefault(); setState({ report: true }, { hash: "#review" }); await renderRoute(); });

window.addEventListener("popstate", async () => { syncStateFromLocation(); await renderRoute(); });

async function boot() {
  try {
    initFileManager(renderRoute);
    initImportWizard(async () => { await loadExplorer(state.projectId); await renderRoute(); });
    initModelWorkbench();
    await loadProjects();
    await renderRoute();
  } catch (error) { showWorkspaceError(error, { startup: true }); }
}

boot();
