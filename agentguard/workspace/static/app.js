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
let polling = false, session = 0;
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
    if (el.dataset.page === view) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
}
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
async function packs() {
  const rows = await api("packs");
  const prior = $("source").value;
  $("source").replaceChildren(option("catalog", "Sample catalog"), ...rows.map(p => option(p.id, p.name + " · " + p.id.slice(0, 8))));
  if ($("source").dataset.web === "true") $("source").add(option("web", "Live web"));
  if ([...$("source").options].some(o => o.value === prior)) $("source").value = prior;
  $("pack-list").replaceChildren(...rows.map(p => button(p.name + " · " + p.size + " documents · " + p.id.slice(0, 8), async () => {
    const pack = await api("packs/" + p.id);
    const preview = $("pack-preview");
    preview.replaceChildren(node("h3", pack.name), node("small", pack.id));
    pack.documents.forEach(d => preview.append(node("h3", d.title), node("p", d.content), node("small", d.url)));
    preview.append(button("Research this pack", () => {
      $("source").value = p.id; show("runs"); document.querySelector("[data-view=runs] details").open = true; $("question").focus();
    }));
  }, "row")));
  if (!rows.length) $("pack-list").append(node("p", "No evidence packs saved."));
}
async function workspace() {
  const info = await api("workspace");
  $("health").textContent = info.workers + " active workers · " + (info.runs.queued || 0) + " queued · " + (info.runs.running || 0) + " running";
  return info;
}
async function history() {
  const version = ++listing;
  const rows = await api("runs?limit=16&offset=" + offset + ($("filter").value ? "&status=" + $("filter").value : ""));
  if (version !== listing) return;
  $("history").replaceChildren(...rows.slice(0, 15).map(r => {
    const el = button(r.kind + " · " + r.status + " · " + new Date(r.created * 1000).toLocaleString(), () => inspect(r.id), "row");
    el.setAttribute("aria-pressed", String(selected === r.id)); return el;
  }));
  if (!rows.length) $("history").append(node("p", "No runs in this selection."));
  $("prev").disabled = offset === 0; $("next").disabled = rows.length <= 15;
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
      panel.append(node("p", name + " · " + pass.count + "/" + pass.total + " scenarios passed", "metric"));
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
    offset = 0; $("filter").value = ""; show("runs"); await inspect(r.id); await history();
    message("Run queued.");
  } finally { submitter.disabled = false; }
}
action($("authenticate"), async () => {
  token = $("token").value.trim(); session++;
  const info = await workspace();
  $("token").value = ""; $("source").dataset.web = String(info.web_enabled);
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
  scenarioOptions(); await packs(); await history();
  $("login").hidden = true; $("workspace").hidden = false; $("disconnect").hidden = false; show("runs"); message("");
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
action($("pack-form"), async () => {
  const documents = [...$("documents").children].map(box => Object.fromEntries([...box.querySelectorAll("[name]")].map(el => [el.name, el.value])));
  const r = await api("packs", {name:$("pack-name").value, documents});
  $("pack-form").reset(); $("documents").replaceChildren(); documentEditor(); await packs(); message("Saved pack " + r.id.slice(0,12) + ".");
}, "submit");
action($("research"), () => {
  const value = $("source").value, pack = !["web","catalog"].includes(value);
  return submit($("research"), {kind:"research", query:$("question").value, source: pack ? "pack" : value,
    pack_id: pack ? value : null, drafter:$("drafter").value});
}, "submit");
action($("category"), scenarioOptions, "change");
action($("evaluation-form"), () => submit($("evaluation-form"), {kind:"evaluation",
  profiles:[...$("profiles").querySelectorAll(":checked")].map(el => el.value),
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
