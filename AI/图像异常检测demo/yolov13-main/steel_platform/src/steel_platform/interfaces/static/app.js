"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  queue: [], current: null, image: null, boxes: [], selected: -1,
  dirty: false, undo: [], redo: [], mode: "select", drag: null,
  view: { scale: 1, x: 0, y: 0 }, queueOpen: true,
};
const canvas = $("reviewCanvas");
const ctx = canvas.getContext("2d");

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let error = { message: `${response.status} ${response.statusText}` };
    try { error = await response.json(); } catch (_) {}
    throw new Error(error.message || "请求失败");
  }
  return response.headers.get("content-type")?.includes("json") ? response.json() : response;
}

function showMessage(text, kind = "") {
  $("message").textContent = text;
  $("message").className = `message ${kind}`;
}

function setDirty(value) {
  state.dirty = value;
  $("dirtyBadge").classList.toggle("hidden", !value);
}

function snapshot() { return state.boxes.map((box) => ({ ...box })); }
function pushUndo() {
  state.undo.push(snapshot());
  if (state.undo.length > 100) state.undo.shift();
  state.redo = [];
}
function undo() {
  if (!state.undo.length) return;
  state.redo.push(snapshot()); state.boxes = state.undo.pop(); state.selected = -1; setDirty(true); render();
}
function redo() {
  if (!state.redo.length) return;
  state.undo.push(snapshot()); state.boxes = state.redo.pop(); state.selected = -1; setDirty(true); render();
}

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (state.image) resetView(); else render();
}

function resetView() {
  if (!state.image) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  state.view.scale = Math.min((w - 50) / state.image.naturalWidth, (h - 50) / state.image.naturalHeight);
  state.view.x = (w - state.image.naturalWidth * state.view.scale) / 2;
  state.view.y = (h - state.image.naturalHeight * state.view.scale) / 2;
  render();
}

function normToScreen(nx, ny) {
  return { x: state.view.x + nx * state.image.naturalWidth * state.view.scale, y: state.view.y + ny * state.image.naturalHeight * state.view.scale };
}
function screenToNorm(x, y, clamp = false) {
  let nx = (x - state.view.x) / (state.image.naturalWidth * state.view.scale);
  let ny = (y - state.view.y) / (state.image.naturalHeight * state.view.scale);
  if (clamp) { nx = Math.max(0, Math.min(1, nx)); ny = Math.max(0, Math.min(1, ny)); }
  return { x: nx, y: ny };
}
function boxEdges(box) {
  return { left: box.x_center - box.width / 2, right: box.x_center + box.width / 2, top: box.y_center - box.height / 2, bottom: box.y_center + box.height / 2 };
}

function render() {
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  if (!state.image) return;
  ctx.drawImage(state.image, state.view.x, state.view.y, state.image.naturalWidth * state.view.scale, state.image.naturalHeight * state.view.scale);
  state.boxes.forEach((box, index) => {
    const e = boxEdges(box), p1 = normToScreen(e.left, e.top), p2 = normToScreen(e.right, e.bottom);
    ctx.strokeStyle = index === state.selected ? "#2fdbb6" : "#ff5538";
    ctx.lineWidth = index === state.selected ? 3 : 2;
    ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
    ctx.fillStyle = ctx.strokeStyle; ctx.font = "12px Segoe UI";
    ctx.fillRect(p1.x, p1.y - 19, 40, 19); ctx.fillStyle = "#101820"; ctx.fillText(state.current?.expected_class_name || "", p1.x + 4, p1.y - 5);
    if (index === state.selected) {
      [[p1.x,p1.y],[p2.x,p1.y],[p1.x,p2.y],[p2.x,p2.y]].forEach(([x,y]) => { ctx.fillStyle="#fff";ctx.strokeStyle="#1547d7";ctx.fillRect(x-5,y-5,10,10);ctx.strokeRect(x-5,y-5,10,10); });
    }
  });
  if (state.drag?.kind === "new") {
    const a = normToScreen(state.drag.start.x, state.drag.start.y), b = normToScreen(state.drag.end.x, state.drag.end.y);
    ctx.strokeStyle="#2fdbb6";ctx.lineWidth=2;ctx.setLineDash([6,4]);ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);ctx.setLineDash([]);
  }
}

