// Business Idea Checker - the report page (brief section 7).
// The verdict, ratings and money figures come from fixed rules on the server;
// this file only lays them out. Every factual number carries its tag.

let reportTimer = null;

const VERDICTS = {
  pursue: { name: "Pursue", cls: "good" },
  test: { name: "Test small first", cls: "warn" },
  drop: { name: "Drop or rethink", cls: "bad" },
};
const RATING_CLASS = { strong: "good", okay: "mid", weak: "bad" };
const STAGE_ORDER = ["research", "checking", "scoring", "writing"];
const STAGE_LABELS = {
  research: "Finishing research on competitors and the market",
  checking: "Checking your answers against the research",
  scoring: "Scoring the six areas",
  writing: "Writing your report",
};

// ---------- small pieces ----------

function tagChip(tag) {
  if (!tag) return null;
  return el("span", { class: "tag-chip " + tag, title: TAG_HELP[tag] || "" }, tag);
}

const TAG_HELP = {
  verified: "Read on a source page",
  estimated: "Worked out or taken from a nearby market; reasoning shown in the sources",
  assumed: "Your own figure, or no evidence either way",
};

// Writer text marks each number with (verified), (estimated) or (assumed): show them as chips.
function rich(text) {
  const span = el("span", {});
  if (!text) return span;
  const parts = String(text).split(/\((verified|estimated|assumed)\)/);
  parts.forEach((part, i) => span.append(i % 2 ? tagChip(part) : part));
  return span;
}

function figure(n, currency, suffix) {
  if (!n || n.value == null) return el("span", { class: "muted" }, "unknown");
  const value = currency ? money(n.value, currency) : Number(n.value).toLocaleString();
  return el("span", { class: "figure" }, value + (suffix || ""), " ", tagChip(n.tag));
}

function section(title, ...children) {
  return el("section", { class: "report-section" }, el("h2", {}, title), ...children);
}

function para(text, cls) {
  return el("p", { class: cls || "" }, rich(text));
}

function factLine(f) {
  return el("li", {},
    el("span", {}, f.claim, " "), tagChip(f.tag),
    f.source_url
      ? el("a", { class: "src", href: f.source_url, target: "_blank", rel: "noopener noreferrer" }, f.source_name || "source")
      : f.source_name ? el("span", { class: "src muted" }, f.source_name) : null,
    f.tag === "estimated" && f.reasoning ? el("div", { class: "muted small" }, f.reasoning) : null);
}

// ---------- the page ----------

async function showReport() {
  renderProgress(6);
  let report;
  try {
    report = await api("GET", `/api/sessions/${state.session.id}/report`);
  } catch (err) {
    main.replaceChildren(el("p", { class: "error" }, "Could not load the report. Is the app running?"));
    return;
  }
  if (state.view !== "report") return;
  if (report.status === "none") {
    report = await api("POST", `/api/sessions/${state.session.id}/report`);
  }
  if (report.status === "preparing") return showPreparing(report);
  if (report.status === "failed") return showReportFailed(report);
  renderReport(report);
}

function showPreparing(report) {
  const current = STAGE_ORDER.indexOf(report.stage);
  main.replaceChildren(el("div", { class: "screen preparing", role: "status" },
    el("h1", {}, "Preparing your report"),
    el("p", { class: "lead" }, "This usually takes a minute or two."),
    el("ol", { class: "stages" },
      ...STAGE_ORDER.map((stage, i) => el("li", { class: i < current ? "done" : i === current ? "now" : "" },
        i < current ? el("span", { class: "mark" }, "✓")
          : i === current ? el("span", { class: "spinner small" }) : el("span", { class: "mark" }, ""),
        el("span", {}, STAGE_LABELS[stage]))))));
  clearInterval(reportTimer);
  reportTimer = setInterval(async () => {
    try {
      const next = await api("GET", `/api/sessions/${state.session.id}/report`);
      if (state.view !== "report") return clearInterval(reportTimer);
      if (next.status !== "preparing" || next.stage !== report.stage) {
        clearInterval(reportTimer);
        if (next.status === "preparing") showPreparing(next);
        else if (next.status === "failed") showReportFailed(next);
        else renderReport(next);
      }
    } catch (err) { /* try again on the next tick */ }
  }, 2000);
}

