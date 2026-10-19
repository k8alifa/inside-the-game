// Inside the Game: static fan overlay. Reads the baked bundle in data/<match_id>/ and plays it back.
// No server logic, no model calls, no external requests: every URL here is relative.
import {
  buildClock, indexAt, secondsForMinute, minuteAt, latestAt, Slot, itemText, langAvailable, periodLabel,
} from "./engine.js";

const qs = new URLSearchParams(location.search);
const state = {
  view: qs.get("view") === "compare" ? "compare" : "single",
  profile: qs.get("profile") === "casual" ? "casual" : "analyst",
  fav: qs.get("fav") || "",
  player: qs.get("player") || "",
  lang: qs.get("lang") === "es" ? "es" : "en",
  speed: [1, 10, 60, 120].includes(Number(qs.get("speed"))) ? Number(qs.get("speed")) : 60,
  playing: false,
  t: 0,
};
const data = { base: "", manifest: null, events: [], clock: null, stats: [], facts: {}, overlays: {}, players: {} };
const panes = [];
let lastFrame = null;

const $ = (sel, root = document) => root.querySelector(sel);

async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function team(id) {
  return data.manifest.teams.find((t) => t.id === id);
}

function teamColor(id) {
  const t = team(id);
  return t ? t.colors.primary : "var(--chalk-dim)";
}

function itemTeam(item) {
  const f = item && item.fact_ids && data.facts[item.fact_ids[0]];
  return f && f.teams && f.teams.length ? f.teams[0] : null;
}

function timeOf(item) {
  const i = item.at_event - 1;
  return data.clock.abs[Math.max(0, Math.min(i, data.clock.abs.length - 1))];
}

async function overlayFor(profile, fav) {
  const key = `${profile}|${fav}`;
  if (!data.overlays[key]) {
    data.overlays[key] = await getJSON(`${data.base}overlay.${profile}${fav ? "." + fav : ""}.json`);
  }
  return data.overlays[key].items;
}

async function playerFile(pid) {
  if (!data.players[pid]) data.players[pid] = await getJSON(`${data.base}players/${pid}.json`);
  return data.players[pid];
}

// ------------------------------------------------------------------ panes
function createPane(profile) {
  const node = $("#pane-tpl").content.firstElementChild.cloneNode(true);
  $("#stage").appendChild(node);
  const pane = { profile, node, slots: {}, last: {} };
  const [home, away] = data.manifest.teams;
  $(".club.home .short", node).textContent = home.short;
  $(".club.away .short", node).textContent = away.short;
  $(".club.home .chip", node).style.background = home.colors.primary;
  $(".club.away .chip", node).style.background = away.colors.primary;
  node.style.setProperty("--home", home.colors.primary);
  node.style.setProperty("--away", away.colors.primary);
  const proof = $(".proof", node);
  const pop = $(".proof-pop", node);
  const show = (open) => { pop.hidden = !open; proof.setAttribute("aria-expanded", String(open)); };
  proof.addEventListener("click", () => show(pop.hidden));
  proof.addEventListener("mouseenter", () => show(true));
  $(".ticker", node).addEventListener("mouseleave", () => show(false));
  proof.addEventListener("keydown", (e) => { if (e.key === "Escape") show(false); });
  panes.push(pane);
  return pane;
}

async function loadPaneItems(pane) {
  const items = await overlayFor(pane.profile, state.fav);
  const by = (slot) => items.filter((i) => i.slot === slot);
  const tickerItems = pane.profile === "casual" ? items.filter((i) => i.slot === "ticker" || i.slot === "stats") : by("ticker");
  pane.slots = {
    ticker: new Slot(tickerItems, timeOf, { persist: true }),
    alert: new Slot(by("alert"), timeOf, { persist: false }),
    banner: new Slot(by("score"), timeOf, { persist: false }),
    note: new Slot(pane.profile === "analyst" ? by("stats") : [], timeOf, { persist: true }),
    card: new Slot([], timeOf, { persist: true }),
  };
  pane.player = null;
  if (state.player) {
    const pf = await playerFile(state.player);
    pane.player = pf;
    pane.slots.card = new Slot(pf.items[pane.profile].filter((i) => i.slot === "card"), timeOf, { persist: true });
  }
  pane.last = {};
  const label = pane.profile === "analyst" ? "Analyst" : "Casual fan";
  $(".pane-label", pane.node).textContent = state.fav ? `${label}, supporting ${team(state.fav).name}` : label;
  $(".stats", pane.node).hidden = pane.profile !== "analyst";
}