function pointer(event) {
  const rect = canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}
function hitTest(point) {
  for (let i = state.boxes.length - 1; i >= 0; i--) {
    const e = boxEdges(state.boxes[i]), a = normToScreen(e.left,e.top), b = normToScreen(e.right,e.bottom);
    const corners = [{n:"nw",x:a.x,y:a.y},{n:"ne",x:b.x,y:a.y},{n:"sw",x:a.x,y:b.y},{n:"se",x:b.x,y:b.y}];
    const handle = corners.find((c) => Math.hypot(point.x-c.x, point.y-c.y) <= 9);
    if (handle) return { index:i, kind:"resize", handle:handle.n };
    if (point.x >= a.x && point.x <= b.x && point.y >= a.y && point.y <= b.y) return { index:i, kind:"move" };
  }
  return null;
}

canvas.addEventListener("pointerdown", (event) => {
  if (!state.image) return;
  canvas.setPointerCapture(event.pointerId);
  const p = pointer(event);
  if (event.button === 1 || event.altKey) { state.drag = {kind:"pan", start:p, x:state.view.x, y:state.view.y}; return; }
  if (state.mode === "new") { state.drag = {kind:"new", start:screenToNorm(p.x,p.y,true), end:screenToNorm(p.x,p.y,true)}; return; }
  const hit = hitTest(p); state.selected = hit ? hit.index : -1;
  if (hit) { pushUndo(); state.drag = {...hit, start:screenToNorm(p.x,p.y), original:{...state.boxes[hit.index]}}; }
  render();
});
canvas.addEventListener("pointermove", (event) => {
  if (!state.drag || !state.image) return;
  const p = pointer(event);
  if (state.drag.kind === "pan") { state.view.x=state.drag.x+p.x-state.drag.start.x;state.view.y=state.drag.y+p.y-state.drag.start.y;render();return; }
  if (state.drag.kind === "new") { state.drag.end=screenToNorm(p.x,p.y,true);render();return; }
  const n=screenToNorm(p.x,p.y,true), d=state.drag, original=d.original;
  if (d.kind === "move") {
    const dx=n.x-d.start.x,dy=n.y-d.start.y;
    const halfW=original.width/2,halfH=original.height/2;
    state.boxes[d.index]={...original,x_center:Math.max(halfW,Math.min(1-halfW,original.x_center+dx)),y_center:Math.max(halfH,Math.min(1-halfH,original.y_center+dy))};
  } else {
    const e=boxEdges(original), fixed={x:d.handle.includes("w")?e.right:e.left,y:d.handle.includes("n")?e.bottom:e.top};
    const left=Math.min(fixed.x,n.x),right=Math.max(fixed.x,n.x),top=Math.min(fixed.y,n.y),bottom=Math.max(fixed.y,n.y);
    if (right-left>.002&&bottom-top>.002) state.boxes[d.index]={...original,x_center:(left+right)/2,y_center:(top+bottom)/2,width:right-left,height:bottom-top};
  }
  setDirty(true);render();
});
canvas.addEventListener("pointerup", (event) => {
  if (!state.drag) return;
  if (state.drag.kind === "new") {
    const a=state.drag.start,b=state.drag.end,left=Math.min(a.x,b.x),right=Math.max(a.x,b.x),top=Math.min(a.y,b.y),bottom=Math.max(a.y,b.y);
    if (right-left>.002&&bottom-top>.002) { pushUndo();state.boxes.push({class_id:state.current.expected_class_id,x_center:(left+right)/2,y_center:(top+bottom)/2,width:right-left,height:bottom-top});state.selected=state.boxes.length-1;setDirty(true); }
    state.mode="select";$("newBoxBtn").classList.remove("active");
  }
  state.drag=null;render();
});
canvas.addEventListener("wheel", (event) => {
  if (!state.image) return; event.preventDefault(); const p=pointer(event), before=screenToNorm(p.x,p.y);
  state.view.scale=Math.max(.2,Math.min(20,state.view.scale*(event.deltaY<0?1.12:.89)));
  state.view.x=p.x-before.x*state.image.naturalWidth*state.view.scale;state.view.y=p.y-before.y*state.image.naturalHeight*state.view.scale;render();
},{passive:false});

