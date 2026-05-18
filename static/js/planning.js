// ============================================================
// PLANNING (Agent 2)
// ============================================================

async function generatePlan() {
  const loader = document.getElementById("planLoader");
  loader.classList.add("active");
  document.getElementById("btnPlan").disabled = true;
  document.getElementById("planStatus").textContent = "planning...";
  document.getElementById("planEmpty").style.display = "none";
  startLogPolling(msg => {
    document.getElementById("planStatus").textContent = msg;
  });

  try {
    const resp = await fetch(`${API}/api/plan`, { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.error || "Planning failed");
    }

    planData = await resp.json();
    renderSteps(planData);
    addStepLabels();
    if (workspaceData && sourceImage) {
      const nf = makePlanNumFn(planData);
      drawAnnotations(sourceImage, workspaceData, nf);
      renderTagStrip(workspaceData.workspace, nf);
    }
    if (!isVideoMode) document.getElementById("btnExec").disabled = false;
    document.getElementById("planStatus").textContent =
      `${planData.sequence.length} steps — ${planData.strategy || ""}`;

    runEvaluator();
    if (isVideoMode) executePlan();
  } catch (e) {
    document.getElementById("planStatus").style.color = "var(--red)";
    document.getElementById("planStatus").textContent = "error: " + e.message;
    document.getElementById("btnPlan").disabled = false;
  }

  stopLogPolling();
  loader.classList.remove("active");
}

async function runEvaluator() {
  try {
    const resp = await fetch(`${API}/api/evaluate`, { method: "POST" });
    if (!resp.ok) return;
    const ev = await resp.json();
    renderEvalCard(ev);
  } catch (_) {}
}

function renderEvalCard(ev) {
  const card = document.getElementById("evalCard");
  if (!ev || ev.predicted_score == null) { card.classList.remove("visible"); return; }

  const score = ev.predicted_score;
  const stars = "★".repeat(score) + "☆".repeat(5 - score);
  const color = score >= 4 ? "var(--green)" : score >= 3 ? "var(--amber)" : "var(--red)";

  document.getElementById("evalStars").textContent = stars;
  document.getElementById("evalScore").textContent = `${score}/5`;
  document.getElementById("evalScore").style.cssText =
    `background:${color}22;color:${color};font-size:11px;font-weight:600;padding:1px 7px;border-radius:10px;margin-left:auto`;
  document.getElementById("evalCritique").textContent = ev.critique || "";

  const ul = document.getElementById("evalSuggestions");
  ul.innerHTML = "";
  (ev.suggestions || []).forEach(s => {
    const li = document.createElement("li");
    li.textContent = s;
    ul.appendChild(li);
  });

  card.classList.add("visible");
}

function renderSteps(plan) {
  const list = document.getElementById("stepsList");
  list.innerHTML = "";

  const labelMap = {};
  if (workspaceData) {
    workspaceData.workspace.objects.forEach((o) => {
      labelMap[o.id] = o.label.replace(/_/g, " ");
    });
  }

  if (plan.reasoning) {
    const r = document.createElement("div");
    r.style.cssText =
      "padding:8px 10px;margin-bottom:8px;font-size:11px;color:var(--muted);font-style:italic;border-left:2px solid var(--border);padding-left:10px";
    r.textContent = plan.reasoning;
    list.appendChild(r);
  }

  const enforcedCount = plan.sequence.filter(s =>
    s.reason && s.reason.includes("[safety enforced]")
  ).length;
  const skippedCount = plan.sequence.filter(s => s.skip).length;
  if (enforcedCount > 0) {
    const warn = document.createElement("div");
    warn.style.cssText =
      "padding:6px 10px;margin-bottom:8px;font-size:11px;color:var(--amber);" +
      "border-left:2px solid var(--amber);padding-left:10px;font-family:var(--mono)";
    warn.textContent =
      `⚠ Safety enforcement relocated ${enforcedCount} target(s) away from safety zones`;
    list.appendChild(warn);
  }
  if (skippedCount > 0) {
    const skip = document.createElement("div");
    skip.style.cssText =
      "padding:6px 10px;margin-bottom:8px;font-size:11px;color:#e24b4a;" +
      "border-left:2px solid #e24b4a;padding-left:10px;font-family:var(--mono)";
    skip.textContent = `✖ ${skippedCount} step(s) skipped — no safe carry path found`;
    list.appendChild(skip);
  }

  for (const step of plan.sequence) {
    const label = labelMap[step.object_id] || step.object_id;
    const enforced = step.reason && step.reason.includes("[safety enforced]");
    const skipped = !!step.skip;
    const el = document.createElement("div");
    el.className = "step-item" + (skipped ? " step-skipped" : "");
    el.id = `step-${step.step}`;
    const reasonText = (step.reason || "")
      .replace(" [safety enforced]", "")
      .replace(" [skipped: no safe path available]", "")
      .trim();
    el.innerHTML = skipped
      ? `
      <div class="step-num" style="opacity:0.4">${step.step}</div>
      <div class="step-text" style="opacity:0.5;text-decoration:line-through">
          <div class="step-label">${label} — no safe path</div>
          <div class="step-reason">${reasonText}</div>
      </div>
      <span style="color:#e24b4a;font-size:11px;margin-left:auto;align-self:center">✖ skipped</span>`
      : `
      <div class="step-num">${step.step}</div>
      <div class="step-text">
          <div class="step-label">${label} → (${step.to.x.toFixed(2)}, ${step.to.y.toFixed(2)})${enforced ? ' <span style="color:var(--amber);font-size:10px">⚠ relocated</span>' : ''}</div>
          <div class="step-reason">${reasonText}</div>
      </div>`;
    list.appendChild(el);
  }
}
