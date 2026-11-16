/* Khata front-end. Plain JavaScript, no build step. */
const $ = (id) => document.getElementById(id);
const PALETTE = ["#2457c5", "#0f8a5f", "#e0a100", "#d6455d", "#7b4fc9", "#17a2b8", "#e8743b", "#6b7f2a", "#c2578f", "#4a6b8a"];
const OTHER = "#9aa7b5";

const state = { jobId: null, month: "", txns: [], page: 0, perPage: 25, charts: {}, catColors: {} };

/* ---------- formatting ---------- */
const inr = (n) => (n < 0 ? "-" : "") + "₹" + Math.abs(Math.round(n)).toLocaleString("en-IN");
const inr2 = (n) => "₹" + Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function compact(n) {
  const a = Math.abs(n), s = n < 0 ? "-" : "";
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)} crore`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(2)} lakh`;
  return inr(n);
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });

function show(view) {
  for (const v of ["upload", "progress", "dash"]) $("view-" + v).hidden = v !== view;
  window.scrollTo(0, 0);
}

/* ---------- upload ---------- */
let chosen = null;
function setFile(f) {
  chosen = f;
  $("drop").classList.toggle("has-file", !!f);
  $("drop-title").textContent = f ? f.name : "Choose a PDF or drag it here";
  $("drop-sub").textContent = f ? (f.size / 1048576).toFixed(1) + " MB, ready to analyze" : "SBI “Statement of Account”, up to 60 MB";
  $("go").disabled = !f;
}

async function init() {
  const banks = await (await fetch("/api/banks")).json();
  $("bank").innerHTML = banks.map((b) => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join("");

  $("file").addEventListener("change", () => setFile($("file").files[0] || null));
  ["dragover", "dragenter"].forEach((t) => $("drop").addEventListener(t, (e) => { e.preventDefault(); $("drop").classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => $("drop").addEventListener(t, (e) => { e.preventDefault(); $("drop").classList.remove("over"); }));
  $("drop").addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) setFile(f); });

  $("upload-form").addEventListener("submit", upload);
  $("new").addEventListener("click", () => { setFile(null); $("file").value = ""; show("upload"); });
  $("month").addEventListener("change", () => { state.month = $("month").value; loadDashboard(); });
  for (const id of ["q", "f-flow", "f-cat"]) $(id).addEventListener("input", () => { state.page = 0; renderTable(); });
  $("prev").addEventListener("click", () => { state.page--; renderTable(); });
  $("next").addEventListener("click", () => { state.page++; renderTable(); });
}

async function upload(e) {
  e.preventDefault();
  $("upload-error").hidden = true;
  const fd = new FormData();
  fd.append("file", chosen);
  fd.append("bank", $("bank").value);
  if ($("password").value) fd.append("password", $("password").value);
  $("go").disabled = true;
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || "Upload failed.");
    state.jobId = (await res.json()).job_id;
    show("progress");
    poll();
  } catch (err) {
    $("upload-error").textContent = err.message;
    $("upload-error").hidden = false;
    $("go").disabled = false;
  }
}

async function poll() {
  const j = await (await fetch(`/api/jobs/${state.jobId}`)).json();
  $("bar-fill").style.width = Math.max(3, j.progress) + "%";
  $("progress-msg").textContent = j.message;
  if (j.status === "error") {
    show("upload");
    $("upload-error").textContent = j.error;
    $("upload-error").hidden = false;
    $("go").disabled = !chosen;
  } else if (j.status === "done") {
    await openDashboard();
  } else {
    setTimeout(poll, 1000);
  }
}

/* ---------- dashboard ---------- */
async function openDashboard() {
  state.month = "";
  state.txns = await (await fetch(`/api/jobs/${state.jobId}/transactions`)).json();
  $("csv").href = `/api/jobs/${state.jobId}/export.csv`;
  await loadDashboard(true);
  show("dash");
}

async function loadDashboard(first = false) {
  const q = state.month ? `?month=${state.month}` : "";
  const d = await (await fetch(`/api/jobs/${state.jobId}/dashboard${q}`)).json();
  state.data = d;
  if (first) {
    $("month").innerHTML = `<option value="">Whole statement</option>` + d.meta.months.map((m) => `<option value="${m.id}">${m.label}</option>`).join("");
    const cats = [...new Set(state.txns.map((t) => t.category))].sort();
    $("f-cat").innerHTML = `<option value="">All categories</option>` + cats.map((c) => `<option>${esc(c)}</option>`).join("");
    d.categories.forEach((c, i) => (state.catColors[c.name] = i < PALETTE.length ? PALETTE[i] : OTHER));
  }
  $("month").value = state.month;
  renderHeader(d);
  renderTiles(d);
  renderCharts(d);
  renderLists(d);
  state.page = 0;
  renderTable();
}

