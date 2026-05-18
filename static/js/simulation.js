// ============================================================
// THREE.JS SIMULATION
// ============================================================

const simCanvas = document.getElementById("simCanvas");
let renderer, scene, camera, armRoot, turret, seg1, seg2, wrist, clawL, clawR;
let simBlocks = [], simZones = [];
let simPose = { base: 0, s1: 0.25, s2: -0.4 };
let clawSpread = 0.14;
let simLabels = [];
let simHeld = null, simSorting = false, simStepIdx = -1;
let simPhase = "idle", simPhaseT = 0, simPFrom = {}, simPTo = {};
let simPlan = [], simTime = 0;

const ARM1_LEN = 2.2, ARM2_LEN = 1.8, CLAW_LEN = 0.5;

function initSim() {
  const W = simCanvas.clientWidth, H = simCanvas.clientHeight;
  renderer = new THREE.WebGLRenderer({ canvas: simCanvas, antialias: true });
  renderer.setSize(W, H);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setClearColor(0x12131a);

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(40, W / H, 0.1, 100);
  camera.position.set(6, 11, 9);
  camera.lookAt(0, 0, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 0.4));
  const dl = new THREE.DirectionalLight(0xffffff, 0.7);
  dl.position.set(4, 10, 6);
  dl.castShadow = true;
  dl.shadow.mapSize.set(1024, 1024);
  scene.add(dl);
  scene.add(new THREE.DirectionalLight(0x8888ff, 0.2).translateX(-4).translateY(6));

  const tbl = new THREE.Mesh(
    new THREE.BoxGeometry(10, 0.25, 7),
    new THREE.MeshStandardMaterial({ color: 0x2a2a3a, roughness: 0.8 }),
  );
  tbl.position.y = -0.125;
  tbl.receiveShadow = true;
  scene.add(tbl);

  const grd = new THREE.GridHelper(10, 20, 0x3a3a5a, 0x252540);
  grd.position.y = 0.01;
  scene.add(grd);

  buildArm();
  animateSim();
}

function mm(c) {
  return new THREE.MeshStandardMaterial({ color: c, roughness: 0.5, metalness: 0.1 });
}

function buildArm() {
  armRoot = new THREE.Group();
  armRoot.position.set(-3.2, 0, -2.8);
  scene.add(armRoot);

  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.65, 0.35, 24), mm(0x44445e));
  base.position.y = 0.175;
  base.castShadow = true;
  armRoot.add(base);

  turret = new THREE.Group();
  turret.position.y = 0.35;
  armRoot.add(turret);

  const sj = new THREE.Mesh(new THREE.SphereGeometry(0.22, 16, 16), mm(0x8888aa));
  sj.position.y = 0.1;
  turret.add(sj);

  seg1 = new THREE.Group();
  seg1.position.y = 0.1;
  turret.add(seg1);
  const a1 = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.12, ARM1_LEN, 10), mm(0x5a5a7a));
  a1.position.y = ARM1_LEN / 2;
  a1.castShadow = true;
  seg1.add(a1);
  const ej = new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 16), mm(0x8888aa));
  ej.position.y = ARM1_LEN;
  seg1.add(ej);

  seg2 = new THREE.Group();
  seg2.position.y = ARM1_LEN;
  seg1.add(seg2);
  const a2 = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.1, ARM2_LEN, 10), mm(0x5a5a7a));
  a2.position.y = ARM2_LEN / 2;
  a2.castShadow = true;
  seg2.add(a2);
  const wj = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 12), mm(0x8888aa));
  wj.position.y = ARM2_LEN;
  seg2.add(wj);

  wrist = new THREE.Group();
  wrist.position.y = ARM2_LEN;
  seg2.add(wrist);

  const gMat = mm(0x5dca7b);
  const palm = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.12, 0.12), gMat);
  palm.position.y = 0.06;
  wrist.add(palm);

  function finger() {
    const g = new THREE.Group();
    const u = new THREE.Mesh(new THREE.BoxGeometry(0.06, CLAW_LEN, 0.12), gMat);
    u.position.y = -CLAW_LEN / 2;
    u.castShadow = true;
    g.add(u);
    const t = new THREE.Mesh(new THREE.BoxGeometry(0.06, CLAW_LEN * 0.35, 0.08), gMat);
    t.position.set(0.04, -CLAW_LEN - CLAW_LEN * 0.13, 0);
    t.rotation.z = 0.35;
    g.add(t);
    return g;
  }
  clawL = finger();
  clawL.position.set(-0.14, 0, 0);
  wrist.add(clawL);
  clawR = finger();
  clawR.position.set(0.14, 0, 0);
  clawR.scale.x = -1;
  wrist.add(clawR);
}

