/**
 * Узкий harness: фиксированный оклад аутсорсинга + те же adjustment-поля.
 * Не грузит app.html целиком — повторяет ветку calculateEmployee для аутсорсинга.
 */
function normalizeSalaryRecord(rec) {
  const r = rec && typeof rec === "object" ? rec : {};
  return {
    fines: Number(r.fines || 0),
    bonus: Number(r.bonus || 0),
    advance: Number(r.advance || 0),
    products: Number(r.products || 0),
    vacationPay: Number(r.vacationPay || 0),
    taxes: Number(r.taxes || 0),
    issued: !!r.issued,
    vacationPaidEarlier: !!r.vacationPaidEarlier,
  };
}

function outsourcingCalc(employee, adjRaw) {
  if (!employee || employee.outsourcing !== true) throw new Error("not outsourcing");
  const adj = normalizeSalaryRecord(adjRaw);
  const terms = (employee.terms && employee.terms["2026-11"]) || {};
  const fixed = Number(terms.rate != null ? terms.rate : (employee.outsourcingFixedPay || employee.rate || 0));
  const accrued = Math.round(fixed);
  const pay = accrued + adj.bonus - adj.fines - adj.products - adj.advance + adj.vacationPay - adj.taxes;
  const accruedTotal = pay + adj.products + adj.advance;
  const toHand = pay - (adj.vacationPaidEarlier ? adj.vacationPay : 0);
  return { accrued, pay, accruedTotal, toHand, kpiBonus: 0, workedDays: 0 };
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const emp = {
  outsourcing: true,
  rate: 120000,
  outsourcingFixedPay: 120000,
  terms: { "2026-11": { rate: 100000 } },
};
// Исторический месяц зафиксирован в terms — card.rate 120000 не должен перебить.
const jan = outsourcingCalc(emp, {});
assert(jan.accrued === 100000, "fixed from terms month, not card rate");
assert(jan.workedDays === 0, "no timesheet days");
assert(jan.kpiBonus === 0, "no auto KPI");

const withAdj = outsourcingCalc(emp, {
  bonus: 5000,
  advance: 20000,
  fines: 1000,
  products: 500,
  taxes: 0,
});
assert(withAdj.accrued === 100000, "accrued stays fixed");
assert(withAdj.pay === 100000 + 5000 - 1000 - 500 - 20000, "same adj formula");
assert(withAdj.accruedTotal === withAdj.pay + 500 + 20000, "accruedTotal");

console.log("OK wt_outsourcing_calc_harness");
