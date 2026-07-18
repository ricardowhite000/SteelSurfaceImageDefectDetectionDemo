import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { formatBytes, formatDate, zh } from "./locale-zh.js?v=__STATIC_VERSION__";
import { setState, state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const resourceUi = { page: 1, pageSize: 48, q: "", sort: "name", order: "asc", request: 0, route: null };
let routeChanged = async () => {};
let searchTimer;

export function findNode(nodes, nodeId) {
  for (const node of nodes || []) {
    if (node.id === nodeId) return node;
    const child = findNode(node.children || [], nodeId);
    if (child) return child;
  }
  return null;
}

function treeMarkup(nodes, depth = 0) {
  return (nodes || []).map((node) => {
    const selected = node.id === state.node ? " is-selected" : "";
    const children = node.children?.length ? `<div class="tree-children">${treeMarkup(node.children, depth + 1)}</div>` : "";
    return `<div class="tree-node" role="treeitem" aria-level="${depth + 1}" aria-selected="${node.id === state.node}"><button class="tree-button${selected}" type="button" data-node-id="${escapeHtml(node.id)}" title="${escapeHtml(node.name)}"><span>${escapeHtml(node.name)}</span><small>${Number(node.count || 0)}</small></button>${children}</div>`;
  }).join("");
}

export function renderResourceTree(groups) {
  $("resourceTree").innerHTML = treeMarkup(groups);
  document.querySelectorAll("[data-node-id]").forEach((button) => button.addEventListener("click", () => selectNode(button.dataset.nodeId)));
}

function isGroup(node) { return node?.type === "group"; }
function defaultView(type) { return ["source", "collection", "review_round"].includes(type) ? "grid" : "list"; }
function viewMode(type) { return localStorage.getItem(`steel-platform:view:${type}`) || defaultView(type); }

function setView(type, mode, { save = true } = {}) {
  if (save) localStorage.setItem(`steel-platform:view:${type}`, mode);
  $("resourceContent").dataset.view = mode;
  $("gridView").classList.toggle("active", mode === "grid");
  $("listView").classList.toggle("active", mode === "list");
}

function setHeading(node, summary) {
  $("assetHeading").textContent = node?.name || "项目资源";
  $("assetSummary").textContent = summary;
  $("resourceBreadcrumb").innerHTML = `<button type="button" data-breadcrumb-root>文件</button><span>/</span><strong title="${escapeHtml(node?.name || "项目资源")}">${escapeHtml(node?.name || "项目资源")}</strong>`;
  document.querySelector("[data-breadcrumb-root]")?.addEventListener("click", () => selectNode("sources"));
}

function groupMarkup(node) {
  const children = node?.children || [];
  if (!children.length) return `<div class="empty-state">当前分类还没有资源。</div>`;
  return `<div class="resource-folder-grid">${children.map((item) => {
    const reviewLink = item.type === "review_round"
      ? `<a class="review-entry" href="/?project=${encodeURIComponent(state.projectId)}&round=${encodeURIComponent(item.id)}#review" data-open-review="${escapeHtml(item.id)}">进入复核</a>`
      : "";
    return `<article class="resource-folder-card" data-resource-id="${escapeHtml(item.id)}" tabindex="0"><div class="folder-icon" aria-hidden="true">▰</div><div class="folder-copy"><h3 title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</h3><p>${zh("resource", item.type)} · ${Number(item.count || 0)} 项</p><span class="status">${zh("status", item.status)}</span>${reviewLink}</div></article>`;
  }).join("")}</div>`;
}

function bindGroupActions(node) {
  document.querySelectorAll("[data-resource-id]").forEach((card) => {
    const open = () => selectNode(card.dataset.resourceId);
    card.addEventListener("dblclick", open);
    card.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
  });
  document.querySelectorAll("[data-open-review]").forEach((link) => link.addEventListener("click", async (event) => {
    event.preventDefault(); event.stopPropagation();
    setState({ roundId: link.dataset.openReview, report: false, assetId: null }, { hash: "#review" });
    await routeChanged();
  }));
}

function thumbnailUrl(assetId, size = 320) {
  return `${projectPath(state.projectId)}/assets/${encodeURIComponent(assetId)}/thumbnail?size=${size}`;
}

function resourceItemMarkup(item, mode) {
  const title = escapeHtml(item.name);
  const image = item.is_image && item.asset_id
    ? `<img src="${thumbnailUrl(item.asset_id)}" alt="" loading="lazy" decoding="async">`
    : `<div class="file-symbol" aria-hidden="true">${item.item_type === "weights" ? "W" : "F"}</div>`;
  if (mode === "list") {
    return `<button class="file-row" type="button" data-asset-id="${escapeHtml(item.asset_id || "")}" ${item.asset_id ? "" : "disabled"}><span class="file-row-thumb">${image}</span><span class="file-name" title="${title}">${title}</span><span>${zh("item", item.item_type)}</span><span>${formatBytes(item.size_bytes)}</span><span>${formatDate(item.created_at)}</span><span class="status">${zh("status", item.status)}</span></button>`;
  }
  return `<button class="file-card" type="button" data-asset-id="${escapeHtml(item.asset_id || "")}" ${item.asset_id ? "" : "disabled"}><span class="thumbnail">${image}</span><span class="file-card-copy"><strong title="${title}">${title}</strong><small>${formatBytes(item.size_bytes)} · ${zh("status", item.status)}</small></span></button>`;
}

function pagerMarkup(pagination) {
  if (!pagination.total) return "";
  return `<button type="button" data-page="${pagination.page - 1}" ${pagination.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${pagination.page} / ${Math.max(1, pagination.pages)} 页，共 ${pagination.total} 项</span><button type="button" data-page="${pagination.page + 1}" ${pagination.page >= pagination.pages ? "disabled" : ""}>下一页</button>`;
}

async function loadResource(node) {
  const requestId = ++resourceUi.request;
  resourceUi.route = node;
  setHeading(node, "正在加载文件…");
  $("resourceContent").innerHTML = `<div class="loading-state">正在加载文件…</div>`;
  $("resourcePager").innerHTML = "";
  const resource = { type: node.type, id: node.id };
  const query = new URLSearchParams({ page: resourceUi.page, page_size: resourceUi.pageSize, q: resourceUi.q, sort: resourceUi.sort, order: resourceUi.order });
  const page = await api(`${projectPath(state.projectId)}/resources/${resource.type}/${encodeURIComponent(resource.id)}/items?${query}`);
  if (requestId !== resourceUi.request) return;
  const mode = viewMode(node.type);
  setView(node.type, mode, { save: false });
  setHeading(node, `${page.pagination.total} 个文件 · ${zh("resource", node.type)} · ${zh("status", page.resource.status)}`);
  if (!page.items.length) {
    $("resourceContent").innerHTML = `<div class="empty-state">${resourceUi.q ? "没有找到匹配的文件。" : "当前资源中没有文件。"}</div>`;
  } else if (mode === "list") {
    $("resourceContent").innerHTML = `<div class="file-list-head"><span></span><span>名称</span><span>类型</span><span>大小</span><span>创建时间</span><span>状态</span></div><div class="file-list">${page.items.map((item) => resourceItemMarkup(item, mode)).join("")}</div>`;
  } else {
    $("resourceContent").innerHTML = `<div class="file-grid">${page.items.map((item) => resourceItemMarkup(item, mode)).join("")}</div>`;
  }
  $("resourcePager").innerHTML = pagerMarkup(page.pagination);
  document.querySelectorAll("[data-asset-id]:not([disabled])").forEach((button) => button.addEventListener("click", async () => {
    setState({ assetId: button.dataset.assetId, report: false }, { hash: "#files" });
    await routeChanged();
  }));
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
    resourceUi.page = Number(button.dataset.page); loadResource(node).catch(showResourceError);
  }));
}

