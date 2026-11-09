/**
 * Округление итогов «Общее → Зарплата» (зеркало applySummarySalaryTotalsRounding).
 * Без app.html, без БД. Ведомости отделов не затрагиваются.
 */
"use strict";

const SUMMARY_SALARY_ROUND_FROM = "2026-10";

function summarySalaryRoundRub(value) {
  return Math.round(Number(value || 0));
}

function summarySalaryAppliesRound(month) {
  const m = String(month || "");
  return !!(m && m >= SUMMARY_SALARY_ROUND_FROM);
}

function applySummarySalaryTotalsRounding(data, month) {
  if (!data || !summarySalaryAppliesRound(month)) return data;
  const products = Number(data.products || 0);
  const advance = Number(data.advance || 0);
  const vacationPay = Number(data.vacationPay || 0);
  const rawAccrued = data.accruedTotal != null
    ? Number(data.accruedTotal)
    : (Number(data.pay || 0) + products + advance);
  const accruedTotal = summarySalaryRoundRub(rawAccrued);
  const payEff = accruedTotal - products - advance;
  const toHand = summarySalaryRoundRub(payEff - (data.vacationPaidEarlier ? vacationPay : 0));
  const reflect1c = summarySalaryRoundRub(accruedTotal - advance);
  const expenseZp = summarySalaryRoundRub(
    accruedTotal - advance - (data.vacationPaidEarlier ? vacationPay : 0)
  );
  const out = Object.assign({}, data);
  out.accruedTotal = accruedTotal;
  out.toHand = toHand;
  out.reflect1c = reflect1c;
  out.expenseZp = expenseZp;
  return out;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

assert(summarySalaryRoundRub(45028.46) === 45028, "0.46 down");
assert(summarySalaryRoundRub(45028.49) === 45028, "0.49 down");
assert(summarySalaryRoundRub(45028.5) === 45029, "0.50 up");
assert(summarySalaryRoundRub(49173.64) === 49174, "0.64 up");
assert(summarySalaryRoundRub(38199.81) === 38200, "0.81 up");
assert(summarySalaryRoundRub(43028.5) === 43029, "43 028.50 → 43 029");

assert(summarySalaryAppliesRound("2026-09") === false, "Sep off");
assert(summarySalaryAppliesRound("2026-10") === true, "Oct on");
assert(summarySalaryAppliesRound("2026-11") === true, "Nov on");

const raw = {
  pay: 45028.46 - 1250.3 - 2000,
  products: 1250.3,
  advance: 2000,
  vacationPay: 5000,
  vacationPaidEarlier: false,
  accruedTotal: 45028.46,
  toHand: 45028.46 - 1250.3 - 2000,
  reflect1c: 45028.46 - 2000,
  expenseZp: 45028.46 - 2000,
  issued: true,
};

const sep = applySummarySalaryTotalsRounding(raw, "2026-09");
assert(sep === raw, "Sep returns same object");
assert(sep.accruedTotal === 45028.46, "Sep keeps kopecks");

const oct = applySummarySalaryTotalsRounding(raw, "2026-10");
assert(oct !== raw, "Oct copies");
assert(oct.accruedTotal === 45028, "Oct accruedTotal");
assert(oct.toHand === summarySalaryRoundRub(45028 - 1250.3 - 2000), "Oct toHand from rounded base");
assert(oct.reflect1c === summarySalaryRoundRub(45028 - 2000), "Oct reflect1c");
assert(oct.expenseZp === summarySalaryRoundRub(45028 - 2000), "Oct expenseZp");
assert(oct.products === 1250.3, "products not rewritten");
assert(oct.advance === 2000, "advance not rewritten");
assert(oct.issued === true, "issued preserved");

const vac = applySummarySalaryTotalsRounding(
  Object.assign({}, raw, { vacationPaidEarlier: true, toHand: raw.pay - 5000 }),
  "2026-10"
);
assert(vac.toHand === summarySalaryRoundRub(45028 - 1250.3 - 2000 - 5000), "vacation earlier");
assert(vac.expenseZp === summarySalaryRoundRub(45028 - 2000 - 5000), "expense with vacation");
assert(vac.reflect1c === summarySalaryRoundRub(45028 - 2000), "reflect1c ignores vacation earlier");
assert(vac.accruedTotal === 45028, "accrued ignores vacation earlier");

const a = applySummarySalaryTotalsRounding(
  { accruedTotal: 100.46, toHand: 80.46, reflect1c: 90.46, expenseZp: 80.46, products: 10, advance: 10 },
  "2026-10"
);
const b = applySummarySalaryTotalsRounding(
  { accruedTotal: 200.5, toHand: 180.5, reflect1c: 190.5, expenseZp: 180.5, products: 10, advance: 10 },
  "2026-10"
);
assert(a.accruedTotal + b.accruedTotal === 100 + 201, "sheet sum of rounded");

console.log("OK wt_summary_salary_round_harness");