function showReportFailed(report) {
  main.replaceChildren(el("div", { class: "screen" },
    el("h1", {}, "The report could not be made"),
    el("p", { class: "lead" }, report.error || "Something went wrong."),
    el("div", { class: "actions" },
      el("button", { class: "btn plain", type: "button", onclick: () => { state.view = "done"; render(); } }, "See your answers"),
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn primary", type: "button",
        onclick: async () => { await api("POST", `/api/sessions/${state.session.id}/report`); render(); },
      }, "Try again"))));
}

function renderReport(report) {
  const s = report.scores;
  const t = report.text || {};
  const m = s.money;
  const cur = m.currency;
  const facts = Object.fromEntries((report.facts || []).map((f) => [f.id, f]));
  const verdict = VERDICTS[s.verdict];
  const page = el("div", { class: "screen report" });

  if (report.stale) {
    page.append(el("div", { class: "note stale" },
      el("span", {}, "Your answers or the research changed since this report was made. "),
      el("button", {
        class: "link", type: "button",
        onclick: async () => { await api("POST", `/api/sessions/${state.session.id}/report`); render(); },
      }, "Update report")));
  }

  // 1. Verdict
  page.append(el("div", { class: "verdict " + verdict.cls },
    el("div", { class: "verdict-label" }, "Verdict"),
    el("div", { class: "verdict-row" },
      el("div", { class: "verdict-name" }, verdict.name),
      el("span", { class: "pill confidence " + s.confidence },
        `${s.confidence[0].toUpperCase()}${s.confidence.slice(1)} confidence`)),
    para(t.verdict_reasons || s.rule_reasons.join(" "), "verdict-why"),
    s.capped ? el("p", { class: "muted small" }, "Capped at Test small first because most of the data is estimated or assumed.") : null));

  // 2. Scorecard: each row opens its evidence
  const lines = Object.fromEntries((t.area_lines || []).map((a) => [a.area, a.line]));
  const card = el("div", { class: "scorecard" });
  for (const a of s.areas) {
    const cited = new Set();
    a.signals.forEach((sig) => sig.fact_ids.forEach((id) => cited.add(id)));
    card.append(el("details", { class: "area" },
      el("summary", {},
        el("span", { class: "area-name" }, a.name),
        el("span", { class: "rating " + RATING_CLASS[a.rating] }, a.rating),
        el("span", { class: "area-line" }, rich(lines[a.area] || a.rule))),
      el("div", { class: "evidence" },
        el("div", { class: "muted small" }, a.question),
        el("p", { class: "small" }, el("strong", {}, "Rule applied: "), a.rule, " Evidence is mostly ", tagChip(a.basis), "."),
        el("ul", { class: "signals" }, ...a.signals.map((sig) => el("li", {},
          el("span", { class: "sig " + sig.value }, sig.value === "yes" ? "✓" : sig.value === "no" ? "✗" : "?"),
          el("span", {}, sig.question, " ", el("span", { class: "muted" }, sig.note))))),
        cited.size
          ? el("ul", { class: "facts" }, ...[...cited].filter((id) => facts[id]).map((id) => factLine(facts[id])))
          : el("p", { class: "muted small" }, "Based on your answers only."))));
  }
  page.append(card);

  // 3. The idea in one sentence
  const idea = section("Your idea in one sentence", para(t.idea_sentence));
  if (t.adjusted_idea) {
    idea.append(el("div", { class: "compare" },
      el("div", {}, el("div", { class: "muted small" }, "Your version"), para(t.idea_sentence)),
      el("div", {}, el("div", { class: "muted small" }, "Nearest version that could work"), para(t.adjusted_idea))),
      para(t.adjustment_reason, "muted small"));
  }
  if (t.idea_sentence) page.append(idea);

  // 4. Simplified business plan
  const plan = section("Simplified business plan");
  if (t.customer) plan.append(el("h3", {}, "Customer"), para(t.customer));
  plan.append(el("h3", {}, "Market"));
  if (t.market) plan.append(para(t.market));
  const comps = (report.competitors || []).filter((c) => c.price != null).slice(0, 5);
  if (comps.length) {
    plan.append(el("table", { class: "table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Competitor"), el("th", {}, "Price"), el("th", {}, "Source"))),
      el("tbody", {}, ...comps.map((c) => el("tr", {},
        el("td", {}, competitorLabel(c)),
        el("td", {}, money(c.price, c.currency), packText(c) ? ` · ${packText(c)}` : "", " ", tagChip(c.tag)),
        el("td", {}, c.source_url
          ? el("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer" }, c.source_name || "source")
          : "—"))))));
  }
  if (t.edge) {
    plan.append(el("h3", {}, "Your edge"), para(t.edge));
    if (t.not_a_difference) plan.append(el("p", {}, el("strong", {}, "Does not count as a difference: "), rich(t.not_a_difference)));
  }
  if ((t.channels || []).length) plan.append(el("h3", {}, "How you sell"), el("ol", {}, ...t.channels.map((c) => el("li", {}, c))));

  plan.append(el("h3", {}, "Money"));
  const rows = [
    ["Price per sale", figure(m.price, cur)],
    ["Cost to deliver one sale", figure(m.cost_per_sale, cur)],
    ["You keep per sale", figure(m.keep_per_sale, cur)],
    ["Cost to win a customer", figure(m.customer_cost, cur)],
    ["Purchases per customer", figure(m.lifetime_purchases)],
    ["Kept per customer over their lifetime", figure(m.lifetime_keep, cur)],
    ["Sales needed to cover start-up costs", figure(m.sales_to_cover_startup)],
  ];
  plan.append(el("table", { class: "table money" },
    el("tbody", {}, ...rows.map(([label, value]) => el("tr", {}, el("td", {}, label), el("td", {}, value))))));
  const chart = moneyChart(m);
  if (chart) plan.append(chart);
  if (t.money_summary) plan.append(para(t.money_summary));

  plan.append(el("h3", {}, "Start-up budget"));
  if ((m.startup_items || []).length) {
    plan.append(el("table", { class: "table" }, el("tbody", {},
      ...m.startup_items.map((it) => el("tr", {}, el("td", {}, it.item),
        el("td", {}, money(it.amount, cur), " ", tagChip(it.tag)))),
      el("tr", { class: "total" }, el("td", {}, "First stage total"), el("td", {}, figure(m.startup_total, cur))),
      el("tr", {}, el("td", {}, "Your budget"), el("td", {}, figure(m.budget, cur))))));
    const within = s.areas.find((a) => a.area === "fit").within_budget;
    if (within != null) plan.append(el("p", { class: within ? "good-text" : "bad-text" },
      within ? "The first stage fits inside your budget." : "The first stage costs more than your budget."));
  } else {
    plan.append(el("p", { class: "muted" }, "Not enough information to size the first stage."));
  }
  page.append(plan);

  // 5. Alerts, every one, including any already fixed
  const alerts = report.alerts || [];
  if (alerts.length) {
    const list = el("div", { class: "alert-list" });
    for (const a of alerts) {
      const cardEl = alertCard(a);
      if (a.status === "resolved") {
        cardEl.classList.add("resolved");
        cardEl.querySelector(".alert-head").append(el("span", { class: "pill fixed" }, "Fixed in your answers"));
      }
      list.append(cardEl);
    }
    page.append(section("Alerts", list));
  }

  // 6. What to do next
  if (t.cheapest_test) {
    const next = section("What to do next",
      el("div", { class: "test-card" },
        el("div", { class: "muted small" }, "Cheapest test"),
        para(t.cheapest_test.what),
        el("div", { class: "test-meta" },
          el("span", {}, el("strong", {}, "Budget: "), t.cheapest_test.budget),
          el("span", {}, el("strong", {}, "Time limit: "), t.cheapest_test.time_limit))),
      el("div", { class: "gates" },
        el("div", {}, el("h3", {}, "Green gates: keep going if"),
          el("ul", {}, ...(t.green_gates || []).map((g) => el("li", {}, rich(g.measure), ": ", el("strong", {}, g.target))))),
        el("div", {}, el("h3", {}, "Kill switch: stop if"),
          el("ul", {}, ...(t.kill_switch || []).map((g) => el("li", {}, rich(g.measure), ": ", el("strong", {}, g.target)))))),
      el("h3", {}, "First 30 days"),
      el("ol", { class: "checklist" }, ...(t.first_30_days || []).map((step) => el("li", {}, rich(step)))));
    page.append(next);
  }

  // 7. How sure we are
  const sure = section("How sure we are",
    el("p", {}, `${Math.round(s.verified_share * 100)}% of the evidence behind the scores is verified, so confidence is `,
      el("strong", {}, s.confidence), ". Tags: ",
      tagChip("verified"), " read on a source page, ", tagChip("estimated"), " worked out with reasoning shown, ",
      tagChip("assumed"), " your own figure or no evidence."));
  if ((t.what_would_change || []).length) {
    sure.append(el("h3", {}, "What would change the verdict"), el("ul", {}, ...t.what_would_change.map((w) => el("li", {}, rich(w)))));
  }
  for (const note of report.notes || []) sure.append(el("p", { class: "muted small" }, note));
  if ((report.facts || []).length) {
    sure.append(el("h3", {}, "Sources"), el("ul", { class: "facts" }, ...report.facts.map(factLine)));
  }
  page.append(sure);

  if (report.writer_failed) {
    page.append(el("p", { class: "note" }, "The written explanation could not be produced this time; the scores and figures above are complete. ",
      el("button", { class: "link", type: "button", onclick: async () => { await api("POST", `/api/sessions/${state.session.id}/report`); render(); } }, "Try again")));
  }

  page.append(
    el("p", { class: "disclaimer" }, "This report is guidance, not financial or legal advice."),
    el("div", { class: "actions report-actions" },
      el("button", { class: "btn plain", type: "button", onclick: () => { state.view = "done"; render(); } }, "Change an answer and re-run"),
      el("span", { class: "spacer" }),
      el("button", { class: "btn plain", type: "button", onclick: () => downloadPdf(report) }, "Download PDF"),
      el("button", { class: "btn primary", type: "button", onclick: newIdea }, "Check another idea")),
    el("div", { class: "file" }, `Saved to reports/${state.session.id}.json and .html`),
    usageLine(report.usage));

  main.replaceChildren(page);
  window.scrollTo(0, 0);
  if (!report.stale) saveReportFile(page, report);
}