function setClawOpen(v) {
  clawSpread = v;
  clawL.position.x = -v;
  clawR.position.x = v;
}

function normToWorld(nx, ny) {
  return new THREE.Vector3((nx - 0.5) * 10, 0, (ny - 0.5) * 7);
}

function worldToArm(wx, wz) {
  const dx = wx - armRoot.position.x, dz = wz - armRoot.position.z;
  return { angle: Math.atan2(dx, dz), dist: Math.sqrt(dx * dx + dz * dz) };
}

function ikAngles(dist, ty) {
  const L1 = ARM1_LEN, L2 = ARM2_LEN + CLAW_LEN * 1.1;
  const y = Math.max(0.3, ty) - 0.35;
  const r = Math.min(dist, L1 + L2 - 0.3);
  const d = Math.sqrt(r * r + y * y);
  const cA = Math.acos(Math.max(-1, Math.min(1, (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d))));
  const bA = Math.atan2(y, r);
  const s1 = Math.PI / 2 - (cA + bA);
  const cB = Math.acos(Math.max(-1, Math.min(1, (L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2))));
  return { s1, s2: -(Math.PI - cB) };
}

function getGripWorld() {
  wrist.updateWorldMatrix(true, false);
  const v = new THREE.Vector3();
  wrist.getWorldPosition(v);
  return v;
}

const C_MAP = {
  kitchen: 0x378add, reading: 0x5dca7b, writing: 0xd4537e,
  electronics: 0x5d9eca, beverage: 0xef9f27, tools: 0x97c459, other: 0x888888,
};

function updateSimWorkspace(data) {
  simBlocks.forEach((b) => scene.remove(b));
  simBlocks = [];
  simZones.forEach((z) => scene.remove(z));
  simZones = [];

  const ws = data.workspace;

  for (const zone of ws.safety_zones) {
    const pts = zone.polygon;
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const zw = (Math.max(...xs) - Math.min(...xs)) * 10;
    const zh = (Math.max(...ys) - Math.min(...ys)) * 7;
    const WALL_H = 0.4;
    const zoneColor = zone.type === "robot_base" ? 0xf5a623 : 0xe24b4a;
    const zoneMat = new THREE.MeshStandardMaterial({
      color: zoneColor, transparent: true, opacity: 0.22, side: THREE.DoubleSide,
    });

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(zw, zh), zoneMat);
    const wp = normToWorld(cx, cy);
    floor.position.set(wp.x, 0.02, wp.z);
    floor.rotation.x = -Math.PI / 2;
    scene.add(floor);
    simZones.push(floor);

    const worldPts = pts.concat(pts[0]).map((p) => normToWorld(p.x, p.y));
    for (let i = 0; i < worldPts.length - 1; i++) {
      const a = worldPts[i], b = worldPts[i + 1];
      const len = Math.sqrt((b.x - a.x) ** 2 + (b.z - a.z) ** 2);
      if (len < 0.01) continue;
      const wall = new THREE.Mesh(new THREE.PlaneGeometry(len, WALL_H), zoneMat);
      wall.position.set((a.x + b.x) / 2, WALL_H / 2, (a.z + b.z) / 2);
      wall.rotation.y = Math.atan2(b.x - a.x, b.z - a.z);
      scene.add(wall);
      simZones.push(wall);
    }

    const border = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(
        worldPts.map((v) => new THREE.Vector3(v.x, 0.04, v.z)),
      ),
      new THREE.LineBasicMaterial({ color: zoneColor }),
    );
    scene.add(border);
    simZones.push(border);
  }

  for (const obj of ws.objects) {
    const area = obj.area_ratio || 0.01;
    const sz = Math.max(0.25, Math.min(0.8, Math.sqrt(area) * 5));
    const color = C_MAP[obj.category] || 0x888888;
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(sz, sz * 0.7, sz), mm(color));
    const wp = normToWorld(obj.centroid.x, obj.centroid.y);
    mesh.position.set(wp.x, sz * 0.35, wp.z);
    mesh.castShadow = true;
    mesh.userData = { id: obj.id, label: obj.label, oy: sz * 0.35, target: null };
    scene.add(mesh);
    simBlocks.push(mesh);
  }
}

