(function () {
  "use strict";

  const root = document.getElementById("performance-app");
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const TERMINAL_STATES = ["completed", "failed", "aborted", "interrupted"];
  const ACTIVE_STATES = ["queued", "preparing", "running", "stopping"];
  const METRIC_LABELS = {
    average_rps: "Débit moyen (req/s)", p95_ms: "Latence p95 (ms)",
    p99_ms: "Latence p99 (ms)", error_rate: "Taux d'erreur (%)",
    max_cpu_percent: "CPU maximal (%)", max_memory_bytes: "Mémoire maximale (octets)"
  };
  let capabilities = null;
  let currentRun = null;
  let lastSampleId = 0;
  let samples = [];
  let pollTimer = null;

  async function api(url, options) {
    const response = await fetch(url, options || {});
    let body = {};
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) throw new Error(body.error || `Erreur HTTP ${response.status}`);
    return body;
  }

  function error(message) {
    $("page-error").textContent = message;
    $("page-error").classList.toggle("d-none", !message);
  }

  function option(value, label) {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  }

  function number(value, digits = 1) {
    return value == null ? "—" : Number(value).toLocaleString("fr-FR", {maximumFractionDigits: digits});
  }

  function bytes(value) {
    if (value == null) return "—";
    const units = ["o", "Kio", "Mio", "Gio"];
    let amount = Number(value), unit = 0;
    while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
    return `${number(amount, amount >= 10 ? 1 : 2)} ${units[unit]}`;
  }

  function preflightLabel(key) {
    return ({
      feature_enabled: "Lancement autorisé dans Administration > Application",
      environment_mode: "Mode serveur configuré",
      runner: "Runner connecté (heartbeat récent)",
      readyz: "Application prête (/readyz)",
      no_active_run: "Aucun autre test actif",
      target_locked: "Cible conforme à la configuration",
      production_queue_empty: "File de production vide"
    })[key] || key;
  }

  function updateLaunch() {
    if (!capabilities) return;
    const okay = Object.values(capabilities.preflight).every(Boolean);
    $("launch-button").disabled = !capabilities.launch_allowed || !okay ||
      $("confirmation").value !== "LANCER LE TEST";
  }

  function renderPreflight() {
    const list = $("preflight-list");
    list.replaceChildren();
    Object.entries(capabilities.preflight).forEach(([key, okay]) => {
      const li = document.createElement("li");
      li.className = "list-group-item px-0";
      const icon = document.createElement("span");
      icon.className = `preflight-icon ${okay ? "preflight-ok" : "preflight-ko"}`;
      icon.innerHTML = `<i class="bi ${okay ? "bi-check-circle-fill" : "bi-x-circle-fill"}"></i>`;
      li.append(icon, document.createTextNode(preflightLabel(key)));
      list.appendChild(li);
    });
    updateLaunch();
  }

  function allowed(spec) { return (spec.modes || []).includes(capabilities.mode); }

  function populateSelectors() {
    const scenario = $("scenario"), profile = $("profile");
    const selectedScenario = scenario.value;
    const selectedProfile = profile.value;
    scenario.replaceChildren(); profile.replaceChildren();
    Object.entries(capabilities.scenarios).forEach(([key, spec]) => {
      if (capabilities.mode !== "production" || key === "consultation") {
        scenario.appendChild(option(key, spec.label));
      }
    });
    Object.entries(capabilities.profiles).forEach(([key, spec]) => {
      if (allowed(spec)) profile.appendChild(option(key, spec.label));
    });
    if ([...scenario.options].some((item) => item.value === selectedScenario)) {
      scenario.value = selectedScenario;
    }
    if ([...profile.options].some((item) => item.value === selectedProfile)) {
      profile.value = selectedProfile;
    }
    updateEstimate();
  }

  function selectedParams() {
    const key = $("profile").value, spec = capabilities.profiles[key] || {};
    return key === "custom" ? {
      users: +$("users").value,
      spawn_rate: +$("spawn-rate").value,
      duration: +$("duration").value
    } : spec;
  }

  function updateEstimate() {
    if (!capabilities) return;
    const custom = $("profile").value === "custom";
    $("custom-fields").classList.toggle("d-none", !custom);
    const p = selectedParams();
    const factors = {consultation: .45, kiosk: .22, queue_counter: .30, realtime: .08, mixed: .34};
    const count = Math.ceil((p.users || 0) * (p.duration || 0) * (factors[$("scenario").value] || 0));
    $("estimate").textContent = `${count.toLocaleString("fr-FR")} requêtes environ`;
  }

  async function loadCapabilities() {
    error("");
    capabilities = await api("/admin/performance/api/capabilities");
    $("mode-badge").textContent = capabilities.mode;
    $("mode-badge").className = `badge text-bg-${capabilities.mode === "production" ? "danger" : "warning"}`;
    $("runner-badge").textContent = capabilities.runner.healthy ? "Runner opérationnel" : "Runner indisponible";
    $("runner-badge").className = `badge text-bg-${capabilities.runner.healthy ? "success" : "danger"}`;
    $("runner-warning").classList.toggle("d-none", capabilities.runner.healthy);
    $("production-warning").classList.toggle("d-none", capabilities.mode !== "production");
    $("target-url").textContent = capabilities.target || "Non configurée";
    const warning = $("disabled-warning");
    if (capabilities.mode === "disabled") {
      warning.textContent = "La page est consultable, mais le mode serveur est désactivé. Configurez STRESS_TEST_MODE pour autoriser un lancement.";
      warning.classList.remove("d-none");
    } else if (!capabilities.enabled) {
      warning.textContent = "Le lancement est désactivé par défaut. Activez-le dans Administration > Application > Tests de performance.";
      warning.classList.remove("d-none");
    } else {
      warning.classList.add("d-none");
    }
    populateSelectors(); renderPreflight();
  }

  function summaryValue(run, key, suffix = "") {
    const value = (run.summary || {})[key];
    return value == null ? "—" : `${number(value, 2)}${suffix}`;
  }

  function renderHistory(runs) {
    const body = $("history-body");
    const selects = [$("compare-left"), $("compare-right")];
    const selectedComparisons = selects.map((select) => select.value);
    body.replaceChildren(); selects.forEach((select) => select.replaceChildren());
    runs.forEach((run, index) => {
      const tr = document.createElement("tr");
      const date = run.created_at ? new Date(run.created_at).toLocaleString("fr-FR") : "—";
      [date, run.scenario_label, run.profile_label, run.state_label || run.state,
        run.verdict_label || "—", summaryValue(run, "average_rps"),
        summaryValue(run, "p95_ms", " ms")].forEach((value) => {
          const td = document.createElement("td"); td.textContent = value; tr.appendChild(td);
        });
      const reportCell = document.createElement("td");
      const reportButton = document.createElement("button");
      reportButton.type = "button"; reportButton.className = "btn btn-sm btn-primary view-report";
      reportButton.dataset.runId = run.uuid; reportButton.textContent = "Voir le rapport";
      reportCell.appendChild(reportButton); tr.appendChild(reportCell);
      const exportCell = document.createElement("td");
      [["CSV", "export.csv"], ["JSON", "export.json"]].forEach(([label, suffix]) => {
        const link = document.createElement("a");
        link.className = "btn btn-sm btn-outline-secondary me-1";
        link.href = `/admin/performance/api/runs/${run.uuid}/${suffix}`;
        link.textContent = label; exportCell.appendChild(link);
      });
      tr.appendChild(exportCell); body.appendChild(tr);
      selects.forEach((select) => select.appendChild(option(run.uuid, `${date} · ${run.profile_label}`)));
      if (index === 1 && !selectedComparisons[0]) $("compare-left").value = run.uuid;
    });
    selects.forEach((select, index) => {
      if ([...select.options].some((item) => item.value === selectedComparisons[index])) {
        select.value = selectedComparisons[index];
      }
    });
    if (!runs.length) {
      const td = document.createElement("td"); td.colSpan = 9;
      td.className = "text-muted"; td.textContent = "Aucun test enregistré.";
      const tr = document.createElement("tr"); tr.appendChild(td); body.appendChild(tr);
    }
  }

  async function loadHistory() {
    const data = await api("/admin/performance/api/runs");
    renderHistory(data.runs);
    const active = data.runs.find((run) => ACTIVE_STATES.includes(run.state));
    if (active && !currentRun) { currentRun = active; startPolling(); }
  }

  function drawChart() {
    const canvas = $("live-chart"), ctx = canvas.getContext("2d");
    const width = canvas.clientWidth || 800, height = 180, dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr; canvas.height = height * dpr;
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, width, height);
    if (samples.length < 2) return;
    const pad = 24, maxR = Math.max(1, ...samples.map((s) => s.rps || 0));
    const maxP = Math.max(1, ...samples.map((s) => s.p95_ms || 0));
    function line(color, value, max) {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
      samples.forEach((sample, index) => {
        const x = pad + index * (width - 2 * pad) / (samples.length - 1);
        const y = height - pad - (value(sample) || 0) * (height - 2 * pad) / max;
        index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
    line("#0d6efd", (sample) => sample.rps, maxR);
    line("#dc3545", (sample) => sample.p95_ms, maxP);
    ctx.font = "12px sans-serif"; ctx.fillStyle = "#0d6efd"; ctx.fillText("RPS", pad, 14);
    ctx.fillStyle = "#dc3545"; ctx.fillText("p95", pad + 36, 14);
  }

  function renderEndpointReport(endpoints) {
    const body = $("endpoint-body"); body.replaceChildren();
    const rows = Object.entries(endpoints || {}).sort((left, right) =>
      (right[1].errors || 0) - (left[1].errors || 0));
    rows.forEach(([name, data]) => {
      const requests = data.requests || 0, errors = data.errors || 0;
      const rate = requests ? errors / requests * 100 : 0;
      const tr = document.createElement("tr");
      if (errors) tr.classList.add("endpoint-failed");
      [name, number(requests, 0), number(errors, 0), `${number(rate, 1)} %`,
        data.p95_ms == null ? "—" : `${number(data.p95_ms, 0)} ms`,
        data.first_error || (errors ? "Erreur non détaillée" : "Aucune")].forEach((value) => {
          const td = document.createElement("td"); td.textContent = value; tr.appendChild(td);
        });
      body.appendChild(tr);
    });
    if (!rows.length) {
      const td = document.createElement("td"); td.colSpan = 6;
      td.className = "text-muted"; td.textContent = "Aucune mesure par endpoint disponible.";
      const tr = document.createElement("tr"); tr.appendChild(td); body.appendChild(tr);
    }
  }

  function renderFinalReport(run) {
    const terminal = TERMINAL_STATES.includes(run.state);
    $("result-report").classList.toggle("d-none", !terminal);
    $("final-verdict").classList.toggle("d-none", !terminal);
    if (!terminal) return;

    const summary = run.summary || {};
    const verdict = $("final-verdict");
    verdict.classList.remove("alert-success", "alert-warning", "alert-danger");
    verdict.classList.add(run.verdict === "successful" ? "alert-success" :
      run.verdict === "degraded" ? "alert-warning" : "alert-danger");
    const title = document.createElement("strong");
    title.textContent = `Verdict : ${run.verdict_label || run.verdict || run.state_label || run.state}`;
    const explanation = document.createElement("div");
    explanation.textContent = run.result_message || "Le test est terminé.";
    verdict.replaceChildren(title, explanation);

    const started = run.started_at ? new Date(run.started_at).toLocaleString("fr-FR") : "heure inconnue";
    const duration = summary.duration_seconds != null ? `${number(summary.duration_seconds, 0)} s` : "durée inconnue";
    $("result-period").textContent = `Démarré le ${started} · ${duration} · ${run.scenario_label} · ${run.profile_label}`;
    $("result-requests").textContent = number(summary.total_requests, 0);
    $("result-errors").textContent = `${number(summary.total_errors, 0)} (${number(summary.error_rate, 1)} %)`;
    $("result-rps").textContent = `${number(summary.average_rps, 1)} req/s`;
    $("result-p95").textContent = `${number(summary.p95_ms, 0)} ms`;
    $("result-p50").textContent = `${number(summary.p50_ms, 0)} ms`;
    $("result-p99").textContent = `${number(summary.p99_ms, 0)} ms`;
    $("result-cpu").textContent = `${number(summary.max_cpu_percent, 1)} %`;
    $("result-memory").textContent = bytes(summary.max_memory_bytes);

    const diagnosis = [];
    diagnosis.push(run.result_message || "Le test est terminé.");
    if (summary.ready_percent != null) {
      diagnosis.push(`L'application a répondu prête pendant ${number(summary.ready_percent, 1)} % des relevés.`);
    }
    if ((summary.error_rate || 0) > 0) {
      diagnosis.push("Consultez les lignes rouges ci-dessous pour identifier les pages ou actions en erreur.");
    } else {
      diagnosis.push("Aucune requête en erreur n'a été détectée.");
    }
    $("result-diagnosis").textContent = diagnosis.join(" ");
    $("result-technical-error").textContent = run.error_message || "";
    $("result-technical-error").classList.toggle("d-none", !run.error_message);
    renderEndpointReport(summary.endpoints);
    $("result-csv").href = `/admin/performance/api/runs/${run.uuid}/export.csv`;
    $("result-json").href = `/admin/performance/api/runs/${run.uuid}/export.json`;
  }

  function renderLive(run) {
    $("live-section").classList.remove("d-none");
    $("live-identity").textContent = `${run.scenario_label} · ${run.profile_label} · ${run.uuid}`;
    $("metric-state").textContent = run.state_label || run.state;
    const last = samples[samples.length - 1];
    $("metric-rps").textContent = last ? number(last.rps, 1) : "—";
    $("metric-p95").textContent = last && last.p95_ms != null ? `${number(last.p95_ms, 0)} ms` : "—";
    $("metric-errors").textContent = last ? `${number(last.error_rate || 0, 1)} %` : "—";
    $("stop-button").disabled = !ACTIVE_STATES.includes(run.state);
    drawChart(); renderFinalReport(run);
  }

  async function loadReport(runId) {
    error("");
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    const [runData, sampleData] = await Promise.all([
      api(`/admin/performance/api/runs/${runId}`),
      api(`/admin/performance/api/runs/${runId}/samples?after_id=0`)
    ]);
    currentRun = runData.run; samples = sampleData.samples;
    lastSampleId = samples.length ? samples[samples.length - 1].id : 0;
    renderLive(currentRun);
    $("live-section").scrollIntoView({behavior: "smooth", block: "start"});
    if (ACTIVE_STATES.includes(currentRun.state)) startPolling(false);
  }

  async function poll() {
    if (!currentRun) return;
    try {
      const [runData, sampleData] = await Promise.all([
        api(`/admin/performance/api/runs/${currentRun.uuid}`),
        api(`/admin/performance/api/runs/${currentRun.uuid}/samples?after_id=${lastSampleId}`)
      ]);
      currentRun = runData.run;
      if (sampleData.samples.length) {
        samples.push(...sampleData.samples); samples = samples.slice(-600);
        lastSampleId = samples[samples.length - 1].id;
      }
      renderLive(currentRun);
      if (TERMINAL_STATES.includes(currentRun.state)) {
        clearInterval(pollTimer); pollTimer = null;
        await loadHistory(); await loadCapabilities();
      }
    } catch (exception) { error(exception.message); }
  }

  function startPolling(reset = true) {
    if (reset) { samples = []; lastSampleId = 0; }
    renderLive(currentRun);
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, 1000); poll();
  }

  $("run-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = {scenario: $("scenario").value, profile: $("profile").value,
        confirmation: $("confirmation").value, ...selectedParams()};
      const data = await api("/admin/performance/api/runs", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
      });
      currentRun = data.run; startPolling(); await loadHistory();
    } catch (exception) { error(exception.message); }
  });

  $("stop-button").addEventListener("click", async () => {
    if (!currentRun) return;
    try {
      const data = await api(`/admin/performance/api/runs/${currentRun.uuid}/stop`, {method: "POST"});
      currentRun = data.run; renderLive(currentRun);
    } catch (exception) { error(exception.message); }
  });

  $("history-body").addEventListener("click", (event) => {
    const button = event.target.closest(".view-report");
    if (button) loadReport(button.dataset.runId).catch((exception) => error(exception.message));
  });

  $("compare-button").addEventListener("click", async () => {
    try {
      const data = await api(`/admin/performance/api/compare?left=${encodeURIComponent($("compare-left").value)}&right=${encodeURIComponent($("compare-right").value)}`);
      const table = document.createElement("table"); table.className = "table table-sm";
      table.innerHTML = "<thead><tr><th>Mesure</th><th>Référence</th><th>Comparé</th><th>Écart</th><th>Écart %</th></tr></thead>";
      const body = document.createElement("tbody");
      Object.entries(data.differences).forEach(([key, value]) => {
        const tr = document.createElement("tr");
        [METRIC_LABELS[key] || key, number(value.left, 2), number(value.right, 2),
          number(value.absolute, 2), value.percent == null ? "—" : `${number(value.percent, 1)} %`].forEach((text) => {
          const td = document.createElement("td"); td.textContent = text; tr.appendChild(td);
        });
        body.appendChild(tr);
      });
      table.appendChild(body); $("comparison").replaceChildren(table);
      $("comparison").classList.remove("d-none");
    } catch (exception) { error(exception.message); }
  });

  ["profile", "scenario", "users", "spawn-rate", "duration"].forEach((id) =>
    $(id).addEventListener("change", updateEstimate));
  $("confirmation").addEventListener("input", updateLaunch);
  $("refresh-capabilities").addEventListener("click", () =>
    loadCapabilities().catch((exception) => error(exception.message)));
  window.addEventListener("resize", drawChart);
  Promise.all([loadCapabilities(), loadHistory()]).catch((exception) => error(exception.message));
}());
