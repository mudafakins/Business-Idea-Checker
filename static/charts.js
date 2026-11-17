// Business Idea Checker - charts for the report.
// Plain HTML bars, no library. Each chart:
//   - shows only numbers the report already lists in its tables (the table view),
//   - says in its caption how sure those numbers are (verified / estimated / assumed),
//   - has a hover and keyboard-focus tooltip on every mark,
//   - keeps text in text colours; only the marks carry colour.
// Colours come from CSS variables (--chart-*) with light, dark and print values.

const chartTip = el("div", { class: "chart-tip", role: "tooltip" });
document.body.append(chartTip);

function tipOn(target, lines) {
  const show = () => {
    chartTip.replaceChildren(...lines.map(([value, label]) =>
      el("div", { class: "chart-tip-row" }, el("strong", {}, value), label ? el("span", {}, " " + label) : null)));
    const r = target.getBoundingClientRect();
    chartTip.style.display = "block";
    const w = chartTip.offsetWidth;
    chartTip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
    chartTip.style.top = (window.scrollY + r.top - chartTip.offsetHeight - 8) + "px";
    target.classList.add("hot");
  };
  const hide = () => { chartTip.style.display = "none"; target.classList.remove("hot"); };
  target.tabIndex = 0;
  target.addEventListener("pointerenter", show);
  target.addEventListener("pointerleave", hide);
  target.addEventListener("focus", show);
  target.addEventListener("blur", hide);
  return target;
}

function chartBox(title, caption, ...body) {
  return el("figure", { class: "chart2" },
    el("figcaption", {}, el("div", { class: "chart-title" }, title)),
    ...body,
    caption ? el("div", { class: "chart-caption" }, caption) : null);
}

function tagWords(tags) {
  const order = { verified: 0, estimated: 1, assumed: 2 };
  const uniq = [...new Set(tags.filter(Boolean))].sort((a, b) => order[a] - order[b]);
  return uniq.length ? uniq.join(", ") : "assumed";
}

// ---------- 1. Competitor prices, with yours marked ----------

function priceChart(report, m) {
  const all = (report.competitors || []).filter((c) => c.price != null);
  if (all.length < 2) return null;
  // Compare per litre / kg / month when most share a unit; otherwise per pack.
  const units = all.map((c) => c.unit).filter(Boolean);
  const unit = units.length >= 2 ? units.sort((a, b) => units.filter((u) => u === b).length - units.filter((u) => u === a).length)[0] : null;
  const rows = (unit ? all.filter((c) => c.unit === unit && c.unit_price != null) : all)
    .map((c) => ({ c, value: unit ? c.unit_price : c.price }))
    .sort((a, b) => a.value - b.value);
  if (rows.length < 2) return null;
  const cur = rows[0].c.currency;
  const unitWord = unit === "l" ? "litre" : unit;
  const yours = m.price && m.price.value != null && !unit ? m.price.value : yourUnitPrice(report, unit);
  const max = Math.max(...rows.map((r) => r.value), yours || 0) * 1.2;

  const plot = el("div", { class: "hbars" });
  for (const { c, value } of rows) {
    const bar = el("div", { class: "hbar-fill series-1" + (c.tag === "estimated" ? " estimated" : ""), style: `width:${(value / max) * 100}%` });
    tipOn(bar, [[money(value, cur) + (unit ? ` per ${unitWord}` : ""), competitorLabel(c)],
      [money(c.price, c.currency) + (packText(c) ? ` for ${packText(c)}` : ""), c.tag || ""]]);
    plot.append(el("div", { class: "hbar-row" },
      el("div", { class: "hbar-label", title: competitorLabel(c) }, c.brand),
      el("div", { class: "hbar-track" }, bar,
        el("span", { class: "hbar-value" }, money(value, cur), c.tag === "estimated" ? " est." : ""))));
  }
  if (yours) {
    plot.append(el("div", { class: "ref-line", style: `left:calc(var(--label-w) + (100% - var(--label-w)) * ${yours / max})` },
      el("span", { class: "ref-label" }, `Your price ${money(yours, cur)}`)));
  }
  const tags = rows.map((r) => r.c.tag);
  return chartBox(
    unit ? `Competitor prices per ${unitWord}` : "Competitor prices per pack",
    `Lowest to highest. Prices are ${tagWords(tags)}${yours ? "; your price is your own figure (assumed)" : ""}. Sources are in the table below.`,
    plot);
}

