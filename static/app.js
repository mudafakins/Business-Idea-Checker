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

// The entry path from the first answer: one idea, a few ideas, or no idea yet.
const PATHS = { "I have one idea": "one", "I have a few ideas": "few", "I have no idea yet": "none" };

function currentPath() {
  const a = answers().q1;
  return (a && PATHS[a.value]) || "one";
}

// Some questions are only asked on some paths.
function asked(q) {
  return !q.paths || q.paths.includes(currentPath());
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
  if (base.type === "pick") return renderPick(base);
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
      if (state.session.declined) return showDeclined();
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
  if (state.index === 0 && state.ai && !state.returnToDone) {
    screen.append(el("button", {
      class: "quick-card", type: "button",
      onclick: () => { state.view = "quick"; render(); },
    },
      el("span", { class: "qa" },
        el("div", { class: "quick-title" }, "Quick mode"),
        el("div", { class: "q" }, "Short on time? Write everything about your idea in one go and get the report straight away.")),
      el("span", { class: "edit" }, "›")));
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

// ---------- pick an idea (few-ideas and no-idea paths) ----------

async function renderPick(q) {
  renderProgress(q.stage);
  const mode = currentPath() === "few" ? "rank" : "suggest";
  let data;
  try {
    data = await api("GET", `/api/sessions/${state.session.id}/ideas`);
    if (data.status === "none" || data.stale) data = await api("POST", `/api/sessions/${state.session.id}/ideas`, { more: false });
  } catch (err) {
    main.replaceChildren(el("p", { class: "error" }, SAVE_ERROR));
    return;
  }
  if (state.view !== "question" || state.questions[state.index] !== q) return;

  const back = el("button", {
    class: "btn plain", type: "button",
    onclick: () => { state.index = previousIndex(); render(); },
  }, "Back");

  const items = data.items || [];
  if (data.status === "running" && !items.length) {
    main.replaceChildren(el("div", { class: "screen" },
      el("h1", {}, mode === "rank" ? "Checking your ideas" : "Finding ideas that fit you"),
      el("div", { class: "working inline", role: "status" },
        el("div", { class: "spinner", "aria-hidden": "true" }),
        el("p", {}, mode === "rank"
          ? "Looking up each idea and checking it against what you told us. This takes a minute or two."
          : "Looking for ideas that fit your money, time, skills and limits. This takes a minute or two.")),
      el("div", { class: "actions" }, back)));
    return watchIdeas(q);
  }
  if (data.status === "failed" && !items.length) {
    main.replaceChildren(el("div", { class: "screen" },
      el("h1", {}, "That didn't work this time"),
      el("p", { class: "lead" }, "The ideas could not be checked. Try again, or go back and change your answers."),
      el("div", { class: "actions" }, back, el("span", { class: "spacer" }),
        el("button", {
          class: "btn primary", type: "button",
          onclick: async () => { await api("POST", `/api/sessions/${state.session.id}/ideas`, { more: false }); render(); },
        }, "Try again"))));
    return;
  }

  const safe = items.filter((i) => !i.unsafe_reason);
  const unsafe = items.filter((i) => i.unsafe_reason);
  const saved = answers().pick;
  let chosen = saved ? saved.idea_id : null;
  if (!safe.some((i) => i.id === chosen)) chosen = null;
  const go = el("button", { class: "btn primary", type: "button", onclick: () => choose() }, "Test this idea");
  const error = el("div", { class: "error", role: "alert" });
  const refresh = () => { go.disabled = !chosen; };

  async function choose() {
    if (!chosen || go.dataset.busy) return;
    go.dataset.busy = "1";
    go.disabled = true;
    try {
      showWorking("Reading your idea…");
      const result = await api("POST", `/api/sessions/${state.session.id}/pick`, { idea_id: chosen });
      state.session = result.session;
      if (state.session.declined) return showDeclined();
      await advance();
    } catch (err) {
      render();
    }
  }

  const list = el("div", { class: "idea-list" });
  safe.forEach((idea, n) => {
    const card = el("button", {
      class: "idea" + (idea.id === chosen ? " selected" : ""), type: "button",
      "aria-pressed": String(idea.id === chosen),
      onclick: () => {
        chosen = idea.id;
        for (const c of list.children) {
          const on = c.dataset.id === chosen;
          c.classList.toggle("selected", on);
          c.setAttribute("aria-pressed", String(on));
        }
        refresh();
      },
    },
      el("div", { class: "idea-head" },
        el("span", { class: "idea-rank" }, String(n + 1)),
        el("span", { class: "idea-title" }, idea.title),
        n === 0 && !idea.breaks.length ? el("span", { class: "pill best" }, "Best fit") : null,
        el("span", { class: "idea-score" }, `Fit ${idea.score}/10`)),
      el("div", { class: "idea-desc" }, idea.description),
      el("div", { class: "idea-reason" }, idea.reason),
      idea.breaks.length ? el("div", { class: "idea-breaks" }, `Breaks your limit: ${idea.breaks.join(", ")}`) : null,
      idea.evidence.length
        ? el("div", { class: "idea-evidence" }, ...idea.evidence.map((e) => el("div", {}, e.claim, " · ", e.source_name || "source")))
        : null);
    card.dataset.id = idea.id;
    list.append(card);
  });

  const screen = el("div", { class: "screen" },
    el("h1", {}, mode === "rank" ? "Your ideas, ranked" : "Ideas that fit you"),
    el("p", { class: "hint" }, mode === "rank"
      ? "Ranked by how well each fits your money, time, skills, timing and limits, with a quick check on demand. Pick one to test."
      : "Built from the problems and areas you know, and checked against your limits. Pick one to test, or ask for more."),
    list);
  if (unsafe.length) {
    screen.append(el("div", { class: "section-title" }, "Not checked"),
      el("div", { class: "list" }, ...unsafe.map((i) => el("div", { class: "row static" },
        el("span", { class: "qa" }, el("div", { class: "a" }, i.title), el("div", { class: "q" }, i.unsafe_reason))))));
  }
  const actions = el("div", { class: "actions" }, back, el("span", { class: "spacer" }));
  if (mode === "suggest") {
    actions.append(data.status === "running"
      ? el("span", { class: "muted small more-loading" }, el("span", { class: "spinner tiny" }), " Finding 5 more…")
      : el("button", {
        class: "btn plain", type: "button",
        onclick: async () => { await api("POST", `/api/sessions/${state.session.id}/ideas`, { more: true }); render(); },
      }, "Show 5 more"));
  }
  actions.append(go);
  screen.append(actions, error);
  main.replaceChildren(screen);
  refresh();
  if (data.status === "running") watchIdeas(q);
}

function watchIdeas(q) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const data = await api("GET", `/api/sessions/${state.session.id}/ideas`);
      if (data.status === "running") return;
      clearInterval(pollTimer);
      if (state.view === "question" && state.questions[state.index] === q) render();
    } catch (err) { /* try again on the next tick */ }
  }, 3000);
}

