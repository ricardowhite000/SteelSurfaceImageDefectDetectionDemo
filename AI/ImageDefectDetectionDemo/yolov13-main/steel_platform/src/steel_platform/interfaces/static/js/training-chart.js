const LOSS_METRICS = [
  { key: "train/box_loss", label: "训练框损失", color: "#1547d7" },
  { key: "train/cls_loss", label: "训练分类损失", color: "#e05b34" },
  { key: "train/dfl_loss", label: "训练DFL损失", color: "#087b63" },
  { key: "val/box_loss", label: "验证框损失", color: "#7b4cc2" },
  { key: "val/cls_loss", label: "验证分类损失", color: "#b7791f" },
  { key: "val/dfl_loss", label: "验证DFL损失", color: "#b83280" },
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function displayNumber(value) {
  const absolute = Math.abs(value);
  if (absolute !== 0 && (absolute < 0.001 || absolute >= 1000)) return value.toExponential(2);
  return value.toFixed(3).replace(/\.?0+$/, "");
}

export function renderLossChart(series) {
  const rows = Array.isArray(series) ? series : [];
  const metrics = LOSS_METRICS.map((metric) => ({
    ...metric,
    points: rows.flatMap((row, index) => {
      const value = finiteNumber(row?.[metric.key]);
      const epoch = finiteNumber(row?.epoch) ?? index + 1;
      return value === null ? [] : [{ epoch, value }];
    }),
  })).filter((metric) => metric.points.length > 0);

  if (!metrics.length) {
    return '<p class="muted training-chart-empty">暂无可显示的损失数据。</p>';
  }

  const width = 720;
  const height = 300;
  const plot = { left: 58, right: 704, top: 72, bottom: 266 };
  const allValues = metrics.flatMap((metric) => metric.points.map((point) => point.value));
  const allEpochs = metrics.flatMap((metric) => metric.points.map((point) => point.epoch));
  let yMin = Math.min(...allValues);
  let yMax = Math.max(...allValues);
  const rawSpan = yMax - yMin;
  const padding = rawSpan > 0 ? rawSpan * 0.08 : Math.max(Math.abs(yMin) * 0.1, 0.1);
  yMin -= padding;
  yMax += padding;
  const ySpan = yMax - yMin;
  const xMin = Math.min(...allEpochs);
  const xMax = Math.max(...allEpochs);
  const xSpan = xMax - xMin;
  const xFor = (epoch) => xSpan === 0
    ? (plot.left + plot.right) / 2
    : plot.left + ((epoch - xMin) / xSpan) * (plot.right - plot.left);
  const yFor = (value) => plot.bottom - ((value - yMin) / ySpan) * (plot.bottom - plot.top);

  const grid = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const y = plot.bottom - ratio * (plot.bottom - plot.top);
    const value = yMin + ratio * ySpan;
    return `<line x1="${plot.left}" y1="${y.toFixed(2)}" x2="${plot.right}" y2="${y.toFixed(2)}" stroke="#e4e9ee" stroke-width="1"/><text x="${plot.left - 8}" y="${(y + 4).toFixed(2)}" text-anchor="end" fill="#68727d" font-size="10">${escapeHtml(displayNumber(value))}</text>`;
  }).join("");

  const plots = metrics.map((metric) => {
    const coordinates = metric.points.map((point) => ({
      ...point,
      x: xFor(point.epoch),
      y: yFor(point.value),
    }));
    const line = coordinates.length >= 2
      ? `<polyline fill="none" stroke="${metric.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${coordinates.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")}"/>`
      : "";
    const markers = coordinates.map((point) => `<circle class="training-chart-point" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="3.5" fill="${metric.color}" stroke="#fff" stroke-width="1.5"><title>${escapeHtml(metric.label)} · 第${escapeHtml(displayNumber(point.epoch))}轮：${escapeHtml(displayNumber(point.value))}</title></circle>`).join("");
    return `${line}${markers}`;
  }).join("");

  const legend = metrics.map((metric, index) => {
    const column = index % 3;
    const row = Math.floor(index / 3);
    const x = 62 + column * 215;
    const y = 17 + row * 22;
    return `<line x1="${x}" y1="${y}" x2="${x + 18}" y2="${y}" stroke="${metric.color}" stroke-width="3"/><circle cx="${x + 9}" cy="${y}" r="3" fill="${metric.color}"/><text x="${x + 25}" y="${y + 4}" fill="#39434d" font-size="11">${escapeHtml(metric.label)}</text>`;
  }).join("");
  const xLabels = xSpan === 0
    ? `<text x="${(plot.left + plot.right) / 2}" y="286" text-anchor="middle" fill="#68727d" font-size="10">第${escapeHtml(displayNumber(xMin))}轮</text>`
    : `<text x="${plot.left}" y="286" text-anchor="middle" fill="#68727d" font-size="10">第${escapeHtml(displayNumber(xMin))}轮</text><text x="${plot.right}" y="286" text-anchor="middle" fill="#68727d" font-size="10">第${escapeHtml(displayNumber(xMax))}轮</text>`;

  return `<svg class="training-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="训练和验证损失曲线">${legend}${grid}<path d="M${plot.left} ${plot.top}V${plot.bottom}H${plot.right}" fill="none" stroke="#aeb8c2" stroke-width="1.2"/>${plots}${xLabels}</svg>`;
}
