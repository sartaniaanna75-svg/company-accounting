/**
 * Аутсорсинг в зарплатных списках один раз; в табеле — нет.
 */
function includeInMonthPayroll(emp) {
  if (!emp) return false;
  if (emp.jobStatus === "fired") return false;
  return true;
}
function isOutsourcing(emp) { return !!(emp && emp.outsourcing === true); }

function payrollEmployees(ws) {
  const seen = {};
  const list = [];
  function add(e) {
    if (!e || !e.id || seen[e.id]) return;
    if (!includeInMonthPayroll(e)) return;
    seen[e.id] = true;
    list.push(e);
  }
  (ws.employeeIds || []).forEach(function (id) {
    add((ws.employees || []).find(function (e) { return e && e.id === id; }));
  });
  (ws.employees || []).forEach(add);
  return list;
}

function salaryEmployeeGroups(ws) {
  const people = [];
  const seen = {};
  function add(e) {
    if (!e || !e.id || seen[e.id]) return;
    if (!includeInMonthPayroll(e)) return;
    seen[e.id] = true;
    people.push(e);
  }
  (ws.employeeIds || []).forEach(function (id) {
    add((ws.employees || []).find(function (e) { return e && e.id === id; }));
  });
  (ws.employees || []).forEach(add);
  return people;
}

function timesheetPeople(ws) {
  return (ws.employees || []).filter(function (e) {
    if (!e || !e.id) return false;
    if (isOutsourcing(e)) return false;
    return (ws.employeeIds || []).indexOf(e.id) !== -1 || true; // card exists
  }).filter(function (e) { return !isOutsourcing(e); });
}

function uniqueOnce(officeList, generalList) {
  const seen = {};
  let n = 0;
  officeList.concat(generalList).forEach(function (e) {
    if (!e || seen[e.id]) return;
    seen[e.id] = true;
    n += 1;
  });
  return n;
}

function assert(c, m) { if (!c) throw new Error(m); }

const office = {
  code: "office",
  employeeIds: ["staff1"],
  employees: [
    { id: "staff1", name: "Штатный", jobStatus: "working" },
    { id: "out1", name: "Жан Любовь", outsourcing: true, jobStatus: "working", rate: 100000 }
  ]
};

const pay = payrollEmployees(office);
assert(pay.some(function (e) { return e.id === "out1"; }), "office payroll has outsourcing");
assert(pay.some(function (e) { return e.id === "staff1"; }), "office payroll has staff");
assert(pay.filter(function (e) { return e.id === "out1"; }).length === 1, "outsourcing once in payroll");

const gen = salaryEmployeeGroups(office);
assert(gen.some(function (e) { return e.id === "out1"; }), "general sheet has outsourcing");
assert(uniqueOnce(pay, gen) === 2, "two people unique across views");

const ts = timesheetPeople(office);
assert(!ts.some(function (e) { return e.id === "out1"; }), "not in timesheet");
assert(ts.some(function (e) { return e.id === "staff1"; }), "staff still in timesheet pool");

console.log("OK wt_outsourcing_payroll_lists_harness");
