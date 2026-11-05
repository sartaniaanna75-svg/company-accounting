/**
 * Контракт: настройки приёмки → узкий OPS
 * shopPayroll.receivingShared / shopPayroll.sharedBudgets (по 1 leaf),
 * даже если «чужой» collect дал бы 458 листьев. Без saveApp / forcePut.
 */
function assert(c, m) { if (!c) throw new Error(m); }

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
  if (!opsUiEnabled) return false;
  return "ops";
}

const sharedOp = {
  path: "shopPayroll.receivingShared",
  value: true,
  expected_v: 0
};
const budgetOp = {
  path: "shopPayroll.sharedBudgets",
  value: { "2026-11": { metric_receiving: 5000 } },
  expected_v: 0
};

assert(sharedOp.path === "shopPayroll.receivingShared", "path receivingShared");
assert(budgetOp.path === "shopPayroll.sharedBudgets", "path sharedBudgets");

const withNoiseShared = leavesForSave([sharedOp], 458);
assert(withNoiseShared.length === 1, "explicit receivingShared ignores 458 noise");
assert(wouldRejectOver200(withNoiseShared) === false, "1 leaf under 200");

const withNoiseBudget = leavesForSave([budgetOp], 458);
assert(withNoiseBudget.length === 1, "explicit sharedBudgets ignores 458 noise");
assert(wouldRejectOver200(withNoiseBudget) === false, "budget 1 leaf under 200");

const fullCollect = leavesForSave(null, 458);
assert(fullCollect.length === 458, "full collect 458");
assert(wouldRejectOver200(fullCollect) === true, "458 rejected");

assert(noOpsResult(false) === false, "OPS off -> refuse, no saveApp");
assert(noOpsResult(true) === "ops", "OPS on -> narrow path");

console.log("OK wt_shop_receiving_save_harness");
