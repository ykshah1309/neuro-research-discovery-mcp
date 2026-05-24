// neuro-research-discovery web UI — vanilla JS, no build step.
// Loads tool catalog from /api/tools, generates a form from each tool's
// Pydantic-derived input schema, calls /api/tools/{name}, renders structured
// content. Live audit log via SSE.

const $ = (id) => document.getElementById(id);

const state = {
  tools: [],
  selected: null,
  audit_count: 0,
};

// ---- formatting helpers ----

function badge(text, kind) {
  const el = document.createElement("span");
  el.className = `pill pill-${kind}`;
  el.textContent = text;
  return el;
}

function familyOf(name) {
  if (name.startsWith("search_openneuro") || name.startsWith("get_openneuro") || name.startsWith("list_openneuro")) {
    return "OpenNeuro";
  }
  if (name.startsWith("get_neurovault") || name.startsWith("search_neurovault") || name.startsWith("prewarm_neurovault")) {
    return "NeuroVault";
  }
  if (name.startsWith("search_pubmed") || name.startsWith("get_pubmed") || name.startsWith("find_related_pubmed")) {
    return "PubMed";
  }
  return "Bridge";
}

// Render a JSON object with light syntax coloring + an inline notice
// when an `untrusted_text_warning` field is present.
function renderJson(value) {
  const seen = new WeakSet();
  function walk(v, indent) {
    if (v === null) return `<span class="j-null">null</span>`;
    if (typeof v === "string") return `<span class="j-str">${JSON.stringify(v)}</span>`;
    if (typeof v === "number") return `<span class="j-num">${v}</span>`;
    if (typeof v === "boolean") return `<span class="j-bool">${v}</span>`;
    if (Array.isArray(v)) {
      if (v.length === 0) return "[]";
      const inner = v.map(item => `${indent}  ${walk(item, indent + "  ")}`).join(",\n");
      return `[\n${inner}\n${indent}]`;
    }
    if (typeof v === "object") {
      if (seen.has(v)) return "/* circular */";
      seen.add(v);
      const keys = Object.keys(v);
      if (keys.length === 0) return "{}";
      const inner = keys.map(k => {
        const keyHtml = `<span class="j-key">${JSON.stringify(k)}</span>`;
        return `${indent}  ${keyHtml}: ${walk(v[k], indent + "  ")}`;
      }).join(",\n");
      return `{\n${inner}\n${indent}}`;
    }
    return String(v);
  }
  return walk(value, "");
}

// ---- tool list rendering ----

function renderToolList(filter = "") {
  const list = $("tool-list");
  list.innerHTML = "";
  const grouped = {};
  for (const t of state.tools) {
    if (filter && !t.name.toLowerCase().includes(filter.toLowerCase())) continue;
    const fam = familyOf(t.name);
    (grouped[fam] = grouped[fam] || []).push(t);
  }
  const order = ["OpenNeuro", "NeuroVault", "PubMed", "Bridge"];
  for (const fam of order) {
    const tools = grouped[fam];
    if (!tools || !tools.length) continue;
    const group = document.createElement("div");
    group.className = "tool-group";
    const label = document.createElement("div");
    label.className = "tool-group-label";
    label.textContent = fam;
    group.appendChild(label);
    for (const t of tools) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tool-btn";
      btn.textContent = t.name;
      if (state.selected && state.selected.name === t.name) btn.classList.add("active");
      btn.addEventListener("click", () => selectTool(t.name));
      group.appendChild(btn);
    }
    list.appendChild(group);
  }
}

// ---- schema-driven form ----

