"use strict";
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
const option = (value, label) => new Option(label, value);
let token = "", selected = "", page = "runs", offset = 0, scenarios = [], listing = 0;
let polling = false, session = 0, historySnapshot = "";
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
}
function action(element, callback, type = "click") {
  element.addEventListener(type, async event => {
    event.preventDefault();
    try { await callback(); } catch (err) { message(err.message, true); }
  });
}
async function api(path, data, key) {
  const epoch = session;
  const headers = {Authorization: "Bearer " + token};
  const config = {headers};
  if (data !== undefined) {
    config.method = "POST";
    headers["Content-Type"] = "application/json";
    if (key) headers["Idempotency-Key"] = key;
    config.body = JSON.stringify(data);
  }
  const response = await fetch("/v1/" + path, config);
  const body = await response.json();
  if (epoch !== session) throw Error("Workspace session changed.");
  if (!response.ok) throw Error(body.detail || "Workspace request failed.");
  return body;
}
function button(text, callback, cls = "secondary") {
  const el = node("button", text, cls);
  el.type = "button";
  action(el, callback);
  return el;
}
function show(view) {
  page = view;
  document.querySelectorAll("[data-view]").forEach(el => el.hidden = el.dataset.view !== view);
  document.querySelectorAll("[data-page]").forEach(el => {
    if (el.dataset.page === view) {
      el.setAttribute("aria-current", "page");
      $("breadcrumb").textContent = el.textContent;
    }
    else el.removeAttribute("aria-current");
  });
}
function setComposer(open, moveFocus = false) {
  $("composer").hidden = !open;
  $("new-run").setAttribute("aria-expanded", String(open));
  if (moveFocus) (open ? $("question") : $("new-run")).focus();
}
action($("new-run"), () => setComposer($("composer").hidden, true));
action($("close-composer"), () => setComposer(false, true));
function download(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function raw(label, data) {
  const section = node("details");
  section.append(node("summary", label), node("pre", JSON.stringify(data, null, 2)));
  return section;
}
function documentEditor() {
  const box = node("fieldset", undefined, "doc");
  box.append(node("legend", "Document"));
  for (const [name, text, tag, size] of [["title","Title","input",160],["content","Text","textarea",4000],["url","Source URL (optional)","input",2048]]) {
    const label = node("label", text), input = node(tag);
    input.name = name; input.maxLength = size; input.required = name !== "url";
    label.append(input); box.append(label);
  }
  box.append(button("Remove document", () => {
    if ($("documents").children.length === 1) return message("A pack needs at least one document.");
    box.remove(); $("add-document").disabled = false;
  }));
  $("documents").append(box);
  $("add-document").disabled = $("documents").children.length >= 5;
}
function scenarioOptions() {
  const category = $("category").value;
  $("scenario").replaceChildren(option("", "All scenarios"), ...scenarios.filter(s => !category || s.category === category).map(s => option(s.id, s.id)));
}
function checkEditor() {
  const box = node("fieldset", undefined, "doc");
  box.append(node("legend", "Acceptance check"));
  for (const [name, caption, tag] of [["query","Test question","input"],["status","Expected outcome","select"],["contains","Expected text (optional)","input"],["document","Document number (optional)","input"]]) {
    const label = node("label", caption), input = node(tag);
    input.name = name;
    if (name === "status") input.append(option("answered","Answered"),option("insufficient","Insufficient evidence"));
    if (name === "query") { input.required = true; input.maxLength = 1000; }
    if (name === "contains") input.maxLength = 500;
    if (name === "document") { input.type = "number"; input.min = "1"; input.max = "5"; }
    label.append(input); box.append(label);
  }
  box.append(button("Remove check", () => {box.remove(); $("add-check").disabled = false;}));
  $("checks").append(box); $("add-check").disabled = $("checks").children.length >= 12;
}
async function packs() {
  const rows = await api("packs");
  const prior = $("source").value;
  const previousPack = $("eval-pack").value;
  $("eval-pack").replaceChildren(option("", "Bundled suite only"), ...rows.filter(p => p.checks > 0).map(p => option(p.id, p.name)));
  if (rows.some(p => p.id === previousPack && p.checks)) $("eval-pack").value = previousPack;
  const web = option("web", "Live web");
  $("source").replaceChildren(option("catalog", "Sample catalog"), web, ...rows.map(p => option(p.id, p.name + " · " + p.id.slice(0, 8))));
  updateWebOption();
  if ([...$("source").options].some(o => o.value === prior && !o.disabled)) $("source").value = prior;
  $("pack-list").replaceChildren(...rows.map(p => button(p.name + " · " + p.size + " documents · " + p.id.slice(0, 8), async () => {
    const pack = await api("packs/" + p.id);
    const preview = $("pack-preview");
    preview.replaceChildren(node("h3", pack.name), node("small", pack.id));
    pack.documents.forEach(d => preview.append(node("h3", d.title), node("p", d.content), node("small", d.url)));
    if (pack.checks.length) preview.append(raw("Acceptance checks", pack.checks));
    preview.append(button("Research this pack", () => {
      $("source").value = p.id; show("runs"); setComposer(true, true);
    }));
  }, "row")));
  if (!rows.length) $("pack-list").append(node("p", "No evidence packs saved."));
}
function updateWebOption() {
  const web = $("source").querySelector('option[value="web"]');
  if (!web) return;
  const reason = $("source").dataset.webReason;
  web.disabled = Boolean(reason);
  web.textContent = reason === "missing_key" ? "Live web (set TAVILY_API_KEY)"
    : reason === "disabled" ? "Live web (disabled by operator)"
    : reason === "legacy" ? "Live web (restart workspace to update)"
    : reason ? "Live web (unavailable)" : "Live web";
}
async function workspace() {
  const info = await api("workspace");
  $("source").dataset.webReason = info.web_enabled ? "" : (info.web_unavailable_reason ??
    ("web_unavailable_reason" in info ? "unavailable" : "legacy"));
  updateWebOption();
  $("connection").textContent = info.workers ? "Worker connected" : "Waiting for worker";
  $("connection").classList.toggle("online", info.workers > 0);
  const counts = [["Active workers",info.workers],["Queued",info.runs.queued || 0],
    ["Running",info.runs.running || 0],["Completed",info.runs.completed || 0]];
  $("health").replaceChildren(...counts.map(([label,value]) => {
    const tile = node("div", undefined, "counter");
    tile.append(node("span",label),node("strong",String(value))); return tile;
  }));
  return info;
}
async function history() {
  const version = ++listing;
  const rows = await api("runs?limit=16&offset=" + offset + ($("filter").value ? "&status=" + $("filter").value : ""));
  if (version !== listing) return;
  const snapshot = JSON.stringify([offset, $("filter").value, rows]);
  if (snapshot === historySnapshot) return rows;
  historySnapshot = snapshot;
  $("history").replaceChildren(...rows.slice(0, 15).map(r => {
    const el = button("", () => inspect(r.id), "row history-row");
    el.append(node("span", r.kind === "research" ? "Research" : "Evaluation", "job-name"),
      node("small", new Date(r.created * 1000).toLocaleString()), node("span", r.status, "state state-" + r.status));
    el.dataset.runId = r.id;
    el.setAttribute("aria-pressed", String(selected === r.id)); return el;
  }));
  if (!rows.length) $("history").append(node("p", "No runs in this selection."));
  $("prev").disabled = offset === 0; $("next").disabled = rows.length <= 15;
  return rows;
}
async function inspect(id) {
  selected = id;
  const r = await api("runs/" + id);
  if (selected !== id) return;
  const panel = $("detail");
  panel.replaceChildren(node("h2", r.kind === "research" ? "Research run" : "Evaluation run"),
    node("p", r.id, "meta"), node("p", r.status + " · Attempt " + r.attempt + " · " + r.progress + "/" + r.total + " steps"));
  if (r.error) panel.append(node("p", r.error));
  if (r.request.query) panel.append(node("h3", "Question"), node("p", r.request.query));
  if (r.result && r.kind === "research") {
    panel.append(node("h3", "Answer · " + r.result.status), node("pre", r.result.response));
    for (const [key, uri] of Object.entries(r.result.sources)) {
      const citation = (r.result.citations || []).find(c => c.source === key);
      panel.append(node("p", key + " · " + (citation ? citation.title + " · document " + citation.document : uri || "Source excerpt"), "meta"));
    }
    panel.append(raw("Graph trace", r.result.trace));
  }
  if (r.result && r.kind === "evaluation") {
    for (const [name, metrics] of Object.entries(r.result.metrics)) {
      const pass = metrics["Scenario pass"];
      const block = node("div", undefined, "score");
      block.append(node("strong", name), node("span", pass.count + "/" + pass.total + " scenarios passed"));
      const bar = node("progress"); bar.max = pass.total; bar.value = pass.count; bar.setAttribute("aria-label", name + " pass rate");
      block.append(bar); panel.append(block);
    }
    panel.append(button("Assess release", () => { $("candidate").value = id; $("gate-profile").value = r.request.profiles[0]; show("gates"); }));
    panel.append(raw("Trial results", r.result.runs));
  }
  panel.append(raw("Lifecycle events", r.events));
  if (r.status === "running" || r.status === "queued") {
    panel.append(button("Cancel run", async () => { await api("runs/" + id + "/cancel", {}); await inspect(id); await history(); }));
  } else {
    panel.append(button("Download result", () => download(id + ".json", r.result || r)));
  }
  panel.dataset.status = r.status;
  if (!["queued","running"].includes(r.status) && $("message").textContent === "Run queued.") message("");
}
async function submit(form, spec) {
  const signature = JSON.stringify(spec);
  if (form.dataset.signature !== signature) {
    form.dataset.signature = signature; form.dataset.key = crypto.randomUUID();
  }
  const submitter = form.querySelector("button:not([type=button])");
  submitter.disabled = true;
  try {
    const r = await api("runs", spec, form.dataset.key);
    delete form.dataset.signature; delete form.dataset.key;
    offset = 0; $("filter").value = ""; show("runs"); setComposer(false);
    message("Run queued."); await inspect(r.id); await history();
  } finally { submitter.disabled = false; }
}
action($("authenticate"), async () => {
  token = $("token").value.trim(); session++;
  const info = await workspace();
  $("token").value = "";
  $("drafter").replaceChildren(option("extractive","Extractive"));
  if (info.model_enabled) $("drafter").add(option("ollama","Ollama"));
  $("profiles").replaceChildren();
  for (const name of info.profiles) {
    const label = node("label", undefined, "check"), input = node("input");
    input.type = "checkbox"; input.value = name; input.checked = name.endsWith("replay");
    label.append(input, document.createTextNode(name)); $("profiles").append(label);
  }
  $("gate-profile").replaceChildren(...info.profiles.map(p => option(p, p))); $("gate-profile").value = "guarded-replay";
  scenarios = await api("scenarios");
  $("category").replaceChildren(option("", "All categories"), ...[...new Set(scenarios.map(s => s.category))].sort().map(s => option(s,s)));
  scenarioOptions(); await packs();
  const rows = await history();
  $("login").hidden = true; $("workspace").hidden = false; $("disconnect").hidden = false; show("runs"); message("");
  if (rows?.length) await inspect(rows[0].id);
}, "submit");
action($("disconnect"), () => { token = ""; session++; location.reload(); });
document.querySelectorAll("[data-page]").forEach(el => action(el, async () => {
  show(el.dataset.page); message("");
  if (page === "runs") await history();
  if (page === "packs") await packs();
  if (page === "gates") {
    const rows = await api("runs?status=completed&limit=100");
    $("evaluated").replaceChildren(...rows.filter(r => r.kind === "evaluation").map(r => option(r.id,r.id)));
  }
}));
action($("add-document"), documentEditor); documentEditor();
action($("add-check"), checkEditor);
action($("pack-form"), async () => {
  const documents = [...$("documents").children].map(box => Object.fromEntries([...box.querySelectorAll("[name]")].map(el => [el.name, el.value])));
  const checks = [...$("checks").children].map(box => {
    const data = Object.fromEntries([...box.querySelectorAll("[name]")].map(el => [el.name, el.value]));
    data.document = data.document ? Number(data.document) : null;
    return data;
  });
  const r = await api("packs", {name:$("pack-name").value, documents, checks});
  $("pack-form").reset(); $("documents").replaceChildren(); $("checks").replaceChildren(); $("add-check").disabled = false;
  documentEditor(); await packs(); message("Saved pack " + r.id.slice(0,12) + ".");
}, "submit");
action($("research"), () => {
  const value = $("source").value, pack = !["web","catalog"].includes(value);
  return submit($("research"), {kind:"research", query:$("question").value, source: pack ? "pack" : value,
    pack_id: pack ? value : null, drafter:$("drafter").value});
}, "submit");
action($("category"), scenarioOptions, "change");
action($("eval-pack"), () => {
  const pack = $("eval-pack").value;
  for (const el of $("profiles").querySelectorAll("input")) {
    el.disabled = !!pack && el.value.endsWith("replay");
    el.checked = pack ? el.value === "guarded-extractive" : el.value.endsWith("replay");
  }
  $("category").value = ""; scenarioOptions();
}, "change");
action($("evaluation-form"), () => submit($("evaluation-form"), {kind:"evaluation",
  profiles:[...$("profiles").querySelectorAll(":checked")].map(el => el.value),
  pack_id:$("eval-pack").value || null,
  category:$("category").value || null, scenario:$("scenario").value || null}), "submit");
action($("gate-form"), async () => {
  const d = await api("gates", {run_id:$("candidate").value, baseline_id:$("baseline").value || null,
    profile:$("gate-profile").value, minimum_pass_rate:Number($("threshold").value)/100});
  const panel = $("decision"); panel.replaceChildren(node("h2", "Decision · " + d.verdict));
  d.checks.forEach(c => panel.append(node("p", c.name + " · " + c.detail + " · " + c.outcome, "metric")));
  if (d.changes.length) panel.append(raw("Changed scenarios", d.changes));
  panel.append(button("Download decision", () => download("gate-" + d.run_id + ".json", d)));
}, "submit");
action($("filter"), () => { offset = 0; return history(); }, "change");
action($("prev"), () => { offset = Math.max(0, offset - 15); return history(); });
action($("next"), () => { offset += 15; return history(); });
action($("refresh"), async () => { await workspace(); await history(); if (selected) await inspect(selected); });
setInterval(async () => {
  if ($("workspace").hidden || document.hidden || polling || page !== "runs") return;
  polling = true;
  try {
    await workspace(); await history();
    if (selected && ["queued","running"].includes($("detail").dataset.status)) await inspect(selected);
  } catch (err) { message(err.message, true); } finally { polling = false; }
}, 4000);