function showResourceError(error) {
  $("assetSummary").textContent = error?.message || "资源加载失败。";
  $("resourceContent").innerHTML = `<div class="empty-state error">${escapeHtml(error?.message || "资源加载失败，请稍后重试。")}</div>`;
}

export async function renderCurrentNode(tree, nodeId) {
  const node = findNode(tree.groups, nodeId) || tree.groups[0] || null;
  if (!node) { setHeading(null, "当前项目没有可用资源。"); return; }
  if (isGroup(node)) {
    resourceUi.route = node;
    setHeading(node, `${node.count || 0} 项资源`);
    $("resourceContent").dataset.view = "folders";
    $("resourceContent").innerHTML = groupMarkup(node);
    $("resourcePager").innerHTML = "";
    bindGroupActions(node);
    return;
  }
  await loadResource(node);
}

export async function loadExplorer(projectId) {
  if (!projectId) return;
  const tree = await api(projectPath(projectId, "/explorer"));
  state.explorer = tree;
  if (!findNode(tree.groups, state.node)) setState({ node: tree.groups[0]?.id || null, assetId: null }, { replace: true });
  $("projectName").textContent = tree.project.name;
  renderResourceTree(tree.groups);
  await renderCurrentNode(tree, state.node);
}

export async function selectNode(nodeId, { replace = false, hash = "#files" } = {}) {
  if (!state.explorer) return;
  resourceUi.page = 1; resourceUi.q = ""; $("resourceSearch").value = "";
  setState({ node: nodeId, assetId: null, report: false, roundId: null }, { replace, hash });
  renderResourceTree(state.explorer.groups);
  await renderCurrentNode(state.explorer, nodeId);
}

