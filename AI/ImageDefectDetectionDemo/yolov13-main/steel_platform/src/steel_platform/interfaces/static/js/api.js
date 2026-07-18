export async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).message || message; } catch (_) { /* response is not JSON */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

export function projectPath(projectId, suffix = "") {
  return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`;
}
