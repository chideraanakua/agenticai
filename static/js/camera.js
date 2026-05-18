const API = "";
let stream = null;
let isVideoMode = false;
let workspaceData = null;
let planData = null;
let sourceImage = null;
let _logPoll = null;
let _frameLoop = null;
let _offscreenCanvas = null;
let _simWs = null;

function startLogPolling(onLog) {
  _logPoll = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/state`);
      const d = await r.json();
      const logs = d.recent_log || [];
      if (logs.length > 0) onLog(logs[logs.length - 1].msg);
    } catch (_) {}
  }, 800);
}

function stopLogPolling() {
  if (_logPoll) { clearInterval(_logPoll); _logPoll = null; }
}

// ============================================================
// CAMERA
// ============================================================

function toggleCameraMenu(e) {
  if (stream) { stopCamera(); return; }
  const menu = document.getElementById("cameraMenu");
  menu.style.display = menu.style.display === "none" ? "block" : "none";
  e.stopPropagation();
}

document.addEventListener("click", () => {
  const menu = document.getElementById("cameraMenu");
  if (menu) menu.style.display = "none";
});

function stopCamera() {
  const btn = document.getElementById("btnCamera");
  const video = document.getElementById("camera-feed");
  const overlay = document.getElementById("cameraOverlay");
  stream.getTracks().forEach((t) => t.stop());
  stream = null;
  video.srcObject = null;
  btn.textContent = "Start camera";
  isVideoMode = false;
  document.getElementById("btnScan").disabled = true;
  document.getElementById("btnScan").textContent = "Scan workspace";
  document.getElementById("btnPlan").disabled = true;
  overlay.classList.remove("hidden");
  stopFrameLoop();
}

async function startCameraMode(mode) {
  document.getElementById("cameraMenu").style.display = "none";
  const btn = document.getElementById("btnCamera");
  const video = document.getElementById("camera-feed");
  const overlay = document.getElementById("cameraOverlay");
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
    });
    video.srcObject = stream;
    btn.textContent = "Stop camera";
    overlay.classList.add("hidden");
    if (mode === "video") {
      isVideoMode = true;
      document.getElementById("btnPlan").disabled = true;
      document.getElementById("btnScan").disabled = false;
      document.getElementById("btnScan").textContent = "Scan workspace";
      document.getElementById("btnExec").disabled = true;
      startFrameLoop();
    } else {
      isVideoMode = false;
      document.getElementById("btnPlan").disabled = true;
      document.getElementById("btnScan").disabled = false;
      document.getElementById("btnScan").textContent = "Take photo";
    }
  } catch (e) {
    overlay.classList.remove("hidden");
    overlay.innerHTML = `<div style="color:var(--red);font-size:13px">Camera access denied</div><div style="color:var(--muted);font-size:11px;margin-top:4px">Use "Upload" instead</div>`;
  }
}

function uploadFile() {
  document.getElementById("fileInput").click();
}

function handleUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = function (e) {
    const img = document.getElementById("snapshot-img");
    img.src = e.target.result;
    img.style.display = "block";
    document.getElementById("camera-feed").style.display = "none";
    document.getElementById("cameraOverlay").classList.add("hidden");
    sourceImage = e.target.result;
    runDetection(sourceImage);
  };
  reader.readAsDataURL(file);
}
