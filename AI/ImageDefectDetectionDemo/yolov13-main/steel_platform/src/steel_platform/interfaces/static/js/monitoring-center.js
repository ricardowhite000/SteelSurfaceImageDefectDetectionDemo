import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);
const safe = async (path, fallback = []) => { try { return await api(path); } catch (_) { return fallback; } };

export function initMonitoringCenter() { $("monitoringRefresh").addEventListener("click", renderMonitoringCenter); }

export async function renderMonitoringCenter() {
  if (!state.projectId) { $("monitoringMessage").textContent = "请先选择项目。"; return; }
  const base = projectPath(state.projectId);
  const [orders, jobs, models, workbenchOptions, annotationOptions] = await Promise.all([
    safe(`${base}/annotation-work-orders`), safe(`${base}/jobs`), safe(`${base}/models`),
    safe(`${base}/workbench/options`, {}), safe(`${base}/annotation-work-orders/options`, {}),
  ]);
  const jobItems = jobs.items || jobs;
  const modelItems = models.items || models;
  const datasetItems = workbenchOptions.datasets || [];
  const failed = jobItems.filter((item) => ["failed", "interrupted"].includes(item.status)).length;
  const activeOrders = orders.filter((item) => ["ready", "active"].includes(item.status)).length;
  const completedOrders = orders.filter((item) => item.status === "completed").length;
  $("monitoringContent").innerHTML = [
    ["进行中标注工单", activeOrders, "需要继续人工处理"], ["已完成标注工单", completedOrders, "可浏览档案或创建修订"],
    ["模型任务", jobItems.length, `失败或中断 ${failed} 项`], ["已登记模型", modelItems.length, "训练与外部导入模型"],
    ["不可变数据集", datasetItems.length, "用于训练和固定评估"], ["推理运行", (annotationOptions.inference_runs || []).length, "历史推理结果"],
  ].map(([name, value, hint]) => `<article class="monitoring-card"><span>${name}</span><strong>${value}</strong><small>${hint}</small></article>`).join("");
  $("monitoringMessage").textContent = `更新时间：${new Date().toLocaleString("zh-CN")}`;
}
