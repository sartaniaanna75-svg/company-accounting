/**
 * UI-only search for СПУ → Настройка показателей (name substring, case-insensitive).
 * Does not mutate indicator payload / layers / plans.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function mgmtSettingsIndicatorSearchQueryFrom(raw) {
  return String(raw || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function mgmtSettingsIndicatorMatchesSearch(it, q) {
  const needle = String(q || "").toLowerCase();
  if (!needle) return true;
  const name = String((it && it.name) || "").toLowerCase();
  return name.indexOf(needle) !== -1;
}

function filterSettingsList(items, tab, rawQ, bucketFn) {
  const inTab = (items || []).filter(function (it) { return bucketFn(it) === tab; });
  const q = mgmtSettingsIndicatorSearchQueryFrom(rawQ);
  const filtered = q
    ? inTab.filter(function (it) { return mgmtSettingsIndicatorMatchesSearch(it, q); })
    : inTab;
  const tabCounts = {};
  (items || []).forEach(function (it) {
    const b = bucketFn(it);
    if (!b) return;
    tabCounts[b] = (tabCounts[b] || 0) + 1;
  });
  return { inTab: inTab, filtered: filtered, q: q, tabCounts: tabCounts };
}

const items = [
  { indicator_id: "a", name: "Маржинальность продаж", bucket: "key" },
  { indicator_id: "b", name: "Выручка", bucket: "key" },
  { indicator_id: "c", name: "маржин план", bucket: "totals" },
  { indicator_id: "d", name: "ФОТ", bucket: "unset" },
  { indicator_id: "e", name: "Старый маржин", bucket: "archive" }
];

function bucket(it) { return it.bucket; }

let r = filterSettingsList(items, "key", "  МарЖин  ", bucket);
assert(r.q === "маржин", "trim+lower query");
assert(r.filtered.length === 1 && r.filtered[0].indicator_id === "a", "partial case-insensitive in key");
assert(r.tabCounts.key === 2, "tab count ignores search");
assert(r.inTab.length === 2, "inTab full before search");

r = filterSettingsList(items, "totals", "маржин", bucket);
assert(r.filtered.length === 1 && r.filtered[0].indicator_id === "c", "totals tab with same query");

r = filterSettingsList(items, "archive", "маржин", bucket);
assert(r.filtered.length === 1 && r.filtered[0].indicator_id === "e", "archive tab keeps query");

r = filterSettingsList(items, "key", "xyz", bucket);
assert(r.filtered.length === 0, "no matches");

r = filterSettingsList(items, "key", "", bucket);
assert(r.filtered.length === 2, "clear restores full tab list");

r = filterSettingsList(items, "unset", "фот", bucket);
assert(r.filtered.length === 1, "full name case-insensitive");

const before = JSON.stringify(items);
filterSettingsList(items, "key", "маржин", bucket);
assert(JSON.stringify(items) === before, "search does not mutate source list");

console.log("OK wt_spu_settings_indicator_search_harness");
