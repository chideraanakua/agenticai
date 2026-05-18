// ============================================================
// LIVE OVERLAY (bounding boxes on camera feed)
// ============================================================

function _videoRenderRect() {
  const vid = document.getElementById("camera-feed");
  const vw = vid.clientWidth, vh = vid.clientHeight;
  const nw = vid.videoWidth || vw, nh = vid.videoHeight || vh;
  const videoAR = nw / nh, elemAR = vw / vh;
  let rw, rh, rx, ry;
  if (videoAR > elemAR) {
    rw = vw; rh = vw / videoAR; rx = 0; ry = (vh - rh) / 2;
  } else {
    rh = vh; rw = vh * videoAR; rx = (vw - rw) / 2; ry = 0;
  }
  return { x: rx, y: ry, w: rw, h: rh };
}

function _normToOverlay(nx, ny) {
  const r = _videoRenderRect();
  return { x: r.x + nx * r.w, y: r.y + ny * r.h };
}

function _drawLiveOverlay(positions) {
  const canvas = document.getElementById("live-overlay");
  if (!canvas) return;

  const vid = document.getElementById("camera-feed");
  canvas.width = vid.clientWidth;
  canvas.height = vid.clientHeight;

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!positions || !workspaceData) return;

  const ws = workspaceData.workspace;
  const labelMap = {}, colorMap = {};
  ws.objects.forEach(obj => {
    labelMap[obj.id] = obj.label;
    colorMap[obj.id] = CAT_COLORS[obj.category] || "#888";
  });

  const stepMap = {};
  if (planData && planData.sequence) {
    planData.sequence.forEach(step => {
      stepMap[step.object_id] = { num: String(step.step), skip: !!step.skip };
    });
  }

  for (const [objId, pos] of Object.entries(positions)) {
    const [nx1, ny1, nx2, ny2] = pos.bbox;
    const p1 = _normToOverlay(nx1, ny1);
    const p2 = _normToOverlay(nx2, ny2);
    const stepInfo = stepMap[objId];
    const color = stepInfo?.skip ? "#888" : (colorMap[objId] || "#888");
    const label = labelMap[objId] || objId;

    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if (stepInfo?.skip) ctx.setLineDash([5, 3]);
    ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
    ctx.setLineDash([]);

    const r = 12;
    const bx = p1.x + r, by = p1.y + r;
    ctx.beginPath();
    ctx.arc(bx, by, r, 0, Math.PI * 2);
    ctx.fillStyle = stepInfo?.skip ? "rgba(136,136,136,0.7)" : color;
    ctx.fill();
    ctx.fillStyle = "white";
    ctx.font = "bold 12px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(stepInfo ? stepInfo.num : "?", bx, by);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";

    ctx.font = "bold 11px system-ui";
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = color + "cc";
    ctx.fillRect(p1.x, p1.y - 18, tw + 8, 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, p1.x + 4, p1.y - 4);
  }
}

function _clearLiveOverlay() {
  const canvas = document.getElementById("live-overlay");
  if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

// ============================================================
// VIDEO TRACKING + WEBSOCKET
// ============================================================

(function () {
  const tt = document.getElementById("tooltip");
  let showTimer, touchTimer;

  function place(el) {
    const tip = el.getAttribute("data-tip");
    if (!tip) return;
    tt.textContent = tip;
    tt.classList.add("visible");
    const r = el.getBoundingClientRect();
    const tw = tt.offsetWidth, th = tt.offsetHeight;
    const posUp = el.dataset.tipPos === "up";
    let left = r.left + r.width / 2 - tw / 2;
    let top = posUp ? r.top - th - 8 : r.bottom + 8;
    left = Math.max(6, Math.min(window.innerWidth - tw - 6, left));
    top = Math.max(6, Math.min(window.innerHeight - th - 6, top));
    tt.style.left = left + "px";
    tt.style.top = top + "px";
  }

  function hide() { tt.classList.remove("visible"); }

  document.addEventListener("mouseenter", e => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => place(el), 350);
  }, true);

  document.addEventListener("mouseleave", e => {
    if (e.target.closest("[data-tip]")) { clearTimeout(showTimer); hide(); }
  }, true);

  document.addEventListener("touchstart", e => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    place(el);
    clearTimeout(touchTimer);
    touchTimer = setTimeout(hide, 2200);
  }, { passive: true });
})();

function toggleHelp() {
  const panel = document.getElementById("helpPanel");
  const btn = document.getElementById("helpBtn");
  const open = panel.classList.toggle("open");
  btn.classList.toggle("active", open);
}

document.addEventListener("click", e => {
  const panel = document.getElementById("helpPanel");
  const btn = document.getElementById("helpBtn");
  if (panel.classList.contains("open") && !panel.contains(e.target) && e.target !== btn) {
    panel.classList.remove("open");
    btn.classList.remove("active");
  }
});

function openGuide() {
  document.getElementById("guideOverlay").classList.remove("hidden");
  document.getElementById("helpPanel").classList.remove("open");
  document.getElementById("helpBtn").classList.remove("active");
}
function closeGuide() {
  document.getElementById("guideOverlay").classList.add("hidden");
  if (document.getElementById("guideNoShow").checked) {
    try { localStorage.setItem("guide_dismissed", "1"); } catch(_) {}
  }
}
document.getElementById("guideOverlay").addEventListener("click", e => {
  if (e.target === document.getElementById("guideOverlay")) closeGuide();
});
(function () {
  try { if (!localStorage.getItem("guide_dismissed")) openGuide(); } catch(_) { openGuide(); }
})();

