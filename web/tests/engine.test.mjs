// node --test web/tests/   (no npm dependencies)
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildClock, indexAt, secondsForMinute, minuteAt, latestAt, Slot, itemText, langAvailable } from "../engine.js";

const events = [
  { event_id: 1, period: 1, clock: 0 }, { event_id: 2, period: 1, clock: 600 },
  { event_id: 3, period: 1, clock: 2800 }, { event_id: 4, period: 2, clock: 0 }, { event_id: 5, period: 2, clock: 900 },
];

test("clock continues into the second half", () => {
  const c = buildClock(events);
  assert.equal(c.p1End, 2800);
  assert.deepEqual(c.abs, [0, 600, 2800, 2800, 3700]);
  assert.equal(c.total, 3700);
});

test("indexAt finds the last event at or before t", () => {
  const { abs } = buildClock(events);
  assert.equal(indexAt(abs, -1), -1);
  assert.equal(indexAt(abs, 0), 0);
  assert.equal(indexAt(abs, 2799), 1);
  assert.equal(indexAt(abs, 2800), 3);
  assert.equal(indexAt(abs, 99999), 4);
});

test("minute and seconds round-trip around half-time", () => {
  const c = buildClock(events);
  assert.equal(secondsForMinute(10, c), 600);
  assert.equal(secondsForMinute(50, c), 2800 + 300);
  assert.equal(minuteAt(600, c), 10);
  assert.equal(minuteAt(3100, c), 50);
});

test("latestAt picks the newest snapshot not after the event", () => {
  const snaps = [{ at_event: 1 }, { at_event: 3 }, { at_event: 5 }];
  assert.equal(latestAt(snaps, 0), null);
  assert.equal(latestAt(snaps, 4).at_event, 3);
});

test("slot holds an item for show_for_s and skips stale ones at speed", () => {
  const items = [1, 2, 3].map((n) => ({ id: String(n), at_event: n, show_for_s: 4 }));
  const s = new Slot(items, (i) => i.at_event * 10);
  assert.equal(s.tick(10, 0), true);
  assert.equal(s.current.id, "1");
  s.tick(30, 1000); // items 2 and 3 are due, but item 1 is still held
  assert.equal(s.current.id, "1");
  s.tick(30, 4100); // hold over: the newest due item wins, item 2 is skipped
  assert.equal(s.current.id, "3");
});

test("non-persistent slots clear, seek shows the latest due item", () => {
  const s = new Slot([{ id: "a", at_event: 1, show_for_s: 2 }], (i) => i.at_event, { persist: false });
  s.tick(1, 0);
  assert.equal(s.current.id, "a");
  s.tick(5, 2500);
  assert.equal(s.current, null);
  const p = new Slot([{ id: "a", at_event: 1, show_for_s: 2 }, { id: "b", at_event: 9, show_for_s: 2 }], (i) => i.at_event);
  assert.equal(p.seek(5, 0).id, "a");
});

test("translations are only used when verified", () => {
  const item = { text: "Goal!", translations: { es: { text: "¡Gol!", ok: false } } };
  assert.deepEqual(itemText(item, "es"), { text: "Goal!", lang: "en" });
  item.translations.es.ok = true;
  assert.deepEqual(itemText(item, "es"), { text: "¡Gol!", lang: "es" });
  assert.equal(langAvailable({ langs: { en: "verified", es: "unavailable" } }, "es"), false);
  assert.equal(langAvailable({ langs: { en: "verified", es: "partial" } }, "es"), true);
});
