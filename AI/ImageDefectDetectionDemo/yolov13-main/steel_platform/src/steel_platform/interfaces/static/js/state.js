const query = new URLSearchParams(window.location.search);

export const state = {
  projectId: query.get("project"),
  roundId: query.get("round"),
  node: query.get("node"),
  assetId: query.get("asset"),
  report: query.get("report") === "1",
  importSession: query.get("import"),
  explorer: null,
  dirty: false,
};

const parameterKeys = [
  ["project", "projectId"], ["round", "roundId"], ["node", "node"],
  ["asset", "assetId"], ["import", "importSession"],
];

export function syncStateFromLocation() {
  const current = new URLSearchParams(window.location.search);
  parameterKeys.forEach(([parameter, key]) => { state[key] = current.get(parameter); });
  state.report = current.get("report") === "1";
}

export function setState(changes, { replace = false, hash = null } = {}) {
  Object.assign(state, changes);
  const next = new URLSearchParams(window.location.search);
  parameterKeys.forEach(([parameter, key]) => {
    if (state[key]) next.set(parameter, state[key]); else next.delete(parameter);
  });
  if (state.report) next.set("report", "1"); else next.delete("report");
  const nextHash = hash === null ? window.location.hash : hash;
  const url = `${window.location.pathname}${next.size ? `?${next}` : ""}${nextHash}`;
  window.history[replace ? "replaceState" : "pushState"]({}, "", url);
}
