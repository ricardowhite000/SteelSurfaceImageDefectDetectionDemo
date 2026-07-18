import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { setState, state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
const imageExtensions = new Set(["jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"]);
const labelExtensions = new Set(["txt", "json", "xml"]);
const wizard = { step: 1, files: [], counts: null, session: null, committed: false };

function extension(file) { return file.name.split(".").pop().toLowerCase(); }
function countFiles(files) {
  const paths = new Set();
  const counts = { images: 0, labels: 0, unsupported: 0, duplicates: 0, errors: 0 };
  files.forEach((file) => {
    const path = file.webkitRelativePath || file.name;
    if (paths.has(path)) counts.duplicates += 1; else paths.add(path);
    if (!file.size) counts.errors += 1;
    if (imageExtensions.has(extension(file))) counts.images += 1;
    else if (labelExtensions.has(extension(file))) counts.labels += 1;
    else counts.unsupported += 1;
  });
  return counts;
}
function selectionError(counts) {
  if (counts.images === 0) return "所选文件夹中没有受支持的图片。";
  if (counts.duplicates) return "所选文件夹中存在重复的相对路径。";
  if (counts.errors) return "不能导入空文件，请先删除或替换。";
  return "";
}
function countsMarkup(counts) { const labels = { images: "图片", labels: "标签", unsupported: "不支持", duplicates: "重复", errors: "错误" }; return Object.entries(counts).map(([key, value]) => `<div><dt>${labels[key] || key}</dt><dd>${value}</dd></div>`).join(""); }
function setMessage(message, kind = "") { const element = $("importMessage"); element.textContent = message; element.className = `message ${kind}`; }
function render() {
  document.querySelectorAll(".wizard-page").forEach((page) => page.classList.toggle("hidden", Number(page.dataset.page) !== wizard.step));
  document.querySelectorAll(".wizard-steps li").forEach((item) => item.classList.toggle("active", Number(item.dataset.step) === wizard.step));
  $("wizardBack").disabled = wizard.step === 1;
  $("wizardNext").textContent = wizard.step === 5 ? "完成" : wizard.step === 4 ? "提交导入" : "下一步";
  $("scanCounts").innerHTML = countsMarkup(wizard.counts || countFiles([]));
}
async function digest(file) {
  const bytes = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(bytes)].map((part) => part.toString(16).padStart(2, "0")).join("");
}
async function createAndValidateSession() {
  if (!state.projectId) throw new Error("请先选择项目，再导入文件。 ");
  const mode = "managed";
  const session = await api(projectPath(state.projectId, "/imports"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("folderName").textContent, mode: "managed" }) });
  wizard.session = session;
  setState({ importSession: session.id });
  const manifest = await Promise.all(wizard.files.filter((file) => imageExtensions.has(extension(file)) || labelExtensions.has(extension(file))).map(async (file) => ({ relative_path: file.webkitRelativePath || file.name, size_bytes: file.size, media_type: file.type || "application/octet-stream", sha256: await digest(file) })));
  await api(projectPath(state.projectId, `/imports/${session.id}/manifest`), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(manifest) });
  if (mode === "managed") {
    const details = await api(projectPath(state.projectId, `/imports/${session.id}`));
    for (const entry of details.entries) {
      const file = wizard.files.find((candidate) => (candidate.webkitRelativePath || candidate.name) === entry.relative_path);
      if (file) await api(projectPath(state.projectId, `/imports/${session.id}/entries/${entry.id}/content`), { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file });
    }
  }
  const validation = await api(projectPath(state.projectId, `/imports/${session.id}/validate`), { method: "POST" });
  $("validationCounts").innerHTML = countsMarkup({ ...wizard.counts, errors: validation.error_count ?? wizard.counts.errors });
  $("validationMessage").textContent = validation.error_count ? "校验发现错误，请核对统计后再提交。" : "校验通过，可以提交导入。";
  return validation;
}
async function commit() {
  const result = await api(projectPath(state.projectId, `/imports/${wizard.session.id}/commit`), { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } });
  wizard.committed = true;
  $("commitMessage").textContent = `已向当前项目提交 ${result.asset_ids?.length ?? 0} 个文件。`;
  setState({ importSession: null });
}
async function next() {
  if (wizard.step === 1 && !wizard.files.length) { setMessage("请先选择文件夹。", "error"); return; }
  if (wizard.step === 1) {
    const error = selectionError(wizard.counts);
    if (error) { setMessage(error, "error"); return; }
  }
  if (wizard.step === 3) { setMessage("正在创建项目隔离的导入任务…"); await createAndValidateSession(); }
  if (wizard.step === 4) await commit();
  if (wizard.step === 5) { $("importWizard").close(); return; }
  wizard.step += 1; setMessage(""); render();
}
export function initImportWizard(onCommitted) {
  $("openImport").addEventListener("click", () => { wizard.step = 1; wizard.files = []; wizard.counts = countFiles([]); wizard.session = null; wizard.committed = false; setMessage(""); render(); $("importWizard").showModal(); });
  $("importFolder").addEventListener("change", (event) => { wizard.files = [...event.target.files]; wizard.counts = countFiles(wizard.files); $("folderName").textContent = wizard.files[0]?.webkitRelativePath?.split("/")[0] || `已选择 ${wizard.files.length} 个文件`; });
  $("wizardBack").addEventListener("click", () => { wizard.step = Math.max(1, wizard.step - 1); setMessage(""); render(); });
  $("wizardNext").addEventListener("click", async () => { try { await next(); if (wizard.step === 5) await onCommitted(); } catch (error) { setMessage(error.message, "error"); } });
  $("importWizard").addEventListener("close", async () => {
    if (!wizard.session || wizard.committed) return;
    const session = wizard.session;
    wizard.session = null;
    try {
      await api(projectPath(state.projectId, `/imports/${session.id}/cancel`), { method: "POST" });
      setState({ importSession: null }, { replace: true });
    } catch (error) {
      console.warn(`无法取消未完成的导入任务 ${session.id}: ${error.message}`);
    }
  });
}