function deleteSelected() {
  if (state.selected < 0) return; pushUndo();state.boxes.splice(state.selected,1);state.selected=-1;setDirty(true);render();
}

async function loadOverview() {
  try {
    const data=await api("/api/v1/overview"),review=data.review,done=review.completed;
    const metrics=[["已登记原图",data.assets.images],["标签版本",data.assets.annotation_revisions],["复核完成",`${done}/${review.total}`],["模型版本",data.models.length]];
    $("metricCards").innerHTML=metrics.map(([label,value])=>`<article class="metric"><small>${label}</small><b>${value}</b></article>`).join("");
    const ratio=review.total?Math.round(done/review.total*100):0;
    $("reviewChart").innerHTML=`<div class="progress"><i style="width:${ratio}%"></i></div><div class="legend"><span>已完成 ${done}</span><b>${ratio}%</b><span>待复核 ${review.pending}</span></div>`;
    const rows=[["数据集版本",data.datasets.length],["实验运行",data.experiments.length],["任务记录",data.runs.length],["模型版本",data.models.length],["推理运行",data.inference.length]];
    $("platformState").innerHTML=rows.map(([k,v])=>`<div class="state-row"><span>${k}</span><b>${v}</b></div>`).join("");
    $("roundLabel").textContent=review.round?`第 ${review.round} 轮 / ${review.kind}`:"尚无轮次";
    $("classDistribution").innerHTML=Object.entries(review.by_class||{}).map(([name,value])=>{const finished=(value.states.accepted||0)+(value.states.corrected||0);return `<div class="state-row"><code>${name}</code><span>${finished} / ${value.target}</span></div>`}).join("");
    const risks=Object.entries(review.by_risk||{}).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([name,value])=>`<div class="state-row"><span>${name}</span><b>${value}</b></div>`);
    const latest=data.runs[0]?`<div class="state-row"><span>最近任务 · ${data.runs[0].kind}</span><b>${data.runs[0].status}</b></div>`:"<div class=\"state-row\"><span>最近任务</span><b>暂无</b></div>";
    $("riskActivity").innerHTML=risks.join("")+latest;
  } catch(error) { $("metricCards").innerHTML=`<p>${error.message}</p>`; }
}

