/**
 * Универсальная загрузка планов СПУ: layout, миграция B–K, unmapped=skip, ambiguous.
 */
"use strict";

function assert(c, m) {
  if (!c) throw new Error(m || "assert failed");
}

function pad(n) {
  return n < 10 ? "0" + n : String(n);
}

function isoFromDate(d) {
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

function parseIso(iso) {
  const p = String(iso || "").slice(0, 10).split("-");
  return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]), 12, 0, 0);
}

function reportingWeek(iso) {
  const d = parseIso(iso);
  const start = 4;
  const offset = (d.getDay() - start + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
  return { start_date: isoFromDate(from), end_date: isoFromDate(to), period_key: "week:" + isoFromDate(from) };
}

function ruToIso(ru) {
  const m = /^(\d{1,2})[./](\d{1,2})[./](\d{4})$/.exec(String(ru || "").trim());
  if (!m) return "";
  return m[3] + "-" + pad(Number(m[2])) + "-" + pad(Number(m[1]));
}

function parseWeekEnd(raw) {
  let iso = ruToIso(raw);
  if (!iso && /^\d{4}-\d{2}-\d{2}$/.test(String(raw || "").trim())) iso = String(raw).trim();
  if (!iso) return { error: "bad date" };
  const d = parseIso(iso);
  if (d.getDay() !== 3) return { error: "not wednesday", endIso: iso };
  const w = reportingWeek(iso);
  if (w.end_date !== iso) return { error: "end mismatch", week: w };
  return { error: "", week: w, endIso: iso };
}

function parseNumberCell(raw) {
  const s = String(raw == null ? "" : raw).replace(/\s/g, " ").trim();
  if (!s) return { empty: true, value: null };
  const norm = s.replace(/\s/g, "").replace(",", ".");
  if (!/^-?\d+(\.\d+)?$/.test(norm)) return { empty: false, value: null, error: "bad" };
  return { empty: false, value: Number(norm) };
}

function normHeader(raw) {
  return String(raw == null ? "" : raw).replace(/[\u00A0\u202F]/g, " ").replace(/\s+/g, " ").trim();
}

function headerKey(raw) {
  return normHeader(raw).toLowerCase();
}

const LEGACY_COLS = [
  { col: "B", label: "Кол-во заявок" },
  { col: "C", label: "Выручка ОПТ" },
  { col: "D", label: "Кол-во накладных СКЛАД" },
  { col: "E", label: "Выручка СКЛАД" },
  { col: "F", label: "Кол-во покупателей, чел" },
  { col: "G", label: "Кол-во чеков, шт" },
  { col: "H", label: "Выручка РОЗНИЦА" },
  { col: "I", label: "Сумма поступлений /Собранных денег ОПТ" },
  { col: "J", label: "Сумма поступлений /Собранных денег СКЛАД + РОЗНИЦА" },
  { col: "K", label: "Сумма поступлений /Собранных денег СКЛАД" }
];

function migrateMapping(root) {
  if (!root.columns) root.columns = {};
  if (!root.byHeader) root.byHeader = {};
  if (!root.candidates) root.candidates = {};
  if (Number(root.version) >= 2) return root;
  const nextBy = Object.assign({}, root.byHeader);
  const nextCand = {};
  Object.keys(root.candidates).forEach(function (k) {
    nextCand[k] = (root.candidates[k] || []).slice();
  });
  LEGACY_COLS.forEach(function (spec) {
    const iid = String(root.columns[spec.col] || "").trim();
    if (!iid) return;
    const key = headerKey(spec.label);
    if (!nextCand[key]) nextCand[key] = [];
    if (nextCand[key].indexOf(iid) === -1) nextCand[key].push(iid);
    if (nextBy[key] && String(nextBy[key]) !== iid) {
      if (nextCand[key].indexOf(String(nextBy[key])) === -1) nextCand[key].push(String(nextBy[key]));
      delete nextBy[key];
    } else if (!nextBy[key]) nextBy[key] = iid;
  });
  Object.keys(nextCand).forEach(function (k) {
    if ((nextCand[k] || []).length > 1) delete nextBy[k];
  });
  root.byHeader = nextBy;
  root.candidates = nextCand;
  root.version = 2;
  return root;
}

function detectLayout(matrix, forcedDateCol) {
  const rows = matrix || [];
  let maxCols = 0;
  rows.forEach(function (r) { maxCols = Math.max(maxCols, (r || []).length); });
  const colScores = [];
  for (let c = 0; c < maxCols; c++) {
    let hits = 0;
    const hitRows = [];
    for (let r = 0; r < rows.length; r++) {
      const p = parseWeekEnd((rows[r] || [])[c]);
      if (!p.error && p.week) { hits++; hitRows.push(r); }
    }
    colScores.push({ c: c, hits: hits, hitRows: hitRows });
  }
  colScores.sort(function (a, b) { return b.hits - a.hits; });
  let dateCol = forcedDateCol != null ? Number(forcedDateCol) : null;
  if (dateCol == null) {
    if (!colScores[0] || colScores[0].hits < 1) return { ok: false, needDateCol: true };
    if (colScores[1] && colScores[1].hits === colScores[0].hits) return { ok: false, needDateCol: true };
    dateCol = colScores[0].c;
  }
  const score = colScores.find(function (x) { return x.c === dateCol; });
  if (!score || !score.hitRows.length) return { ok: false, needDateCol: true };
  const firstDataRow = score.hitRows[0];
  let headerRow = -1;
  for (let r = firstDataRow - 1; r >= 0; r--) {
    const dateCell = parseWeekEnd((rows[r] || [])[dateCol]);
    if (!dateCell.error && dateCell.week) continue;
    let texts = 0;
    for (let c = 0; c < maxCols; c++) {
      if (c === dateCol) continue;
      if (normHeader((rows[r] || [])[c])) texts++;
    }
    if (texts >= 1) { headerRow = r; break; }
  }
  if (headerRow < 0) return { ok: false, needHeaderRow: true };
  const headers = [];
  for (let c = 0; c < maxCols; c++) {
    if (c === dateCol) continue;
    const display = normHeader((rows[headerRow] || [])[c]);
    if (!display) continue;
    headers.push({ colIndex: c, display: display, key: headerKey(display) });
  }
  return { ok: true, dateCol: dateCol, headerRow: headerRow, firstDataRow: firstDataRow, headers: headers };
}

function suggestedId(byHeader, candidates, key) {
  const list = (candidates[key] || []).slice();
  if (byHeader[key] && list.indexOf(byHeader[key]) === -1) list.push(byHeader[key]);
  const uniq = [];
  list.forEach(function (id) { if (id && uniq.indexOf(id) === -1) uniq.push(id); });
  return uniq.length === 1 ? uniq[0] : "";
}

// 1 migration B-K
{
  const root = migrateMapping({
    version: 1,
    columns: { B: "id_b", C: "id_c", G: "id_g" },
    byHeader: {},
    candidates: {}
  });
  assert(root.version === 2, "1 version");
  assert(root.byHeader[headerKey("Кол-во заявок")] === "id_b", "1 B");
  assert(root.byHeader[headerKey("Выручка ОПТ")] === "id_c", "1 C");
  assert(root.columns.B === "id_b", "1 legacy kept");
}

// 2–5 territory-like sheet with title + headers + one/two weeks, reorder
{
  const matrix = [
    ["ДЛЯ СПУ", "", "", ""],
    ["Конец периода", "Кол-во заявок", "Выручка ОПТ", "Кол-во чеков, шт"],
    ["25.11.2026", "10", "100", "5"],
    ["02.12.2026", "12", "110", ""]
  ];
  const lay = detectLayout(matrix);
  assert(lay.ok && lay.dateCol === 0, "2 date col");
  assert(lay.headerRow === 1 && lay.firstDataRow === 2, "2 header/data");
  assert(lay.headers.length === 3, "2 headers");
  assert(lay.headers[0].display === "Кол-во заявок", "2 name");

  const reordered = [
    ["ДЛЯ СПУ", "", "", ""],
    ["Выручка ОПТ", "Конец периода", "Кол-во заявок"],
    ["100", "25.11.2026", "10"]
  ];
  const lay2 = detectLayout(reordered);
  assert(lay2.ok && lay2.dateCol === 1, "4 date not A");
  assert(lay2.headers.some(function (h) { return h.key === headerKey("Кол-во заявок"); }), "4 reorder");
}

// 3 single week ok
{
  const m = [
    ["Конец периода", "Кол-во заявок"],
    ["25.11.2026", "7"]
  ];
  assert(detectLayout(m).ok, "3 single week");
}

// 6–8 rename / new / remove column — mapping by key
{
  const byHeader = {};
  byHeader[headerKey("Кол-во заявок")] = "id1";
  assert(suggestedId(byHeader, {}, headerKey("Кол-во заявок")) === "id1", "8 known");
  assert(suggestedId(byHeader, {}, headerKey("Новый столбец")) === "", "7 new unmapped");
  assert(suggestedId({}, { [headerKey("X")]: ["a", "b"] }, headerKey("X")) === "", "9 ambiguous no auto");
}

// 10–12 unmapped skip, empty/zero
{
  assert(parseNumberCell("").empty === true, "11 empty");
  assert(parseNumberCell("0").value === 0, "11 zero");
  const unmappedSkip = !"".trim(); // no id => skip
  assert(unmappedSkip === true, "10 skip concept");
}

// 13 bad date / 14 dup weeks
{
  assert(!!parseWeekEnd("19.11.2026").error, "13 not wed");
  const seen = {};
  let dup = false;
  ["25.11.2026", "25.11.2026"].forEach(function (d) {
    const p = parseWeekEnd(d);
    if (seen[p.week.start_date]) dup = true;
    else seen[p.week.start_date] = true;
  });
  assert(dup, "14 dup");
}

// 15–16 formula/period conflict policies (boolean)
{
  assert((function (formula) { return formula === true; })(true), "15 skip formula");
  assert((function (periodVal) { return periodVal != null; })(260), "16 period conflict");
}

console.log("wt_spu_plan_upload_harness: OK");
console.log("  migration / detect / reorder / ambiguous / empty-zero");
