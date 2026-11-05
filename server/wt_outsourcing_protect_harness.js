/**
 * Защита штатных + изоляция terms по месяцам (без app.html runtime).
 */
function employeeBlockedFromEnablingOutsourcing(inRoster, hasMarks) {
  return !!(inRoster || hasMarks);
}

function monthFixed(terms, month, cardRate) {
  const rec = terms && terms[month];
  if (rec && rec.rate != null && Number(rec.rate) > 0) return Number(rec.rate);
  return Number(cardRate || 0);
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

assert(employeeBlockedFromEnablingOutsourcing(true, false) === true, "roster blocks");
assert(employeeBlockedFromEnablingOutsourcing(false, true) === true, "marks block");
assert(employeeBlockedFromEnablingOutsourcing(false, false) === false, "new ok");

const terms = { "2026-10": { rate: 100000 }, "2026-11": { rate: 120000 } };
assert(monthFixed(terms, "2026-10", 120000) === 100000, "Oct keeps 100k");
assert(monthFixed(terms, "2026-11", 120000) === 120000, "Nov uses 120k");

console.log("OK wt_outsourcing_protect_harness");