function makeStepSprite(num) {
  const c = document.createElement('canvas');
  c.width = 64; c.height = 64;
  const ctx = c.getContext('2d');
  ctx.fillStyle = 'rgba(18,19,26,0.88)';
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 28px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(num), 32, 33);
  const tex = new THREE.CanvasTexture(c);
  const spr = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true })
  );
  return spr;
}

function addStepLabels() {
  simLabels.forEach(l => scene.remove(l));
  simLabels = [];
  if (!planData || !planData.sequence) return;
  const ph = document.getElementById('simPlaceholder');
  if (ph) ph.style.display = 'none';
  for (const step of planData.sequence) {
    const block = simBlocks.find(b => b.userData.id === step.object_id);
    if (!block) continue;
    const spr = makeStepSprite(step.step);
    spr.scale.set(0.7, 0.7, 1);
    spr.position.set(block.position.x, block.position.y + 1.0, block.position.z);
    spr.userData.block = block;
    scene.add(spr);
    simLabels.push(spr);
    block.userData.labelSprite = spr;
  }
}

// ============================================================
// SIMULATION ANIMATION
// ============================================================

function executePlan() {
  if (!planData || simSorting) return;
  const ph = document.getElementById("simPlaceholder");
  if (ph) ph.style.display = "none";
  simPlan = planData.sequence;
  simStepIdx = 0;
  simSorting = true;
  document.getElementById("simStatus").textContent = "executing";
  document.getElementById("simStatus").style.color = "var(--amber)";
  beginSimStep();
}