async function rebuildPanes() {
  $("#stage").replaceChildren();
  panes.length = 0;
  $("#stage").classList.toggle("compare", state.view === "compare");
  const profiles = state.view === "compare" ? ["analyst", "casual"] : [state.profile];
  for (const p of profiles) await loadPaneItems(createPane(p));
  seek(state.t);
}

// ------------------------------------------------------------------ rendering
function renderTicker(pane, item) {
  const node = pane.node;
  const textEl = $(".ticker-text", node);
  const proof = $(".proof", node);
  const pop = $(".proof-pop", node);
  if (!item) {
    textEl.textContent = "Kick-off is coming up.";
    proof.hidden = true;
    pop.hidden = true;
    $(".edge", node).style.background = "var(--chalk-dim)";
    return;
  }
  const { text, lang } = itemText(item, state.lang);
  textEl.textContent = text;
  if (state.lang !== "en" && lang === "en") {
    const tag = document.createElement("span");
    tag.className = "lang-tag";
    tag.textContent = "English (translation not verified)";
    textEl.appendChild(tag);
  }
  $(".edge", node).style.background = teamColor(itemTeam(item));
  proof.hidden = false;
  proof.classList.toggle("fallback", item.source === "template-fallback");
  proof.textContent = item.source === "template-fallback" ? "Proof (safe line)" : "Proof";
  pop.replaceChildren(...proofContent(item));
}

function proofContent(item) {
  const out = [];
  const h = document.createElement("h3");
  h.textContent = "Where this line comes from";
  out.push(h);
  const src = document.createElement("p");
  src.textContent = {
    model: "Written by the model, then checked against the match data.",
    template: "Built from the match data by a template, then checked.",
    "template-fallback": "The model's line failed the check, so this safe line from the data is shown instead.",
  }[item.source] || item.source;
  out.push(src);
  const facts = document.createElement("ul");
  for (const fid of item.fact_ids) {
    const li = document.createElement("li");
    const code = document.createElement("code");
    code.textContent = fid;
    li.append(code, " ", (data.facts[fid] && data.facts[fid].summary) || "");
    facts.appendChild(li);
  }
  const fl = document.createElement("p");
  fl.textContent = `Facts (${item.fact_ids.length}):`;
  out.push(fl, facts);
  const ev = document.createElement("p");
  ev.textContent = `Match events: ${item.evidence.length ? item.evidence.join(", ") : "none"}`;
  out.push(ev);
  if (item.blocked && item.blocked.length) {
    const why = document.createElement("p");
    why.className = "why";
    why.textContent = `Blocked: ${item.blocked.join("; ")}`;
    out.push(why);
  }
  return out;
}

const ALERT_LABEL = { goal: "Goal", yellow_card: "Yellow card", red_card: "Red card" };

function renderAlert(pane, item) {
  const box = $(".alert", pane.node);
  box.hidden = !item;
  if (!item) return;
  $(".alert-kind", box).textContent = ALERT_LABEL[item.kind] || "Moment";
  $(".alert-text", box).textContent = itemText(item, state.lang).text;
  box.style.borderLeftColor = teamColor(itemTeam(item));
}

function renderBanner(pane, item) {
  const box = $(".banner", pane.node);
  box.hidden = !item;
  if (item) box.textContent = itemText(item, state.lang).text;
}