async function loadQueue() {
  const params=new URLSearchParams();
  if ($("classFilter").value) params.set("class_id",$("classFilter").value);
  if ($("stateFilter").value) params.set("state",$("stateFilter").value);
  if ($("searchFilter").value) params.set("search",$("searchFilter").value);
  try { state.queue=(await api(`/api/v1/review/queues?${params}`)).items;renderQueue(); }
  catch(error){showMessage(error.message,"error");}
}
function renderQueue() {
  $("queueCount").textContent=`${state.queue.length} 项`;
  $("queueList").innerHTML=state.queue.map((item,index)=>`<button class="queue-item ${state.current?.id===item.id?"active":""}" data-index="${index}"><span class="thumb-code">${item.expected_class_name}</span><span><b>${item.filename}</b><small>${item.selection_reason} · ${item.source_status}</small></span><i class="status-dot ${item.state}"></i></button>`).join("");
  document.querySelectorAll(".queue-item").forEach((node)=>node.addEventListener("click",()=>selectItem(Number(node.dataset.index))));
}
async function selectItem(index, force=false) {
  if (state.dirty&&!force&&!confirm("当前边界框或备注尚未保存，确定放弃修改并切换吗？")) return;
  try {
    state.current=await api(`/api/v1/review/items/${state.queue[index].id}`);state.boxes=state.current.boxes.map((b)=>({...b}));state.undo=[];state.redo=[];state.selected=-1;state.mode="select";setDirty(false);showMessage("");
    $("filename").textContent=state.current.filename;$("itemPosition").textContent=`队列 ${index+1} / ${state.queue.length}`;$("note").value=state.current.note||"";
    $("itemMeta").innerHTML=[["状态",state.current.state],["预期类别",`${state.current.expected_class_id} / ${state.current.expected_class_name}`],["来源风险",state.current.source_status],["选择原因",state.current.selection_reason],["候选框",state.current.candidate_box_count],["置信度",state.current.min_confidence==null?"—":`${state.current.min_confidence.toFixed(3)}–${state.current.max_confidence.toFixed(3)}`],["标签版本",state.current.revision]].map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join("");
    const image=new Image();image.onload=()=>{state.image=image;$("emptyCanvas").classList.add("hidden");resetView();};image.onerror=()=>showMessage("图片加载失败","error");image.src=`/api/v1/assets/${state.current.image_asset_id}/content`;
    renderQueue();
  } catch(error){showMessage(error.message,"error");}
}
async function saveDecision(decision) {
  if (!state.current) return;
  if ((decision==="accepted"||decision==="corrected")&&!state.boxes.length){showMessage("有效缺陷图片至少需要一个框；看不出缺陷请填写原因后排除。","error");return;}
  const note=$("note").value.trim();if(decision==="excluded"&&!note){showMessage("排除图片必须填写原因。","error");$("note").focus();return;}
  try {
    const result=await api(`/api/v1/review/items/${state.current.id}/decision`,{method:"PUT",headers:{"Content-Type":"application/json","Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({expected_revision:state.current.revision,decision,boxes:state.boxes,note})});
    state.current.revision=result.revision;state.current.state=result.state;setDirty(false);showMessage("服务端已保存不可变标签版本。","ok");await loadQueue();
    const next=state.queue.findIndex((item)=>item.state==="pending");if(next>=0) await selectItem(next,true);else{state.current=null;state.image=null;render();showMessage("当前筛选队列已复核完成。","ok");}
    loadOverview();
  } catch(error){showMessage(error.message,"error");}
}

function toggleQueue(force) { state.queueOpen=force??!state.queueOpen;$("queuePanel").classList.toggle("hidden",!state.queueOpen);$("openQueue").classList.toggle("hidden",state.queueOpen);document.querySelector(".review-layout").style.gridTemplateColumns=state.queueOpen?"280px minmax(500px,1fr) 300px":"minmax(500px,1fr) 300px";setTimeout(resizeCanvas,0); }
document.querySelectorAll(".nav").forEach((button)=>button.addEventListener("click",()=>{document.querySelectorAll(".nav").forEach(n=>n.classList.toggle("active",n===button));$("overviewView").classList.toggle("hidden",button.dataset.view!=="overview");$("reviewView").classList.toggle("hidden",button.dataset.view!=="review");if(button.dataset.view==="review")setTimeout(resizeCanvas,0);else loadOverview();}));
[$("classFilter"),$("stateFilter")].forEach((node)=>node.addEventListener("change",loadQueue));let searchTimer;$("searchFilter").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadQueue,250);});
$("note").addEventListener("input",()=>setDirty(true));$("undoBtn").onclick=undo;$("redoBtn").onclick=redo;$("deleteBtn").onclick=deleteSelected;$("resetViewBtn").onclick=resetView;$("newBoxBtn").onclick=()=>{state.mode="new";$("newBoxBtn").classList.add("active");};$("acceptBtn").onclick=()=>saveDecision("accepted");$("saveBtn").onclick=()=>saveDecision("corrected");$("doubtBtn").onclick=()=>saveDecision("doubtful");$("excludeBtn").onclick=()=>saveDecision("excluded");$("closeQueue").onclick=()=>toggleQueue(false);$("openQueue").onclick=()=>toggleQueue(true);
window.addEventListener("keydown",(event)=>{if(["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName))return;if(event.ctrlKey&&event.key.toLowerCase()==="z"){event.preventDefault();undo();}else if(event.ctrlKey&&event.key.toLowerCase()==="y"){event.preventDefault();redo();}else if(event.key==="Delete")deleteSelected();else if(event.key.toLowerCase()==="r")$("newBoxBtn").click();else if(event.key.toLowerCase()==="q")toggleQueue();else if(event.key.toLowerCase()==="a")saveDecision("accepted");else if(event.key.toLowerCase()==="s")saveDecision("corrected");else if(event.key.toLowerCase()==="d")saveDecision("doubtful");else if(event.key.toLowerCase()==="x")saveDecision("excluded");});
window.addEventListener("beforeunload",(event)=>{if(state.dirty){event.preventDefault();event.returnValue="";}});window.addEventListener("resize",resizeCanvas);

Promise.all([api("/health/ready"),loadOverview(),loadQueue()]).then(()=>{$("health").textContent="● 系统就绪";}).catch((error)=>{$("health").textContent=`● 未就绪：${error.message}`;});
