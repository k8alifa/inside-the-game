// Playback and scheduling logic for the overlay. Pure functions and small classes, no DOM,
// so they can be tested with `node --test web/tests/`.

/** Absolute match seconds for each event (second half continues after the first). */
export function buildClock(events) {
  let p1End = 0;
  for (const e of events) if (e.period === 1 && e.clock > p1End) p1End = e.clock;
  const abs = events.map((e) => (e.period === 1 ? e.clock : p1End + e.clock));
  return { abs, p1End, total: abs.length ? abs[abs.length - 1] : 0 };
}

/** Index of the last event at or before t (-1 if none). Binary search. */
export function indexAt(abs, t) {
  let lo = 0, hi = abs.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (abs[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

/** Match seconds for the start of a match minute (0-based), for the scrub bar. */
export function secondsForMinute(minute, clock) {
  if (minute < 45) return minute * 60;
  return clock.p1End + (minute - 45) * 60;
}

/** Elapsed match minute (0-based) at match seconds t, for the scrub bar. */
export function minuteAt(t, clock) {
  if (t <= clock.p1End) return Math.min(45, Math.floor(t / 60));
  return 45 + Math.floor((t - clock.p1End) / 60);
}

/** Last element of a list sorted by `at_event` whose at_event <= eventId. */
export function latestAt(list, eventId) {
  let lo = 0, hi = list.length - 1, ans = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].at_event <= eventId) { ans = list[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

/**
 * One overlay slot (ticker, alert, ...). Items become due when match time passes them.
 * A shown item stays at least show_for_s wall seconds; meanwhile only the newest due item
 * waits (older ones are skipped at high speed). Non-persistent slots clear after show_for_s.
 */
export class Slot {
  constructor(items, timeOf, { persist = true } = {}) {
    this.items = items.map((i) => ({ ...i, t: timeOf(i) })).sort((a, b) => a.t - b.t);
    this.persist = persist;
    this.reset();
  }

  reset() {
    this.cursor = 0;
    this.current = null;
    this.pending = null;
    this.shownAt = -Infinity;
  }

  /** Jump: show the latest item due at t straight away. */
  seek(t, now) {
    this.reset();
    while (this.cursor < this.items.length && this.items[this.cursor].t <= t) this.cursor++;
    const last = this.cursor ? this.items[this.cursor - 1] : null;
    if (last && (this.persist || t - last.t < 30)) { this.current = last; this.shownAt = now; }
    return this.current;
  }

  /** Advance to match time t at wall time now (ms). Returns true when the shown item changed. */
  tick(t, now) {
    while (this.cursor < this.items.length && this.items[this.cursor].t <= t) {
      const next = this.items[this.cursor++];
      if (!this.pending || !this.pending.boost || next.boost) this.pending = next;
    }
    const held = this.current ? (now - this.shownAt) / 1000 < this.current.show_for_s : false;
    if (this.pending && !held) {
      this.current = this.pending;
      this.pending = null;
      this.shownAt = now;
      return true;
    }
    if (!this.persist && this.current && !held && !this.pending) {
      this.current = null;
      return true;
    }
    return false;
  }
}

/** Text for the chosen language; falls back to English when a translation is not verified. */
export function itemText(item, lang) {
  const tr = item.translations && item.translations[lang];
  if (lang !== "en" && tr && tr.ok) return { text: tr.text, lang };
  return { text: item.text, lang: "en" };
}

/** Whether a language can be offered at all, from the bundle manifest. */
export function langAvailable(manifest, lang) {
  if (lang === "en") return true;
  const status = manifest.langs && manifest.langs[lang];
  return status === "verified" || status === "partial";
}

export function periodLabel(row, isLast) {
  if (!row) return "Pre-match";
  if (isLast) return "Full time";
  if (row.type === "half_end" && row.period === 1) return "Half-time";
  return row.period === 1 ? "First half" : "Second half";
}
