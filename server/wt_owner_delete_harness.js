/**
 * Клиентский контракт: удаление только при server is_owner; не все admin.
 */
"use strict";

function assert(c, m) { if (!c) throw new Error(m); }

function canOwnerDeleteEmployee(wtServerUser) {
  return !!(wtServerUser && wtServerUser.is_owner === true);
}

assert(!canOwnerDeleteEmployee(null), "no session");
assert(!canOwnerDeleteEmployee({ user_id: "user_admin", role: "admin" }), "admin without is_owner");
assert(!canOwnerDeleteEmployee({ user_id: "user_admin", role: "admin", is_owner: false }), "explicit false");
assert(!canOwnerDeleteEmployee({ user_id: "usr_1", role: "admin", is_owner: "true" }), "string true rejected");
assert(canOwnerDeleteEmployee({ user_id: "user_admin", role: "admin", is_owner: true }), "owner ok");

function rememberIsOwner(user) {
  return {
    user_id: String(user.user_id || ""),
    is_owner: user.is_owner === true
  };
}
assert(rememberIsOwner({ user_id: "a", is_owner: true }).is_owner === true, "remember true");
assert(rememberIsOwner({ user_id: "a", is_owner: 1 }).is_owner === false, "remember coerces");

/** Зеркало server is_payload_owner / employee_delete_owner_denied (без Python в PATH). */
function isPayloadOwner(envOwner, user) {
  const owner = String(envOwner || "").trim();
  if (!owner) return false;
  if (!user || user.is_active === false) return false;
  return String(user.user_id || "").trim() === owner;
}
function employeeDeleteOwnerDenied(envOwner, user, incoming) {
  const items = (incoming && incoming.employeeRemovals) || [];
  if (!items.length) return null;
  if (!isPayloadOwner(envOwner, user)) return "employee_delete_forbidden";
  return null;
}
const owner = { user_id: "user_admin", role: "admin", is_active: true };
const other = { user_id: "usr_x", role: "admin", is_active: true };
assert(!isPayloadOwner("", owner), "unset env denies");
assert(isPayloadOwner("user_admin", owner), "match owner");
assert(!isPayloadOwner("user_admin", other), "other admin denied");
assert(employeeDeleteOwnerDenied("", owner, { employeeRemovals: [{ employee_id: "E" }] }) === "employee_delete_forbidden", "put unset");
assert(employeeDeleteOwnerDenied("user_admin", other, { employeeRemovals: [{ employee_id: "E" }] }) === "employee_delete_forbidden", "put other");
assert(employeeDeleteOwnerDenied("user_admin", owner, { employeeRemovals: [{ employee_id: "E" }] }) === null, "put owner ok");
assert(employeeDeleteOwnerDenied("user_admin", owner, {}) === null, "no removals skip");

console.log("OK wt_owner_delete_harness");
