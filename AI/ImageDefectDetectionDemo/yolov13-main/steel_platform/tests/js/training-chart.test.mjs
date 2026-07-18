import assert from "node:assert/strict";

import { renderLossChart } from "../../src/steel_platform/interfaces/static/js/training-chart.js";

const oneEpoch = renderLossChart([{
  epoch: 1,
  time: 43.4,
  "train/box_loss": 1.21,
  "train/cls_loss": 1.00,
  "train/dfl_loss": 1.13,
  "val/box_loss": 1.56,
  "val/cls_loss": 1.32,
  "val/dfl_loss": 1.44,
}]);

assert.equal((oneEpoch.match(/<circle class="training-chart-point"/g) || []).length, 6);
assert.equal((oneEpoch.match(/<polyline\b/g) || []).length, 0);
assert.match(oneEpoch, /训练框损失/);
assert.match(oneEpoch, /验证DFL损失/);
assert.doesNotMatch(oneEpoch, /\btime\b/);
assert.doesNotMatch(oneEpoch, /NaN|Infinity/);

const multiEpoch = renderLossChart([
  { epoch: 1, "train/box_loss": 1.2, "val/box_loss": 1.5 },
  { epoch: 2, "train/box_loss": 1.0, "val/box_loss": 1.3 },
]);
assert.equal((multiEpoch.match(/<polyline\b/g) || []).length, 2);
assert.equal((multiEpoch.match(/<circle class="training-chart-point"/g) || []).length, 4);
assert.doesNotMatch(multiEpoch, /NaN|Infinity/);

const partial = renderLossChart([
  { epoch: 1, "train/box_loss": "bad", "val/box_loss": 1.5 },
  { epoch: 2, "train/box_loss": null, "val/box_loss": 1.5 },
]);
assert.equal((partial.match(/<circle class="training-chart-point"/g) || []).length, 2);
assert.doesNotMatch(partial, /NaN|Infinity/);

assert.match(renderLossChart([]), /暂无可显示的损失数据/);
assert.match(renderLossChart([{ epoch: 1, time: 2 }]), /暂无可显示的损失数据/);