// An illegal or unsafe idea is declined politely at the idea stage (brief, section 6).
function showDeclined() {
  state.view = "declined";
  renderProgress(2);
  const path = currentPath();
  main.replaceChildren(el("div", { class: "screen" },
    el("h1", {}, "We can't check this idea"),
    el("p", { class: "lead" }, state.session.declined),
    el("p", { class: "hint" }, "You're welcome to try a different idea."),
    el("div", { class: "actions" },
      el("button", { class: "btn plain", type: "button", onclick: newIdea }, "Start over"),
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn primary", type: "button",
        onclick: () => {
          state.index = state.questions.findIndex((x) => x.id === (path === "one" ? "q8" : "pick"));
          state.view = "question";
          render();
        },
      }, path === "one" ? "Describe a different idea" : "Choose a different idea"))));
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
    const q = state.questions[i];
    if (asked(q) && !isSkipped(q)) return i;
  }
  return -1;
}

function previousIndex() {
  for (let i = state.index - 1; i >= 0; i--) {
    const q = state.questions[i];
    if (asked(q) && !isSkipped(q)) return i;
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
      if (!asked(base) && !a) continue;
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
  state.returnToDone = false;
  state.returnToIndex = null;
  state.followUp = null;
  window.scrollTo(0, 0);
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

// ---------- quick mode: one text box instead of the form ----------

let quickDraft = "";

const QUICK_PROMPTS = [
  "The idea: what you will sell, and to whom",
  "Where you will run it, how much money and time you can put in",
  "What you are good at or have access to, and what you refuse to do",
  "Who buys, what problem it solves, and what they use today",
  "Competitors you know, and what you will do differently",
  "Your price, how customers will find you, how often they buy, and your cost per sale",
];

function showQuick() {
  state.view = "quick";
  renderProgress(2);
  stageName.textContent = "Quick mode";
  const error = el("div", { class: "error", role: "alert" });
  const go = el("button", { class: "btn primary", type: "button", onclick: submit }, "Get my report");
  const input = el("textarea", {
    class: "field quick-field",
    "aria-label": "Everything about your idea",
    placeholder: "Write as much or as little as you like, in your own words. Anything you leave out is treated as unknown.",
    oninput: (e) => { quickDraft = e.target.value; refresh(); },
    onkeydown: (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); } },
  });
  input.value = quickDraft;
  const refresh = () => { go.disabled = quickDraft.trim().length < 40; };

  async function submit() {
    if (go.disabled || go.dataset.busy) return;
    go.dataset.busy = "1";
    showWorking("Reading everything you wrote…");
    const res = await fetch("/api/sessions/quick", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: quickDraft }),
    }).catch(() => null);
    if (!res || !res.ok) {
      const detail = res ? (await res.json().catch(() => ({}))).detail : null;
      showQuick();
      main.querySelector(".error").textContent = detail || SAVE_ERROR;
      return;
    }
    const data = await res.json();
    quickDraft = "";
    clearInterval(alertTimer);
    alertTimer = null;
    state.session = data.session;
    localStorage.setItem(STORAGE_KEY, state.session.id);
    state.alerts = [];
    startAlertWatch();
    if (state.session.declined) return showDeclined();
    state.index = state.questions.length - 1;
    state.view = "report";
    render();
  }

  main.replaceChildren(el("div", { class: "screen" },
    el("h1", {}, "Tell us everything about your idea"),
    el("p", { class: "hint" }, "It helps to cover:"),
    el("ul", { class: "quick-prompts" }, ...QUICK_PROMPTS.map((t) => el("li", {}, t))),
    input,
    el("div", { class: "actions" },
      el("button", { class: "btn plain", type: "button", onclick: () => { state.view = "question"; state.index = 0; render(); } }, "Use the full form"),
      el("span", { class: "spacer" }),
      go),
    error,
    el("p", { class: "file" }, "You can check and correct what was understood afterwards, under “Change an answer”.")));
  refresh();
  input.focus();
}