// Your price per unit, when the answer gives a pack size we can read (e.g. "449 for 1 litre").
function yourUnitPrice(report, unit) {
  const m = report.scores.money;
  if (!m.price || m.price.value == null || !unit) return null;
  const answer = ((state.session && state.session.answers.q17) || {}).value || "";
  const size = String(answer).toLowerCase().match(/(\d+(?:\.\d+)?)\s*(ml|l|litre|liter|kg|g)\b/);
  if (!size) return null;
  let qty = parseFloat(size[1]);
  const u = size[2];
  if (u === "ml" || u === "g") qty /= 1000;
  const base = u === "kg" || u === "g" ? "kg" : "l";
  return base === unit && qty > 0 ? m.price.value / qty : null;
}

// ---------- 2. One sale: money in against money out ----------

function saleChart(m) {
  const price = m.price && m.price.value, cost = m.cost_per_sale && m.cost_per_sale.value;
  if (price == null || cost == null) return null;
  const cac = m.customer_cost && m.customer_cost.value;
  const cur = m.currency;
  const left = price - cost - (cac || 0);
  const max = Math.max(price, cost + (cac || 0));
  const steps = [
    { label: "Price", value: price, start: 0, cls: "series-1", tag: m.price.tag },
    { label: "Cost to deliver", value: -cost, start: price - cost, cls: "series-2", tag: m.cost_per_sale.tag },
  ];
  if (cac != null) steps.push({ label: "Cost to win the customer", value: -cac, start: price - cost - cac, cls: "series-2", tag: m.customer_cost.tag });
  steps.push({ label: left >= 0 ? "Left from the first sale" : "Lost on the first sale", value: left, total: true,
    cls: left >= 0 ? "good" : "critical", tag: null });

  // A shared scale from the lowest point to the highest.
  const lo = Math.min(0, left, ...steps.filter((s) => !s.total).map((s) => s.start)), hi = max;
  const pct = (v) => ((v - lo) / (hi - lo)) * 80;
  const plot = el("div", { class: "hbars waterfall" });
  for (const s of steps) {
    const a = s.total ? Math.min(0, s.value) : Math.min(s.start, s.start + Math.abs(s.value));
    const b = s.total ? Math.max(0, s.value) : Math.max(s.start, s.start + Math.abs(s.value));
    const bar = el("div", { class: "hbar-fill " + s.cls, style: `left:${pct(a)}%;width:${Math.max(0.6, pct(b) - pct(a))}%` });
    const sign = s.total ? (s.value >= 0 ? "+" : "−") : (s.value >= 0 ? "" : "−");
    tipOn(bar, [[sign + money(Math.abs(s.value), cur), s.label], s.tag ? [s.tag, ""] : ["worked out", ""]]);
    plot.append(el("div", { class: "hbar-row" },
      el("div", { class: "hbar-label" }, s.label),
      el("div", { class: "hbar-track floating" }, bar,
        el("span", { class: "hbar-value", style: `left:calc(${pct(b)}% + 6px)` }, sign + money(Math.abs(s.value), cur)))));
  }
  const parts = [m.price.tag, m.cost_per_sale.tag].concat(cac != null ? [m.customer_cost.tag] : []);
  return chartBox("One sale: money in and money out",
    `Price and costs are ${tagWords(parts)}.` + (cac == null ? " The cost to win a customer is unknown, so it is left out." : ""),
    plot);
}

// ---------- 3. A customer's worth against the cost of winning them ----------

function lifetimeChart(m) {
  const worth = m.lifetime_keep && m.lifetime_keep.value, cac = m.customer_cost && m.customer_cost.value;
  if (worth == null || cac == null || worth <= 0) return null;
  const cur = m.currency;
  const max = Math.max(worth, cac) * 1.2;
  const plot = el("div", { class: "hbars" });
  for (const [label, value, cls, tag] of [
    ["Kept over a customer's lifetime", worth, "series-1", m.lifetime_keep.tag],
    ["Cost to win that customer", cac, "series-2", m.customer_cost.tag]]) {
    const bar = el("div", { class: "hbar-fill " + cls, style: `width:${(value / max) * 100}%` });
    tipOn(bar, [[money(value, cur), label], [tag || "", ""]]);
    plot.append(el("div", { class: "hbar-row" }, el("div", { class: "hbar-label" }, label),
      el("div", { class: "hbar-track" }, bar, el("span", { class: "hbar-value" }, money(value, cur)))));
  }
  const ratio = worth / cac;
  return chartBox(`A customer is worth ${ratio.toFixed(1)} times what it costs to win them`,
    `A common rule of thumb looks for 3 times or more. Lifetime is ${m.lifetime_keep.tag}; the cost to win a customer is ${m.customer_cost.tag}.`,
    plot);
}