function renderScore(pane, row, isLast) {
  const key = row ? `${row.event_id}|${isLast}` : "none";
  if (pane.last.score === key) return;
  pane.last.score = key;
  const [h, a] = row ? row.score : [0, 0];
  $(".home-goals", pane.node).textContent = h;
  $(".away-goals", pane.node).textContent = a;
  $(".clock", pane.node).textContent = row ? row.display_clock : "0'";
  $(".period", pane.node).textContent = periodLabel(row, isLast);
}

function renderStats(pane, row) {
  if (pane.profile !== "analyst") return;
  const snap = row ? latestAt(data.stats, row.event_id) : null;
  const key = snap ? snap.at_event : "none";
  if (pane.last.stats === key) return;
  pane.last.stats = key;
  const [home, away] = data.manifest.teams;
  const s = snap ? snap.teams : null;
  const v = (tid, k) => (s ? s[tid][k] : 0);
  const node = $(".stats", pane.node);
  const ph = s ? v(home.id, "possession_pct") : 50;
  $(".possession .h", node).style.width = `${ph}%`;
  $(".possession .a", node).style.width = `${100 - ph}%`;
  $(".possession .vals", node).textContent = s ? `${Math.round(ph)}% / ${Math.round(100 - ph)}%` : "-";
  $(".shots", node).textContent = `${v(home.id, "shots")} - ${v(away.id, "shots")}`;
  $(".sot", node).textContent = `${v(home.id, "shots_on_target")} - ${v(away.id, "shots_on_target")}`;
  $(".xg", node).textContent = `${v(home.id, "xg").toFixed(2)} - ${v(away.id, "xg").toFixed(2)}`;
  const m = snap && snap.momentum;
  const mh = m ? m.share[home.id] : 50;
  $(".momentum .h", node).style.width = `${mh}%`;
  $(".momentum .a", node).style.width = `${100 - mh}%`;
  $(".mom-label", node).textContent = m ? `Threat, min ${m.window[0]}-${m.window[1]}` : "Momentum";
}

function renderNote(pane, item) {
  const el = $(".stats-note", pane.node);
  el.textContent = item ? itemText(item, state.lang).text : "";
}

function renderCard(pane, row) {
  const card = $(".card", pane.node);
  const pf = pane.player;
  card.hidden = !pf;
  if (!pf) return;
  const snap = row ? latestAt(pf.card, row.event_id) : null;
  const key = `${pf.player.id}|${snap ? snap.at_event : 0}|${state.lang}`;
  if (pane.last.card !== key) {
    pane.last.card = key;
    $(".card-name", card).textContent = pf.player.name;
    $(".card-role", card).textContent = `${team(pf.player.team).short}, ${pf.player.role === "GK" ? "goalkeeper" : "outfield"}`;
    $(".chip", card).style.background = teamColor(pf.player.team);
    const g = snap || { touches: 0, passes_completed: 0, passes_attempted: 0, shots: 0, xg: 0, goals: 0, ball_wins: 0, saves: 0 };
    const keeper = pf.player.role === "GK";
    const cells = pane.profile === "analyst"
      ? [["Touches", g.touches], ["Passes", `${g.passes_completed}/${g.passes_attempted}`],
         keeper ? ["Saves", g.saves] : ["Shots", g.shots], keeper ? ["Ball wins", g.ball_wins] : ["xG", g.xg.toFixed(2)],
         ["Goals", g.goals], keeper ? ["Shots", g.shots] : ["Ball wins", g.ball_wins]]
      : [["Touches", g.touches], keeper ? ["Saves", g.saves] : ["Shots", g.shots], ["Goals", g.goals]];
    $(".card-grid", card).replaceChildren(...cells.map(([label, value]) => {
      const d = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      d.append(dt, dd);
      return d;
    }));
  }
}

function renderCardLine(pane, item) {
  const el = $(".card-line", pane.node);
  if (!item) { el.textContent = ""; return; }
  const asof = document.createElement("span");
  asof.className = "card-asof";
  asof.textContent = `Checked line, as of ${item.display_clock}`;
  el.replaceChildren(asof, itemText(item, state.lang).text);
}