function fieldFromSchema(name, schema, required) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.htmlFor = `field-${name}`;
  label.textContent = name;
  if (required) {
    const star = document.createElement("span");
    star.className = "required";
    star.textContent = "*";
    label.appendChild(star);
  }
  wrap.appendChild(label);

  // Resolve `anyOf: [X, {type: 'null'}]` to the non-null type.
  let resolved = schema;
  if (Array.isArray(schema.anyOf)) {
    resolved = schema.anyOf.find(s => s.type !== "null") || schema;
  }

  let input;
  if (Array.isArray(resolved.enum)) {
    input = document.createElement("select");
    if (!required) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "(none)";
      input.appendChild(opt);
    }
    for (const v of resolved.enum) {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      input.appendChild(opt);
    }
  } else if (resolved.type === "integer" || resolved.type === "number") {
    input = document.createElement("input");
    input.type = "number";
    if (resolved.minimum !== undefined) input.min = resolved.minimum;
    if (resolved.maximum !== undefined) input.max = resolved.maximum;
    if (schema.default !== undefined) input.placeholder = `default: ${schema.default}`;
  } else if (resolved.type === "boolean") {
    const row = document.createElement("div");
    row.className = "field-row";
    input = document.createElement("input");
    input.type = "checkbox";
    if (schema.default === true) input.checked = true;
    row.appendChild(input);
    const span = document.createElement("span");
    span.className = "help";
    span.textContent = schema.description || "";
    row.appendChild(span);
    input.id = `field-${name}`;
    input.dataset.field = name;
    input.dataset.kind = "boolean";
    wrap.appendChild(row);
    return wrap;
  } else {
    input = document.createElement("input");
    input.type = "text";
    if (schema.default !== undefined) input.placeholder = `default: ${schema.default}`;
  }

  input.id = `field-${name}`;
  input.dataset.field = name;
  input.dataset.kind = resolved.type || (resolved.enum ? "enum" : "string");
  if (required) input.required = true;
  wrap.appendChild(input);

  if (schema.description) {
    const help = document.createElement("div");
    help.className = "help";
    help.textContent = schema.description;
    wrap.appendChild(help);
  }

  return wrap;
}

function renderForm(tool) {
  const root = $("form-fields");
  root.innerHTML = "";
  const schema = tool.inputSchema || { properties: {} };
  const required = new Set(schema.required || []);
  const props = schema.properties || {};
  // No fields? Render a placeholder so the form still has something visible.
  if (Object.keys(props).length === 0) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "This tool takes no arguments.";
    root.appendChild(note);
    return;
  }
  for (const [name, sub] of Object.entries(props)) {
    root.appendChild(fieldFromSchema(name, sub, required.has(name)));
  }
}

function collectFormValues() {
  const out = {};
  const inputs = $("form-fields").querySelectorAll("[data-field]");
  inputs.forEach(el => {
    const name = el.dataset.field;
    const kind = el.dataset.kind;
    if (kind === "boolean") {
      out[name] = el.checked;
    } else if (kind === "integer" || kind === "number") {
      const raw = el.value;
      if (raw === "") return;
      out[name] = kind === "integer" ? parseInt(raw, 10) : parseFloat(raw);
    } else {
      const raw = el.value;
      if (raw === "" || raw === undefined) return;
      out[name] = raw;
    }
  });
  return out;
}

// ---- tool selection + run ----

function selectTool(name) {
  const tool = state.tools.find(t => t.name === name);
  if (!tool) return;
  state.selected = tool;
  $("selected-name").textContent = tool.name;
  $("selected-desc").textContent = tool.description || "";
  $("tool-form").classList.remove("hidden");
  $("response").classList.add("hidden");
  renderForm(tool);
  renderToolList($("tool-search").value);
}

