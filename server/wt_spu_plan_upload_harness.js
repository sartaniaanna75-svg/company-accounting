/**
 * Загрузка планов СПУ: недели чт–ср, пусто vs 0, без смены приоритетов period_plans.
 * Зеркало ключевых правил app.html (без DOM / БД).
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

function reportingWeek(iso, startDay) {
  const d = parseIso(iso);
  const start = startDay == null ? 4 : startDay;
  const offset = (d.getDay() - start + 7) % 7;
  const from = new Date(d.getFullYear(), d.getMonth(), d.getDate() - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
  const fromIso = isoFromDate(from);
  const toIso = isoFromDate(to);
  return { start_date: fromIso, end_date: toIso, period_key: "week:" + fromIso };
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
  const w = reportingWeek(iso, 4);
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

// A: 25.11.2026 (ср) → week 19–25.11, key week:2026-11-19
{
  const p = parseWeekEnd("25.11.2026");
  assert(!p.error && p.week.start_date === "2026-11-19", "A thu start");
  assert(p.week.end_date === "2026-11-25", "A wed end");
  assert(p.week.period_key === "week:2026-11-19", "A period key");
}

// B: year boundary 30.12.2026 (ср)
{
  const p = parseWeekEnd("30.12.2026");
  assert(!p.error, "B parse");
  assert(p.week.start_date === "2026-12-24", "B start");
  assert(p.week.end_date === "2026-12-30", "B end");
}

// C: Thursday is not week end
{
  const p = parseWeekEnd("19.11.2026");
  assert(!!p.error, "C not wednesday");
}

// D: empty vs zero
{
  assert(parseNumberCell("").empty === true, "D empty");
  assert(parseNumberCell("0").empty === false && parseNumberCell("0").value === 0, "D zero");
  assert(parseNumberCell("1 234,5").value === 1234.5, "D spaces comma");
}

// E: duplicate week detection logic
{
  const seen = {};
  const rows = ["25.11.2026", "25.11.2026"];
  let dup = false;
  rows.forEach(function (r) {
    const p = parseWeekEnd(r);
    if (seen[p.week.start_date]) dup = true;
    else seen[p.week.start_date] = true;
  });
  assert(dup, "E duplicate weeks");
}

// F: period conflict policy — upload must not apply when period plan covers
{
  function hasPeriodConflict(periodVal) {
    return periodVal != null && Number.isFinite(Number(periodVal));
  }
  assert(hasPeriodConflict(260) === true, "F conflict");
  assert(hasPeriodConflict(null) === false, "F no conflict");
}

console.log("wt_spu_plan_upload_harness: OK");
console.log("  A week end wednesday → thu key");
console.log("  B year boundary");
console.log("  C reject non-wednesday");
console.log("  D empty vs zero / numbers");
console.log("  E duplicate weeks");
console.log("  F period conflict skip policy");
