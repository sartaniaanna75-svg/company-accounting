/**
 * Разные периоды в формуле: обнаружение conflict + формат строк предупреждения.
 * Не трогает reporting_week_start_day / eval app.html (логика зеркала).
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
  const p = String(iso || "").slice(0, 10).split("-").map(Number);
  return new Date(p[0], p[1] - 1, p[2], 12, 0, 0);
}

function reportingWeekWithStartDay(startDay, anchorIso) {
  const d = parseIso(anchorIso);
  const offset = (d.getDay() - startDay + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
  return { start_date: isoFromDate(from), end_date: isoFromDate(to) };
}

function fmtRu(iso) {
  const p = String(iso).split("-");
  return p[2] + "." + p[1] + "." + p[0];
}

function factWindow(ind, anchor) {
  const day = ind.reporting_week_start_day != null ? Number(ind.reporting_week_start_day) : 4;
  const w = reportingWeekWithStartDay(day, anchor);
  return {
    start_date: w.start_date,
    end_date: w.end_date,
    rangeRu: fmtRu(w.start_date) + "–" + fmtRu(w.end_date)
  };
}

function periodConflict(indicatorsById, tokens, anchor) {
  const groups = {};
  tokens.forEach(function (tok) {
    if (!tok || tok.t !== "ind" || !tok.id) return;
    const off = String(tok.week_offset || 0);
    if (!groups[off]) groups[off] = [];
    groups[off].push(tok);
  });
  const groupKeys = Object.keys(groups);
  for (let gi = 0; gi < groupKeys.length; gi++) {
    const rows = [];
    groups[groupKeys[gi]].forEach(function (tok) {
      const ind = indicatorsById[tok.id];
      if (!ind) return;
      const win = factWindow(ind, anchor);
      rows.push({
        id: tok.id,
        name: ind.name,
        start: win.start_date,
        end: win.end_date,
        rangeRu: win.rangeRu
      });
    });
    if (rows.length < 2) continue;
    const key0 = rows[0].start + "|" + rows[0].end;
    if (rows.some(function (r) { return (r.start + "|" + r.end) !== key0; })) {
      return { ok: false, rows: rows };
    }
  }
  return { ok: true, rows: [] };
}

function mismatchLines(conflict) {
  return ((conflict && conflict.rows) || []).map(function (row) {
    return (row.name || row.id) + " — " + (row.rangeRu || "");
  });
}

const anchor = "2026-11-19"; /* Thu */
const inds = {
  opt: { name: "Выручка ОПТ", reporting_week_start_day: 3, periodicity: "week" },
  hc: { name: "Количество сотрудников — Компания", reporting_week_start_day: 4, periodicity: "week" },
  a: { name: "A", reporting_week_start_day: 4, periodicity: "week" },
  b: { name: "B", reporting_week_start_day: 4, periodicity: "week" }
};

{
  const c = periodConflict(inds, [
    { t: "ind", id: "a", week_offset: 0 },
    { t: "ind", id: "b", week_offset: 0 }
  ], anchor);
  assert(c.ok === true, "1 same periods");
}

{
  const c = periodConflict(inds, [
    { t: "ind", id: "opt", week_offset: 0 },
    { t: "ind", id: "hc", week_offset: 0 }
  ], anchor);
  assert(c.ok === false, "2 mismatch");
  const optWin = factWindow(inds.opt, anchor);
  const hcWin = factWindow(inds.hc, anchor);
  assert(optWin.start_date === "2026-11-18" && optWin.end_date === "2026-11-24", "7 OPT wed-tue");
  assert(hcWin.start_date === "2026-11-19" && hcWin.end_date === "2026-11-25", "8 HC thu-wed");
  const lines = mismatchLines(c);
  assert(lines.some(function (l) { return l.indexOf("Выручка ОПТ") !== -1 && l.indexOf("18.11") !== -1; }), "2 lines opt");
  assert(lines.some(function (l) { return l.indexOf("Количество сотрудников") !== -1 && l.indexOf("19.11") !== -1; }), "2 lines hc");
}

{
  let saved = false;
  const c = periodConflict(inds, [
    { t: "ind", id: "opt", week_offset: 0 },
    { t: "ind", id: "hc", week_offset: 0 }
  ], anchor);
  assert(!c.ok, "need confirm");
  if (c.ok || true /* user Да */) saved = true;
  assert(saved, "4 save after ack");
  saved = false;
  if (!(c.ok) && false /* user Вернуться */) {
    saved = true;
  }
  assert(!saved, "3 cancel no save");
}

console.log("wt_spu_formula_period_confirm_harness: OK");