function beginSimStep() {
  if (simStepIdx >= simPlan.length) {
    simSorting = false;
    simPhase = "idle";
    document.getElementById("simStatus").textContent = "complete";
    document.getElementById("simStatus").style.color = "var(--green)";
    document.querySelectorAll(".step-item").forEach((e) => e.classList.remove("active"));
    return;
  }

  const step = simPlan[simStepIdx];

  if (step.skip) {
    const stepEl = document.getElementById(`step-${step.step}`);
    if (stepEl) stepEl.classList.add("done");
    simStepIdx++;
    setTimeout(beginSimStep, 150);
    return;
  }

  const block = simBlocks.find((b) => b.userData.id === step.object_id);
  if (!block) { simStepIdx++; beginSimStep(); return; }

  const tgt = normToWorld(step.to.x, step.to.y);
  block.userData.target = tgt;

  document.querySelectorAll(".step-item").forEach((e) => e.classList.remove("active"));
  const stepEl = document.getElementById(`step-${step.step}`);
  if (stepEl) {
    stepEl.classList.add("active");
    stepEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const pa = worldToArm(block.position.x, block.position.z);
  const ik = ikAngles(pa.dist, block.position.y + 0.1);
  setSimPhase(
    "reachAbove",
    { base: simPose.base, s1: simPose.s1, s2: simPose.s2 },
    { base: pa.angle, s1: Math.min(ik.s1 + 0.45, 1.4), s2: ik.s2 + 0.2 },
    block,
  );
}

function setSimPhase(p, from, to, block) {
  simPhase = p; simPhaseT = 0; simPFrom = from; simPTo = to;
  if (block) simPFrom._block = block;
}

function sm(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
function lerp(a, b, t) { return a + (b - a) * t; }

function advanceSimPhase() {
  const block = simPFrom._block || simBlocks[0];
  const step = simPlan[simStepIdx];
  const tgt = block.userData.target;

  if (simPhase === "reachAbove") {
    const pa = worldToArm(block.position.x, block.position.z);
    const ik = ikAngles(pa.dist, block.position.y);
    setSimPhase("descend", { s1: simPose.s1, s2: simPose.s2 }, { s1: ik.s1, s2: ik.s2 }, block);
  } else if (simPhase === "descend") {
    setSimPhase("grab", { clw: clawSpread }, { clw: 0.05 }, block);
  } else if (simPhase === "grab") {
    simHeld = block;
    setSimPhase("liftUp", { s1: simPose.s1, s2: simPose.s2 }, { s1: 0.75, s2: -0.55 }, block);
  } else if (simPhase === "liftUp") {
    const ta = worldToArm(tgt.x, tgt.z);
    setSimPhase("swing", { base: simPose.base }, { base: ta.angle }, block);
  } else if (simPhase === "swing") {
    const ta = worldToArm(tgt.x, tgt.z);
    const ik = ikAngles(ta.dist, block.userData.oy);
    setSimPhase("lowerTo", { s1: simPose.s1, s2: simPose.s2 }, { s1: ik.s1, s2: ik.s2 }, block);
  } else if (simPhase === "lowerTo") {
    block.position.set(tgt.x, block.userData.oy, tgt.z);
    simHeld = null;
    setSimPhase("release", { clw: clawSpread }, { clw: 0.14 }, block);
  } else if (simPhase === "release") {
    setSimPhase("retract", { s1: simPose.s1, s2: simPose.s2 }, { s1: 0.25, s2: -0.45 }, block);
  } else if (simPhase === "retract") {
    const stepEl = document.getElementById(`step-${step.step}`);
    if (stepEl) stepEl.classList.add("done");
    pingStepComplete(simStepIdx);
    simStepIdx++;
    simPhase = "pause";
    setTimeout(beginSimStep, 300);
  }
}

function animateSim() {
  requestAnimationFrame(animateSim);
  const dt = 0.016;
  simTime += dt;

  if (simSorting && simPhase !== "idle" && simPhase !== "pause") {
    simPhaseT = Math.min(1, simPhaseT + dt * 1.8);
    const e = sm(simPhaseT);

    if (simPFrom.base !== undefined && simPTo.base !== undefined)
      simPose.base = lerp(simPFrom.base, simPTo.base, e);
    if (simPFrom.s1 !== undefined && simPTo.s1 !== undefined)
      simPose.s1 = lerp(simPFrom.s1, simPTo.s1, e);
    if (simPFrom.s2 !== undefined && simPTo.s2 !== undefined)
      simPose.s2 = lerp(simPFrom.s2, simPTo.s2, e);
    if (simPFrom.clw !== undefined && simPTo.clw !== undefined)
      setClawOpen(lerp(simPFrom.clw, simPTo.clw, e));

    if (simPhaseT >= 1) advanceSimPhase();
  }

  turret.rotation.y = simPose.base;
  seg1.rotation.x = -simPose.s1;
  seg2.rotation.x = -simPose.s2;
  wrist.rotation.x = simPose.s1 + simPose.s2;

  if (simHeld) {
    const gp = getGripWorld();
    simHeld.position.set(gp.x, gp.y - CLAW_LEN - 0.1, gp.z);
  }

  simZones.forEach((z) => {
    if (z.material && z.material.opacity !== undefined)
      z.material.opacity = 0.12 + 0.04 * Math.sin(simTime * 3);
  });

  simLabels.forEach(lbl => {
    const b = lbl.userData.block;
    if (b) lbl.position.set(b.position.x, b.position.y + 1.0, b.position.z);
  });

  const ca = simTime * 0.06;
  camera.position.set(Math.sin(ca) * 11, 10, Math.cos(ca) * 9);
  camera.lookAt(0, 1, 0);

  renderer.render(scene, camera);
}

// ============================================================
// INIT
// ============================================================

window.addEventListener("resize", () => {
  if (renderer) {
    const W = simCanvas.clientWidth, H = simCanvas.clientHeight;
    renderer.setSize(W, H);
    camera.aspect = W / H;
    camera.updateProjectionMatrix();
  }
});

initSim();
