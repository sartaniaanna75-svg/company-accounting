/**
 * Фиксация численности СПУ: чтение не пишет manualFacts;
 * новичок не попадает в прошлую неделю без явного подтверждения.
 */
"use strict";

function isWorking(e) { return !e || e.jobStatus !== "fired"; }
function normalizeFiredOnIso(value) {
  const m = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? m[1] + "-" + m[2] + "-" + m[3] : "";
}
function employeeFiredOnIso(e) { return normalizeFiredOnIso(e && e.fired_on); }
function employeeCountsInStaffDirectoryOnDate(employee, asOfIso) {
  if (!employee || !employee.id) return false;
  const asOf = normalizeFiredOnIso(asOfIso);
  const fo = employeeFiredOnIso(employee);
  if (fo) return fo > asOf;
  return isWorking(employee);
}
function mgmtStaffDirectoryFact(list, asOfIso) {
  const seen = {};
  let n = 0;
  list.forEach(function (e) {
    if (!e || !e.id || seen[e.id]) return;
    if (!employeeCountsInStaffDirectoryOnDate(e, asOfIso)) return;
    seen[e.id] = true;
    n += 1;
  });
  return n;
}
function mgmtStaffDirectoryPeriodIsClosed(asOfIso, today) {
  return !!(asOfIso && today && asOfIso < today);
}
function mgmtReadStaffDirectoryValue(opts) {
  const closed = mgmtStaffDirectoryPeriodIsClosed(opts.asOf, opts.today);
  const frozen = opts.frozen;
  // ВАЖНО: чтение не вызывает запись.
  if (closed && frozen != null && Number.isFinite(Number(frozen))) {
    return { value: Number(frozen), status: "frozen", wrote: false };
  }
  if (closed) return { value: null, status: "unfrozen", wrote: false };
  return { value: mgmtStaffDirectoryFact(opts.people, opts.asOf), status: "auto", wrote: false };
}
function assert(c, m) { if (!c) throw new Error(m); }

const people = [
  { id: "old", jobStatus: "working" },
  { id: "newbie", jobStatus: "working" } // появился 6.11 — в карточке нет даты создания
];
const prevWed = "2026-11-04";
const today = "2026-11-09";

// Live на прошлую среду по текущему справочнику включил бы новичка — поэтому автофиксации нет.
const livePrev = mgmtStaffDirectoryFact(people, prevWed);
assert(livePrev === 2, "live would count newbie");

const closedNoFact = mgmtReadStaffDirectoryValue({
  asOf: prevWed, today: today, people: people, frozen: null
});
assert(closedNoFact.status === "unfrozen", "closed without fact");
assert(closedNoFact.value == null, "no auto value");
assert(closedNoFact.wrote === false, "read does not write");

const afterManual = mgmtReadStaffDirectoryValue({
  asOf: prevWed, today: today, people: people, frozen: 1 // владелец зафиксировал 1 без новичка
});
assert(afterManual.status === "frozen" && afterManual.value === 1, "manual freeze kept");
assert(afterManual.wrote === false, "reread no write");

const openWeek = mgmtReadStaffDirectoryValue({
  asOf: "2026-11-11", today: today, people: people, frozen: null
});
assert(openWeek.status === "auto" && openWeek.value === 2, "open week live");
assert(openWeek.wrote === false, "open read no write");

console.log("OK wt_staff_directory_freeze_harness");