function renderHeader(d) {
  const t = d.totals, m = d.meta;
  const where = state.month ? d.meta.months.find((x) => x.id === state.month).label : `${t.from} to ${t.to}`;
  $("meta").textContent = `${m.bank}${m.account_last4 ? " · account ending " + m.account_last4 : ""} · ${where}`;
  $("hero").innerHTML = `You received <span class="in">${compact(t.money_in)}</span> and paid out <span class="out">${compact(t.money_out)}</span>.`;

  const c = m.check;
  const el = $("check");
  el.hidden = false;
  el.classList.toggle("warn", c.checked ? !c.ok : c.review_rows > 0);
  if (c.checked && c.ok) {
    el.textContent = `✓ Checked against your bank’s own totals (${inr2(c.stated_debits)} out, ${inr2(c.stated_credits)} in). Everything matches.` + (c.fixed_rows ? ` ${c.fixed_rows} misread number(s) were corrected using your running balance.` : "");
  } else if (c.checked) {
    el.textContent = `Heads up: totals differ from the bank’s summary by ${inr2(Math.abs(c.diff_debits))} (out) and ${inr2(Math.abs(c.diff_credits))} (in). ${c.review_rows} row(s) are highlighted in the table for you to check.`;
  } else {
    el.textContent = c.review_rows ? `${c.review_rows} row(s) are highlighted in the table because they were hard to read.` : "Could not find the bank’s printed totals to double-check against.";
    el.hidden = !c.review_rows && c.checked === false ? false : el.hidden;
  }
}

function renderTiles(d) {
  const t = d.totals;
  const tiles = [
    ["in", "Money in", inr(t.money_in), `${t.receipts} deposits`],
    ["out", "Money out", inr(t.money_out), `${t.payments} payments`],
    ["net", t.net >= 0 ? "Left over" : "Overspent by", inr(Math.abs(t.net)), t.money_in ? `${Math.round((100 * Math.abs(t.net)) / t.money_in)}% of money in` : ""],
    ["", "Balance at the end", t.closing_balance == null ? "–" : inr(t.closing_balance), `about ${inr(t.avg_daily_spend)} spent per day`],
  ];
  $("tiles").innerHTML = tiles.map(([c, l, v, s]) => `<div class="tile ${c}"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`).join("");
}

function chart(id, cfg) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart($(id), cfg);
}
const axisMoney = { ticks: { callback: (v) => (v >= 1e5 ? v / 1e5 + "L" : v >= 1e3 ? v / 1e3 + "k" : v) }, grid: { color: "#e8edf2" } };

function renderCharts(d) {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = "#5b6b7d";

  chart("c-monthly", {
    type: "bar",
    data: {
      labels: d.monthly.map((m) => m.label),
      datasets: [
        { label: "Money in", data: d.monthly.map((m) => m.money_in), backgroundColor: d.monthly.map((m) => (state.month && m.id !== state.month ? "#a8d8c6" : "#0f8a5f")), borderRadius: 4 },
        { label: "Money out", data: d.monthly.map((m) => m.money_out), backgroundColor: d.monthly.map((m) => (state.month && m.id !== state.month ? "#efb4be" : "#d6455d")), borderRadius: 4 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ...axisMoney }, x: { grid: { display: false } } },
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${inr(c.raw)}` } } },
      onClick: (_, els) => { if (els.length) { state.month = d.monthly[els[0].index].id; loadDashboard(); } },
    },
  });

  chart("c-cat", {
    type: "doughnut",
    data: { labels: d.categories.map((c) => c.name), datasets: [{ data: d.categories.map((c) => c.amount), backgroundColor: d.categories.map((c) => state.catColors[c.name] || OTHER), borderWidth: 2, borderColor: "#fff" }] },
    options: { maintainAspectRatio: false, cutout: "62%", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.label}: ${inr(c.raw)}` } } } },
  });

  chart("c-balance", {
    type: "line",
    data: { labels: d.balance.map((b) => b.date), datasets: [{ label: "Balance", data: d.balance.map((b) => b.balance), borderColor: "#2457c5", backgroundColor: "rgba(36,87,197,.10)", fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15 }] },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { y: { ...axisMoney }, x: { ticks: { maxTicksLimit: 8, callback: function (v) { return fmtDate(this.getLabelForValue(v)).replace(/ \d{4}$/, ""); } }, grid: { display: false } } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { title: (i) => fmtDate(i[0].label), label: (c) => `Balance: ${inr(c.raw)}` } } },
    },
  });

  const max = Math.max(...d.weekday.map((w) => w.amount));
  chart("c-weekday", {
    type: "bar",
    data: { labels: d.weekday.map((w) => w.day), datasets: [{ data: d.weekday.map((w) => w.amount), backgroundColor: d.weekday.map((w) => (w.amount === max ? "#d6455d" : "#e7a4af")), borderRadius: 4 }] },
    options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true, ...axisMoney }, x: { grid: { display: false } } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `Spent ${inr(c.raw)} in ${d.weekday[c.dataIndex].count} payments` } } } },
  });
}

