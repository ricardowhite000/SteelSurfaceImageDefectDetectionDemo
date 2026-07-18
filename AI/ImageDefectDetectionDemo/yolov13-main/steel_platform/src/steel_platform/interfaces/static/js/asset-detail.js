import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { classLabels, formatBytes, formatDate, zh } from "./locale-zh.js?v=__STATIC_VERSION__";
import { findNode } from "./file-manager.js?v=__STATIC_VERSION__";
import { state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const detailState = { detail: null, image: null, overlayId: null };
let resizeBound = false;

function selectedOverlay() { return detailState.detail?.overlays.find((item) => item.id === detailState.overlayId) || null; }

function draw() {
  const canvas = $("assetDetailCanvas");
  const stage = $("detailCanvasStage");
  const image = detailState.image;
  if (!canvas || !image) return;
  const rect = stage.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  canvas.style.width = `${rect.width}px`; canvas.style.height = `${rect.height}px`;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  const scale = Math.min((rect.width - 32) / image.naturalWidth, (rect.height - 32) / image.naturalHeight);
  const width = image.naturalWidth * scale; const height = image.naturalHeight * scale;
  const x = (rect.width - width) / 2; const y = (rect.height - height) / 2;
  context.drawImage(image, x, y, width, height);
  const overlay = selectedOverlay();
  for (const box of overlay?.boxes || []) {
    const left = x + (box.x_center - box.width / 2) * width;
    const top = y + (box.y_center - box.height / 2) * height;
    const boxWidth = box.width * width; const boxHeight = box.height * height;
    context.strokeStyle = "#ff5a36"; context.lineWidth = 2; context.strokeRect(left, top, boxWidth, boxHeight);
    const classCode = ["Cr", "In", "Pa", "PS", "RS", "Sc"][box.class_id] || String(box.class_id);
    const label = classLabels[classCode] || classCode;
    context.font = '12px "Microsoft YaHei",sans-serif';
    const labelWidth = context.measureText(label).width + 12;
    context.fillStyle = "#ff5a36"; context.fillRect(left, Math.max(y, top - 22), labelWidth, 22);
    context.fillStyle = "#fff"; context.fillText(label, left + 6, Math.max(y + 15, top - 7));
  }
}

function overlayLabel(overlay) {
  return `${zh("origin", overlay.origin)} · ${zh("status", overlay.decision)} · ${overlay.box_count} 个框`;
}

function renderMetadata(detail) {
  const overlay = selectedOverlay();
  const values = [
    ["资产编号", detail.asset_id || state.assetId],
    ["图片尺寸", `${detail.width} × ${detail.height} 像素`],
    ["文件大小", formatBytes(detail.size_bytes)], ["媒体类型", detail.media_type],
    ["来源", detail.source_name || "—"], ["资源状态", zh("status", detail.status)],
    ["创建时间", formatDate(detail.created_at)], ["SHA256", detail.sha256],
    ["当前标签", overlay ? overlayLabel(overlay) : "无可用检测框"],
    ["标签哈希", overlay?.sha256 || "—"], ["父标签版本", overlay?.parent_id || "—"],
  ];
  $("assetDetailMeta").innerHTML = values.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

export async function renderAssetDetail() {
  const resource = findNode(state.explorer?.groups, state.node);
  if (!state.projectId || !state.assetId || !resource || resource.type === "group") throw new Error("图片详情地址缺少有效的项目、资源或图片编号。");
  $("assetDetailMessage").textContent = "正在加载原图和检测框…";
  const detail = await api(`${projectPath(state.projectId)}/resources/${resource.type}/${encodeURIComponent(resource.id)}/assets/${encodeURIComponent(state.assetId)}`);
  detailState.detail = detail; detailState.overlayId = detail.selected_overlay_id;
  $("detailFilename").textContent = detail.name;
  $("detailBreadcrumb").textContent = `文件 / ${resource.name} / ${detail.name}`;
  const selector = $("overlaySelector");
  selector.innerHTML = detail.overlays.length ? detail.overlays.map((overlay) => `<option value="${escapeHtml(overlay.id)}">${escapeHtml(overlayLabel(overlay))}</option>`).join("") : '<option value="">无检测框版本</option>';
  selector.disabled = !detail.overlays.length; selector.value = detailState.overlayId || "";
  selector.onchange = () => { detailState.overlayId = selector.value || null; renderMetadata(detail); draw(); };
  renderMetadata(detail);
  const image = new Image();
  await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error("原始图片加载失败，请检查数据源是否在线。")); image.src = `${projectPath(state.projectId)}/assets/${encodeURIComponent(state.assetId)}/content`; });
  detailState.image = image; $("assetDetailMessage").textContent = ""; draw();
  if (!resizeBound) { window.addEventListener("resize", draw); resizeBound = true; }
}

export function clearAssetDetail() { detailState.detail = null; detailState.image = null; detailState.overlayId = null; }
