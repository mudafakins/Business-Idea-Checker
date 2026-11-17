// Business Idea Checker - step 1: the form, one question per screen.

const STORAGE_KEY = "idea-checker-session";

const state = {
  stages: [],
  questions: [],
  session: null,      // created on the first saved answer
  index: 0,
  view: "question",   // "question" | "done"
  returnToDone: false,
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

// ---------- question screen ----------

function renderQuestion() {
  const q = state.questions[state.index];
  const saved = answers()[q.id];
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
    if (!dontKnow && !hasAnswer()) return;
    const answer = dontKnow
      ? { value: null, other: null, dont_know: true }
      : q.type === "multi"
        ? { value: [...picked], other: other.trim() || null, dont_know: false }
        : { value: text.trim(), other: null, dont_know: false };
    next.disabled = true;
    error.textContent = "";
    try {
      if (!state.session) {
        state.session = await api("POST", "/api/sessions");
        localStorage.setItem(STORAGE_KEY, state.session.id);
      }
      state.session = await api("PUT", `/api/sessions/${state.session.id}/answers/${q.id}`, answer);
      await advance();
    } catch (err) {
      error.textContent = "Could not save. Check that the app is still running, then try again.";
      refresh();
    }
  }

  const screen = el("div", { class: "screen" }, el("h1", {}, q.text));
  if (q.hint) screen.append(el("p", { class: "hint" }, q.hint));
  if (q.note) screen.append(el("div", { class: "note" }, q.note));

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
        el("span", {}, option.label),
        option.soon
          ? el("span", { class: "badge" }, "Coming later")
          : el("span", { class: "check", "aria-hidden": "true" }, "✓"));
      row.dataset.label = option.label;
      list.append(row);
    }
    screen.append(list);
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
  } else if (state.index > 0) {
    actions.append(el("button", {
      class: "btn plain", type: "button",
      onclick: () => { state.index -= 1; render(); },
    }, "Back"));
  }
  actions.append(el("span", { class: "spacer" }));
  if (q.allow_unknown !== false) {
    actions.append(el("button", { class: "btn plain", type: "button", onclick: () => submit(true) },
      "I don't know"));
  }
  actions.append(next);
  screen.append(actions, error);

  main.replaceChildren(screen);
  refresh();
  if (focusTarget) focusTarget.focus();
}

async function advance() {
  if (state.returnToDone) return showDone();
  if (state.index < state.questions.length - 1) {
    state.index += 1;
    return render();
  }
  state.session = await api("POST", `/api/sessions/${state.session.id}/complete`);
  showDone();
}

// ---------- finish screen ----------

function answerText(a) {
  if (!a) return "Not answered";
  if (a.dont_know) return "I don't know";
  const parts = Array.isArray(a.value) ? [...a.value] : [a.value];
  if (a.other) parts.push(a.other);
  return parts.filter(Boolean).join(", ");
}

function showDone() {
  state.view = "done";
  state.returnToDone = false;
  renderProgress(6);

  const screen = el("div", { class: "screen" },
    el("h1", {}, "Your answers are saved"),
    el("p", { class: "lead" },
      "The report will appear here once research and scoring are built. Tap any answer to change it."));

  for (const stage of state.stages) {
    const qs = state.questions.filter((q) => q.stage === stage.id);
    if (!qs.length) continue;
    const list = el("div", { class: "list" });
    for (const q of qs) {
      const a = answers()[q.id];
      list.append(el("button", {
        class: "row", type: "button",
        onclick: () => {
          state.index = state.questions.indexOf(q);
          state.view = "question";
          state.returnToDone = true;
          render();
        },
      },
        el("span", { class: "qa" },
          el("div", { class: "q" }, q.text),
          el("div", { class: "a" + (!a || a.dont_know ? " unknown" : "") }, answerText(a))),
        el("span", { class: "edit" }, "Edit")));
    }
    screen.append(el("div", { class: "section-title" }, stage.name), list);
  }

  screen.append(
    el("div", { class: "file" }, `Saved to reports/${state.session.id}.json`),
    el("div", { class: "actions" },
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn primary", type: "button",
        onclick: () => {
          localStorage.removeItem(STORAGE_KEY);
          state.session = null;
          state.index = 0;
          state.view = "question";
          render();
        },
      }, "Check another idea")));

  main.replaceChildren(screen);
  window.scrollTo(0, 0);
}

// ---------- start ----------

function render() {
  if (state.view === "done") showDone();
  else renderQuestion();
}

async function start() {
  const data = await api("GET", "/api/questions");
  state.stages = data.stages;
  state.questions = data.questions;

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
    if (state.session.completed || firstOpen === -1) state.view = "done";
    else state.index = firstOpen;
  }
  render();
}

start().catch(() => {
  main.replaceChildren(el("p", { class: "error" }, "Could not load the form. Is the app running?"));
});
