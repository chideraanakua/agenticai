// ============================================================
// DETECTION (Agent 1)
// ============================================================

function _isBlackFrame(canvas) {
  const ctx = canvas.getContext("2d");
  const cx = Math.floor(canvas.width * 0.25), cy = Math.floor(canvas.height * 0.25);
  const cw = Math.floor(canvas.width * 0.5), ch = Math.floor(canvas.height * 0.5);
  if (cw < 1 || ch < 1) return true;
  const data = ctx.getImageData(cx, cy, cw, ch).data;
  let sum = 0;
  for (let i = 0; i < data.length; i += 4) sum += data[i] + data[i + 1] + data[i + 2];
  return (sum / (data.length / 4)) < 8;
}

let _blackFrameRetries = 0;

async function scanWorkspace() {
  const video = document.getElementById("camera-feed");
  if (video.videoWidth === 0) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);

  if (_isBlackFrame(canvas) && _blackFrameRetries < 5) {
    _blackFrameRetries++;
    if ('requestVideoFrameCallback' in video) {
      video.requestVideoFrameCallback(() => scanWorkspace());
    } else {
      setTimeout(() => scanWorkspace(), 300);
    }
    return;
  }
  _blackFrameRetries = 0;

  const dataUrl = canvas.toDataURL("image/jpeg", 0.85);

  const img = document.getElementById("snapshot-img");
  img.src = dataUrl;
  img.style.display = "block";
  video.style.display = "none";

  sourceImage = dataUrl;
  await runDetection(dataUrl);
}