// ---------- history: every saved run, reopened from the reports folder ----------

const VERDICT_SHORT = { pursue: "Pursue", test: "Test small first", drop: "Drop or rethink" };
let viewBeforeHistory = null;

// Start new: a fresh check. The current one stays saved in History.
document.getElementById("new-link").addEventListener("click", () => {
  closeAlerts();
  newIdea();
});

document.getElementById("history-link").addEventListener("click", () => {
  if (state.view === "history") return;
  viewBeforeHistory = state.view;
  state.view = "history";
  closeAlerts();
  render();
});

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) +
    ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

async function showHistory() {
  renderProgress(-1);
  let runs = [];
  try {
    runs = await api("GET", "/api/sessions");
  } catch (err) {
    main.replaceChildren(el("p", { class: "error" }, "Could not load your reports. Is the app running?"));
    return;
  }
  if (state.view !== "history") return;

  const list = el("div", { class: "list" });
  for (const run of runs) {
    const status = run.verdict
      ? el("span", { class: "rating " + ({ pursue: "good", test: "mid", drop: "bad" })[run.verdict] }, VERDICT_SHORT[run.verdict])
      : el("span", { class: "muted small" }, run.declined ? "Declined"
        : run.completed ? "Answers finished, no report yet" : `In progress · ${run.answered} of ${run.questions} answered`);
    const current = state.session && state.session.id === run.id;
    list.append(el("button", { class: "row", type: "button", onclick: () => openRun(run.id) },
      el("span", { class: "qa" },
        el("div", { class: "a" }, run.title || "No idea chosen yet"),
        el("div", { class: "q history-meta" }, when(run.created_at), " · ", status,
          run.verdict && run.confidence ? ` · ${run.confidence} confidence` : "",
          current ? " · open now" : "")),
      el("span", { class: "edit" }, "Open")));
  }

  const back = viewBeforeHistory && state.session
    ? el("button", {
      class: "btn plain", type: "button",
      onclick: () => { state.view = viewBeforeHistory || "question"; render(); },
    }, "Back")
    : null;
  main.replaceChildren(el("div", { class: "screen" },
    el("h1", {}, "Your reports"),
    el("p", { class: "lead" }, runs.length
      ? "Every run is saved in the reports folder. Open one to see its report or carry on where you left off."
      : "Nothing saved yet. Your first run will appear here."),
    runs.length ? list : null,
    el("div", { class: "actions" }, back, el("span", { class: "spacer" }),
      el("button", { class: "btn primary", type: "button", onclick: newIdea }, "Check another idea"))));
  window.scrollTo(0, 0);
}

// Open a saved run: the report if there is one, otherwise where it was left.
async function openRun(id) {
  let session;
  try {
    session = await api("GET", `/api/sessions/${id}`);
  } catch (err) {
    return;
  }
  clearInterval(alertTimer);
  alertTimer = null;
  state.session = session;
  localStorage.setItem(STORAGE_KEY, id);
  state.alerts = session.alerts || [];
  state.returnToDone = false;
  state.returnToIndex = null;
  state.followUp = null;
  placeSession();
  startAlertWatch();
  updateAlertButton();
  render();
}

// Decide which screen a loaded session should show.
function placeSession() {
  const firstOpen = state.questions.findIndex((q) => asked(q) && !state.session.answers[q.id]);
  state.index = firstOpen === -1 ? state.questions.length - 1 : firstOpen;
  if (state.session.declined) state.view = "declined";
  else if (state.session.completed || firstOpen === -1) state.view = state.ai && state.session.completed ? "report" : "done";
  else state.view = "question";
  const pending = pendingFollowUp();
  if (pending && !state.session.declined) {
    state.followUp = pending;
    state.view = "followup";
  }
}

// ---------- start ----------

function render() {
  clearInterval(pollTimer);
  clearInterval(reportTimer);
  if (state.view === "history") showHistory();
  else if (state.view === "quick") showQuick();
  else if (state.view === "declined") showDeclined();
  else if (state.view === "report") showReport();
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
    placeSession();
    state.alerts = state.session.alerts || [];
    startAlertWatch();
  }
  updateAlertButton();
  render();
}

start().catch(() => {
  main.replaceChildren(el("p", { class: "error" }, "Could not load the form. Is the app running?"));
});