function _setTrackingBadge(text, color) {
  const b = document.getElementById("trackingBadge");
  if (!b) return;
  b.textContent = text;
  b.style.color = color;
  b.style.display = "block";
  b.setAttribute("data-tip",
    text === "● tracking"
      ? "Watching for changes — the AI will update the plan if objects move"
      : "Something moved or a path is blocked — creating a new plan"
  );
}

function startFrameLoop() {
  if (_frameLoop) return;
  _offscreenCanvas = document.createElement("canvas");
  _offscreenCanvas.width = 320;
  _offscreenCanvas.height = 240;
  _frameLoop = setInterval(sendVideoFrame, 200);
  _setTrackingBadge("● tracking", "var(--green)");
}

function stopFrameLoop() {
  if (_frameLoop) { clearInterval(_frameLoop); _frameLoop = null; }
  const b = document.getElementById("trackingBadge");
  if (b) b.style.display = "none";
  _clearLiveOverlay();
}

async function sendVideoFrame() {
  const video = document.getElementById("camera-feed");
  if (!video || !video.videoWidth) return;
  const ctx = _offscreenCanvas.getContext("2d");
  ctx.drawImage(video, 0, 0, _offscreenCanvas.width, _offscreenCanvas.height);
  const frame = _offscreenCanvas.toDataURL("image/jpeg", 0.6);
  try {
    const resp = await fetch(`${API}/api/video/frame`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame }),
    });
    if (!resp.ok) return;
    handleFrameResponse(await resp.json());
  } catch (_) {}
}

function handleFrameResponse(data) {
  _drawLiveOverlay(data.positions);
  document.querySelectorAll(".step-item.stale, .step-item.blocked")
    .forEach(el => el.classList.remove("stale", "blocked"));
  for (const idx of (data.stale_steps || [])) {
    const el = document.getElementById(`step-${idx + 1}`);
    if (el && !el.classList.contains("done")) el.classList.add("stale");
  }
  for (const idx of (data.blocked_steps || [])) {
    const el = document.getElementById(`step-${idx + 1}`);
    if (el && !el.classList.contains("done")) el.classList.add("blocked");
  }
  if (data.replanning) {
    const s = document.getElementById("simStatus");
    if (s) { s.textContent = "recalculating…"; s.style.color = "var(--amber)"; }
    _setTrackingBadge("⟳ recalculating…", "var(--amber)");
  } else if (_frameLoop) {
    _setTrackingBadge("● tracking", "var(--green)");
  }
}

function pingStepComplete(idx) {
  fetch(`${API}/api/step/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step_index: idx }),
  }).catch(() => {});
}

function initWebSocket() {
  if (_simWs) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  _simWs = new WebSocket(`${proto}://${location.host}/ws/unity`);

  _simWs.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type !== "replan") return;
      if (data.workspace) {
        workspaceData = data.workspace;
        updateSimWorkspace(workspaceData);
      }
      planData = data.plan;
      renderSteps(planData);
      addStepLabels();
      document.getElementById("planStatus").textContent =
        `${planData.sequence.length} steps — replanned`;
      _setTrackingBadge("● tracking", "var(--green)");
      simPlan = null;
      simSorting = false;
      executePlan();
    } catch (_) {}
  };

  _simWs.onclose = () => { _simWs = null; setTimeout(initWebSocket, 3000); };
}

initWebSocket();

async function maybeRestoreSession() {
  const sid = new URLSearchParams(location.search).get("session");
  if (!sid) return;

  const banner = document.getElementById("sessionBanner");
  const bannerLabel = document.getElementById("sessionBannerLabel");
  banner.style.display = "flex";
  const grid = document.querySelector(".grid");
  grid.style.height = `calc(100vh - 49px - ${banner.offsetHeight}px)`;

  try {
    const s = await fetch(`${API}/api/sessions/${sid}`).then(r => r.json());

    if (s.error) { bannerLabel.textContent = `Session ${sid} not found`; return; }
    if (!s.plan) {
      bannerLabel.textContent = `Session ${sid} — detection only, no plan recorded`;
      return;
    }

    workspaceData = s.workspace;
    planData = s.plan;

    updateSimWorkspace(workspaceData);
    renderSteps(planData);
    addStepLabels();

    const nf = makePlanNumFn(planData);
    if (s.image_original) {
      try {
        const blob = await fetch(s.image_original).then(r => r.blob());
        const dataUrl = await new Promise(res => {
          const reader = new FileReader();
          reader.onload = e => res(e.target.result);
          reader.readAsDataURL(blob);
        });
        sourceImage = dataUrl;
        drawAnnotations(dataUrl, workspaceData, nf);
      } catch (_) {}
    }
    renderTagStrip(workspaceData.workspace, nf);

    document.getElementById("btnExec").disabled = false;
    document.getElementById("planStatus").textContent =
      `${planData.sequence.length} steps — ${planData.strategy || ""}`;

    bannerLabel.textContent = `Viewing saved session ${sid} — read-only replay`;
  } catch (err) {
    bannerLabel.textContent = `Failed to load session ${sid}: ${err.message}`;
  }
}
maybeRestoreSession();
