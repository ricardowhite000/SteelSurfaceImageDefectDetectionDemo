import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { classLabels, zh } from "./locale-zh.js?v=__STATIC_VERSION__";
import { state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function metric(label, value, hint = "") { return `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${hint ? `<small>${escapeHtml(hint)}</small>` : ""}</article>`; }
function distribution(title, values, group) {
  const entries = Object.entries(values || {});
  const total = entries.reduce((sum, [, value]) => sum + Number(value), 0);
  return `<section class="report-panel"><h2>${escapeHtml(title)}</h2>${entries.length ? `<div class="distribution-list">${entries.map(([key, value]) => `<div><span>${escapeHtml(zh(group, key))}</span><div class="bar"><i style="width:${total ? Math.round(value / total * 100) : 0}%"></i></div><strong>${value}</strong></div>`).join("")}</div>` : '<p class="muted">暂无数据。</p>'}</section>`;
}

export async function renderReviewReport() {
  if (!state.projectId || !state.roundId) throw new Error("报告地址缺少项目或复核任务编号。");
  $("reviewReportMessage").textContent = "正在生成中文复核报告…";
  const report = await api(`${projectPath(state.projectId)}/review-rounds/${encodeURIComponent(state.roundId)}/report`);
  $("reportBreadcrumb").textContent = `复核 / ${report.round.name} / 报告`;
  const summary = report.summary;
  const classRows = Object.entries(report.by_class || {}).map(([code, row]) => `<tr><td><strong>${escapeHtml(classLabels[code] || code)}</strong></td><td>${row.total}</td><td>${row.accepted}</td><td>${row.corrected}</td><td>${row.doubtful}</td><td>${row.excluded}</td><td>${row.pending}</td><td>${row.effective_rate}%</td></tr>`).join("");
  const problems = (report.problems || []).map((item) => `<tr><td>${escapeHtml(item.filename)}</td><td>${escapeHtml(classLabels[item.class_name] || item.class_name)}</td><td>${escapeHtml(zh("status", item.state))}</td><td>${escapeHtml(item.note || "未填写备注")}</td><td>${item.revision}</td></tr>`).join("");
  $("reviewReportContent").innerHTML = `
    <header class="report-hero"><div><p class="eyebrow">复核质量报告</p><h1>${escapeHtml(report.round.name)}</h1><p>任务状态：${escapeHtml(zh("status", report.round.status))} · 有效完成率用于衡量已接受与已修正样本占比。</p></div><div class="completion-ring" style="--progress:${summary.completion_rate}"><strong>${summary.completion_rate}%</strong><span>总体完成率</span></div></header>
    <section class="metric-grid">${metric("总计", summary.total, "队列条目")}${metric("已接受", summary.accepted)}${metric("已修正", summary.corrected)}${metric("存疑", summary.doubtful)}${metric("已排除", summary.excluded)}${metric("待复核", summary.pending)}</section>
    <section class="report-panel"><h2>逐类别复核结果</h2><div class="table-wrap"><table><thead><tr><th>类别</th><th>总计</th><th>接受</th><th>修正</th><th>存疑</th><th>排除</th><th>待复核</th><th>有效完成率</th></tr></thead><tbody>${classRows || '<tr><td colspan="8" class="empty-row">暂无类别数据。</td></tr>'}</tbody></table></div></section>
    <div class="report-columns">${distribution("来源风险分布", report.by_risk, "risk")}${distribution("抽样原因分布", report.by_selection_reason, "reason")}</div>
    <section class="report-panel"><h2>需要继续处理的问题图片</h2><p class="muted">仅列出存疑与排除项，备注和标签版本可用于后续专项补标。</p><div class="table-wrap"><table><thead><tr><th>文件名</th><th>类别</th><th>状态</th><th>备注</th><th>标签版本</th></tr></thead><tbody>${problems || '<tr><td colspan="5" class="empty-row">当前没有存疑或排除图片。</td></tr>'}</tbody></table></div></section>`;
  $("reviewReportMessage").textContent = "";
}