function renderLists(d) {
  $("cat-list").innerHTML = d.categories.map((c) =>
    `<li data-cat="${esc(c.name)}" title="Show these transactions"><span class="dot" style="background:${state.catColors[c.name] || OTHER}"></span><span>${esc(c.name)}</span><span class="amt">${inr(c.amount)}</span><span class="pct">${c.pct}%</span></li>`).join("");
  document.querySelectorAll("#cat-list li").forEach((li) => li.addEventListener("click", () => {
    $("f-cat").value = li.dataset.cat; $("f-flow").value = "out"; state.page = 0; renderTable();
    $("txn-card").scrollIntoView({ behavior: "smooth" });
  }));
  $("insights").innerHTML = d.insights.map((t) => `<li>${esc(t)}</li>`).join("") || "<li>Not enough data yet.</li>";
  $("sources").innerHTML = d.income_sources.map((s) => `<li><span>${esc(s.name)}</span><strong>${inr(s.amount)}</strong></li>`).join("") || "<li>No deposits in this period.</li>";
  $("payees").innerHTML = d.top_payees.map((p) => `<tr><td>${esc(p.name)}<div class="muted">${p.times} payment${p.times > 1 ? "s" : ""}</div></td><td class="num"><strong>${inr(p.total)}</strong></td></tr>`).join("") || "<tr><td>No named payees found.</td></tr>";
  $("top-exp").innerHTML = d.top_expenses.map((e) => `<tr><td class="muted">${e.date}</td><td>${esc(e.to)}</td><td><span class="badge">${esc(e.category)}</span></td><td class="num"><strong>${inr2(e.amount)}</strong></td></tr>`).join("");
}

/* ---------- transaction table (filters run in the browser) ---------- */
function filtered() {
  const q = $("q").value.trim().toLowerCase(), flow = $("f-flow").value, cat = $("f-cat").value;
  return state.txns.filter((t) =>
    (!state.month || t.month === state.month) &&
    (!cat || t.category === cat) &&
    (!flow || (flow === "out" ? t.debit > 0 : t.credit > 0)) &&
    (!q || t.description.toLowerCase().includes(q) || (t.payee || "").toLowerCase().includes(q)));
}

function renderTable() {
  const rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / state.perPage));
  state.page = Math.min(Math.max(0, state.page), pages - 1);
  const slice = rows.slice(state.page * state.perPage, (state.page + 1) * state.perPage);
  $("txn-body").innerHTML = slice.map((t) => {
    const who = t.payee || t.kind;
    return `<tr class="${t.review ? "review" : ""}">
      <td class="d">${fmtDate(t.date)}</td>
      <td class="desc"><div class="who">${esc(who)}${t.review ? ' <span title="This row was hard to read. Please check it against your PDF.">⚠</span>' : ""}</div><div class="raw">${esc(t.description)}</div></td>
      <td><span class="badge">${esc(t.category)}</span></td>
      <td class="num o">${t.debit ? inr2(t.debit) : ""}</td><td class="num i">${t.credit ? inr2(t.credit) : ""}</td>
      <td class="num">${t.balance == null ? "" : inr2(t.balance)}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No transactions match these filters.</td></tr>`;
  $("txn-count").textContent = `(${rows.length.toLocaleString("en-IN")})`;
  $("page-info").textContent = `Page ${state.page + 1} of ${pages}`;
  $("prev").disabled = state.page === 0;
  $("next").disabled = state.page >= pages - 1;
}

init();
