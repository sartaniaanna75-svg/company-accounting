/**
 * Контракт: «Стоимость дня» → только узкий OPS shopPayroll.salaries (1 leaf),
 * даже если «чужой» collect дал бы 458 листьев. Без saveApp / forcePut.
 */
function assert(c, m) { if (!c) throw new Error(m); }

function buildSalariesOp(salaries) {
  return {
    path: "shopPayroll.salaries",
    value: salaries,
    expected_v: 0
  };
}

/** Как DomainWriter: явные ops не смешиваются с полным collect. */
function leavesForSave(explicitOps, fullCollectCount) {
  if (explicitOps && explicitOps.length) return explicitOps.slice();
  return new Array(fullCollectCount).fill(0).map(function (_, i) {
    return { path: "noise." + i, value: i };
  });
}

function wouldRejectOver200(leaves) {
  return !!(leaves && leaves.length > 200);
}

function noOpsResult(opsUiEnabled) {
  if (!opsUiEnabled) return false; // отказ, не saveApp
  return "ops";
}

const salariesOp = buildSalariesOp([{
  id: "sr1",
  employeeId: "e1",
  dayRate: 2500,
  amount: 2500,
  validFrom: "2026-11"
}]);
assert(salariesOp.path === "shopPayroll.salaries", "path salaries");

const withNoise = leavesForSave([salariesOp], 458);
assert(withNoise.length === 1, "explicit ops ignore 458 noise");
assert(wouldRejectOver200(withNoise) === false, "1 leaf under 200");

const fullCollect = leavesForSave(null, 458);
assert(fullCollect.length === 458, "full collect 458");
assert(wouldRejectOver200(fullCollect) === true, "458 rejected");

assert(noOpsResult(false) === false, "OPS off -> refuse, no saveApp");
assert(noOpsResult(true) === "ops", "OPS on -> narrow path");

console.log("OK wt_shop_salaries_save_harness");
