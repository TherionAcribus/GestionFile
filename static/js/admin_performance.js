(function () {
  "use strict";
  const root = document.getElementById("performance-app");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
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
  function error(message) { $("page-error").textContent = message; $("page-error").classList.toggle("d-none", !message); }
  function option(value, label) { const node = document.createElement("option"); node.value = value; node.textContent = label; return node; }
  function preflightLabel(key) { return ({feature_enabled:"Lancement autorise dans Administration > Application",environment_mode:"Mode serveur configure",runner:"Runner sain et recent",readyz:"Application prete (/readyz)",no_active_run:"Aucun autre test actif",target_locked:"Cible conforme a la configuration",production_queue_empty:"File de production vide"})[key] || key; }
  function updateLaunch() {
    if (!capabilities) return;
    const okay = Object.values(capabilities.preflight).every(Boolean);
    $("launch-button").disabled = !capabilities.launch_allowed || !okay || $("confirmation").value !== "LANCER LE TEST";
  }
  function renderPreflight() {
    const list = $("preflight-list"); list.replaceChildren();
    Object.entries(capabilities.preflight).forEach(([key, okay]) => {
      const li = document.createElement("li"); li.className = "list-group-item px-0";
      li.innerHTML = `<span class="preflight-icon ${okay ? "preflight-ok" : "preflight-ko"}"><i class="bi ${okay ? "bi-check-circle-fill" : "bi-x-circle-fill"}"></i></span>${preflightLabel(key)}`;
      list.appendChild(li);
    }); updateLaunch();
  }
  function allowed(spec) { return (spec.modes || []).includes(capabilities.mode); }
  function populateSelectors() {
    const scenario = $("scenario"), profile = $("profile"); scenario.replaceChildren(); profile.replaceChildren();
    Object.entries(capabilities.scenarios).forEach(([key, spec]) => { if (capabilities.mode !== "production" || key === "consultation") scenario.appendChild(option(key, spec.label)); });
    Object.entries(capabilities.profiles).forEach(([key, spec]) => { if (allowed(spec)) profile.appendChild(option(key, spec.label)); });
    updateEstimate();
  }
  function selectedParams() {
    const key = $("profile").value, spec = capabilities.profiles[key] || {};
    return key === "custom" ? {users:+$("users").value,spawn_rate:+$("spawn-rate").value,duration:+$("duration").value} : spec;
  }
  function updateEstimate() {
    if (!capabilities) return;
    const custom = $("profile").value === "custom"; $("custom-fields").classList.toggle("d-none", !custom);
    const p = selectedParams(); const factors={consultation:.45,kiosk:.22,queue_counter:.30,realtime:.08,mixed:.34};
    const count = Math.ceil((p.users||0)*(p.duration||0)*(factors[$("scenario").value]||0));
    $("estimate").textContent = `${count.toLocaleString("fr-FR")} requetes environ`;
  }
  async function loadCapabilities() {
    error(""); capabilities = await api("/admin/performance/api/capabilities");
    $("mode-badge").textContent = capabilities.mode; $("mode-badge").className = `badge text-bg-${capabilities.mode === "production" ? "danger" : "warning"}`;
    $("runner-badge").textContent = capabilities.runner.healthy ? "Runner operationnel" : "Runner indisponible";
    $("runner-badge").className = `badge text-bg-${capabilities.runner.healthy ? "success" : "danger"}`;
    $("production-warning").classList.toggle("d-none", capabilities.mode !== "production"); $("target-url").textContent = capabilities.target || "Non configuree";
    const disabledWarning = $("disabled-warning");
    if (capabilities.mode === "disabled") {
      disabledWarning.textContent = "La page est consultable, mais le mode serveur est desactive. Configurez STRESS_TEST_MODE pour autoriser un lancement.";
      disabledWarning.classList.remove("d-none");
    } else if (!capabilities.enabled) {
      disabledWarning.textContent = "Le lancement est desactive par defaut. Activez-le dans Administration > Application > Tests de performance.";
      disabledWarning.classList.remove("d-none");
    } else {
      disabledWarning.classList.add("d-none");
    }
    populateSelectors(); renderPreflight();
  }
  function summaryValue(run,key,suffix="") { const value=(run.summary||{})[key]; return value == null ? "—" : `${typeof value === "number" ? value.toLocaleString("fr-FR",{maximumFractionDigits:2}) : value}${suffix}`; }
  function renderHistory(runs) {
    const body=$("history-body"); body.replaceChildren(); const selects=[$("compare-left"),$("compare-right")]; selects.forEach(s=>s.replaceChildren());
    runs.forEach((run,index)=>{
      const tr=document.createElement("tr"); const date=run.created_at ? new Date(run.created_at).toLocaleString("fr-FR") : "—";
      tr.innerHTML=`<td>${date}</td><td>${run.scenario_label}</td><td>${run.profile_label}</td><td>${run.state}</td><td>${run.verdict||"—"}</td><td>${summaryValue(run,"average_rps")}</td><td>${summaryValue(run,"p95_ms"," ms")}</td><td><a class="btn btn-sm btn-outline-secondary" href="/admin/performance/api/runs/${run.uuid}/export.csv">CSV</a> <a class="btn btn-sm btn-outline-secondary" href="/admin/performance/api/runs/${run.uuid}/export.json">JSON</a></td>`; body.appendChild(tr);
      selects.forEach((s)=>s.appendChild(option(run.uuid,`${date} · ${run.profile_label}`))); if(index===1) $("compare-left").value=run.uuid;
    }); if(!runs.length) body.innerHTML='<tr><td colspan="8" class="text-muted">Aucun test enregistre.</td></tr>';
  }
  async function loadHistory() { const data=await api("/admin/performance/api/runs"); renderHistory(data.runs); const active=data.runs.find(r=>["queued","preparing","running","stopping"].includes(r.state)); if(active&&!currentRun){currentRun=active;startPolling();} }
  function drawChart() {
    const canvas=$("live-chart"), ctx=canvas.getContext("2d"), width=canvas.clientWidth||800,height=180,dpr=window.devicePixelRatio||1; canvas.width=width*dpr;canvas.height=height*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
    if(samples.length<2)return; const pad=24,maxR=Math.max(1,...samples.map(s=>s.rps||0)),maxP=Math.max(1,...samples.map(s=>s.p95_ms||0));
    ctx.strokeStyle="#0d6efd";ctx.lineWidth=2;ctx.beginPath();samples.forEach((s,i)=>{const x=pad+i*(width-2*pad)/(samples.length-1),y=height-pad-(s.rps||0)*(height-2*pad)/maxR;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();
    ctx.strokeStyle="#dc3545";ctx.beginPath();samples.forEach((s,i)=>{const x=pad+i*(width-2*pad)/(samples.length-1),y=height-pad-(s.p95_ms||0)*(height-2*pad)/maxP;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();
    ctx.font="12px sans-serif";ctx.fillStyle="#0d6efd";ctx.fillText("RPS",pad,14);ctx.fillStyle="#dc3545";ctx.fillText("p95",pad+36,14);
  }
  function renderLive(run) {
    $("live-section").classList.remove("d-none"); $("live-identity").textContent=`${run.scenario_label} · ${run.profile_label} · ${run.uuid}`; $("metric-state").textContent=run.state;
    const last=samples[samples.length-1]; $("metric-rps").textContent=last ? last.rps.toFixed(1) : "—"; $("metric-p95").textContent=last&&last.p95_ms!=null ? `${Math.round(last.p95_ms)} ms` : "—"; $("metric-errors").textContent=last ? `${(last.error_rate||0).toFixed(1)} %` : "—";
    $("stop-button").disabled=!["queued","preparing","running","stopping"].includes(run.state); drawChart();
    if(["completed","failed","aborted","interrupted"].includes(run.state)){const v=$("final-verdict");v.classList.remove("d-none","alert-success","alert-warning","alert-danger");v.classList.add(run.verdict==="successful"?"alert-success":run.verdict==="degraded"?"alert-warning":"alert-danger");v.textContent=`Verdict : ${run.verdict||run.state}${run.stop_reason?` · ${run.stop_reason}`:""}`;}
  }
  async function poll() {
    if(!currentRun)return; try {const [runData,sampleData]=await Promise.all([api(`/admin/performance/api/runs/${currentRun.uuid}`),api(`/admin/performance/api/runs/${currentRun.uuid}/samples?after_id=${lastSampleId}`)]);currentRun=runData.run;if(sampleData.samples.length){samples.push(...sampleData.samples);samples=samples.slice(-300);lastSampleId=samples[samples.length-1].id;}renderLive(currentRun);if(["completed","failed","aborted","interrupted"].includes(currentRun.state)){clearInterval(pollTimer);pollTimer=null;await loadHistory();await loadCapabilities();}} catch(e){error(e.message);} }
  function startPolling(){samples=[];lastSampleId=0;renderLive(currentRun);if(pollTimer)clearInterval(pollTimer);pollTimer=setInterval(poll,1000);poll();}
  $("run-form").addEventListener("submit",async(e)=>{e.preventDefault();try{const payload={scenario:$("scenario").value,profile:$("profile").value,confirmation:$("confirmation").value,...selectedParams()};const data=await api("/admin/performance/api/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});currentRun=data.run;startPolling();await loadHistory();}catch(err){error(err.message);}});
  $("stop-button").addEventListener("click",async()=>{if(!currentRun)return;try{const data=await api(`/admin/performance/api/runs/${currentRun.uuid}/stop`,{method:"POST"});currentRun=data.run;renderLive(currentRun);}catch(e){error(e.message);}});
  $("compare-button").addEventListener("click",async()=>{try{const data=await api(`/admin/performance/api/compare?left=${encodeURIComponent($("compare-left").value)}&right=${encodeURIComponent($("compare-right").value)}`);const rows=Object.entries(data.differences).map(([k,v])=>`<tr><th>${k}</th><td>${v.left??"—"}</td><td>${v.right??"—"}</td><td>${v.absolute==null?"—":v.absolute.toFixed(2)}</td><td>${v.percent==null?"—":v.percent.toFixed(1)+" %"}</td></tr>`).join("");const box=$("comparison");box.innerHTML=`<table class="table table-sm"><thead><tr><th>Mesure</th><th>Reference</th><th>Compare</th><th>Ecart</th><th>Ecart %</th></tr></thead><tbody>${rows}</tbody></table>`;box.classList.remove("d-none");}catch(e){error(e.message);}});
  ["profile","scenario","users","spawn-rate","duration"].forEach(id=>$(id).addEventListener("change",updateEstimate));$("confirmation").addEventListener("input",updateLaunch);$("refresh-capabilities").addEventListener("click",()=>loadCapabilities().catch(e=>error(e.message)));window.addEventListener("resize",drawChart);
  Promise.all([loadCapabilities(),loadHistory()]).catch(e=>error(e.message));
}());