// ---------- 4. Start-up costs inside the budget ----------

// Four blue steps, light to dark, validated as an ordinal ramp in both modes.
// More than four items: the smallest fold into "Other items" rather than inventing a fifth step.
const BUDGET_STEPS = ["seq-1", "seq-2", "seq-3", "seq-4"];

function budgetChart(m) {
  let items = (m.startup_items || []).filter((i) => i.amount > 0).sort((a, b) => b.amount - a.amount);
  if (items.length > BUDGET_STEPS.length) {
    const rest = items.slice(BUDGET_STEPS.length - 1);
    const order = { assumed: 0, estimated: 1, verified: 2 };
    items = items.slice(0, BUDGET_STEPS.length - 1).concat([{
      item: `Other items (${rest.length})`, amount: rest.reduce((n, i) => n + i.amount, 0),
      tag: rest.map((i) => i.tag).sort((a, b) => order[a] - order[b])[0],
    }]);
  }
  const total = m.startup_total && m.startup_total.value;
  const budget = m.budget && m.budget.value;
  if (!items.length || !total) return null;
  const cur = m.currency;
  const whole = Math.max(total, budget || 0);
  const bar = el("div", { class: "seg-bar" });
  const legend = el("ul", { class: "chart-legend" });
  items.forEach((it, i) => {
    const cls = BUDGET_STEPS[Math.min(i, BUDGET_STEPS.length - 1)];
    const seg = el("div", { class: "seg " + cls, style: `width:${(it.amount / whole) * 100}%` });
    tipOn(seg, [[money(it.amount, cur), it.item], [it.tag, ""]]);
    bar.append(seg);
    legend.append(el("li", {}, el("span", { class: "key " + cls }), `${it.item} · ${money(it.amount, cur)}`));
  });
  if (budget && budget > total) {
    const rest = el("div", { class: "seg rest", style: `width:${((budget - total) / whole) * 100}%` });
    tipOn(rest, [[money(budget - total, cur), "left in your budget"]]);
    bar.append(rest);
    legend.append(el("li", {}, el("span", { class: "key rest" }), `Left in your budget · ${money(budget - total, cur)}`));
  }
  const over = budget && total > budget;
  const title = budget
    ? (over ? `The first stage needs ${money(total - budget, cur)} more than your budget` : `The first stage uses ${Math.round((total / budget) * 100)}% of your budget`)
    : "What the first stage costs";
  const marker = budget && over
    ? el("div", { class: "budget-mark", style: `left:${(budget / whole) * 100}%` }, el("span", {}, `Your budget ${money(budget, cur)}`))
    : null;
  return chartBox(title, `Costs are ${tagWords(items.map((i) => i.tag))}; your budget is your own figure.`,
    el("div", { class: "seg-wrap" }, bar, marker), legend);
}

// ---------- 5. How sure the evidence is ----------

function evidenceChart(report) {
  const facts = report.facts || [];
  const s = report.scores;
  const used = new Set();
  s.areas.forEach((a) => a.signals.forEach((sig) => sig.fact_ids.forEach((id) => used.add(id))));
  const tags = facts.filter((f) => used.has(f.id)).map((f) => f.tag);
  ["price", "cost_per_sale", "customer_cost", "purchases_per_year", "lifetime_months"]
    .forEach((k) => { if (s.money[k] && s.money[k].tag) tags.push(s.money[k].tag); });
  if (!tags.length) return null;
  const counts = { verified: 0, estimated: 0, assumed: 0 };
  tags.forEach((t) => { counts[t] = (counts[t] || 0) + 1; });
  const total = tags.length;
  const bar = el("div", { class: "seg-bar" });
  const legend = el("ul", { class: "chart-legend" });
  for (const [tag, cls, words] of [["verified", "good", "read on a source page"],
    ["estimated", "warning", "worked out, reasoning shown"], ["assumed", "neutral", "your figure or no evidence"]]) {
    if (!counts[tag]) continue;
    const seg = el("div", { class: "seg " + cls, style: `width:${(counts[tag] / total) * 100}%` });
    tipOn(seg, [[`${counts[tag]} of ${total}`, tag], [words, ""]]);
    bar.append(seg);
    legend.append(el("li", {}, el("span", { class: "key " + cls }), `${tag[0].toUpperCase()}${tag.slice(1)} · ${counts[tag]}`));
  }
  return chartBox(`${Math.round((counts.verified / total) * 100)}% of the evidence is verified`,
    "Every finding and figure behind the six scores, by how sure it is.", bar, legend);
}