export async function loadProjects() {
  const projects = await api("/api/v1/projects");
  const selector = $("projectSelector");
  selector.innerHTML = projects.length ? projects.map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("") : '<option value="">暂无项目</option>';
  selector.disabled = !projects.length;
  $("openImport").disabled = !projects.length;
  if (!projects.some((project) => project.id === state.projectId)) setState({ projectId: projects[0]?.id || null, node: null, assetId: null, roundId: null, report: false, importSession: null }, { replace: true });
  selector.value = state.projectId || "";
  if (state.projectId) await loadExplorer(state.projectId);
}

export function initFileManager(onRouteChanged) {
  routeChanged = onRouteChanged;
  $("resourceSearch").addEventListener("input", () => {
    clearTimeout(searchTimer); searchTimer = setTimeout(() => { resourceUi.q = $("resourceSearch").value; resourceUi.page = 1; if (resourceUi.route && !isGroup(resourceUi.route)) loadResource(resourceUi.route).catch(showResourceError); }, 250);
  });
  $("resourceSort").addEventListener("change", () => { resourceUi.sort = $("resourceSort").value; resourceUi.page = 1; if (resourceUi.route && !isGroup(resourceUi.route)) loadResource(resourceUi.route).catch(showResourceError); });
  $("resourceOrder").addEventListener("click", () => { resourceUi.order = resourceUi.order === "asc" ? "desc" : "asc"; $("resourceOrder").textContent = resourceUi.order === "asc" ? "升序" : "降序"; if (resourceUi.route && !isGroup(resourceUi.route)) loadResource(resourceUi.route).catch(showResourceError); });
  for (const [id, mode] of [["gridView", "grid"], ["listView", "list"]]) $(id).addEventListener("click", () => { if (!resourceUi.route || isGroup(resourceUi.route)) return; setView(resourceUi.route.type, mode); loadResource(resourceUi.route).catch(showResourceError); });
  $("refreshResource").addEventListener("click", () => { if (resourceUi.route && !isGroup(resourceUi.route)) loadResource(resourceUi.route).catch(showResourceError); else if (state.explorer) renderCurrentNode(state.explorer, state.node); });
}