const RENDER = { ticker: renderTicker, alert: renderAlert, banner: renderBanner, note: renderNote, card: renderCardLine };

function update(now, forceAll = false) {
  const idx = indexAt(data.clock.abs, state.t);
  const row = idx >= 0 ? data.events[idx] : null;
  const isLast = idx === data.events.length - 1;
  for (const pane of panes) {
    renderScore(pane, row, isLast);
    renderStats(pane, row);
    renderCard(pane, row);
    for (const [name, slot] of Object.entries(pane.slots)) {
      if (slot.tick(state.t, now) || forceAll) RENDER[name](pane, slot.current);
    }
  }
  const minute = minuteAt(state.t, data.clock);
  const scrub = $("#scrub");
  if (document.activeElement !== scrub) scrub.value = String(minute);
  $("#scrub-out").textContent = row ? row.display_clock : "0'";
}

function seek(t) {
  state.t = Math.max(0, Math.min(t, data.clock.total));
  const now = performance.now();
  for (const pane of panes) {
    pane.last = {};
    for (const slot of Object.values(pane.slots)) slot.seek(state.t, now);
  }
  update(now, true);
}

function frame(now) {
  if (state.playing) {
    if (lastFrame !== null) state.t += ((now - lastFrame) / 1000) * state.speed;
    if (state.t >= data.clock.total) { state.t = data.clock.total; setPlaying(false); }
    update(now);
  }
  lastFrame = now;
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ controls
function setPlaying(on) {
  state.playing = on;
  const btn = $("#btn-play");
  btn.setAttribute("aria-label", on ? "Pause" : "Play");
  btn.setAttribute("aria-pressed", String(on));
}

function setPressed(groupSel, attr, value) {
  for (const b of document.querySelectorAll(`${groupSel} button`)) {
    b.setAttribute("aria-pressed", String(b.dataset[attr] === String(value)));
  }
}

function syncUrl() {
  const p = new URLSearchParams();
  if (state.view === "compare") p.set("view", "compare");
  if (state.profile !== "analyst") p.set("profile", state.profile);
  if (state.fav) p.set("fav", state.fav);
  if (state.player) p.set("player", state.player);
  if (state.lang !== "en") p.set("lang", state.lang);
  if (state.speed !== 60) p.set("speed", String(state.speed));
  history.replaceState(null, "", `${location.pathname}${p.toString() ? "?" + p : ""}`);
  const link = $("#view-link");
  const other = new URLSearchParams(p);
  if (state.view === "compare") { other.delete("view"); link.textContent = "Back to single view"; }
  else { other.set("view", "compare"); link.textContent = "Compare analyst and casual"; }
  link.href = `?${other}`;
}

function nextMomentTime() {
  let best = Infinity;
  for (const pane of panes) {
    for (const name of ["ticker", "alert", "banner"]) {
      for (const it of pane.slots[name].items) if (it.t > state.t + 0.5 && it.t < best) best = it.t;
    }
  }
  return best;
}

function wireControls() {
  document.querySelectorAll("#profile-group button").forEach((b) => b.addEventListener("click", async () => {
    state.profile = b.dataset.profile;
    setPressed("#profile-group", "profile", state.profile);
    syncUrl();
    await rebuildPanes();
  }));
  $("#fav-select").addEventListener("change", async (e) => { state.fav = e.target.value; syncUrl(); await rebuildPanes(); });
  $("#player-select").addEventListener("change", async (e) => { state.player = e.target.value; syncUrl(); await rebuildPanes(); });
  $("#lang-select").addEventListener("change", (e) => {
    state.lang = langAvailable(data.manifest, e.target.value) ? e.target.value : "en";
    e.target.value = state.lang;
    syncUrl();
    update(performance.now(), true);
  });
  document.querySelectorAll("#speed-group button").forEach((b) => b.addEventListener("click", () => {
    state.speed = Number(b.dataset.speed);
    setPressed("#speed-group", "speed", state.speed);
    syncUrl();
  }));
  $("#btn-play").addEventListener("click", () => {
    if (state.t >= data.clock.total) seek(0);
    setPlaying(!state.playing);
  });
  $("#btn-restart").addEventListener("click", () => seek(0));
  $("#btn-next").addEventListener("click", () => { const t = nextMomentTime(); if (t < Infinity) seek(t); });
  $("#scrub").addEventListener("input", (e) => seek(secondsForMinute(Number(e.target.value), data.clock)));
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "select" || tag === "input" || tag === "textarea") return;
    if (e.code === "Space") {
      e.preventDefault();
      if (tag !== "button") $("#btn-play").click();
    } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const step = (e.shiftKey ? 5 : 1) * 60 * (e.key === "ArrowRight" ? 1 : -1);
      seek(state.t + step);
    }
  });
}