function usageLine(u) {
  if (!u || !u.calls) return null;
  const cost = u.cost_usd != null ? ` · about $${u.cost_usd.toFixed(2)} at API prices` : "";
  return el("div", { class: "file" }, `${u.calls} AI calls · ${u.searches || 0} web searches${cost}`);
}

function reportTitle(report) {
  const idea = (report.text && report.text.idea_sentence) || "Business idea";
  const short = idea.length > 60 ? idea.slice(0, 57).replace(/\s+\S*$/, "") + "…" : idea;
  return `Idea report – ${short} – ${new Date(report.finished_at || Date.now()).toLocaleDateString()}`;
}

// PDF: the browser's own "Save as PDF", using the print layout in style.css.
function downloadPdf(report) {
  const before = document.title;
  document.title = reportTitle(report);          // becomes the suggested file name
  const restore = () => { document.title = before; window.removeEventListener("afterprint", restore); };
  window.addEventListener("afterprint", restore);
  window.print();
}

// The rendered report is also saved as a standalone HTML file in reports/,
// so it opens in any browser without the app running.
let styleText = null;

async function saveReportFile(page, report) {
  try {
    if (styleText === null) styleText = await (await fetch("/static/style.css")).text();
    const copy = page.cloneNode(true);
    copy.querySelectorAll(".report-actions, .note.stale, .alert-actions, button.link, .file")
      .forEach((n) => n.remove());
    const stamp = new Date(report.finished_at || Date.now()).toLocaleString();
    const html = '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">' +
      `<title>${escapeHtml(reportTitle(report))}</title><style>${styleText}</style></head>` +
      `<body class="standalone"><main class="main"><div class="brand">Idea Checker · ${escapeHtml(stamp)}</div>` +
      copy.outerHTML + "</main></body></html>";
    await api("POST", `/api/sessions/${state.session.id}/report/file`, { html });
  } catch (err) { /* the JSON is the record; the HTML copy is a convenience */ }
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// One chart at most: money in against money out for a first sale, only when the
// numbers are solid (all three known, at most one of them the person's own guess).
function moneyChart(m) {
  const parts = [m.price, m.cost_per_sale, m.customer_cost];
  if (parts.some((n) => !n || n.value == null)) return null;
  if (parts.filter((n) => n.tag === "assumed").length > 1) return null;
  const inn = m.price.value;
  const out = m.cost_per_sale.value + m.customer_cost.value;
  const max = Math.max(inn, out) || 1;
  const bar = (label, value, cls) => el("div", { class: "bar-row" },
    el("span", { class: "bar-label" }, label),
    el("span", { class: "bar " + cls, style: `width:${Math.max(4, (value / max) * 100)}%` }),
    el("span", { class: "bar-value" }, money(value, m.currency)));
  return el("div", { class: "chart", role: "img", "aria-label": `First sale: ${money(inn, m.currency)} in, ${money(out, m.currency)} out` },
    el("div", { class: "muted small" }, "First sale: money in against money out"),
    bar("In", inn, "in"),
    bar("Out", out, "out"),
    el("div", { class: "muted small" }, "Out = cost to deliver one sale + cost to win the customer."));
}
