import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { setState, state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
let options = null;
let workOrders = [];
let reroute = async () => {};
let amendmentParent = null;

const labels = {
  draft: "草稿", ready: "就绪", active: "进行中", completed: "已完成", archived: "已归档", cancelled: "已取消",
  succeeded: "成功", failed: "失败", interrupted: "已中断", running: "运行中", planned: "已计划",
  manual_annotation: "初始标注", inference_review: "机器候选复核", amendment: "修订工单",
  no_box: "无框", class_conflict: "类别冲突", class_mismatch: "类别冲突", low_confidence: "低置信度", unannotated: "未标注",
};

function riskLabel(value) { return String(value).split(";").map((part) => labels[part] || part).join("＋"); }

function message(value = "", kind = "") { $("annotationMessage").textContent = value; $("annotationMessage").className = `message ${kind}`; }
function endpoint(suffix = "") { return projectPath(state.projectId, `/annotation-work-orders${suffix}`); }
function requestOptions(method, body, key = crypto.randomUUID()) { return { method, headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify(body) }; }

function selectedClassIds() { return [...$("workOrderClasses").querySelectorAll("input:checked")].map((input) => Number(input.value)); }
function spec() {
  const taskType = $("workOrderTaskType").value;
  const [sourceType, sourceId] = $("workOrderInference").value.split(":", 2);
  const riskStatuses = [];
  if ($("riskNoBox").checked) riskStatuses.push("no_box");
  if ($("riskConflict").checked) riskStatuses.push("class_conflict");
  if ($("riskLowConfidence").checked) riskStatuses.push("low_confidence");
  const maxBoxes = $("workOrderMaxBoxes").value;
  return {
    name: $("workOrderName").value.trim(), description: "由标注中心条件筛选创建",
    task_type: taskType, source_type: sourceType, source_id: sourceId,
    filters: {
      class_ids: selectedClassIds(), risk_statuses: riskStatuses,
      max_min_confidence: $("riskLowConfidence").checked ? Number($("workOrderConfidence").value) : null,
      include_no_box: $("riskNoBox").checked, box_count_min: Number($("workOrderMinBoxes").value || 0),
      box_count_max: maxBoxes === "" ? null : Number(maxBoxes), comparison_score_min: null,
      total_limit: Number($("workOrderLimit").value), per_class_limit: null,
      exclude_reviewed: $("workOrderExcludeReviewed").checked, seed: Number($("workOrderSeed").value || 42),
    },
  };
}

function renderOptions() {
  renderSourceOptions();
  $("workOrderClasses").innerHTML = options.classes.map((item) => `<label><input type="checkbox" value="${item.id}">${item.name}</label>`).join("");
}

function renderSourceOptions() {
  const manual = $("workOrderTaskType").value === "manual_annotation";
  $("workOrderSourceLabel").firstChild.textContent = manual ? "未标注数据来源" : "推理运行";
  const entries = manual
    ? [...options.sources.map((item) => ({ ...item, type: "source" })), ...options.collections.map((item) => ({ ...item, type: "collection" }))]
    : options.inference_runs.map((item) => ({ ...item, type: "inference" }));
  $("workOrderInference").innerHTML = entries.length
    ? entries.map((item) => `<option value="${item.type}:${item.id}">${item.name}${item.status ? ` · ${labels[item.status] || item.status}` : ""}</option>`).join("")
    : '<option value=":">暂无可用来源</option>';
  ["riskNoBox", "riskConflict", "riskLowConfidence", "workOrderConfidence"].forEach((id) => { $(id).disabled = manual; });
}

function renderList() {
  const container = $("workOrderList");
  if (!workOrders.length) { container.innerHTML = '<div class="empty-state">暂无标注工单。可以从一次推理运行创建专项复核工单。</div>'; return; }
  container.innerHTML = workOrders.map((order) => `<article class="work-order-card" data-id="${order.id}">
    <div><span class="status-badge ${order.status === "completed" ? "success" : ""}">${labels[order.status] || order.status}</span><small>${labels[order.task_type] || order.task_type}</small>
    <h3 title="${order.name}">${order.name}</h3><p>${order.target_count} 张图片 · 版本 ${order.revision}</p></div>
    <div class="work-order-actions"><button data-action="open">${order.status === "completed" || order.status === "archived" ? "浏览档案" : "进入工单"}</button><button data-action="report">查看报告</button>${order.status === "completed" || order.status === "archived" ? '<button data-action="amend">创建修订</button>' : ""}</div>
  </article>`).join("");
  container.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => act(button.closest("article").dataset.id, button.dataset.action)));
}

