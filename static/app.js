// Business Idea Checker - the form, one question per screen.
// The server's Conductor may reword later questions, skip ones already
// covered, and ask one follow-up after a vague answer.

const STORAGE_KEY = "idea-checker-session";
const IDEA_QUESTION = "q8";

const state = {
  stages: [],
  questions: [],
  ai: false,
  session: null,      // created on the first saved answer
  index: 0,
  view: "question",   // "question" | "followup" | "done"
  followUp: null,     // { id, text } while a follow-up is on screen
  returnToDone: false,
  returnToIndex: null, // where to go back to after changing an answer from an alert
  alerts: [],
  checking: false,
};

const main = document.getElementById("main");
const progress = document.getElementById("progress");
const stageName = document.getElementById("stage-name");

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) {
    if (child != null) node.append(child);
  }
  return node;
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${url} failed (${res.status})`);
  return res.json();
}

function answers() {
  return state.session ? state.session.answers : {};
}

// The question as it should be shown: the fixed one, plus any rewording.
function view(q) {
  const override = state.session && state.session.questions && state.session.questions[q.id];
  if (!override) return q;
  return {
    ...q,
    text: override.text || q.text,
    hint: override.hint || q.hint,
    options: override.options || q.options,
  };
}

// A question is skipped when an earlier answer already covered it.
function isSkipped(q) {
  const a = answers()[q.id];
  return !!(a && a.inferred);
}

const SAVE_ERROR = "Could not save. Check that the app is still running, then try again.";

// ---------- progress ----------

function renderProgress(stageId) {
  const shown = state.stages.filter((s) => s.id > 0);
  progress.replaceChildren(
    ...shown.map((s) =>
      el("span", { class: s.id < stageId ? "done" : s.id === stageId ? "now" : "" })
    )
  );
  const stage = state.stages.find((s) => s.id === stageId);
  stageName.textContent = stageId > 0 && stage ? stage.name : "";
}

function showWorking(message) {
  main.replaceChildren(el("div", { class: "screen working", role: "status" },
    el("div", { class: "spinner", "aria-hidden": "true" }),
    el("p", {}, message)));
}

// ---------- research (competitors and prices) ----------

let pollTimer = null;

function research() {
  return (state.session && state.session.research) || { status: "none" };
}

function money(amount, currency) {
  if (amount == null) return "";
  try {
    // Rupees read naturally in lakh grouping (₹10,00,000).
    return new Intl.NumberFormat(currency === "INR" ? "en-IN" : undefined, {
      style: "currency", currency: currency || "USD",
      // Whole numbers for anything over 100; pennies only matter on small prices.
      maximumFractionDigits: amount % 1 === 0 || amount >= 100 ? 0 : 2,
    }).format(amount);
  } catch (err) {
    return `${currency || ""} ${amount}`.trim();
  }
}

const PACK_LABELS = { per_month: "per month", per_year: "per year", per_session: "per session", per_hour: "per hour", one_off: "one-off" };

function packText(c) {
  if (c.pack_size && ["ml", "l", "g", "kg"].includes(c.pack_unit)) return `${c.pack_size} ${c.pack_unit === "l" ? "L" : c.pack_unit}`;
  if (c.pack_size && c.pack_unit === "pieces") return `${c.pack_size} pieces`;
  return PACK_LABELS[c.pack_unit] || "";
}

function competitorLabel(c) {
  return c.product.toLowerCase().startsWith(c.brand.toLowerCase()) ? c.product : `${c.brand} ${c.product}`;
}

function competitorDetail(c) {
  const bits = [];
  if (c.price != null) {
    bits.push(money(c.price, c.currency));
    const pack = packText(c);
    if (pack) bits.push(pack);
    if (c.tag === "estimated") bits.push("estimated");
  } else {
    bits.push("price not found");
  }
  if (c.kind === "substitute") bits.push("what people use instead");
  return bits.join(" · ");
}

function sourceList(competitors) {
  const withSource = competitors.filter((c) => c.source_url);
  if (!withSource.length) return null;
  return el("div", { class: "sources" },
    el("div", { class: "sources-title" }, "Sources"),
    ...withSource.map((c) => el("div", {},
      el("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer" },
        `${competitorLabel(c)} — ${c.source_name || "source"}`))));
}

function rangeText(r) {
  let text = `Competitors charge ${money(r.pack_low, r.currency)} to ${money(r.pack_high, r.currency)} per pack`;
  if (r.unit) text += ` (${money(r.unit_low, r.currency)} to ${money(r.unit_high, r.currency)} per ${r.unit === "l" ? "litre" : r.unit})`;
  text += `, from ${r.count} product${r.count === 1 ? "" : "s"}.`;
  if (r.tag !== "verified") text += " Some prices are estimates.";
  return text;
}

// The research panel shown on questions 15 and 17. onRetry re-renders the screen.
function researchBox(kind) {
  const r = research();
  if (r.status === "none") return null;
  if (r.status === "running") {
    return el("div", { class: "note research", role: "status" },
      el("span", { class: "spinner small", "aria-hidden": "true" }),
      el("span", {}, "Looking up real competitors and prices. This usually takes a minute or two; you can answer now or wait."));
  }
  if (r.status === "failed") {
    return el("div", { class: "note research" },
      el("span", {}, "Could not look up competitors this time. "),
      el("button", {
        class: "link", type: "button",
        onclick: async () => {
          state.session.research = await api("POST", `/api/sessions/${state.session.id}/research`);
          render();
        },
      }, "Try again"));
  }
  const competitors = r.competitors || [];
  const box = el("div", { class: "note research done" });
  if (kind === "prices") {
    box.append(el("div", {}, r.price_range ? rangeText(r.price_range) : "No competitor prices with a source were found."));
  } else if (!competitors.length) {
    box.append(el("div", {}, "No competitors were found. Name the closest one you know."));
  }
  if (r.market_note) box.append(el("div", { class: "market-note" }, r.market_note));
  // On the price question, list only the prices the range was worked out from.
  const direct = competitors.some((c) => c.price != null && c.kind === "direct");
  const sources = sourceList(competitors.filter((c) =>
    kind !== "prices" || (c.price != null && (!direct || c.kind === "direct"))));
  if (kind === "prices" && sources) box.append(sources);
  return box.children.length ? box : null;
}

// Question 15 becomes a pick-one list of the competitors research found.
function withResearch(q) {
  const r = research();
  if (q.research !== "competitors" || r.status !== "done" || !(r.competitors || []).length) return q;
  return {
    ...q,
    type: "choice",
    options: [
      ...r.competitors.map((c) => ({ label: competitorLabel(c), detail: competitorDetail(c) })),
      { label: "None of these is close" },
    ],
  };
}

function watchResearch(onDone) {
  clearInterval(pollTimer);
  if (research().status !== "running") return;
  pollTimer = setInterval(async () => {
    try {
      const r = await api("GET", `/api/sessions/${state.session.id}/research`);
      if (r.status === "running") return;
      clearInterval(pollTimer);
      state.session.research = r;
      onDone();
    } catch (err) { /* try again on the next tick */ }
  }, 3000);
}

// ---------- question screen ----------

function renderQuestion() {
  const base = state.questions[state.index];
  const q = withResearch(view(base));
  const saved = isSkipped(base) ? null : answers()[q.id];
  renderProgress(q.stage);

  // Draft of the answer being edited on this screen.
  let text = saved && typeof saved.value === "string" ? saved.value : "";
  let picked = new Set(saved && Array.isArray(saved.value) ? saved.value : []);
  let other = (saved && saved.other) || "";

  const error = el("div", { class: "error", role: "alert" });
  const next = el("button", { class: "btn primary", type: "button", onclick: () => submit(false) },
    state.returnToDone ? "Save" : "Continue");

  const hasAnswer = () =>
    q.type === "multi" ? picked.size > 0 || other.trim() !== "" : text.trim() !== "";
  const refresh = () => { next.disabled = !hasAnswer(); };

  async function submit(dontKnow) {
    if (next.dataset.busy) return;
    if (!dontKnow && !hasAnswer()) return;
    const answer = dontKnow
      ? { value: null, other: null, dont_know: true }
      : q.type === "multi"
        ? { value: [...picked], other: other.trim() || null, dont_know: false }
        : { value: text.trim(), other: null, dont_know: false };
    next.dataset.busy = "1";
    next.disabled = true;
    error.textContent = "";
    // Reading the idea takes a few seconds, so say what is happening.
    const slow = state.ai && !dontKnow && q.id === IDEA_QUESTION;
    try {
      if (!state.session) {
        state.session = await api("POST", "/api/sessions");
        localStorage.setItem(STORAGE_KEY, state.session.id);
      }
      if (slow) showWorking("Reading your idea…");
      const result = await api("PUT", `/api/sessions/${state.session.id}/answers/${q.id}`, answer);
      state.session = result.session;
      startAlertWatch();
      await advance();
    } catch (err) {
      if (!main.querySelector(".error")) render();
      const shown = main.querySelector(".error");
      if (shown) shown.textContent = SAVE_ERROR;
      delete next.dataset.busy;
      refresh();
    }
  }

  const screen = el("div", { class: "screen" }, el("h1", {}, q.text));
  if (q.hint) screen.append(el("p", { class: "hint" }, q.hint));
  if (q.note) screen.append(el("div", { class: "note" }, q.note));
  let box = q.research ? researchBox(q.research) : null;
  if (box) screen.append(box);

  let focusTarget = null;

  if (q.type === "text" || q.type === "long") {
    const input = el(q.type === "long" ? "textarea" : "input", {
      class: "field",
      placeholder: q.placeholder || "",
      "aria-label": q.text,
      oninput: (e) => { text = e.target.value; refresh(); },
      onkeydown: (e) => {
        const enter = e.key === "Enter";
        if (enter && (q.type === "text" || e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          submit(false);
        }
      },
    });
    input.value = text;
    screen.append(input);
    focusTarget = input;
  } else {
    const list = el("div", { class: "list" });
    for (const option of q.options) {
      const isOn = () => (q.type === "multi" ? picked.has(option.label) : text === option.label);
      const row = el("button", {
        class: "row" + (isOn() ? " selected" : ""),
        type: "button",
        disabled: !!option.soon,
        "aria-pressed": String(isOn()),
        onclick: () => {
          if (q.type === "multi") {
            picked.has(option.label) ? picked.delete(option.label) : picked.add(option.label);
          } else {
            text = option.label;
          }
          for (const r of list.children) {
            const on = q.type === "multi" ? picked.has(r.dataset.label) : text === r.dataset.label;
            r.classList.toggle("selected", on);
            r.setAttribute("aria-pressed", String(on));
          }
          refresh();
        },
      },
        el("span", { class: "qa" },
          el("div", {}, option.label),
          option.detail ? el("div", { class: "q" }, option.detail) : null),
        option.soon
          ? el("span", { class: "badge" }, "Coming later")
          : el("span", { class: "check", "aria-hidden": "true" }, "✓"));
      row.dataset.label = option.label;
      list.append(row);
    }
    screen.append(list);
    if (q.research === "competitors") {
      const sources = sourceList(research().competitors || []);
      if (sources) screen.append(sources);
    }
    if (q.type === "multi") {
      const input = el("input", {
        class: "field",
        placeholder: q.other_placeholder || "Anything else?",
        "aria-label": q.other_placeholder || "Anything else?",
        oninput: (e) => { other = e.target.value; refresh(); },
        onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); submit(false); } },
      });
      input.value = other;
      screen.append(input);
    }
  }

  const actions = el("div", { class: "actions" });
  if (state.returnToDone) {
    actions.append(el("button", { class: "btn plain", type: "button", onclick: showDone }, "Cancel"));
  } else if (state.returnToIndex !== null) {
    actions.append(el("button", {
      class: "btn plain", type: "button",
      onclick: () => { state.index = state.returnToIndex; state.returnToIndex = null; render(); },
    }, "Cancel"));
  } else if (previousIndex() !== -1) {
    actions.append(el("button", {
      class: "btn plain", type: "button",
      onclick: () => { state.index = previousIndex(); render(); },
    }, "Back"));
  }
  actions.append(el("span", { class: "spacer" }));
  if (q.allow_unknown !== false) {
    actions.append(el("button", { class: "btn plain", type: "button", onclick: () => submit(true) },
      "I don't know"));
  }
  actions.append(next);
  screen.append(actions, error);
  if (state.index === 0 && !state.ai) {
    screen.append(el("p", { class: "file" },
      "AI is off, so questions use their standard wording."));
  }

  main.replaceChildren(screen);
  refresh();
  if (focusTarget) focusTarget.focus();

  // When research finishes while this screen is open, show it without losing
  // anything typed: redraw if nothing is typed yet, otherwise offer to.
  if (q.research) {
    watchResearch(() => {
      const untouched = text.trim() === "" && picked.size === 0 && other.trim() === "";
      if (untouched || q.research === "prices") {
        const fresh = researchBox(q.research);
        if (q.research === "competitors" || !box) return render();
        box.replaceWith(fresh);
        box = fresh;
      } else {
        const fresh = el("div", { class: "note research done" },
          el("span", {}, "Competitors found. "),
          el("button", { class: "link", type: "button", onclick: render }, "Show them"));
        if (box) box.replaceWith(fresh); else screen.insertBefore(fresh, screen.children[1]);
        box = fresh;
      }
    });
  }
}

// ---------- follow-up screen ----------

function renderFollowUp() {
  const { id, text: question, about } = state.followUp;
  const base = state.questions.find((q) => q.id === id);
  renderProgress(base.stage);

  let text = "";
  const error = el("div", { class: "error", role: "alert" });
  const next = el("button", { class: "btn primary", type: "button", onclick: () => submit(false) }, "Continue");
  const refresh = () => { next.disabled = text.trim() === ""; };

  async function submit(skip) {
    if (next.dataset.busy) return;
    if (!skip && text.trim() === "") return;
    next.dataset.busy = "1";
    next.disabled = true;
    error.textContent = "";
    const slow = state.ai && !skip && id === IDEA_QUESTION;
    try {
      if (slow) showWorking("Reading your idea…");
      const result = await api("PUT", `/api/sessions/${state.session.id}/answers/${id}/follow_up`,
        { value: skip ? null : text.trim() });
      state.session = result.session;
      state.followUp = null;
      state.view = "question";
      await advance(true);
    } catch (err) {
      if (!main.querySelector(".error")) render();
      const shown = main.querySelector(".error");
      if (shown) shown.textContent = SAVE_ERROR;
      delete next.dataset.busy;
      refresh();
    }
  }

  const input = el("textarea", {
    class: "field",
    "aria-label": question,
    oninput: (e) => { text = e.target.value; refresh(); },
    onkeydown: (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(false); }
    },
  });

  main.replaceChildren(el("div", { class: "screen" },
    el("div", { class: "eyebrow" }, "One more detail"),
    el("h1", {}, question),
    about ? el("p", { class: "hint" }, `About your answer to: ${about}`) : null,
    input,
    el("div", { class: "actions" },
      el("span", { class: "spacer" }),
      el("button", { class: "btn plain", type: "button", onclick: () => submit(true) }, "Skip"),
      next),
    error));
  refresh();
  input.focus();
}

// ---------- moving between questions ----------

function nextIndex() {
  for (let i = state.index + 1; i < state.questions.length; i++) {
    if (!isSkipped(state.questions[i])) return i;
  }
  return -1;
}

function previousIndex() {
  for (let i = state.index - 1; i >= 0; i--) {
    if (!isSkipped(state.questions[i])) return i;
  }
  return -1;
}

// A follow-up the Conductor has asked for and the person has not yet seen.
function pendingFollowUp() {
  for (const q of state.questions) {
    const a = answers()[q.id];
    if (a && a.follow_up && a.follow_up.status === "pending") {
      return { id: q.id, text: a.follow_up.question, about: a.question };
    }
  }
  return null;
}

// afterFollowUp: the current question is already answered, so stay on it if it
// is still open (the follow-up interrupted before it was shown).
async function advance(afterFollowUp) {
  const pending = pendingFollowUp();
  if (pending) {
    state.followUp = pending;
    state.view = "followup";
    return render();
  }
  if (state.returnToDone) return showDone();
  if (state.returnToIndex !== null) {
    state.index = state.returnToIndex;
    state.returnToIndex = null;
    state.view = "question";
    return render();
  }
  const current = state.questions[state.index];
  const stay = afterFollowUp && !answers()[current.id];
  const i = stay ? state.index : nextIndex();
  if (i !== -1) {
    state.index = i;
    state.view = "question";
    return render();
  }
  // Checks on the last answers may still be running; the server waits for them.
  if (state.ai) showWorking("Checking your answers…");
  state.session = await api("POST", `/api/sessions/${state.session.id}/complete`);
  if (pendingFollowUp()) return advance(true);
  state.view = state.ai ? "report" : "done";
  render();
}

// ---------- finish screen ----------

function answerText(a) {
  if (!a) return "Not answered";
  if (a.dont_know) return "I don't know";
  const parts = Array.isArray(a.value) ? [...a.value] : [a.value];
  if (a.other) parts.push(a.other);
  if (a.follow_up && a.follow_up.answer) parts.push(a.follow_up.answer);
  return parts.filter(Boolean).join(", ");
}

function showDone() {
  state.view = "done";
  state.followUp = null;
  state.returnToDone = false;
  renderProgress(6);

  const screen = el("div", { class: "screen" },
    el("h1", {}, "Your answers"),
    el("p", { class: "lead" }, state.ai
      ? "Tap any answer to change it, then update your report."
      : "Your answers are saved. The report needs the AI, which is off."));

  for (const stage of state.stages) {
    const qs = state.questions.filter((q) => q.stage === stage.id);
    if (!qs.length) continue;
    const list = el("div", { class: "list" });
    for (const base of qs) {
      const q = view(base);
      const a = answers()[q.id];
      list.append(el("button", {
        class: "row", type: "button",
        onclick: () => {
          state.index = state.questions.indexOf(base);
          state.view = "question";
          state.returnToDone = true;
          render();
        },
      },
        el("span", { class: "qa" },
          el("div", { class: "q" }, q.text),
          el("div", { class: "a" + (!a || a.dont_know ? " unknown" : "") }, answerText(a)),
          a && a.inferred ? el("div", { class: "q" }, "Taken from your earlier answers") : null),
        el("span", { class: "edit" }, "Edit")));
    }
    screen.append(el("div", { class: "section-title" }, stage.name), list);
  }

  screen.append(
    el("div", { class: "file" }, `Saved to reports/${state.session.id}.json`),
    el("div", { class: "actions" },
      state.ai && state.session.completed
        ? el("button", { class: "btn plain", type: "button", onclick: () => { state.view = "report"; render(); } }, "Back to report")
        : null,
      el("span", { class: "spacer" }),
      state.ai
        ? el("button", {
          class: "btn primary", type: "button",
          onclick: async () => {
            await api("POST", `/api/sessions/${state.session.id}/report`);
            state.view = "report";
            render();
          },
        }, "Update report")
        : el("button", { class: "btn primary", type: "button", onclick: newIdea }, "Check another idea")));

  main.replaceChildren(screen);
  window.scrollTo(0, 0);
}

function newIdea() {
  localStorage.removeItem(STORAGE_KEY);
  state.session = null;
  state.alerts = [];
  state.checking = false;
  clearInterval(alertTimer);
  alertTimer = null;
  updateAlertButton();
  state.index = 0;
  state.view = "question";
  render();
}

// ---------- alerts ----------
//
// A small button in the corner, always visible. The Checker runs on the server
// in the background; the button picks up its results by polling.

let alertTimer = null;
const alertButton = el("button", { class: "alert-button", type: "button", onclick: () => showAlerts() });
document.body.append(alertButton);

function openAlerts() {
  return state.alerts.filter((a) => a.status === "open");
}

function updateAlertButton() {
  const open = openAlerts();
  const red = open.some((a) => a.severity === "red");
  alertButton.className = "alert-button" + (open.length ? (red ? " red" : " amber") : "");
  const label = open.length
    ? `${open.length} alert${open.length === 1 ? "" : "s"}`
    : state.checking ? "Checking" : "No alerts";
  alertButton.replaceChildren(
    state.checking ? el("span", { class: "spinner tiny", "aria-hidden": "true" })
                   : el("span", { class: "alert-dot", "aria-hidden": "true" }),
    el("span", {}, label));
  alertButton.setAttribute("aria-label", open.length
    ? `${label}${red ? ", including a serious problem" : ""}. Open the list.`
    : `${label}. Open the list.`);
}

async function refreshAlerts() {
  if (!state.session) return;
  try {
    const data = await api("GET", `/api/sessions/${state.session.id}/alerts`);
    state.alerts = data.alerts;
    state.checking = data.checking;
    updateAlertButton();
  } catch (err) { /* try again on the next tick */ }
}

function startAlertWatch() {
  if (alertTimer || !state.session) return;
  refreshAlerts();
  alertTimer = setInterval(refreshAlerts, 4000);
}

function changeAnswer(questionId) {
  closeAlerts();
  const index = state.questions.findIndex((q) => q.id === questionId);
  if (index === -1) return;
  if (state.view === "done") {
    state.returnToDone = true;
  } else if (state.view === "question" && state.index !== index) {
    state.returnToIndex = state.index;
  }
  state.index = index;
  state.view = "question";
  render();
}

function alertCard(alert) {
  const card = el("div", { class: "alert-card " + alert.severity },
    el("div", { class: "alert-head" },
      el("span", { class: "pill " + alert.severity }, alert.severity === "red" ? "Serious" : "Concern"),
      el("span", { class: "alert-about" }, alert.question)),
    el("div", { class: "alert-label" }, "You said"),
    el("div", { class: "alert-text" }, alert.you_said),
    el("div", { class: "alert-label" }, "The data says"),
    el("div", { class: "alert-text" }, alert.data_says),
    el("div", { class: "alert-label" }, "Suggested change"),
    el("div", { class: "alert-text" }, alert.suggestion));
  const sources = alert.sources || [];
  if (sources.length) {
    card.append(el("div", { class: "alert-label" }, "Source"));
    for (const src of sources) {
      const name = src.name || "Research";
      card.append(el("div", { class: "alert-source" },
        src.url ? el("a", { href: src.url, target: "_blank", rel: "noopener noreferrer" }, name)
                : el("span", {}, name),
        src.tag === "estimated" ? el("span", { class: "tag" }, " · estimate") : null));
    }
  } else {
    card.append(el("div", { class: "alert-label" }, "Source"),
      el("div", { class: "alert-source" }, "Your own answers"));
  }
  card.append(el("div", { class: "alert-actions" },
    el("button", { class: "link", type: "button", onclick: () => changeAnswer(alert.question_id) },
      "Change my answer")));
  return card;
}

function closeAlerts() {
  const overlay = document.querySelector(".overlay");
  if (overlay) overlay.remove();
  document.removeEventListener("keydown", onAlertKey);
  alertButton.focus({ preventScroll: true });
}

function onAlertKey(e) {
  if (e.key === "Escape") closeAlerts();
}

function showAlerts() {
  closeAlerts();
  const open = openAlerts();
  const close = el("button", { class: "btn plain", type: "button", onclick: closeAlerts }, "Close");
  const body = el("div", { class: "sheet-body" });
  if (open.length) {
    body.append(el("p", { class: "hint" },
      "These don't stop you. Change an answer, or carry on; they will all be listed in your report."));
    for (const alert of open) body.append(alertCard(alert));
  } else {
    body.append(el("p", { class: "hint" }, state.checking
      ? "Checking your answers against the research…"
      : "Nothing conflicts with the research so far. As you answer, anything that does will show here."));
  }
  const overlay = el("div", {
    class: "overlay",
    onclick: (e) => { if (e.target === overlay) closeAlerts(); },
  },
    el("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": "Alerts" },
      el("div", { class: "sheet-head" }, el("h2", {}, "Alerts"), close),
      body));
  document.body.append(overlay);
  document.addEventListener("keydown", onAlertKey);
  close.focus();

  const unseen = open.filter((a) => !a.seen).map((a) => a.key);
  if (unseen.length && state.session) {
    api("POST", `/api/sessions/${state.session.id}/alerts/seen`, { keys: unseen })
      .then((data) => { state.alerts = data.alerts; })
      .catch(() => {});
  }
}

// ---------- start ----------

function render() {
  clearInterval(pollTimer);
  clearInterval(reportTimer);
  if (state.view === "report") showReport();
  else if (state.view === "done") showDone();
  else if (state.view === "followup") renderFollowUp();
  else renderQuestion();
}

async function start() {
  const data = await api("GET", "/api/questions");
  state.stages = data.stages;
  state.questions = data.questions;
  state.ai = data.ai;

  const savedId = localStorage.getItem(STORAGE_KEY);
  if (savedId) {
    try {
      state.session = await api("GET", `/api/sessions/${savedId}`);
    } catch (err) {
      localStorage.removeItem(STORAGE_KEY);
    }
  }
  if (state.session) {
    // Resume where the user left off.
    const firstOpen = state.questions.findIndex((q) => !state.session.answers[q.id]);
    if (state.session.completed || firstOpen === -1) state.view = state.ai && state.session.completed ? "report" : "done";
    else state.index = firstOpen;
    const pending = pendingFollowUp();
    if (pending) {
      state.followUp = pending;
      state.view = "followup";
      if (firstOpen === -1) state.index = state.questions.length - 1;
    }
    state.alerts = state.session.alerts || [];
    startAlertWatch();
  }
  updateAlertButton();
  render();
}

start().catch(() => {
  main.replaceChildren(el("p", { class: "error" }, "Could not load the form. Is the app running?"));
});
