export const classLabels = {
  Cr: "Cr（裂纹）", In: "In（夹杂）", Pa: "Pa（斑块）",
  PS: "PS（点蚀表面）", RS: "RS（轧入氧化皮）", Sc: "Sc（划痕）",
};

const dictionaries = {
  resource: { source: "数据源", collection: "集合", review_round: "复核任务", dataset: "数据集", model: "模型", inference: "推理运行" },
  status: { available: "可用", missing: "缺失", unreadable: "不可读", changed: "内容已变化", importing: "导入中", active: "进行中", completed: "已完成", draft: "草稿", ready: "就绪", planned: "已计划", running: "运行中", succeeded: "成功", failed: "失败", cancelled: "已取消", interrupted: "已中断", pending: "待校验", archived: "仅归档", rejected: "校验未通过", evaluated: "已评估", not_evaluated: "未评估", accepted: "已接受", corrected: "已修正", doubtful: "存疑", excluded: "已排除", train: "训练集", val: "验证集", test: "测试集", ok: "正常", low_confidence: "低置信度", no_box: "无候选框" },
  risk: { ok: "正常", review: "人工抽查", low_confidence: "低置信度", no_box: "无候选框", class_mismatch: "类别冲突", class_mismatch_low_confidence: "类别冲突且低置信度", collection: "集合导入" },
  reason: { risk: "风险优先", risk_priority: "风险优先", uncertainty: "不确定性优先", diversity: "多样性优先", replacement: "候补补位", audit: "质量抽查", sampled: "常规抽样", seed: "种子样本", v1_v2_delta: "模型版本差异优先", collection: "集合来源" },
  item: { image: "图片", weights: "模型权重", manifest: "清单", label: "标签", artifact: "产物" },
  origin: { human: "人工确认", machine: "机器候选", imported: "导入标签" },
  jobKind: { train: "模型训练", evaluate: "模型评估", infer: "模型推理", verify_model: "模型校验" },
  preset: { smoke: "GPU冒烟训练", smoke_cpu: "CPU冒烟训练", formal: "正式训练", fixed_val: "固定验证集评估", visual: "常规推理", infer_cpu: "CPU常规推理", pseudo_label: "辅助标注", video: "视频推理", metadata: "元数据校验" },
  modelPurpose: { base_weight: "基础迁移权重", detector: "钢材缺陷检测器" },
};

export function zh(group, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (group === "risk" && String(value).includes(";")) {
    return String(value).split(";").map((part) => dictionaries.risk[part] || part).join("＋");
  }
  return dictionaries[group]?.[value] || classLabels[value] || String(value);
}

export function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && amount >= 1024; index += 1) { amount /= 1024; unit = units[index]; }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
