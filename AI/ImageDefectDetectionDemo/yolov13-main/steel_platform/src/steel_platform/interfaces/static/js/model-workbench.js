import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { formatDate, zh } from "./locale-zh.js?v=__STATIC_VERSION__";
import { state } from "./state.js?v=__STATIC_VERSION__";
import { renderLossChart } from "./training-chart.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
const view = { options: null, jobs: [], models: [], selectedJob: null, logOffset: 0, timer: null };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function key(prefix) {
  return `${prefix}-${Date.now()}-${crypto.randomUUID?.() || Math.random().toString(16).slice(2)}`;
}

function setMessage(message, error = false) {
  const node = $("workbenchMessage");
  node.textContent = message || "";
  node.classList.toggle("error", error);
}

function optionRows(rows, { readyDetector = false } = {}) {
  const filtered = readyDetector
    ? rows.filter((row) => row.purpose === "detector" && row.verification_status === "ready")
    : rows;
  return filtered.map((row) => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)} · ${escapeHtml(row.id.slice(0, 8))}</option>`).join("");
}

function populateOptions() {
  const options = view.options;
  const datasets = optionRows(options.datasets || []);
  $("trainingDataset").innerHTML = datasets || '<option value="">没有可用数据集</option>';
  $("trainingModel").innerHTML = optionRows(options.models || []) || '<option value="">没有已验证模型</option>';
  $("inferenceModel").innerHTML = optionRows(options.models || [], { readyDetector: true }) || '<option value="">没有可用检测器</option>';
  $("inferenceSource").innerHTML = optionRows(options.sources || []) || '<option value="">没有可用数据源</option>';
  const devices = (options.allowed_devices || ["cpu"]).map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
  $("trainingDevice").innerHTML = devices;
  $("inferenceDevice").innerHTML = devices;
}

function switchTab(tab) {
  document.querySelectorAll("[data-workbench-tab]").forEach((button) => button.classList.toggle("active", button.dataset.workbenchTab === tab));
  const panels = { training: "workbenchTrainingPanel", inference: "workbenchInferencePanel", jobs: "workbenchJobsPanel", models: "workbenchModelsPanel" };
  Object.entries(panels).forEach(([name, id]) => $(id).classList.toggle("hidden", name !== tab));
  if (tab === "jobs") loadJobs();
  if (tab === "models") loadModels();
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  return api(projectPath(state.projectId, path), { ...options, headers });
}

async function createTraining(event) {
  event.preventDefault();
  const kind = $("trainingJobKind").value;
  const preset = kind === "evaluate" ? "fixed_val" : $("trainingPreset").value;
  const parameters = kind === "evaluate" ? {
    imgsz: Number($("trainingImageSize").value), batch: Number($("trainingBatch").value),
    workers: Number($("trainingWorkers").value), device: $("trainingDevice").value,
  } : {
    epochs: Number($("trainingEpochs").value), imgsz: Number($("trainingImageSize").value),
    batch: Number($("trainingBatch").value), patience: Number($("trainingPatience").value),
    workers: Number($("trainingWorkers").value), device: $("trainingDevice").value,
  };
  try {
    const job = await request("/jobs", { method: "POST", body: JSON.stringify({
      name: $("trainingJobName").value, kind, preset,
      input_refs: [
        { role: "dataset", ref_type: "dataset", ref_id: $("trainingDataset").value },
        { role: "model", ref_type: "model", ref_id: $("trainingModel").value },
      ], parameters,
    }) });
    setMessage(`任务“${job.name}”已创建为草稿。请在任务中心检查并生成命令。`);
    view.selectedJob = job;
    switchTab("jobs");
  } catch (error) { setMessage(error.message, true); }
}

async function createInference(event) {
  event.preventDefault();
  try {
    const refType = $("inferenceSourceType").value;
    let refId = refType === "asset" ? $("inferenceAssetId").value.trim() : $("inferenceSource").value;
    const file = $("inferenceSourceFile").files?.[0];
    if (refType === "asset" && file) {
      const staged = await api(projectPath(state.projectId, "/inference-files"), {
        method: "POST", headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": file.name }, body: file,
      });
      refId = staged.asset_id;
      $("inferenceAssetId").value = refId;
    }
    if (!refId) throw new Error("请选择数据源，或填写图片/视频资产编号。");
    const job = await request("/jobs", { method: "POST", body: JSON.stringify({
      name: $("inferenceJobName").value, kind: "infer", preset: $("inferencePreset").value,
      input_refs: [
        { role: "model", ref_type: "model", ref_id: $("inferenceModel").value },
        { role: "source", ref_type: refType, ref_id: refId },
      ],
      parameters: { conf: Number($("inferenceConfidence").value), imgsz: Number($("inferenceImageSize").value), device: $("inferenceDevice").value },
    }) });
    setMessage(`推理任务“${job.name}”已创建。`);
    view.selectedJob = job;
    switchTab("jobs");
  } catch (error) { setMessage(error.message, true); }
}

function statusTone(status) { return ["failed", "interrupted"].includes(status) ? "danger" : status === "succeeded" ? "success" : ""; }

function renderJobs() {
  const node = $("workbenchJobList");
  if (!view.jobs.length) { node.innerHTML = '<div class="empty-state">暂无任务。</div>'; return; }
  node.innerHTML = view.jobs.map((job) => `<button type="button" class="job-row ${view.selectedJob?.id === job.id ? "active" : ""}" data-job-id="${escapeHtml(job.id)}">
    <span><strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(zh("jobKind", job.kind))} · ${escapeHtml(zh("status", job.status))}</small></span>
    <time>${escapeHtml(formatDate(job.created_at))}</time><i class="job-status ${statusTone(job.status)}"></i></button>`).join("");
  node.querySelectorAll("[data-job-id]").forEach((button) => button.addEventListener("click", () => selectJob(button.dataset.jobId)));
}

function jobProgress(job) {
  const current = Number(job.progress?.current || 0); const total = Number(job.progress?.total || 0);
  if (!total) return "等待进度数据";
  return `${Math.min(100, Math.round(current / total * 100))}%（${current}/${total}）`;
}

function renderJobDetail() {
  const job = view.selectedJob;
  if (!job) return;
  $("workbenchJobDetail").innerHTML = `<div class="job-detail-head"><div><p class="eyebrow">${escapeHtml(zh("jobKind", job.kind))}</p><h2>${escapeHtml(job.name)}</h2></div><span class="status-badge ${statusTone(job.status)}">${escapeHtml(zh("status", job.status))}</span></div>
    <dl class="detail-list"><dt>任务编号</dt><dd>${escapeHtml(job.id)}</dd><dt>预设</dt><dd>${escapeHtml(zh("preset", job.preset))}</dd><dt>进度</dt><dd>${escapeHtml(jobProgress(job))}</dd><dt>创建时间</dt><dd>${escapeHtml(formatDate(job.created_at))}</dd><dt>退出码</dt><dd>${escapeHtml(job.exit_code ?? "—")}</dd><dt>失败原因</dt><dd>${escapeHtml(job.error_message || "—")}</dd></dl>
    <div class="job-actions"><button id="prepareSelectedJob" type="button" ${job.status !== "draft" ? "disabled" : ""}>生成并冻结命令</button><button id="showJobResults" type="button">刷新结果</button></div>`;
  $("prepareSelectedJob")?.addEventListener("click", prepareSelectedJob);
  $("showJobResults")?.addEventListener("click", loadResults);
  $("workbenchCommandPreview").textContent = job.command || "任务尚未准备，当前没有命令快照。";
  $("copyWorkbenchCommand").disabled = !job.command;
  $("launchWorkbenchTerminal").disabled = job.status !== "ready";
  $("cancelWorkbenchJob").disabled = !["draft", "ready", "running"].includes(job.status);
  $("ingestWorkbenchResults").disabled = job.status === "running" || !job.runtime;
}

async function loadJobs() {
  if (!state.projectId) return;
  try {
    view.jobs = await request("/jobs");
    if (view.selectedJob) view.selectedJob = view.jobs.find((row) => row.id === view.selectedJob.id) || null;
    renderJobs();
    if (view.selectedJob) { renderJobDetail(); await loadLog(); if (view.selectedJob.status === "succeeded") await loadResults(); }
  } catch (error) { setMessage(error.message, true); }
}

async function selectJob(jobId) {
  view.selectedJob = await request(`/jobs/${jobId}`);
  view.logOffset = 0;
  renderJobs(); renderJobDetail(); await loadLog(true); await loadResults();
}

async function prepareSelectedJob() {
  try {
    const job = view.selectedJob;
    view.selectedJob = await request(`/jobs/${job.id}/prepare`, { method: "POST", headers: { "Idempotency-Key": key("prepare") }, body: JSON.stringify({ expected_revision: job.revision }) });
    setMessage("命令快照已生成。请检查命令后复制，或打开 PowerShell 人工确认执行。")
    await loadJobs();
  } catch (error) { setMessage(error.message, true); }
}

async function launchTerminal() {
  try {
    const job = view.selectedJob;
    view.selectedJob = await request(`/jobs/${job.id}/terminal-launch`, { method: "POST", headers: { "Idempotency-Key": key("launch") }, body: JSON.stringify({ expected_revision: job.revision }) });
    setMessage("PowerShell 已打开。请在终端核对任务信息并按 Enter 开始执行。")
    await loadJobs();
  } catch (error) { setMessage(error.message, true); }
}

async function cancelJob() {
  try {
    const job = view.selectedJob;
    view.selectedJob = await request(`/jobs/${job.id}/cancel`, { method: "POST", headers: { "Idempotency-Key": key("cancel") }, body: JSON.stringify({ expected_revision: job.revision }) });
    setMessage("取消请求已提交。运行中的任务会在安全检查点停止。")
    await loadJobs();
  } catch (error) { setMessage(error.message, true); }
}

async function ingestResults() {
  try {
    const job = view.selectedJob;
    view.selectedJob = await request(`/jobs/${job.id}/ingest`, { method: "POST", headers: { "Idempotency-Key": key("ingest") }, body: JSON.stringify({ expected_revision: job.revision }) });
    setMessage("已有产物已重新校验并登记。")
    await loadJobs(); await loadResults();
  } catch (error) { setMessage(error.message, true); }
}

async function loadLog(reset = false) {
  if (!view.selectedJob) return;
  if (reset) { view.logOffset = 0; $("workbenchLog").textContent = ""; }
  try {
    const log = await request(`/jobs/${view.selectedJob.id}/log?after=${view.logOffset}`);
    if (log.content) $("workbenchLog").textContent += log.content;
    if (!$("workbenchLog").textContent) $("workbenchLog").textContent = "暂无日志。";
    view.logOffset = log.next_offset;
  } catch (error) { $("workbenchLog").textContent = `日志读取失败：${error.message}`; }
}

async function loadResults() {
  if (!view.selectedJob) return;
  try {
    const result = await request(`/jobs/${view.selectedJob.id}/results`);
    const resultFiles = result.files || [];
    const previews = resultFiles.map((file) => {
      if (file.media_type?.startsWith("video/")) {
        const playable = ["video/mp4", "video/webm", "video/ogg"].includes(file.media_type);
        if (playable) return `<figure class="result-media"><video controls preload="metadata" src="${escapeHtml(file.content_url)}"></video><figcaption>${escapeHtml(file.relative_path)}</figcaption><p class="video-playback-error hidden">浏览器无法解码该视频，可使用下方文件链接下载查看。</p></figure>`;
        return `<figure class="result-media result-media-fallback"><div class="video-format-notice">当前格式不能在浏览器内播放，请使用下方文件链接下载查看。</div><figcaption>${escapeHtml(file.relative_path)}</figcaption></figure>`;
      }
      if (file.media_type?.startsWith("image/") && /(^|\/)(results|confusion_matrix|PR_curve|F1_curve|P_curve|R_curve)\.(png|jpg|jpeg)$/i.test(file.relative_path)) return `<figure class="result-media"><img loading="lazy" src="${escapeHtml(file.content_url)}" alt="${escapeHtml(file.relative_path)}"><figcaption>${escapeHtml(file.relative_path)}</figcaption></figure>`;
      return "";
    }).join("");
    const files = resultFiles.map((file) => `<li><a href="${escapeHtml(file.download_url)}" download="${escapeHtml(file.download_name)}">${escapeHtml(file.relative_path)}</a><small>${escapeHtml(file.sha256.slice(0, 12))}…</small></li>`).join("");
    $("workbenchResults").innerHTML = `${renderLossChart(result.series || [])}${previews ? `<div class="result-media-grid">${previews}</div>` : ""}${files ? `<ul class="result-files">${files}</ul>` : '<p class="muted">尚无已登记结果。</p>'}`;
    $("workbenchResults").querySelectorAll("video").forEach((video) => video.addEventListener("error", () => {
      video.closest("figure")?.querySelector(".video-playback-error")?.classList.remove("hidden");
    }, { once: true }));
  } catch (error) { $("workbenchResults").innerHTML = `<p class="message error">结果读取失败：${escapeHtml(error.message)}</p>`; }
}

async function loadModels() {
  if (!state.projectId) return;
  try {
    view.models = await request("/models");
    $("workbenchModelList").innerHTML = `<table><thead><tr><th>名称</th><th>格式</th><th>用途</th><th>校验状态</th><th>评估状态</th><th>创建时间</th></tr></thead><tbody>${view.models.map((model) => `<tr><td>${escapeHtml(model.name)}<small class="block-muted">${escapeHtml(model.id)}</small></td><td>${escapeHtml(model.format.toUpperCase())}</td><td>${escapeHtml(zh("modelPurpose", model.purpose))}</td><td>${escapeHtml(zh("status", model.verification_status))}</td><td>${escapeHtml(zh("status", model.evaluation_status))}</td><td>${escapeHtml(formatDate(model.created_at))}</td></tr>`).join("") || '<tr><td colspan="6" class="empty-row">暂无模型。</td></tr>'}</tbody></table>`;
  } catch (error) { setMessage(error.message, true); }
}

async function importModel(event) {
  event.preventDefault();
  try {
    const file = $("modelImportFile").files?.[0];
    let assetId = $("modelImportAssetId").value.trim();
    if (file) {
      const staged = await api(projectPath(state.projectId, "/model-files"), {
        method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Filename": file.name }, body: file,
      });
      assetId = staged.asset_id;
      $("modelImportAssetId").value = assetId;
      if (!$("modelImportName").value) $("modelImportName").value = file.name.replace(/\.(pt|onnx)$/i, "");
      $("modelImportFormat").value = staged.format;
    }
    if (!assetId) throw new Error("请选择 .pt/.onnx 文件，或填写已有文件资产编号。");
    const result = await request("/model-imports", { method: "POST", body: JSON.stringify({
      name: $("modelImportName").value, weights_asset_id: assetId,
      format: $("modelImportFormat").value, purpose: $("modelImportPurpose").value,
      class_names: null, source_note: $("modelImportNote").value,
    }) });
    setMessage(result.verification_job_id ? "模型已登记，并创建可加载性校验任务。" : "ONNX 已登记为仅归档模型。")
    await loadOptions(); await loadModels();
  } catch (error) { setMessage(error.message, true); }
}

async function loadOptions() {
  if (!state.projectId) return;
  view.options = await request("/workbench/options");
  populateOptions();
}

export async function renderModelWorkbench() {
  if (!state.projectId) { setMessage("请先选择项目。", true); return; }
  await loadOptions(); await loadJobs();
  if (!view.timer) view.timer = window.setInterval(async () => {
    if (window.location.hash === "#workbench" && view.selectedJob) await loadJobs();
  }, 2500);
}

export function useAssetForInference(assetId) {
  switchTab("inference");
  $("inferenceSourceType").value = "asset";
  $("inferenceSourceSelectLabel").classList.add("hidden");
  $("inferenceAssetLabel").classList.remove("hidden");
  $("inferenceAssetId").value = assetId;
  setMessage("已带入图片资产。请选择检测模型并创建推理任务。");
}

export function initModelWorkbench() {
  document.querySelectorAll("[data-workbench-tab]").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.workbenchTab)));
  $("trainingJobForm")?.addEventListener("submit", createTraining);
  $("inferenceJobForm")?.addEventListener("submit", createInference);
  $("modelImportForm")?.addEventListener("submit", importModel);
  $("workbenchRefresh")?.addEventListener("click", renderModelWorkbench);
  $("refreshJobs")?.addEventListener("click", loadJobs);
  $("refreshModels")?.addEventListener("click", loadModels);
  $("copyWorkbenchCommand")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(view.selectedJob?.command || ""); setMessage("命令已复制到剪贴板。");
  });
  $("launchWorkbenchTerminal")?.addEventListener("click", launchTerminal);
  $("cancelWorkbenchJob")?.addEventListener("click", cancelJob);
  $("ingestWorkbenchResults")?.addEventListener("click", ingestResults);
  $("trainingJobKind")?.addEventListener("change", () => {
    const evaluate = $("trainingJobKind").value === "evaluate";
    $("trainingPreset").disabled = evaluate;
    $("trainingEpochs").disabled = evaluate;
  });
  $("inferenceSourceType")?.addEventListener("change", () => {
    const usesAsset = $("inferenceSourceType").value === "asset";
    $("inferenceSourceSelectLabel").classList.toggle("hidden", usesAsset);
    $("inferenceAssetLabel").classList.toggle("hidden", !usesAsset);
  });
}