async function act(id, action) {
  if (action === "report") setState({ roundId: id, report: true, assetId: null }, { hash: "#annotation" });
  else if (action === "amend") { await openAmendment(id); return; }
  else setState({ roundId: id, report: false, assetId: null }, { hash: "#annotation" });
  await reroute();
}

async function preview() {
  try {
    const result = await api(endpoint("/preview"), requestOptions("POST", spec()));
    $("workOrderPreview").innerHTML = `<strong>匹配 ${result.matched} 张，最终选择 ${result.selected} 张</strong><p>类别：${Object.entries(result.by_class).map(([key, value]) => `${key} ${value}`).join("；") || "无"}</p><p>风险：${Object.entries(result.by_risk).map(([key, value]) => `${riskLabel(key)} ${value}`).join("；") || "无"}</p>`;
  } catch (error) { $("workOrderPreview").textContent = error.message; }
}

async function createAndFreeze(event) {
  event.preventDefault();
  try {
    const created = await api(endpoint(), requestOptions("POST", spec()));
    const frozen = await api(endpoint(`/${created.id}/freeze`), requestOptions("POST", { expected_revision: created.revision }));
    message(`已创建并冻结工单“${frozen.name}”。`);
    setState({ roundId: frozen.id, report: false, assetId: null }, { hash: "#annotation" });
    await reroute();
  } catch (error) { message(error.message, "error"); }
}

async function openAmendment(parentId) {
  amendmentParent = parentId;
  const order = workOrders.find((item) => item.id === parentId);
  $("amendmentName").value = `${order?.name || "历史工单"}－修订`;
  $("amendmentItems").innerHTML = '<div class="empty-state">正在加载图片…</div>';
  $("amendmentDialog").showModal();
  try {
    const page = await api(endpoint(`/${parentId}/items`));
    $("amendmentItems").innerHTML = page.items.map((item) => `<label><input type="checkbox" value="${item.id}"><span>${item.filename}</span><small>${labels[item.state] || item.state}</small></label>`).join("") || '<div class="empty-state">原工单没有图片。</div>';
  } catch (error) { $("amendmentMessage").textContent = error.message; }
}

async function createAmendment() {
  const itemIds = [...$("amendmentItems").querySelectorAll("input:checked")].map((item) => item.value);
  if (!itemIds.length) { $("amendmentMessage").textContent = "请至少选择一张图片。"; return; }
  try {
    const created = await api(endpoint(`/${amendmentParent}/amendments`), requestOptions("POST", { name: $("amendmentName").value.trim(), item_ids: itemIds }));
    $("amendmentDialog").close(); setState({ roundId: created.id, report: false }, { hash: "#annotation" }); await reroute();
  } catch (error) { $("amendmentMessage").textContent = error.message; }
}

export function initAnnotationCenter(renderRoute) {
  reroute = renderRoute;
  $("annotationRefresh").addEventListener("click", renderAnnotationCenter);
  $("openWorkOrderCreator").addEventListener("click", () => $("workOrderCreator").classList.remove("hidden"));
  $("closeWorkOrderCreator").addEventListener("click", () => $("workOrderCreator").classList.add("hidden"));
  $("previewWorkOrder").addEventListener("click", preview);
  $("workOrderTaskType").addEventListener("change", renderSourceOptions);
  $("workOrderForm").addEventListener("submit", createAndFreeze);
  $("createAmendment").addEventListener("click", createAmendment);
}

export async function renderAnnotationCenter() {
  if (!state.projectId) { message("请先选择项目。", "error"); return; }
  try { [options, workOrders] = await Promise.all([api(endpoint("/options")), api(endpoint())]); renderOptions(); renderList(); message(); }
  catch (error) { message(error.message, "error"); }
}