async function runSelected(ev) {
  ev.preventDefault();
  if (!state.selected) return;
  const args = collectFormValues();
  const name = state.selected.name;
  const btn = $("run-btn");
  const status = $("run-status");
  btn.disabled = true;
  status.textContent = "running…";

  const t0 = performance.now();
  let payload;
  try {
    const resp = await fetch(`/api/tools/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    });
    payload = await resp.json();
  } catch (e) {
    payload = { isError: true, structuredContent: { error_type: "network", human_readable_message: String(e) } };
  }
  const elapsed = ((performance.now() - t0) / 1000).toFixed(2);

  $("response").classList.remove("hidden");
  $("resp-timing").textContent = `${elapsed}s`;
  const errPill = $("resp-error");
  if (payload.isError) {
    errPill.classList.remove("hidden");
    errPill.classList.add("pill-bad");
    errPill.textContent = payload.structuredContent?.error_type || "error";
  } else {
    errPill.classList.add("hidden");
  }
  $("response-body").innerHTML = renderJson(payload.structuredContent);

  btn.disabled = false;
  status.textContent = `done in ${elapsed}s`;
}

// ---- live audit log ----

function appendAuditLine(line) {
  const log = $("audit-log");
  let parsed;
  try { parsed = JSON.parse(line); } catch { parsed = null; }
  const div = document.createElement("div");
  div.className = "audit-entry";
  if (parsed && parsed.is_error) div.classList.add("is-error");
  if (parsed) {
    const tool = document.createElement("span");
    tool.className = "audit-tool";
    tool.textContent = parsed.tool;
    div.appendChild(tool);
    const meta = document.createElement("span");
    meta.className = "audit-meta";
    const hit = parsed.cache_hits ?? 0;
    const miss = parsed.cache_misses ?? 0;
    const err = parsed.error_type ? ` err=${parsed.error_type}` : "";
    const via = parsed.via ? ` ${parsed.via}` : "";
    meta.textContent = `  ${parsed.elapsed_ms}ms  hit=${hit} miss=${miss}${err}${via}`;
    div.appendChild(meta);
  } else {
    div.textContent = line;
  }
  log.appendChild(div);
  // Cap displayed entries so memory doesn't grow unbounded over a long demo.
  while (log.childNodes.length > 300) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
  state.audit_count += 1;
}

function startAuditStream() {
  const es = new EventSource("/api/audit/stream");
  es.addEventListener("audit", ev => appendAuditLine(ev.data));
  es.addEventListener("error", () => {
    // Browser will retry automatically; nothing to do.
  });
  return es;
}

// ---- cache pill ----

async function refreshCachePill() {
  try {
    const r = await fetch("/api/cache/status").then(r => r.json());
    const pill = $("cache-pill");
    pill.classList.remove("pill-muted", "pill-good", "pill-warn", "pill-bad");
    if (r.status === "fresh") {
      pill.classList.add("pill-good");
      pill.textContent = `cache: fresh · ${r.collection_count?.toLocaleString() || "?"} collections`;
    } else if (r.status === "stale_but_serveable") {
      pill.classList.add("pill-warn");
      pill.textContent = "cache: stale (serving)";
    } else if (r.status === "expired") {
      pill.classList.add("pill-warn");
      pill.textContent = "cache: expired";
    } else {
      pill.classList.add("pill-bad");
      pill.textContent = "cache: missing — first search will be slow";
    }
  } catch {
    $("cache-pill").textContent = "cache: ?";
  }
}

// ---- bootstrap ----

async function init() {
  // Tool catalog
  const { tools } = await fetch("/api/tools").then(r => r.json());
  state.tools = tools;
  renderToolList();

  // Version pill
  const { version } = await fetch("/api/version").then(r => r.json());
  $("version-pill").textContent = `v${version}`;

  // Cache pill — refresh every 30s
  refreshCachePill();
  setInterval(refreshCachePill, 30000);

  // Live audit stream
  startAuditStream();

  // Wire up controls
  $("tool-form").addEventListener("submit", runSelected);
  $("reset-btn").addEventListener("click", () => {
    if (state.selected) renderForm(state.selected);
    $("response").classList.add("hidden");
  });
  $("tool-search").addEventListener("input", e => renderToolList(e.target.value));
  $("audit-clear").addEventListener("click", () => { $("audit-log").innerHTML = ""; });

  // Pre-select the showcase tool so guests have something obvious to click.
  if (state.tools.find(t => t.name === "comprehensive_literature_search")) {
    selectTool("comprehensive_literature_search");
  } else if (state.tools.length) {
    selectTool(state.tools[0].name);
  }
}

init().catch(err => {
  document.body.innerHTML = `<pre style="padding:20px;color:#f85149">${err.stack || err}</pre>`;
});