async function runDetection(imageDataUrl) {
  const loader = document.getElementById("detectLoader");
  loader.classList.add("active");
  document.getElementById("btnScan").disabled = true;
  document.getElementById("btnPlan").disabled = true;
  document.getElementById("objCount").textContent = "detecting...";
  startLogPolling(msg => {
    document.getElementById("objCount").textContent = msg;
  });

  try {
    const base64 = imageDataUrl.includes(",")
      ? imageDataUrl.split(",")[1]
      : imageDataUrl;

    const resp = await fetch(`${API}/api/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: base64 }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.error || "Detection failed");
    }

    workspaceData = await resp.json();
    const objs = workspaceData.workspace.objects;
    const zones = workspaceData.workspace.safety_zones;

    document.getElementById("objCount").textContent =
      `${objs.length} objects, ${zones.length} zones`;
    if (!isVideoMode) document.getElementById("btnPlan").disabled = false;
    document.getElementById("planStatus").textContent =
      `${objs.length} object${objs.length !== 1 ? "s" : ""} detected — ready to plan`;
    document.getElementById("planStatus").style.color = "var(--green)";
    const planEmpty = document.getElementById("planEmpty");
    if (planEmpty) planEmpty.innerHTML =
      `<div style="font-size:24px;opacity:0.3">⟡</div>
       <div>${objs.length} object${objs.length !== 1 ? "s" : ""} detected — ${isVideoMode ? "generating plan…" : "click Generate plan"}</div>`;

    drawAnnotations(sourceImage, workspaceData);
    updateSimWorkspace(workspaceData);

    if (stream) {
      document.getElementById("camera-feed").style.display = "block";
      document.getElementById("snapshot-img").style.display = "none";
      const initialPositions = {};
      workspaceData.workspace.objects.forEach(obj => {
        const bb = obj.bounding_box;
        initialPositions[obj.id] = {
          centroid: obj.centroid,
          bbox: [bb.top_left.x, bb.top_left.y, bb.bottom_right.x, bb.bottom_right.y],
        };
      });
      _drawLiveOverlay(initialPositions);
    }
  } catch (e) {
    document.getElementById("objCount").textContent = "error";
    document.getElementById("objCount").style.color = "var(--red)";
    const banner = document.getElementById("errorBanner");
    const bannerLabel = document.getElementById("errorBannerLabel");
    if (banner && bannerLabel) {
      bannerLabel.textContent = e.message;
      banner.style.display = "flex";
    }
    console.error("Detection error:", e.message);
  }

  stopLogPolling();
  loader.classList.remove("active");
  document.getElementById("btnScan").disabled = isVideoMode || !stream;
  if (isVideoMode && workspaceData) generatePlan();
}

// ============================================================
// ANNOTATION CANVAS
// ============================================================

const CAT_COLORS = {
  kitchen: "#378ADD",
  reading: "#5dca7b",
  writing: "#d4537e",
  electronics: "#5d9eca",
  beverage: "#ef9f27",
  tools: "#97c459",
  other: "#888",
};

function makePlanNumFn(plan) {
  const stepMap = {};
  for (const step of plan.sequence) {
    stepMap[step.object_id] = { num: String(step.step), skip: !!step.skip };
  }
  return (obj, idx) => stepMap[obj.id] || { num: String(idx + 1), skip: false };
}

function drawAnnotations(imgSrc, data, numFn = null) {
  document.getElementById("annotEmpty").style.display = "none";
  const canvas = document.getElementById("annotated-canvas");
  const ctx = canvas.getContext("2d");

  const img = new Image();
  img.onload = function () {
    canvas.width = img.width;
    canvas.height = img.height;
    canvas.style.display = "block";
    ctx.drawImage(img, 0, 0);

    const ws = data.workspace;
    const w = img.width, h = img.height;

    for (const zone of ws.safety_zones) {
      const pts = zone.polygon;
      const isRobotBase = zone.type === "robot_base";
      const zoneRgb = isRobotBase ? "245,166,35" : "226,75,74";
      ctx.fillStyle = `rgba(${zoneRgb},0.18)`;
      ctx.beginPath();
      ctx.moveTo(pts[0].x * w, pts[0].y * h);
      for (let i = 1; i < pts.length; i++)
        ctx.lineTo(pts[i].x * w, pts[i].y * h);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = `rgb(${zoneRgb})`;
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      const lx = pts[0].x * w + 8, ly = pts[0].y * h + 22;
      ctx.fillStyle = `rgba(${zoneRgb},0.85)`;
      const lw = 105, lh = 20, lr = 4;
      ctx.beginPath();
      ctx.roundRect(lx, ly - 15, lw, lh, lr);
      ctx.fill();
      ctx.fillStyle = "white";
      ctx.font = "bold 11px system-ui";
      ctx.fillText(isRobotBase ? "⚠ ROBOT BASE" : "⚠ SAFETY ZONE", lx + 6, ly - 1);
    }

    ws.objects.forEach((obj, idx) => {
      const bb = obj.bounding_box;
      const x1 = bb.top_left.x * w, y1 = bb.top_left.y * h;
      const x2 = bb.bottom_right.x * w, y2 = bb.bottom_right.y * h;
      const numInfo = numFn ? numFn(obj, idx) : { num: String(idx + 1), skip: false };
      const color = numInfo.skip ? "#888" : (CAT_COLORS[obj.category] || "#888");

      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      if (numInfo.skip) ctx.setLineDash([5, 3]);
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      ctx.setLineDash([]);

      const r = 12;
      const bx = x1 + r, by = y1 + r;
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fillStyle = numInfo.skip ? "rgba(136,136,136,0.7)" : color;
      ctx.fill();
      ctx.fillStyle = "white";
      ctx.font = `bold 12px system-ui`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(numInfo.num, bx, by);
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";

      if (obj.coord_source === "opencv") {
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        ctx.beginPath();
        ctx.roundRect(x2 - 44, y2 - 15, 42, 13, 3);
        ctx.fill();
        ctx.fillStyle = "#5dca7b";
        ctx.font = "9px monospace";
        ctx.textAlign = "right";
        ctx.fillText("cv2", x2 - 3, y2 - 5);
        ctx.textAlign = "left";
      }
    });
  };
  img.src = imgSrc;

  renderTagStrip(data.workspace);
}

function renderTagStrip(ws, numFn = null) {
  const strip = document.getElementById("tagStrip");
  if (!strip) return;
  strip.innerHTML = "";
  strip.style.display = "flex";

  ws.objects.forEach((obj, idx) => {
    const numInfo = numFn ? numFn(obj, idx) : { num: String(idx + 1), skip: false };
    const color = numInfo.skip ? "#888" : (CAT_COLORS[obj.category] || "#888");
    const tag = document.createElement("span");
    tag.className = "obj-tag";
    tag.style.cssText = `
      background: ${color}1a;
      border-color: ${color}55;
      color: ${color};
      ${numInfo.skip ? "opacity:0.55;" : ""}
    `;
    tag.setAttribute("data-tip",
      `${obj.label.replace(/_/g," ")} — ${obj.category}${numInfo.skip ? " · skipped, no safe route found" : ""}`
    );
    tag.innerHTML = `
      <span class="tag-dot" style="background:${color}"></span>
      <span class="tag-num">${numInfo.num}</span>
      <span style="color:var(--text);${numInfo.skip ? "text-decoration:line-through" : ""}">${obj.label.replace(/_/g, " ")}</span>
      <span style="color:var(--muted);font-size:10px">${obj.category}</span>
      <span class="tag-source">${obj.coord_source === "opencv" ? "cv2" : "~"}</span>
    `;
    strip.appendChild(tag);
  });

  for (const zone of ws.safety_zones) {
    const tag = document.createElement("span");
    tag.className = "zone-tag";
    tag.setAttribute("data-tip",
      zone.type === "robot_base"
        ? "Where the robot arm is mounted — kept clear"
        : "A person was detected here — the robot won't reach into this area"
    );
    tag.textContent = `⚠ ${zone.label || zone.type === "robot_base" ? "robot base" : "human"}`;
    strip.appendChild(tag);
  }
}