function fillSelects() {
  const m = data.manifest;
  const fav = $("#fav-select");
  for (const tid of m.favourites) fav.add(new Option(team(tid).name, tid));
  fav.value = m.favourites.includes(state.fav) ? state.fav : "";
  state.fav = fav.value;
  const pl = $("#player-select");
  for (const p of m.focus_players) {
    pl.add(new Option(`${p.name} (${team(p.team).short}${p.role === "GK" ? ", goalkeeper" : ""})`, p.id));
  }
  pl.value = m.focus_players.some((p) => p.id === state.player) ? state.player : "";
  state.player = pl.value;
  const lang = $("#lang-select");
  for (const opt of lang.options) opt.disabled = !langAvailable(m, opt.value);
  if (!langAvailable(m, "es")) {
    $("#lang-field").title = "Spanish needs a bake with a model. This bundle was made without one, so only English is shown.";
  }
  if (!langAvailable(m, state.lang)) state.lang = "en";
  lang.value = state.lang;
  setPressed("#profile-group", "profile", state.profile);
  setPressed("#speed-group", "speed", state.speed);
  $("#profile-group").hidden = state.view === "compare";
  $("#scrub").max = String(minuteAt(data.clock.total, data.clock));
}

async function main() {
  try {
    const index = await getJSON("data/index.json");
    const matchId = qs.get("match") && index.matches.includes(qs.get("match")) ? qs.get("match") : index.default;
    data.base = `data/${matchId}/`;
    const [manifest, timeline, stats, facts] = await Promise.all([
      getJSON(`${data.base}manifest.json`), getJSON(`${data.base}timeline.json`),
      getJSON(`${data.base}stats.json`), getJSON(`${data.base}facts.json`),
    ]);
    Object.assign(data, { manifest, events: timeline.events, stats: stats.snapshots, facts });
    data.clock = buildClock(data.events);
    const [home, away] = manifest.teams;
    $("#match-title").textContent = `${home.name} v ${away.name}`;
    fillSelects();
    wireControls();
    syncUrl();
    const startMinute = Number(qs.get("t"));
    const atEvent = Number(qs.get("at"));  // jump to an exact event (handy for demos)
    state.t = atEvent >= 1 && atEvent <= data.events.length ? data.clock.abs[atEvent - 1]
      : Number.isFinite(startMinute) && startMinute > 0 ? secondsForMinute(startMinute, data.clock) : 0;
    await rebuildPanes();
    if (qs.get("autoplay") === "1") setPlaying(true);
    requestAnimationFrame(frame);
    window.__itg = { state, ready: true, seek, setPlaying };
  } catch (err) {
    const box = document.createElement("p");
    box.className = "error";
    box.textContent = `Could not load the match bundle (${err.message}). Run "itg-bake --match data/samples/NBA-HCF-7 --web", then serve the folder with "python -m http.server -d web 8000".`;
    $("#stage").replaceChildren(box);
    window.__itg = { ready: false, error: String(err) };
  }
}

main();
