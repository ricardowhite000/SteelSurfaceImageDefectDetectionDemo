import { api, projectPath } from "./api.js?v=__STATIC_VERSION__";
import { state } from "./state.js?v=__STATIC_VERSION__";

const $ = (id) => document.getElementById(id);

export function initMonitoringCenter() { $("monitoringRefresh").addEventListener("click", renderMonitoringCenter); }

export async function renderMonitoringCenter() {
  if (!state.projectId) { $("monitoringMessage").textContent = "请先选择项目。"; return; }
  const base = projectPath(state.projectId);
  const modules = [
    ["orders", "标注工单", `${base}/annotation-work-orders`],
    ["jobs", "模型任务", `${base}/jobs`],
    ["models", "模型库", `${base}/models`],
    ["workbench", "数据集", `${base}/workbench/options`],
    ["annotation", "推理运行", `${base}/annotation-work-orders/options`],
  ];
  const settled = await Promise.allSettled(modules.map(([, , path]) => api(path)));
  const loaded = {};
  const failures = [];
  settled.forEach((result, index) => {
    const [key, label] = modules[index];
    if (result.status === "fulfilled") loaded[key] = result.value;
    else failures.push(`${label}：${result.reason?.message || "数据加载失败"}`);
  });
  const orders = loaded.orders || [];
  const jobs = loaded.jobs || [];
  const models = loaded.models || [];
  const workbenchOptions = loaded.workbench || {};
  const annotationOptions = loaded.annotation || {};
  const jobItems = jobs.items || jobs;
  const modelItems = models.items || models;
  const datasetItems = workbenchOptions.datasets || [];
  const failed = jobItems.filter((item) => ["failed", "interrupted"].includes(item.status)).length;
  const activeOrders = orders.filter((item) => ["ready", "active"].includes(item.status)).length;
  const completedOrders = orders.filter((item) => item.status === "completed").length;
  $("monitoringContent").innerHTML = [
    ["进行中标注工单", loaded.orders ? activeOrders : "—", loaded.orders ? "需要继续人工处理" : "数据加载失败"], ["已完成标注工单", loaded.orders ? completedOrders : "—", loaded.orders ? "可浏览档案或创建修订" : "数据加载失败"],
    ["模型任务", loaded.jobs ? jobItems.length : "—", loaded.jobs ? `失败或中断 ${failed} 项` : "数据加载失败"], ["已登记模型", loaded.models ? modelItems.length : "—", loaded.models ? "训练与外部导入模型" : "数据加载失败"],
    ["不可变数据集", loaded.workbench ? datasetItems.length : "—", loaded.workbench ? "用于训练和固定评估" : "数据加载失败"], ["推理运行", loaded.annotation ? (annotationOptions.inference_runs || []).length : "—", loaded.annotation ? "历史推理结果" : "数据加载失败"],
  ].map(([name, value, hint]) => `<article class="monitoring-card"><span>${name}</span><strong>${value}</strong><small>${hint}</small></article>`).join("");
  $("monitoringMessage").textContent = failures.length
    ? `部分数据加载失败。失败模块：${failures.join("；")}。可点击刷新重试。`
    : `更新时间：${new Date().toLocaleString("zh-CN")}`;
}
