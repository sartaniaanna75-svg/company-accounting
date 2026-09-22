/* Финконтроль: ежедневный учёт + недельная сверка (чт→ср). Расширяет существующий financeRec. */
let frUi = {
    areaId: "",
    weekIso: "",
    inner: "work",
    workTab: "income",
    opKind: "all",
    sourceId: "",
    catId: "",
    date: "",
    q: "",
    importSourceId: ""
};

const FR_ACCOUNT_TYPES = [
    { id: "cash", label: "Касса" },
    { id: "bank", label: "Расчётный счёт" },
    { id: "card", label: "Карта" },
    { id: "in_transit", label: "Деньги в пути" },
    { id: "other", label: "Вклад/прочее" }
];

function isFinanceRecMode() {
    return isSummary() && currentEntry === "financeRec";
}

function isFrNavSection(id) {
    return id === "financeRec" || id === "financeRec-settings";
}

function frPermRank(value) {
    if (value === "setup") return 5;
    if (value === "close") return 4;
    if (value === "check") return 3;
    if (value === "fill" || value === "edit") return 2;
    if (value === "view") return 1;
    return 0;
}

function frUserLevel() {
    if (typeof isAppAdmin === "function" && isAppAdmin()) return "setup";
    const u = typeof currentAuthUser === "function" ? currentAuthUser() : null;
    const setup = (u && u.permissions && u.permissions.financeRecSetup) || "none";
    if (frPermRank(setup) > 0 || setup === "edit" || setup === "view") return "setup";
    const v = (u && u.permissions && u.permissions.financeRec) || "none";
    return v;
}

function frCan(min) {
    try {
        if (typeof isAppAdmin === "function" && isAppAdmin()) return true;
        return frPermRank(frUserLevel()) >= frPermRank(min || "view");
    } catch (err) {
        console.error(err);
        return false;
    }
}

function frMoney(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "0,00 ₽";
    return (typeof money2 === "function" ? money2(x) : x.toFixed(2) + " ₽");
}

function frNum(v) {
    const n = Number(String(v == null ? "" : v).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? Math.round(n * 100) / 100 : 0;
}

function frRound(n) {
    return Math.round(frNum(n) * 100) / 100;
}

function frFmtDate(iso) {
    return typeof mgmtFmtRu === "function" ? mgmtFmtRu(iso) : String(iso || "");
}

function frShiftIso(iso, days) {
    const d = typeof mgmtParseIso === "function" ? mgmtParseIso(iso) : new Date(String(iso) + "T12:00:00");
    if (!d || isNaN(d.getTime())) return iso;
    d.setDate(d.getDate() + Number(days || 0));
    return typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(d) : iso;
}

function ensureFinanceRec() {
    if (!appData || typeof appData !== "object") return null;
    if (!appData.financeRec || typeof appData.financeRec !== "object") {
        appData.financeRec = {};
    }
    const root = appData.financeRec;
    if (!Array.isArray(root.areas)) {
        root.areas = [
            { finance_area_id: "farea_shop", name: "Магазин Володарского", sort_order: 10, is_active: true },
            { finance_area_id: "farea_territory", name: "Территория", sort_order: 20, is_active: true }
        ];
    }
    if (!Array.isArray(root.sources)) root.sources = [];
    if (!Array.isArray(root.categories)) root.categories = [];
    if (!Array.isArray(root.operations)) root.operations = [];
    if (!Array.isArray(root.openings)) root.openings = [];
    if (!Array.isArray(root.day_closings)) root.day_closings = [];
    if (!Array.isArray(root.period_states)) root.period_states = [];
    if (!Array.isArray(root.formulas)) root.formulas = [];
    if (!Array.isArray(root.audit)) root.audit = [];
    if (!Array.isArray(root.import_batches)) root.import_batches = [];
    root.areas.forEach(function (a, i) {
        if (!a || typeof a !== "object") return;
        if (!a.finance_area_id) a.finance_area_id = "farea_" + (i + 1);
        if (a.is_active == null) a.is_active = true;
        if (a.sort_order == null) a.sort_order = (i + 1) * 10;
        if (a.primary_cash_source_id == null) a.primary_cash_source_id = "";
    });
    root.sources.forEach(function (s, i) {
        if (!s || typeof s !== "object") return;
        if (!s.finance_source_id) s.finance_source_id = "fsrc_" + (i + 1);
        if (!s.account_type || !FR_ACCOUNT_TYPES.some(function (t) { return t.id === s.account_type; })) {
            s.account_type = "cash";
        }
        if (s.is_active == null) s.is_active = true;
        if (s.sort_order == null) s.sort_order = (i + 1) * 10;
    });
    root.operations.forEach(function (o) {
        if (!o || typeof o !== "object") return;
        if (!o.movement_kind) {
            o.movement_kind = o.type === "in" ? "income" : "expense";
        }
        if (o.movement_kind === "transfer") {
            o.type = "transfer";
            if (!o.finance_source_to_id) o.finance_source_to_id = "";
        } else if (o.movement_kind === "income" || o.movement_kind === "non_income") {
            o.type = "in";
        } else {
            o.type = "out";
            o.movement_kind = "expense";
        }
        if (!o.import_fingerprint) o.import_fingerprint = "";
        if (!o.import_batch_id) o.import_batch_id = "";
    });
    root.areas.forEach(function (a) {
        if (!a) return;
        if (!root.formulas.some(function (f) { return f && f.finance_area_id === a.finance_area_id; })) {
            root.formulas.push({
                finance_area_id: a.finance_area_id,
                terms: [
                    { metric_id: "finm_income", sign: "+" },
                    { metric_id: "finm_non_income", sign: "+" },
                    { metric_id: "finm_outflow", sign: "-" }
                ]
            });
        }
        const cashList = (root.sources || []).filter(function (s) {
            return s && s.finance_area_id === a.finance_area_id && s.account_type === "cash" && s.is_active !== false;
        });
        if (a.primary_cash_source_id) {
            const ok = cashList.some(function (s) { return s.finance_source_id === a.primary_cash_source_id; });
            if (!ok) a.primary_cash_source_id = cashList[0] ? cashList[0].finance_source_id : "";
        } else if (cashList.length) {
            a.primary_cash_source_id = cashList[0].finance_source_id;
        }
    });
    return root;
}

function frAccountTypeLabel(typeId) {
    const hit = FR_ACCOUNT_TYPES.find(function (t) { return t.id === typeId; });
    return hit ? hit.label : "Счёт";
}

function frPrimaryCashId(areaId) {
    const area = ((appData.financeRec && appData.financeRec.areas) || []).find(function (a) {
        return a && a.finance_area_id === areaId;
    });
    if (!area) return "";
    if (area.primary_cash_source_id) {
        const src = frSources(areaId, true).find(function (s) {
            return s.finance_source_id === area.primary_cash_source_id;
        });
        if (src && src.account_type === "cash" && src.is_active !== false) return src.finance_source_id;
    }
    const firstCash = frSources(areaId, false).find(function (s) { return s.account_type === "cash"; });
    return firstCash ? firstCash.finance_source_id : "";
}

function frSetPrimaryCash(sourceId) {
    if (!frCan("setup")) return;
    const area = frActiveArea();
    if (!area) return;
    const src = frSources(area.finance_area_id, true).find(function (s) {
        return s.finance_source_id === sourceId;
    });
    if (!src || src.account_type !== "cash") {
        toast("Основной можно назначить только счёт типа «Касса».", "error");
        return;
    }
    area.primary_cash_source_id = sourceId;
    frAudit("primary_cash", { finance_source_id: sourceId, detail: src.name || "" });
    saveApp();
    renderFinanceRec();
    toast("Основная касса сохранена", "success");
}

function frAllowedAreaIds() {
    ensureFinanceRec();
    const all = (appData.financeRec.areas || []).filter(function (a) { return a && a.is_active !== false; });
    if (typeof isAppAdmin === "function" && isAppAdmin()) return all.map(function (a) { return a.finance_area_id; });
    const u = typeof currentAuthUser === "function" ? currentAuthUser() : null;
    const spec = (u && u.permissions && u.permissions.financeRecAreas) || "both";
    if (spec === "shop") return all.filter(function (a) { return a.finance_area_id === "farea_shop"; }).map(function (a) { return a.finance_area_id; });
    if (spec === "territory") return all.filter(function (a) { return a.finance_area_id === "farea_territory"; }).map(function (a) { return a.finance_area_id; });
    return all.map(function (a) { return a.finance_area_id; });
}

function frAreas() {
    const allow = frAllowedAreaIds();
    return ((appData.financeRec && appData.financeRec.areas) || []).filter(function (a) {
        return a && allow.indexOf(a.finance_area_id) !== -1;
    }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); });
}

function frActiveArea() {
    const list = frAreas();
    if (!list.length) return null;
    if (!frUi.areaId || !list.some(function (a) { return a.finance_area_id === frUi.areaId; })) {
        frUi.areaId = list[0].finance_area_id;
    }
    return list.find(function (a) { return a.finance_area_id === frUi.areaId; }) || list[0];
}

function frWeek(iso) {
    const src = iso || frUi.weekIso || (typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(new Date()) : "");
    return typeof mgmtSpuReportingWeek === "function" ? mgmtSpuReportingWeek(src) : { start_date: src, end_date: src, period_key: "week:" + src };
}

function frEnsureWeek() {
    const w = frWeek(frUi.weekIso);
    frUi.weekIso = w.start_date;
    return w;
}

function frWeekDates(w) {
    w = w || frEnsureWeek();
    const out = [];
    let d = w.start_date;
    for (let i = 0; i < 7; i++) {
        out.push(d);
        d = frShiftIso(d, 1);
    }
    return out;
}

function frSources(areaId, includeInactive) {
    const aid = areaId || (frActiveArea() && frActiveArea().finance_area_id);
    return ((appData.financeRec && appData.financeRec.sources) || []).filter(function (s) {
        return s && s.finance_area_id === aid && (includeInactive || s.is_active !== false);
    }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); });
}

function frCats(areaId, includeInactive) {
    const aid = areaId || (frActiveArea() && frActiveArea().finance_area_id);
    return ((appData.financeRec && appData.financeRec.categories) || []).filter(function (s) {
        return s && s.finance_area_id === aid && (includeInactive || s.is_active !== false);
    }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); });
}

function frPeriodState(areaId, periodKey) {
    ensureFinanceRec();
    let row = (appData.financeRec.period_states || []).find(function (p) {
        return p && p.finance_area_id === areaId && p.period_key === periodKey;
    });
    if (!row) {
        const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
        row = {
            finance_period_id: nextPrefixedId("fper", (appData.financeRec.period_states || []).map(function (x) { return x && x.finance_period_id; })),
            finance_area_id: areaId,
            period_key: periodKey,
            start_date: w.start_date,
            end_date: w.end_date,
            status: "draft"
        };
        appData.financeRec.period_states.push(row);
    }
    return row;
}

function frIsClosed(areaId, periodKey) {
    const st = frPeriodState(areaId, periodKey);
    return st && st.status === "closed";
}

function frCanEditData() {
    if (!frCan("fill")) return false;
    const a = frActiveArea();
    const w = frEnsureWeek();
    if (!a) return false;
    if (frIsClosed(a.finance_area_id, w.period_key) && !frCan("setup")) return false;
    return true;
}

function frOpeningRow(areaId, periodKey, sourceId) {
    ensureFinanceRec();
    return (appData.financeRec.openings || []).find(function (o) {
        return o && o.finance_area_id === areaId && o.period_key === periodKey && o.finance_source_id === sourceId;
    }) || null;
}

function frOpeningValue(areaId, periodKey, sourceId) {
    const row = frOpeningRow(areaId, periodKey, sourceId);
    return row ? frNum(row.value) : 0;
}

function frPrevPeriodKey(periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const prev = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, -1) : null;
    return prev ? prev.period_key : "";
}

function frOpKind(o) {
    if (!o) return "expense";
    if (o.movement_kind === "transfer" || o.type === "transfer") return "transfer";
    if (o.movement_kind === "income" || o.movement_kind === "non_income" || o.movement_kind === "expense") return o.movement_kind;
    return o.type === "in" ? "income" : "expense";
}

function frOps(areaId, periodKey) {
    return ((appData.financeRec && appData.financeRec.operations) || []).filter(function (o) {
        return o && !o.is_deleted && o.finance_area_id === areaId && o.period_key === periodKey;
    });
}

function frOpsForDate(areaId, periodKey, date, kind) {
    return frOps(areaId, periodKey).filter(function (o) {
        if (date && o.date !== date) return false;
        if (!kind) return true;
        if (kind === "transfer") return frOpKind(o) === "transfer";
        return frOpKind(o) === kind;
    });
}

function frDayClosingRow(areaId, date, sourceId) {
    ensureFinanceRec();
    return (appData.financeRec.day_closings || []).find(function (r) {
        return r && r.finance_area_id === areaId && r.date === date && r.finance_source_id === sourceId;
    }) || null;
}

function frDayActual(areaId, date, sourceId) {
    const row = frDayClosingRow(areaId, date, sourceId);
    if (!row || row.actual_closing == null || row.actual_closing === "") return null;
    return frNum(row.actual_closing);
}

function frDayMovements(areaId, periodKey, date, sourceId) {
    let income = 0, nonIncome = 0, expense = 0, transferIn = 0, transferOut = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        if (o.date !== date) return;
        const amt = frNum(o.amount);
        if (!(amt > 0)) return;
        const kind = frOpKind(o);
        if (kind === "transfer") {
            if (o.finance_source_id === sourceId) transferOut += amt;
            if (o.finance_source_to_id === sourceId) transferIn += amt;
            return;
        }
        if (o.finance_source_id !== sourceId) return;
        if (kind === "income") income += amt;
        else if (kind === "non_income") nonIncome += amt;
        else expense += amt;
    });
    return {
        income: frRound(income),
        non_income: frRound(nonIncome),
        expense: frRound(expense),
        transfer_in: frRound(transferIn),
        transfer_out: frRound(transferOut)
    };
}

function frDayOpening(areaId, date, sourceId) {
    const curWeek = frWeek(date);
    if (date === curWeek.start_date) {
        return frOpeningValue(areaId, curWeek.period_key, sourceId);
    }
    const prev = frShiftIso(date, -1);
    if (!prev || prev === date) {
        return frOpeningValue(areaId, curWeek.period_key, sourceId);
    }
    /* Конечный расчётный остаток предыдущего дня = начало текущего; факт не обязателен. */
    const prevActual = frDayActual(areaId, prev, sourceId);
    if (prevActual != null) return prevActual;
    const prevWeek = frWeek(prev);
    return frDayCalculated(areaId, prevWeek.period_key, prev, sourceId).calculated;
}

function frDayCalculated(areaId, periodKey, date, sourceId) {
    const opening = frDayOpening(areaId, date, sourceId);
    const mv = frDayMovements(areaId, periodKey, date, sourceId);
    const calculated = frRound(
        opening + mv.income + mv.non_income + mv.transfer_in - mv.expense - mv.transfer_out
    );
    const actual = frDayActual(areaId, date, sourceId);
    const discrepancy = actual == null ? null : frRound(actual - calculated);
    return {
        opening: opening,
        income: mv.income,
        non_income: mv.non_income,
        expense: mv.expense,
        transfer_in: mv.transfer_in,
        transfer_out: mv.transfer_out,
        calculated: calculated,
        actual: actual,
        discrepancy: discrepancy,
        status: actual == null ? "calc" : (Math.abs(discrepancy) < 0.005 ? "ok" : "bad")
    };
}

function frCarryIfNeeded(areaId, periodKey) {
    const sources = frSources(areaId, true);
    const prevKey = frPrevPeriodKey(periodKey);
    if (!prevKey) return;
    const prevW = frWeek(String(prevKey).replace(/^week:/, ""));
    sources.forEach(function (s) {
        const sid = s.finance_source_id;
        let row = frOpeningRow(areaId, periodKey, sid);
        if (row && row.is_manual) return;
        const wedActual = frDayActual(areaId, prevW.end_date, sid);
        const value = wedActual != null
            ? wedActual
            : frDayCalculated(areaId, prevKey, prevW.end_date, sid).calculated;
        if (!row) {
            ensureFinanceRec();
            row = {
                finance_area_id: areaId,
                period_key: periodKey,
                finance_source_id: sid,
                is_manual: false,
                carried: true
            };
            appData.financeRec.openings.push(row);
        }
        row.value = value;
        row.carried = true;
        row.is_manual = false;
    });
}

function frSourceTotals(areaId, periodKey, sourceId) {
    const opening = frOpeningValue(areaId, periodKey, sourceId);
    let income = 0, nonIncome = 0, expense = 0, transferIn = 0, transferOut = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        const amt = frNum(o.amount);
        if (!(amt > 0)) return;
        const kind = frOpKind(o);
        if (kind === "transfer") {
            if (o.finance_source_id === sourceId) transferOut += amt;
            if (o.finance_source_to_id === sourceId) transferIn += amt;
            return;
        }
        if (o.finance_source_id !== sourceId) return;
        if (kind === "income") income += amt;
        else if (kind === "non_income") nonIncome += amt;
        else expense += amt;
    });
    income = frRound(income);
    nonIncome = frRound(nonIncome);
    expense = frRound(expense);
    transferIn = frRound(transferIn);
    transferOut = frRound(transferOut);
    const inflow = frRound(income + nonIncome);
    const closing = frRound(opening + income + nonIncome + transferIn - expense - transferOut);
    return {
        opening: opening,
        income: income,
        non_income: nonIncome,
        expense: expense,
        transfer_in: transferIn,
        transfer_out: transferOut,
        inflow: inflow,
        outflow: expense,
        closing: closing
    };
}

function frAreaTotals(areaId, periodKey) {
    const acc = {
        opening: 0, income: 0, non_income: 0, expense: 0,
        transfer_in: 0, transfer_out: 0, inflow: 0, outflow: 0, closing: 0
    };
    frSources(areaId, true).forEach(function (s) {
        const t = frSourceTotals(areaId, periodKey, s.finance_source_id);
        acc.opening += t.opening;
        acc.income += t.income;
        acc.non_income += t.non_income;
        acc.expense += t.expense;
        acc.transfer_in += t.transfer_in;
        acc.transfer_out += t.transfer_out;
        acc.inflow += t.inflow;
        acc.outflow += t.outflow;
        acc.closing += t.closing;
    });
    Object.keys(acc).forEach(function (k) { acc[k] = frRound(acc[k]); });
    return acc;
}

function frWeekActualClosing(areaId, periodKey) {
    /* Сумма конечных остатков всех счетов направления (включая неактивные с остатком). */
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    let sum = 0;
    frSources(areaId, true).forEach(function (s) {
        sum += frDayCalculated(areaId, periodKey, w.end_date, s.finance_source_id).calculated;
    });
    return { value: frRound(sum), hasActual: true };
}

function frHasWeekFacts(areaId, periodKey) {
    return frSources(areaId, false).length > 0;
}

function frWeekCalculated(areaId, periodKey) {
    const tot = frAreaTotals(areaId, periodKey);
    /* Расчётный остаток компании: начало + поступления − расходы (перемещения не доход/расход). */
    return frRound(tot.opening + tot.income + tot.non_income - tot.expense);
}

function frWeekDiscrepancy(areaId, periodKey) {
    const calculated = frWeekCalculated(areaId, periodKey);
    const accountsEnd = frWeekActualClosing(areaId, periodKey).value;
    return frRound(accountsEnd - calculated);
}

function frFirstBadDay(areaId, periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const days = frWeekDates(w);
    for (let i = 0; i < days.length; i++) {
        const date = days[i];
        const sources = frSources(areaId, false);
        for (let j = 0; j < sources.length; j++) {
            const row = frDayCalculated(areaId, periodKey, date, sources[j].finance_source_id);
            if (row.status === "bad") return date;
        }
    }
    return "";
}

function frMetricValue(metricId, areaId, periodKey) {
    const t = frAreaTotals(areaId, periodKey);
    if (metricId === "finm_opening") return t.opening;
    if (metricId === "finm_income") return t.income;
    if (metricId === "finm_non_income") return t.non_income;
    if (metricId === "finm_inflow") return t.inflow;
    if (metricId === "finm_outflow") return t.outflow;
    if (metricId === "finm_closing") return t.closing;
    if (metricId === "finm_net") return frRound(t.income + t.non_income - t.expense);
    if (metricId === "finm_actual_closing") return frWeekActualClosing(areaId, periodKey).value;
    if (metricId === "finm_discrepancy") return frWeekDiscrepancy(areaId, periodKey);
    return 0;
}

function financeRecReadMetric(metricId, areaId, periodKey) {
    try {
        ensureFinanceRec();
        return frMetricValue(metricId, areaId, periodKey);
    } catch (err) {
        console.error(err);
        return null;
    }
}

function frFormulaTerms(areaId) {
    const row = ((appData.financeRec && appData.financeRec.formulas) || []).find(function (f) { return f && f.finance_area_id === areaId; });
    return (row && row.terms && row.terms.length) ? row.terms : [
        { metric_id: "finm_income", sign: "+" },
        { metric_id: "finm_non_income", sign: "+" },
        { metric_id: "finm_outflow", sign: "-" }
    ];
}

function frDiscrepancy(areaId, periodKey) {
    return frWeekDiscrepancy(areaId, periodKey);
}

function frAudit(kind, extra) {
    try {
        ensureFinanceRec();
        const a = frActiveArea();
        const w = frEnsureWeek();
        appData.financeRec.audit.push(Object.assign({
            audit_id: nextPrefixedId("faud", appData.financeRec.audit.map(function (x) { return x && x.audit_id; })),
            at: new Date().toISOString(),
            user_id: typeof currentAuthUserId === "function" ? currentAuthUserId() : "",
            kind: kind,
            finance_area_id: a ? a.finance_area_id : "",
            period_key: w.period_key
        }, extra || {}));
    } catch (err) { console.error(err); }
}

function frStatusLabel(st) {
    return { draft: "Черновик", filled: "Заполнено", review: "На проверке", reconciled: "Сверено", closed: "Закрыто" }[st] || "Черновик";
}

function frShiftWeek(delta) {
    const w = frEnsureWeek();
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, delta) : w;
    frUi.weekIso = next.start_date;
    renderFinanceRec();
}

function setFrArea(id) {
    frUi.areaId = id;
    renderFinanceRec();
}

function setFrInner(tab) {
    frUi.inner = tab === "settings" ? "settings" : "work";
    currentSection = frUi.inner === "settings" ? "financeRec-settings" : "financeRec";
    renderFinanceRec();
}

function setFrWorkTab(tab) {
    const ok = { income: 1, non_income: 1, expense: 1, transfer: 1, days: 1, week: 1 };
    frUi.workTab = ok[tab] ? tab : "income";
    renderFinanceRec();
}

function renderFinanceRec() {
    try {
        ensureFinanceRec();
        const host = document.getElementById("financeRecHost");
        if (!host) return;
        if (!frCan("view")) {
            host.innerHTML = '<div class="note">Нет доступа к финансовой сверке.</div>';
            return;
        }
        const area = frActiveArea();
        const w = frEnsureWeek();
        if (!area) {
            host.innerHTML = '<div class="note">Нет доступных направлений.</div>';
            return;
        }
        frCarryIfNeeded(area.finance_area_id, w.period_key);
        const tot = frAreaTotals(area.finance_area_id, w.period_key);
        const weekCalc = frWeekCalculated(area.finance_area_id, w.period_key);
        const accountsEnd = frWeekActualClosing(area.finance_area_id, w.period_key).value;
        const disc = frWeekDiscrepancy(area.finance_area_id, w.period_key);
        const hasFacts = frHasWeekFacts(area.finance_area_id, w.period_key);
        const balanced = hasFacts && Math.abs(disc) < 0.005;
        const st = frPeriodState(area.finance_area_id, w.period_key);
        const closed = st.status === "closed";
        const canFill = frCanEditData();
        const canSetup = frCan("setup");
        const inner = (currentSection === "financeRec-settings" || frUi.inner === "settings") ? "settings" : "work";
        frUi.inner = inner;
        if (!frUi.workTab) frUi.workTab = "income";

        let html = '<div class="fr-wrap">';
        html += '<div class="fr-crumb">Финконтроль → <b>' + escapeHtml(area.name) + "</b></div>";
        html += '<div class="fr-tabs">';
        frAreas().forEach(function (a) {
            html += '<button type="button" class="metrics-tab' + (a.finance_area_id === area.finance_area_id ? " active" : "") + '" onclick="setFrArea(\'' + a.finance_area_id + "')\">"
                + escapeHtml(a.name) + "</button>";
        });
        html += "</div>";
        html += '<div class="fr-weekbar">';
        html += '<span class="fr-week-label">Рабочая неделя: '
            + escapeHtml(frFmtDate(w.start_date) + " — " + frFmtDate(w.end_date) + " (чт–ср)") + "</span>";
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeek(-1)">← Пред. неделя</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeekToCurrent()">Текущая</button>';
        html += '<button type="button" class="btn btn-secondary btn-small" onclick="frShiftWeek(1)">След. неделя →</button>';
        html += '<span class="fr-status-pill">' + escapeHtml(frStatusLabel(st.status)) + "</span>";
        html += '<button type="button" class="metrics-tab' + (inner === "work" ? " active" : "") + '" onclick="setFrInner(\'work\')">Работа</button>';
        if (canSetup) html += '<button type="button" class="metrics-tab' + (inner === "settings" ? " active" : "") + '" onclick="setFrInner(\'settings\')">Настройки</button>';
        html += "</div>";

        if (inner === "settings") {
            html += renderFrSettingsHtml(area);
            host.innerHTML = html + "</div>";
            return;
        }

        html += '<div class="fr-cards fr-cards-5">';
        html += '<div class="fr-card"><span>Начало недели</span><b>' + frMoney(tot.opening) + "</b></div>";
        html += '<div class="fr-card"><span>Доходы</span><b class="fr-in">' + frMoney(tot.income) + "</b></div>";
        html += '<div class="fr-card"><span>Внедоходовые</span><b class="fr-non">' + frMoney(tot.non_income) + "</b></div>";
        html += '<div class="fr-card"><span>Расходы</span><b class="fr-out">' + frMoney(tot.expense) + "</b></div>";
        html += '<div class="fr-card ' + (balanced ? "fr-ok" : (hasFacts ? "fr-bad" : "")) + '"><span>Расхождение</span><b>'
            + (hasFacts ? frMoney(disc) : "—") + "</b><span>"
            + (hasFacts ? (balanced ? "✓ 0 ₽ — неделя сошлась" : "⚠ Не сошлось") : "Добавьте счета") + "</span></div>";
        html += "</div>";

        html += '<div class="fr-work-tabs">';
        [
            ["income", "Доходы"],
            ["non_income", "Внедоходовые поступления"],
            ["expense", "Расходы"],
            ["transfer", "Перемещения"],
            ["days", "День"],
            ["week", "Сверка недели"]
        ].forEach(function (t) {
            html += '<button type="button" class="metrics-tab' + (frUi.workTab === t[0] ? " active" : "") + '" onclick="setFrWorkTab(\'' + t[0] + "')\">"
                + escapeHtml(t[1]) + "</button>";
        });
        html += "</div>";

        if (frUi.workTab === "income") html += renderFrMovementTab(area.finance_area_id, w, "income", canFill && !closed);
        else if (frUi.workTab === "non_income") html += renderFrMovementTab(area.finance_area_id, w, "non_income", canFill && !closed);
        else if (frUi.workTab === "expense") html += renderFrExpenseTab(area.finance_area_id, w, canFill && !closed);
        else if (frUi.workTab === "transfer") html += renderFrTransferTab(area.finance_area_id, w, canFill && !closed);
        else if (frUi.workTab === "days") html += renderFrDaysTab(area.finance_area_id, w, canFill && !closed);
        else html += renderFrWeekTab(area.finance_area_id, w, tot, weekCalc, accountsEnd, disc, balanced, st, closed, hasFacts);

        html += "</div>";
        host.innerHTML = html;
    } catch (err) {
        console.error(err);
        const host = document.getElementById("financeRecHost");
        if (host) host.innerHTML = '<div class="note">Ошибка финансовой сверки. Остальные разделы Work Time не затронуты.</div>';
    }
}

function frKindTitle(kind) {
    if (kind === "income") return "Доходы";
    if (kind === "non_income") return "Внедоходовые поступления";
    if (kind === "transfer") return "Перемещения между своими счетами";
    return "Расходы";
}

function frKindNote(kind) {
    if (kind === "income") return "Ежедневные поступления выручки / доходов. Увеличивают остаток выбранного счёта.";
    if (kind === "non_income") return "Поступления, которые увеличивают остаток денег, но НЕ являются доходом бизнеса.";
    if (kind === "transfer") return "Перемещение НЕ доход и НЕ расход: на одном счёте сумма уменьшается, на другом увеличивается.";
    return "Расходы денежных средств. Перед загрузкой Excel выберите счёт списания.";
}

function renderFrMovementTab(areaId, w, kind, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let weekSum = 0;
    let html = '<div class="fr-box"><h3>' + escapeHtml(frKindTitle(kind)) + "</h3>";
    html += '<div class="fr-muted" style="margin-bottom:10px">' + escapeHtml(frKindNote(kind)) + "</div>";
    if (!sources.length) {
        html += '<div class="note">Сначала добавьте счета и кассы во вкладке «Настройки».</div></div>';
        return html;
    }
    days.forEach(function (date) {
        const ops = frOpsForDate(areaId, w.period_key, date, kind).slice().sort(function (a, b) {
            return String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
        });
        let daySum = 0;
        ops.forEach(function (o) { daySum += frNum(o.amount); });
        daySum = frRound(daySum);
        weekSum += daySum;
        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span>";
        html += '<span class="fr-day-total">Итого за день: ' + frMoney(daySum) + "</span></div>";
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
        html += "<th>Счёт / касса</th><th class=\"fr-num\">Сумма</th><th>Комментарий</th>";
        if (canEdit) html += "<th></th>";
        html += "</tr></thead><tbody>";
        if (!ops.length) {
            html += '<tr><td colspan="' + (canEdit ? 4 : 3) + '"><div class="empty-row">Нет записей</div></td></tr>';
        }
        ops.forEach(function (o) {
            const src = sources.find(function (s) { return s.finance_source_id === o.finance_source_id; })
                || frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_id; });
            html += "<tr><td>" + escapeHtml((src && src.name) || o.finance_source_id || "—") + "</td>";
            html += '<td class="fr-num">' + frMoney(o.amount) + "</td>";
            html += '<td class="fr-comment-cell">' + escapeHtml(o.comment || "") + "</td>";
            if (canEdit) {
                html += '<td><button type="button" class="btn btn-small" onclick="openFrOpForm(\'' + o.finance_operation_id + "')\">✎</button> "
                    + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\'' + o.finance_operation_id + "')\">✕</button></td>";
            }
            html += "</tr>";
        });
        html += "</tbody></table></div>";
        if (canEdit) {
            html += '<div class="fr-inline-add">';
            html += '<select id="frAddSrc_' + kind + '_' + date + '">'
                + sources.map(function (s) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '">' + escapeHtml(s.name) + "</option>";
                }).join("") + "</select>";
            html += '<input class="fr-amt" id="frAddAmt_' + kind + '_' + date + '" placeholder="Сумма" inputmode="decimal">';
            html += '<input class="fr-com" id="frAddCom_' + kind + '_' + date + '" placeholder="Комментарий">';
            html += '<button type="button" class="btn btn-primary btn-small" onclick="frQuickAddOp(\'' + kind + "','" + date + "')\">+ Добавить</button>";
            html += "</div>";
        }
        html += "</div>";
    });
    html += '<div class="fr-day-head" style="border-radius:10px;margin-top:4px"><span>Итого за неделю</span><span class="fr-day-total">'
        + frMoney(frRound(weekSum)) + "</span></div>";
    html += "</div>";
    return html;
}

function renderFrExpenseTab(areaId, w, canEdit) {
    let html = renderFrMovementTab(areaId, w, "expense", canEdit);
    if (!canEdit) return html;
    if (!frSources(areaId, false).length) return html;
    return html.replace(
        '</h3><div class="fr-muted"',
        '</h3><div class="fr-toolbar-row">'
            + '<button type="button" class="btn btn-primary btn-small" onclick="openFrExcelImport()">Загрузить Excel</button>'
            + '<span class="fr-muted">Перед загрузкой выберите счёт списания. Для наличных — основная касса по умолчанию.</span>'
            + "</div><div class=\"fr-muted\""
    );
}

function renderFrTransferTab(areaId, w, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let weekSum = 0;
    let html = '<div class="fr-box"><h3>Перемещения между своими счетами</h3>';
    html += '<div class="fr-muted" style="margin-bottom:10px">' + escapeHtml(frKindNote("transfer")) + "</div>";
    if (sources.length < 2) {
        html += '<div class="note">Для перемещений нужно минимум два активных счёта.</div></div>';
        return html;
    }
    days.forEach(function (date) {
        const ops = frOpsForDate(areaId, w.period_key, date, "transfer").slice().sort(function (a, b) {
            return String(a.finance_operation_id || "").localeCompare(String(b.finance_operation_id || ""));
        });
        let daySum = 0;
        ops.forEach(function (o) { daySum += frNum(o.amount); });
        daySum = frRound(daySum);
        weekSum += daySum;
        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span>";
        html += '<span class="fr-day-total">Итого за день: ' + frMoney(daySum) + "</span></div>";
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
        html += "<th>Откуда</th><th>Куда</th><th class=\"fr-num\">Сумма</th><th>Комментарий</th>";
        if (canEdit) html += "<th></th>";
        html += "</tr></thead><tbody>";
        if (!ops.length) {
            html += '<tr><td colspan="' + (canEdit ? 5 : 4) + '"><div class="empty-row">Нет перемещений</div></td></tr>';
        }
        ops.forEach(function (o) {
            const from = sources.find(function (s) { return s.finance_source_id === o.finance_source_id; })
                || frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_id; });
            const to = sources.find(function (s) { return s.finance_source_id === o.finance_source_to_id; })
                || frSources(areaId, true).find(function (s) { return s.finance_source_id === o.finance_source_to_id; });
            html += "<tr><td>" + escapeHtml((from && from.name) || "—") + "</td>";
            html += "<td>" + escapeHtml((to && to.name) || "—") + "</td>";
            html += '<td class="fr-num">' + frMoney(o.amount) + "</td>";
            html += "<td>" + escapeHtml(o.comment || "") + "</td>";
            if (canEdit) {
                html += '<td><button type="button" class="btn btn-small" onclick="openFrTransferForm(\'' + o.finance_operation_id + "')\">✎</button> "
                    + '<button type="button" class="btn btn-small" onclick="frDeleteOp(\'' + o.finance_operation_id + "')\">✕</button></td>";
            }
            html += "</tr>";
        });
        html += "</tbody></table></div>";
        if (canEdit) {
            html += '<div class="fr-inline-add fr-inline-transfer">';
            html += '<select id="frTrFrom_' + date + '" title="Откуда">'
                + sources.map(function (s) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '">' + escapeHtml(s.name) + "</option>";
                }).join("") + "</select>";
            html += '<span class="fr-muted">→</span>';
            html += '<select id="frTrTo_' + date + '" title="Куда">'
                + sources.map(function (s, i) {
                    return '<option value="' + escapeAttribute(s.finance_source_id) + '"' + (i === 1 ? " selected" : "") + ">"
                        + escapeHtml(s.name) + "</option>";
                }).join("") + "</select>";
            html += '<input class="fr-amt" id="frTrAmt_' + date + '" placeholder="Сумма" inputmode="decimal">';
            html += '<input class="fr-com" id="frTrCom_' + date + '" placeholder="Комментарий">';
            html += '<button type="button" class="btn btn-primary btn-small" onclick="frQuickAddTransfer(\'' + date + "')\">+ Переместить</button>";
            html += "</div>";
        }
        html += "</div>";
    });
    html += '<div class="fr-day-head" style="border-radius:10px;margin-top:4px"><span>Итого перемещений за неделю</span><span class="fr-day-total">'
        + frMoney(frRound(weekSum)) + "</span></div>";
    if (canEdit) {
        html += '<div style="margin-top:10px"><button type="button" class="btn btn-secondary btn-small" onclick="openFrTransferForm()">+ Перемещение (форма)</button></div>';
    }
    html += "</div>";
    return html;
}

function frQuickAddTransfer(date) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const from = ((document.getElementById("frTrFrom_" + date) || {}).value || "");
    const to = ((document.getElementById("frTrTo_" + date) || {}).value || "");
    const amt = frNum((document.getElementById("frTrAmt_" + date) || {}).value);
    const comment = String(((document.getElementById("frTrCom_" + date) || {}).value) || "").trim();
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!from || !to) { toast("Выберите счета", "error"); return; }
    if (from === to) { toast("Счета «откуда» и «куда» должны отличаться", "error"); return; }
    ensureFinanceRec();
    const opWeek = frWeek(date);
    appData.financeRec.operations.push({
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
        finance_area_id: area.finance_area_id,
        period_key: opWeek.period_key || w.period_key,
        date: date,
        type: "transfer",
        movement_kind: "transfer",
        finance_category_id: "",
        finance_source_id: from,
        finance_source_to_id: to,
        amount: amt,
        comment: comment,
        is_deleted: false,
        created_at: new Date().toISOString()
    });
    frAudit("transfer_add", { finance_source_id: from, detail: to + " " + date, new_value: amt });
    saveApp();
    renderFinanceRec();
}

function frQuickAddOp(kind, date) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const srcEl = document.getElementById("frAddSrc_" + kind + "_" + date);
    const amtEl = document.getElementById("frAddAmt_" + kind + "_" + date);
    const comEl = document.getElementById("frAddCom_" + kind + "_" + date);
    const amt = frNum(amtEl && amtEl.value);
    const src = srcEl && srcEl.value;
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!src) { toast("Выберите счёт", "error"); return; }
    ensureFinanceRec();
    const movement_kind = kind === "non_income" ? "non_income" : (kind === "income" ? "income" : "expense");
    const opWeek = frWeek(date);
    appData.financeRec.operations.push({
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
        finance_area_id: area.finance_area_id,
        period_key: opWeek.period_key || w.period_key,
        date: date,
        type: movement_kind === "expense" ? "out" : "in",
        movement_kind: movement_kind,
        finance_category_id: "",
        finance_source_id: src,
        amount: amt,
        comment: String((comEl && comEl.value) || "").trim(),
        is_deleted: false,
        created_at: new Date().toISOString()
    });
    frAudit("op_add", { finance_source_id: src, new_value: amt, detail: movement_kind + " " + date });
    saveApp();
    renderFinanceRec();
}

function renderFrDaysTab(areaId, w, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let html = '<div class="fr-box"><h3>День — расчёт по каждому счёту</h3>';
    html += '<div class="fr-muted" style="margin-bottom:10px">'
        + "Остаток на начало + поступления + внедоходовые + входящие перемещения − расходы − исходящие перемещения "
        + "= <b>расчётный остаток на конец дня</b>. Конец дня автоматически становится началом следующего. "
        + "Расчётный остаток сотрудник не вводит.</div>";
    if (!sources.length) {
        html += '<div class="note">Нет активных счетов и касс.</div></div>';
        return html;
    }
    days.forEach(function (date) {
        html += '<div class="fr-day-block">';
        html += '<div class="fr-day-head"><span>' + escapeHtml(frFmtDate(date)) + "</span></div>";
        html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
        html += "<th>Счёт / касса</th><th>Тип</th>"
            + "<th class=\"fr-num\">Начало</th>"
            + "<th class=\"fr-num\">Поступления</th>"
            + "<th class=\"fr-num\">Внедоходовые</th>"
            + "<th class=\"fr-num\">Вх. перемещ.</th>"
            + "<th class=\"fr-num\">Расходы</th>"
            + "<th class=\"fr-num\">Исх. перемещ.</th>"
            + "<th class=\"fr-num\">Конец дня</th></tr></thead><tbody>";
        let dayEnd = 0;
        sources.forEach(function (s) {
            const row = frDayCalculated(areaId, w.period_key, date, s.finance_source_id);
            dayEnd += row.calculated;
            html += "<tr>";
            html += "<td>" + escapeHtml(s.name)
                + (frPrimaryCashId(areaId) === s.finance_source_id ? ' <span class="fr-badge fr-badge-ok">осн. касса</span>' : "")
                + "</td>";
            html += "<td>" + escapeHtml(frAccountTypeLabel(s.account_type)) + "</td>";
            html += '<td class="fr-num">' + frMoney(row.opening) + "</td>";
            html += '<td class="fr-num fr-in">' + frMoney(row.income) + "</td>";
            html += '<td class="fr-num fr-non">' + frMoney(row.non_income) + "</td>";
            html += '<td class="fr-num fr-in">' + frMoney(row.transfer_in) + "</td>";
            html += '<td class="fr-num fr-out">' + frMoney(row.expense) + "</td>";
            html += '<td class="fr-num fr-out">' + frMoney(row.transfer_out) + "</td>";
            html += '<td class="fr-num"><b>' + frMoney(row.calculated) + "</b></td>";
            html += "</tr>";
        });
        html += '</tbody><tfoot><tr><td colspan="8">Итого по счетам на конец дня</td>'
            + '<td class="fr-num"><b>' + frMoney(frRound(dayEnd)) + "</b></td></tr></tfoot></table></div>";
        html += "</div>";
    });
    html += "</div>";
    return html;
}

function frSaveDayActual(sourceId, date, value) {
    /* Факт остатка опционален (инвентаризация); расчётный остаток всегда считается автоматически. */
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    ensureFinanceRec();
    let row = frDayClosingRow(area.finance_area_id, date, sourceId);
    const raw = String(value == null ? "" : value).trim();
    if (!row) {
        row = {
            finance_day_closing_id: nextPrefixedId("fdc", (appData.financeRec.day_closings || []).map(function (x) { return x && x.finance_day_closing_id; })),
            finance_area_id: area.finance_area_id,
            period_key: w.period_key,
            date: date,
            finance_source_id: sourceId
        };
        appData.financeRec.day_closings.push(row);
    }
    if (raw === "") row.actual_closing = null;
    else row.actual_closing = frNum(raw);
    row.updated_at = new Date().toISOString();
    const calc = frDayCalculated(area.finance_area_id, w.period_key, date, sourceId);
    frAudit("day_actual", {
        finance_source_id: sourceId,
        detail: date,
        new_value: row.actual_closing,
        old_value: calc.calculated
    });
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, 1) : null;
    if (date === w.end_date && next) frCarryIfNeeded(area.finance_area_id, next.period_key);
    saveApp();
    renderFinanceRec();
}

function renderFrWeekTab(areaId, w, tot, weekCalc, accountsEnd, disc, balanced, st, closed, hasFacts) {
    const sources = frSources(areaId, false);
    let html = '<div class="fr-layout"><div>';
    html += '<div class="fr-box"><h3>Сверка недели (чт–ср)</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">'
        + "Расчётный остаток = начало недели + поступления − расходы. "
        + "Сравнивается с суммой конечных остатков всех счетов. Перемещения не доход и не расход.</div>";
    html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
    html += "<th>Счёт / касса</th><th>Тип</th><th class=\"fr-num\">Начало</th>"
        + "<th class=\"fr-num\">Доходы</th><th class=\"fr-num\">Внедоходовые</th>"
        + "<th class=\"fr-num\">Расходы</th><th class=\"fr-num\">Перемещения ±</th>"
        + "<th class=\"fr-num\">Конец недели</th></tr></thead><tbody>";
    sources.forEach(function (s) {
        const t = frSourceTotals(areaId, w.period_key, s.finance_source_id);
        const netTransfer = frRound(t.transfer_in - t.transfer_out);
        html += "<tr><td>" + escapeHtml(s.name)
            + (frPrimaryCashId(areaId) === s.finance_source_id ? ' <span class="fr-badge fr-badge-ok">осн.</span>' : "")
            + "</td>";
        html += "<td>" + escapeHtml(frAccountTypeLabel(s.account_type)) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.opening) + "</td>";
        html += '<td class="fr-num fr-in">' + frMoney(t.income) + "</td>";
        html += '<td class="fr-num fr-non">' + frMoney(t.non_income) + "</td>";
        html += '<td class="fr-num fr-out">' + frMoney(t.expense) + "</td>";
        html += '<td class="fr-num">' + (netTransfer >= 0 ? "+" : "") + frMoney(netTransfer) + "</td>";
        html += '<td class="fr-num"><b>' + frMoney(t.closing) + "</b></td></tr>";
    });
    html += '</tbody><tfoot><tr><td colspan="2">ИТОГО по счетам</td>';
    html += '<td class="fr-num">' + frMoney(tot.opening) + "</td>";
    html += '<td class="fr-num">' + frMoney(tot.income) + "</td>";
    html += '<td class="fr-num">' + frMoney(tot.non_income) + "</td>";
    html += '<td class="fr-num">' + frMoney(tot.expense) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(tot.transfer_in - tot.transfer_out)) + "</td>";
    html += '<td class="fr-num"><b>' + frMoney(accountsEnd) + "</b></td></tr></tfoot></table></div>";
    html += "</div></div><div>";
    html += '<div class="fr-ctrl ' + (balanced ? "fr-ok" : (hasFacts ? "fr-bad" : "")) + '"><h3>Контрольная сверка</h3>';
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>Начало недели</span><b>' + frMoney(tot.opening) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>+ Поступления (доходы)</span><b class="fr-in">' + frMoney(tot.income) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>+ Внедоходовые</span><b class="fr-non">' + frMoney(tot.non_income) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>− Расходы</span><b class="fr-out">' + frMoney(tot.expense) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between;border-top:1px solid #e5e7eb;padding-top:6px"><span>Расчётный остаток</span><b>' + frMoney(weekCalc) + "</b></div>";
    html += '<div style="margin:6px 0;display:flex;justify-content:space-between"><span>Сумма остатков счетов</span><b>' + frMoney(accountsEnd) + "</b></div>";
    html += '<div class="fr-muted" style="margin-top:8px">РАСХОЖДЕНИЕ = итог счетов − расчётный</div>';
    html += '<div class="fr-disc">' + (hasFacts ? frMoney(disc) : "—") + "</div>";
    html += "<div>" + (hasFacts ? (balanced ? "✓ 0 ₽ — неделя сошлась" : "⚠ Есть расхождение") : "Добавьте счета в настройках") + "</div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Статус периода</h3>';
    html += "<p>" + escapeHtml(frStatusLabel(st.status)) + (closed ? " · только просмотр" : "") + "</p>";
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'filled\')">Отметить заполненным</button> ';
    if (frCan("check") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'review\')">На проверку</button> ';
    if (frCan("check") && !closed && balanced) html += '<button type="button" class="btn btn-primary btn-small" onclick="frSetStatus(\'reconciled\')">Сверено</button> ';
    if (frCan("close") && !closed) html += '<button type="button" class="btn btn-primary btn-small" onclick="frClosePeriod()">Закрыть период</button> ';
    if (closed && frCan("setup")) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frReopenPeriod()">Открыть закрытый период</button>';
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Перенос остатков</h3>';
    html += '<p class="fr-muted">Расчётный остаток среды станет началом следующей недели (четверг).</p>';
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frPushCarry()">Перенести в следующую неделю</button>';
    html += "</div></div></div>";
    return html;
}

function renderFrSettingsHtml(area) {
    const primaryId = frPrimaryCashId(area.finance_area_id);
    let html = '<div class="fr-layout"><div class="fr-box"><h3>Счета и кассы</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Справочник денежных счетов. Названия задаёте вы. Можно несколько счетов одного типа.</div>';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'source\')">+ Счёт / касса</button>';
    html += '<table class="fr-table" style="margin-top:8px"><thead><tr><th>Название</th><th>Тип</th><th>Порядок</th><th>Статус</th><th>Основная касса</th><th></th></tr></thead><tbody>';
    frSources(area.finance_area_id, true).forEach(function (s) {
        const isPrimary = primaryId === s.finance_source_id;
        html += "<tr><td>" + escapeHtml(s.name) + "</td>";
        html += "<td>" + escapeHtml(frAccountTypeLabel(s.account_type)) + "</td>";
        html += "<td>" + escapeHtml(String(s.sort_order || "")) + "</td>";
        html += "<td>" + (s.is_active === false ? "Неактивен" : "Активен") + "</td>";
        html += "<td>";
        if (s.account_type === "cash" && s.is_active !== false) {
            if (isPrimary) html += '<span class="fr-badge fr-badge-ok">Основная</span>';
            else if (frCan("setup")) {
                html += '<button type="button" class="btn btn-small btn-secondary" onclick="frSetPrimaryCash(\''
                    + s.finance_source_id + "')\">Сделать основной</button>";
            }
        } else html += "—";
        html += "</td>";
        html += '<td><button type="button" class="btn btn-small" onclick="openFrDictForm(\'source\',\'' + s.finance_source_id + "')\">✎</button></td></tr>";
    });
    if (!frSources(area.finance_area_id, true).length) {
        html += '<tr><td colspan="6"><div class="empty-row">Счетов пока нет</div></td></tr>';
    }
    html += "</tbody></table></div>";
    html += '<div class="fr-box"><h3>Статьи движения (необязательно)</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Для ежедневных расходов статья не обязательна — используйте комментарий / получателя.</div>';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'cat\')">+ Статья</button>';
    html += '<table class="fr-table"><thead><tr><th>Название</th><th>Тип</th><th>Статус</th><th></th></tr></thead><tbody>';
    frCats(area.finance_area_id, true).forEach(function (s) {
        html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(s.default_type === "in" ? "Приход" : (s.default_type === "out" ? "Расход" : "Любой")) + "</td><td>"
            + (s.is_active === false ? "Неактивна" : "Активна") + '</td><td><button type="button" class="btn btn-small" onclick="openFrDictForm(\'cat\',\'' + s.finance_category_id + "')\">✎</button></td></tr>";
    });
    html += "</tbody></table></div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Входящие остатки недели</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Остаток на начало четверга по каждому счёту. Обычно переносится автоматически со среды предыдущей недели.</div>';
    html += '<table class="fr-table"><thead><tr><th>Счёт / касса</th><th class="fr-num">Остаток на начало недели</th></tr></thead><tbody>';
    const w = frEnsureWeek();
    frSources(area.finance_area_id, false).forEach(function (s) {
        const v = frOpeningValue(area.finance_area_id, w.period_key, s.finance_source_id);
        html += "<tr><td>" + escapeHtml(s.name) + '</td><td class="fr-num">';
        if (frCanEditData()) {
            html += '<input style="width:120px;text-align:right" value="' + escapeAttribute(String(v))
                + "\" onchange=\"frSaveOpening('" + s.finance_source_id + "',this.value)\">";
        } else html += frMoney(v);
        html += "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Настройка сверки (для СПУ)</h3>';
    html += '<p class="fr-muted">Показатели: finm_opening, finm_income, finm_non_income, finm_outflow, finm_closing, finm_discrepancy. Недельное расхождение = сумма остатков счетов − (начало + поступления − расходы).</p>';
    const terms = frFormulaTerms(area.finance_area_id);
    html += '<div id="frFormulaBox">';
    terms.forEach(function (t, i) {
        html += '<div class="fr-filters" data-i="' + i + '"><select class="frSign"><option value="+"' + (t.sign !== "-" ? " selected" : "") + ">+</option><option value=\"-\"" + (t.sign === "-" ? " selected" : "") + ">-</option></select>";
        html += '<select class="frMetric">'
            + [
                ["finm_opening", "Начальные остатки"],
                ["finm_income", "Доходы"],
                ["finm_non_income", "Внедоходовые"],
                ["finm_inflow", "Все поступления"],
                ["finm_outflow", "Расходы"],
                ["finm_closing", "Расчётный остаток"],
                ["finm_actual_closing", "Сумма остатков счетов"],
                ["finm_discrepancy", "Расхождение"],
                ["finm_net", "Доходы + внедоходовые − расходы"]
            ].map(function (p) {
                return '<option value="' + p[0] + '"' + (t.metric_id === p[0] ? " selected" : "") + ">" + p[1] + "</option>";
            }).join("") + "</select></div>";
    });
    html += "</div>";
    html += '<button type="button" class="btn btn-secondary btn-small" onclick="frAddFormulaTerm()">+ Показатель</button> ';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="frSaveFormula()">Сохранить формулу</button></div>';
    html += '<div class="fr-box" style="margin-top:12px"><h3>Права / доступы</h3><p class="fr-muted">Права задаются в «Сотрудники, должности и доступы». Отдельная система пользователей не создаётся.</p>';
    if (typeof isAppAdmin === "function" && isAppAdmin()) {
        html += '<button type="button" class="btn btn-secondary" onclick="showSection(\'users\')">Открыть доступы</button>';
    }
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>История изменений</h3><div class="fr-table-wrap"><table class="fr-table"><thead><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Детали</th></tr></thead><tbody>';
    const aud = (appData.financeRec.audit || []).filter(function (x) { return x && x.finance_area_id === area.finance_area_id; }).slice(-40).reverse();
    if (!aud.length) html += '<tr><td colspan="4">Пока нет записей.</td></tr>';
    aud.forEach(function (x) {
        html += "<tr><td>" + escapeHtml(String(x.at || "").replace("T", " ").slice(0, 19)) + "</td><td>" + escapeHtml(x.user_id || "") + "</td><td>"
            + escapeHtml(x.kind || "") + "</td><td>" + escapeHtml(x.detail || x.comment || "") + "</td></tr>";
    });
    html += "</tbody></table></div></div>";
    return html;
}

function frSaveOpening(sourceId, value) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const old = frOpeningValue(area.finance_area_id, w.period_key, sourceId);
    const next = frNum(value);
    let why = "";
    /* Причина нужна только при корректировке уже заданного ненулевого остатка. */
    if (Math.abs(old) >= 0.005) {
        why = prompt("Причина корректировки входящего остатка:");
        if (!String(why || "").trim()) { renderFinanceRec(); return; }
    } else {
        why = "начальный остаток";
    }
    let row = frOpeningRow(area.finance_area_id, w.period_key, sourceId);
    if (!row) {
        row = { finance_area_id: area.finance_area_id, period_key: w.period_key, finance_source_id: sourceId };
        appData.financeRec.openings.push(row);
    }
    row.value = next;
    row.is_manual = true;
    row.carried = false;
    row.updated_at = new Date().toISOString();
    frAudit("opening_edit", { old_value: old, new_value: next, finance_source_id: sourceId, comment: String(why).trim() });
    saveApp();
    renderFinanceRec();
}

function openFrOpForm(id) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    if (rec && frOpKind(rec) === "transfer") {
        openFrTransferForm(id);
        return;
    }
    const kind = rec ? frOpKind(rec) : (frUi.workTab === "non_income" ? "non_income" : (frUi.workTab === "expense" ? "expense" : "income"));
    let html = "<h3>" + (rec ? "Операция" : "Новая операция") + "</h3>";
    html += '<input type="hidden" id="frOpId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frOpDate" type="date" value="' + escapeAttribute((rec && rec.date) || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Тип движения</label><select id="frOpKind">'
        + '<option value="income"' + (kind === "income" ? " selected" : "") + ">Доход</option>"
        + '<option value="non_income"' + (kind === "non_income" ? " selected" : "") + ">Внедоходовое поступление</option>"
        + '<option value="expense"' + (kind === "expense" ? " selected" : "") + ">Расход</option>"
        + "</select></div>";
    html += '<div class="form-group"><label>Счёт / касса</label><select id="frOpSrc">' + frSources(area.finance_area_id).map(function (c) {
        return '<option value="' + c.finance_source_id + '"' + (rec && rec.finance_source_id === c.finance_source_id ? " selected" : "") + ">"
            + escapeHtml(c.name) + " (" + escapeHtml(frAccountTypeLabel(c.account_type)) + ")</option>";
    }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frOpAmt" value="' + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий / получатель</label><input id="frOpComment" value="' + escapeAttribute((rec && rec.comment) || "") + '"></div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrOp()">Сохранить</button> <button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrOp() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const id = ((document.getElementById("frOpId") || {}).value || "");
    const amt = frNum((document.getElementById("frOpAmt") || {}).value);
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    let rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    const old = rec ? rec.amount : null;
    if (!rec) {
        rec = { finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })) };
        appData.financeRec.operations.push(rec);
    }
    const movement_kind = ((document.getElementById("frOpKind") || {}).value || "expense");
    rec.finance_area_id = area.finance_area_id;
    rec.date = ((document.getElementById("frOpDate") || {}).value || w.start_date);
    const opWeek = frWeek(rec.date);
    rec.period_key = opWeek.period_key || w.period_key;
    rec.movement_kind = movement_kind === "income" || movement_kind === "non_income" ? movement_kind : "expense";
    rec.type = rec.movement_kind === "expense" ? "out" : "in";
    rec.finance_category_id = rec.finance_category_id || "";
    rec.finance_source_id = ((document.getElementById("frOpSrc") || {}).value || "");
    rec.amount = amt;
    rec.comment = String(((document.getElementById("frOpComment") || {}).value || "")).trim();
    rec.is_deleted = false;
    rec.updated_at = new Date().toISOString();
    frAudit(id ? "op_edit" : "op_add", { finance_operation_id: rec.finance_operation_id, old_value: old, new_value: amt, finance_source_id: rec.finance_source_id });
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
}

function frDeleteOp(id) {
    if (!frCanEditData()) return;
    const rec = (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; });
    if (!rec) return;
    rec.is_deleted = true;
    rec.deleted_at = new Date().toISOString();
    rec.deleted_by = typeof currentAuthUserId === "function" ? currentAuthUserId() : "";
    frAudit("op_delete", { finance_operation_id: id, old_value: rec.amount, finance_source_id: rec.finance_source_id, detail: rec.comment || "" });
    saveApp();
    renderFinanceRec();
}

function openFrDictForm(kind, id) {
    if (!frCan("setup")) return;
    const area = frActiveArea();
    const list = kind === "source" ? appData.financeRec.sources : appData.financeRec.categories;
    const rec = id ? list.find(function (x) { return x && (kind === "source" ? x.finance_source_id : x.finance_category_id) === id; }) : null;
    let html = "<h3>" + (kind === "source" ? "Счёт / касса" : "Статья движения") + "</h3>";
    html += '<input type="hidden" id="frDictKind" value="' + kind + '"><input type="hidden" id="frDictId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Название</label><input id="frDictName" value="' + escapeAttribute(rec ? rec.name : "") + '"></div>';
    if (kind === "source") {
        html += '<div class="form-group"><label>Тип</label><select id="frDictAccountType">';
        FR_ACCOUNT_TYPES.forEach(function (t) {
            html += '<option value="' + t.id + '"'
                + ((rec ? rec.account_type : "cash") === t.id ? " selected" : "") + ">"
                + escapeHtml(t.label) + "</option>";
        });
        html += "</select></div>";
    }
    html += '<div class="form-group"><label>Порядок</label><input id="frDictOrder" type="number" value="' + escapeAttribute(String(rec ? rec.sort_order : 10)) + '"></div>';
    if (kind === "cat") {
        html += '<div class="form-group"><label>Тип по умолчанию</label><select id="frDictType"><option value="any">Любой</option><option value="in"' + (rec && rec.default_type === "in" ? " selected" : "") + ">Приход</option><option value=\"out\"" + (rec && rec.default_type === "out" ? " selected" : "") + ">Расход</option></select></div>";
    }
    html += '<div class="form-group"><label>Статус</label><select id="frDictActive"><option value="1"' + (!rec || rec.is_active !== false ? " selected" : "") + ">Активен</option><option value=\"0\"" + (rec && rec.is_active === false ? " selected" : "") + ">Неактивен</option></select></div>";
    html += '<div class="note">Запись с историей не удаляется физически — только деактивируется. ID стабильный.</div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrDict()">Сохранить</button> <button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrDict() {
    if (!frCan("setup")) return;
    const kind = ((document.getElementById("frDictKind") || {}).value || "source");
    const id = ((document.getElementById("frDictId") || {}).value || "");
    const name = String((document.getElementById("frDictName") || {}).value || "").replace(/\s+/g, " ").trim();
    if (!name) { toast("Укажите название", "error"); return; }
    const area = frActiveArea();
    const list = kind === "source" ? appData.financeRec.sources : appData.financeRec.categories;
    const key = kind === "source" ? "finance_source_id" : "finance_category_id";
    const prefix = kind === "source" ? "fsrc" : "fcat";
    let rec = id ? list.find(function (x) { return x && x[key] === id; }) : null;
    if (!rec) {
        rec = { finance_area_id: area.finance_area_id };
        rec[key] = nextPrefixedId(prefix, list.map(function (x) { return x && x[key]; }));
        list.push(rec);
    }
    rec.name = name;
    rec.sort_order = Number((document.getElementById("frDictOrder") || {}).value || 10);
    rec.is_active = ((document.getElementById("frDictActive") || {}).value || "1") !== "0";
    if (kind === "source") {
        const typeId = ((document.getElementById("frDictAccountType") || {}).value || "cash");
        rec.account_type = FR_ACCOUNT_TYPES.some(function (t) { return t.id === typeId; }) ? typeId : "cash";
        if (rec.account_type === "cash" && !area.primary_cash_source_id) {
            area.primary_cash_source_id = rec.finance_source_id;
        }
        if (area.primary_cash_source_id === rec.finance_source_id && (rec.account_type !== "cash" || rec.is_active === false)) {
            area.primary_cash_source_id = "";
            const other = frSources(area.finance_area_id, false).find(function (s) {
                return s.finance_source_id !== rec.finance_source_id && s.account_type === "cash";
            });
            if (other) area.primary_cash_source_id = other.finance_source_id;
        }
    }
    if (kind === "cat") rec.default_type = ((document.getElementById("frDictType") || {}).value || "any");
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
    toast("Сохранено", "success");
}

function frAddFormulaTerm() {
    const box = document.getElementById("frFormulaBox");
    if (!box) return;
    const div = document.createElement("div");
    div.className = "fr-filters";
    div.innerHTML = '<select class="frSign"><option value="+">+</option><option value="-">-</option></select>'
        + '<select class="frMetric">'
        + '<option value="finm_opening">Начальные остатки</option>'
        + '<option value="finm_income">Доходы</option>'
        + '<option value="finm_non_income">Внедоходовые</option>'
        + '<option value="finm_inflow">Все поступления</option>'
        + '<option value="finm_outflow">Расходы</option>'
        + '<option value="finm_closing">Расчётный остаток</option>'
        + '<option value="finm_actual_closing">Фактический остаток</option>'
        + '<option value="finm_discrepancy">Расхождение</option>'
        + '<option value="finm_net">Доходы + внедоходовые − расходы</option>'
        + "</select>";
    box.appendChild(div);
}

function frSaveFormula() {
    if (!frCan("setup")) return;
    const area = frActiveArea();
    const box = document.getElementById("frFormulaBox");
    if (!box) return;
    const terms = [];
    box.querySelectorAll(".fr-filters").forEach(function (row) {
        const sign = (row.querySelector(".frSign") || {}).value || "+";
        const metric = (row.querySelector(".frMetric") || {}).value || "finm_income";
        terms.push({ metric_id: metric, sign: sign === "-" ? "-" : "+" });
    });
    let row = (appData.financeRec.formulas || []).find(function (f) { return f && f.finance_area_id === area.finance_area_id; });
    if (!row) { row = { finance_area_id: area.finance_area_id }; appData.financeRec.formulas.push(row); }
    row.terms = terms;
    saveApp();
    toast("Формула сверки сохранена", "success");
}

function frSetStatus(status) {
    if (status === "filled" && !frCan("fill")) return;
    if ((status === "review" || status === "reconciled") && !frCan("check")) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const st = frPeriodState(area.finance_area_id, w.period_key);
    if (st.status === "closed" && !frCan("setup")) return;
    const old = st.status;
    st.status = status;
    frAudit("status", { old_value: old, new_value: status });
    saveApp();
    renderFinanceRec();
}

function frClosePeriod() {
    const area = frActiveArea();
    const w = frEnsureWeek();
    const disc = frWeekDiscrepancy(area.finance_area_id, w.period_key);
    if (Math.abs(disc) >= 0.005) {
        if (!frCan("setup")) {
            toast("Период не может быть закрыт. Контрольная сверка не равна 0.", "error");
            return;
        }
        const why = prompt("Принудительное закрытие. Укажите причину:");
        if (!String(why || "").trim()) return;
        frAudit("force_close", { comment: why, old_value: disc });
    }
    if (!frCan("close") && !frCan("setup")) return;
    const st = frPeriodState(area.finance_area_id, w.period_key);
    st.status = "closed";
    st.closed_at = new Date().toISOString();
    st.closed_by = typeof currentAuthUserId === "function" ? currentAuthUserId() : "";
    frPushCarry(true);
    frAudit("close", { new_value: "closed" });
    saveApp();
    renderFinanceRec();
    toast("Период закрыт.");
}

function frReopenPeriod() {
    if (!frCan("setup")) return;
    const why = prompt("Причина открытия закрытого периода:");
    if (!String(why || "").trim()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const st = frPeriodState(area.finance_area_id, w.period_key);
    st.status = "review";
    frAudit("reopen", { comment: why });
    saveApp();
    renderFinanceRec();
}

function frPushCarry(silent) {
    const area = frActiveArea();
    const w = frEnsureWeek();
    const next = typeof mgmtSpuShiftReportingWeek === "function" ? mgmtSpuShiftReportingWeek(w.start_date, 1) : null;
    if (!next) return;
    frSources(area.finance_area_id, true).forEach(function (s) {
        const sid = s.finance_source_id;
        const wed = frDayCalculated(area.finance_area_id, w.period_key, w.end_date, sid);
        const value = wed.actual != null ? wed.actual : wed.calculated;
        let row = frOpeningRow(area.finance_area_id, next.period_key, sid);
        if (!row) {
            row = { finance_area_id: area.finance_area_id, period_key: next.period_key, finance_source_id: sid };
            appData.financeRec.openings.push(row);
        }
        if (row.is_manual && !silent) return;
        row.value = value;
        row.carried = true;
        row.is_manual = false;
    });
    if (!silent) {
        saveApp();
        toast("Остатки перенесены на следующую неделю.");
        renderFinanceRec();
    }
}

function frShiftWeekToCurrent() {
    const iso = typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(new Date()) : frUi.weekIso;
    frUi.weekIso = frWeek(iso).start_date;
    renderFinanceRec();
}

function openFrTransferForm(id) {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const sources = frSources(area.finance_area_id, false);
    if (sources.length < 2) {
        toast("Нужно минимум два активных счёта", "error");
        return;
    }
    const rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    let html = "<h3>" + (rec ? "Перемещение" : "Новое перемещение") + "</h3>";
    html += '<input type="hidden" id="frTrId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frTrDate" type="date" value="'
        + escapeAttribute((rec && rec.date) || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Откуда</label><select id="frTrFromModal">'
        + sources.map(function (s) {
            return '<option value="' + s.finance_source_id + '"'
                + (rec && rec.finance_source_id === s.finance_source_id ? " selected" : "") + ">"
                + escapeHtml(s.name) + "</option>";
        }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Куда</label><select id="frTrToModal">'
        + sources.map(function (s, i) {
            const sel = rec ? rec.finance_source_to_id === s.finance_source_id : i === 1;
            return '<option value="' + s.finance_source_id + '"' + (sel ? " selected" : "") + ">"
                + escapeHtml(s.name) + "</option>";
        }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frTrAmtModal" value="'
        + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frTrComModal" value="'
        + escapeAttribute((rec && rec.comment) || "") + '"></div>';
    html += '<div class="note">Перемещение не является доходом или расходом компании.</div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="saveFrTransfer()">Сохранить</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function saveFrTransfer() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const id = ((document.getElementById("frTrId") || {}).value || "");
    const from = ((document.getElementById("frTrFromModal") || {}).value || "");
    const to = ((document.getElementById("frTrToModal") || {}).value || "");
    const amt = frNum((document.getElementById("frTrAmtModal") || {}).value);
    const date = ((document.getElementById("frTrDate") || {}).value || w.start_date);
    const comment = String(((document.getElementById("frTrComModal") || {}).value) || "").trim();
    if (!(amt > 0)) { toast("Укажите сумму", "error"); return; }
    if (!from || !to || from === to) { toast("Выберите разные счета «откуда» и «куда»", "error"); return; }
    ensureFinanceRec();
    let rec = id ? (appData.financeRec.operations || []).find(function (o) { return o && o.finance_operation_id === id; }) : null;
    if (!rec) {
        rec = { finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })) };
        appData.financeRec.operations.push(rec);
    }
    const opWeek = frWeek(date);
    rec.finance_area_id = area.finance_area_id;
    rec.period_key = opWeek.period_key || w.period_key;
    rec.date = date;
    rec.type = "transfer";
    rec.movement_kind = "transfer";
    rec.finance_source_id = from;
    rec.finance_source_to_id = to;
    rec.finance_category_id = "";
    rec.amount = amt;
    rec.comment = comment;
    rec.is_deleted = false;
    rec.updated_at = new Date().toISOString();
    frAudit(id ? "transfer_edit" : "transfer_add", { finance_source_id: from, detail: to, new_value: amt });
    saveApp();
    closeMgmtModal();
    renderFinanceRec();
}

function frHashStr(s) {
    let h = 2166136261;
    const str = String(s || "");
    for (let i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16);
}

function frExpenseFingerprint(areaId, sourceId, date, amount, payee) {
    return frHashStr([
        areaId,
        sourceId,
        String(date || ""),
        frRound(amount).toFixed(2),
        String(payee || "").replace(/\s+/g, " ").trim().toLowerCase()
    ].join("|"));
}

function frParseExpenseDate(raw) {
    const s = String(raw == null ? "" : raw).trim();
    if (!s) return "";
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return m[1] + "-" + m[2] + "-" + m[3];
    m = s.match(/^(\d{1,2})[./](\d{1,2})[./](\d{2,4})/);
    if (m) {
        let y = Number(m[3]);
        if (y < 100) y += 2000;
        const dd = String(m[1]).padStart(2, "0");
        const mm = String(m[2]).padStart(2, "0");
        return y + "-" + mm + "-" + dd;
    }
    m = s.match(/^(\d{1,2})[./](\d{1,2})$/);
    if (m) {
        const w = frEnsureWeek();
        const y = String(w.start_date || "").slice(0, 4) || String(new Date().getFullYear());
        return y + "-" + String(m[2]).padStart(2, "0") + "-" + String(m[1]).padStart(2, "0");
    }
    if (/^\d{5}(\.\d+)?$/.test(s)) {
        /* Excel serial date */
        const serial = Math.floor(Number(s));
        const epoch = new Date(Date.UTC(1899, 11, 30));
        epoch.setUTCDate(epoch.getUTCDate() + serial);
        return typeof mgmtIsoFromDate === "function" ? mgmtIsoFromDate(epoch) : epoch.toISOString().slice(0, 10);
    }
    return "";
}

function frSplitImportLine(line) {
    const t = String(line || "");
    if (t.indexOf("\t") >= 0) return t.split("\t");
    if (t.indexOf(";") >= 0) return t.split(";");
    if (t.indexOf(",") >= 0) {
        const out = [];
        let cur = "";
        let q = false;
        for (let i = 0; i < t.length; i++) {
            const ch = t[i];
            if (ch === '"') { q = !q; continue; }
            if (ch === "," && !q) { out.push(cur); cur = ""; continue; }
            cur += ch;
        }
        out.push(cur);
        return out;
    }
    return t.split(/\s{2,}/);
}

function frParseExpenseImportText(text) {
    const lines = String(text || "").replace(/^\uFEFF/, "").split(/\r?\n/);
    const rows = [];
    lines.forEach(function (line, idx) {
        const cells = frSplitImportLine(line).map(function (c) { return String(c || "").trim(); });
        if (!cells.length || cells.every(function (c) { return !c; })) return;
        const joined = cells.join(" ").toLowerCase();
        if (idx === 0 && (/дата/.test(joined) || /date/.test(joined)) && (/сумм/.test(joined) || /amount/.test(joined))) {
            return;
        }
        let date = "";
        let amount = 0;
        let payee = "";
        let amountIdx = -1;
        for (let i = 0; i < cells.length; i++) {
            const n = frNum(cells[i]);
            const d = frParseExpenseDate(cells[i]);
            if (!date && d) { date = d; continue; }
            if (amountIdx < 0 && /^-?\d/.test(cells[i].replace(/\s/g, "")) && Number.isFinite(n) && Math.abs(n) > 0) {
                amount = Math.abs(n);
                amountIdx = i;
            }
        }
        if (!(amount > 0)) return;
        const payeeParts = [];
        cells.forEach(function (c, i) {
            if (i === amountIdx) return;
            if (frParseExpenseDate(c) === date && date) return;
            if (c) payeeParts.push(c);
        });
        payee = payeeParts.join(" ").replace(/\s+/g, " ").trim();
        if (!date) date = frEnsureWeek().start_date;
        rows.push({ date: date, amount: frRound(amount), payee: payee, raw: cells.join(" | ") });
    });
    return rows;
}

function openFrExcelImport() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const sources = frSources(area.finance_area_id, false);
    if (!sources.length) {
        toast("Сначала создайте счета в настройках", "error");
        return;
    }
    const primary = frPrimaryCashId(area.finance_area_id);
    if (!frUi.importSourceId || !sources.some(function (s) { return s.finance_source_id === frUi.importSourceId; })) {
        frUi.importSourceId = primary || sources[0].finance_source_id;
    }
    let html = "<h3>Загрузить расходы из Excel</h3>";
    html += '<div class="form-group"><label>С какого счёта оплачены расходы</label><select id="frImportSrc" onchange="frUi.importSourceId=this.value">';
    sources.forEach(function (s) {
        const mark = (primary === s.finance_source_id ? " — основная касса" : "");
        html += '<option value="' + s.finance_source_id + '"'
            + (frUi.importSourceId === s.finance_source_id ? " selected" : "") + ">"
            + escapeHtml(s.name) + " (" + escapeHtml(frAccountTypeLabel(s.account_type)) + ")"
            + escapeHtml(mark) + "</option>";
    });
    html += "</select></div>";
    html += '<div class="note">Для обычного наличного расходника выберите основную кассу (подставлена по умолчанию). '
        + "Файл: CSV / TXT / вставка из Excel. Колонки: Дата | Сумма | Получатель. Строки без суммы игнорируются.</div>";
    html += '<div class="form-group"><label>Файл</label><input type="file" id="frImportFile" accept=".csv,.txt,.tsv,.xlsx,.xls"></div>';
    html += '<div class="form-group"><label>Или вставьте таблицу из Excel (Ctrl+V)</label>'
        + '<textarea id="frImportPaste" rows="8" style="width:100%" placeholder="Дата\tСумма\tПолучатель"></textarea></div>';
    html += '<div class="toolbar"><button type="button" class="btn btn-primary" onclick="frRunExpenseImport()">Импортировать</button> '
        + '<button type="button" class="btn btn-secondary" onclick="closeMgmtModal()">Отмена</button></div>';
    openMgmtModal(html);
}

function frRunExpenseImport() {
    if (!frCanEditData()) return;
    const area = frActiveArea();
    const w = frEnsureWeek();
    const sourceId = ((document.getElementById("frImportSrc") || {}).value || frUi.importSourceId || "");
    if (!sourceId) { toast("Выберите счёт списания", "error"); return; }
    frUi.importSourceId = sourceId;
    const fileInput = document.getElementById("frImportFile");
    const file = fileInput && fileInput.files && fileInput.files[0];
    const paste = String(((document.getElementById("frImportPaste") || {}).value) || "");
    function finish(text, meta) {
        frApplyExpenseImport(text, sourceId, meta || {});
    }
    if (file) {
        const name = String(file.name || "").toLowerCase();
        const reader = new FileReader();
        reader.onload = function () {
            try {
                let text = "";
                if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
                    /* Бинарный Excel: пробуем как текст (CSV, сохранённый с расширением) + подсказка. */
                    text = String(reader.result || "");
                    if (text.indexOf("PK") === 0 || /[\x00-\x08]/.test(text.slice(0, 200))) {
                        toast("Для .xlsx сохраните лист как CSV или вставьте таблицу (Ctrl+V).", "error");
                        return;
                    }
                } else {
                    text = String(reader.result || "");
                }
                finish(text, {
                    file_name: file.name || "",
                    file_size: file.size || 0,
                    file_mtime: file.lastModified || 0
                });
            } catch (err) {
                console.error(err);
                toast("Не удалось прочитать файл", "error");
            }
        };
        reader.onerror = function () { toast("Ошибка чтения файла", "error"); };
        reader.readAsText(file, "UTF-8");
        return;
    }
    if (!paste.trim()) {
        toast("Выберите файл или вставьте данные из Excel", "error");
        return;
    }
    finish(paste, { file_name: "paste", file_size: paste.length, file_mtime: Date.now() });
}

function frApplyExpenseImport(text, sourceId, meta) {
    const area = frActiveArea();
    const w = frEnsureWeek();
    ensureFinanceRec();
    const rows = frParseExpenseImportText(text);
    if (!rows.length) {
        toast("Не найдено строк с суммой", "error");
        return;
    }
    const batchKey = frHashStr([
        area.finance_area_id,
        sourceId,
        meta.file_name || "",
        meta.file_size || 0,
        meta.file_mtime || 0,
        frHashStr(String(text || "").replace(/\s+/g, " "))
    ].join("|"));
    const existingBatch = (appData.financeRec.import_batches || []).find(function (b) {
        return b && b.batch_key === batchKey && b.finance_area_id === area.finance_area_id;
    });
    if (existingBatch) {
        toast("Этот файл (или такой же набор строк) уже импортирован — повторно не загружен.", "error");
        return;
    }
    const existingFp = {};
    (appData.financeRec.operations || []).forEach(function (o) {
        if (!o || o.is_deleted) return;
        if (o.finance_area_id !== area.finance_area_id) return;
        if (o.import_fingerprint) existingFp[o.import_fingerprint] = true;
        if (frOpKind(o) === "expense") {
            existingFp[frExpenseFingerprint(o.finance_area_id, o.finance_source_id, o.date, o.amount, o.comment)] = true;
        }
    });
    const batchId = nextPrefixedId("fimp", (appData.financeRec.import_batches || []).map(function (x) { return x && x.import_batch_id; }));
    let added = 0;
    let skippedDup = 0;
    let skippedNoAmt = 0;
    rows.forEach(function (r) {
        if (!(r.amount > 0)) { skippedNoAmt++; return; }
        const fp = frExpenseFingerprint(area.finance_area_id, sourceId, r.date, r.amount, r.payee);
        if (existingFp[fp]) { skippedDup++; return; }
        existingFp[fp] = true;
        const opWeek = frWeek(r.date);
        appData.financeRec.operations.push({
            finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
            finance_area_id: area.finance_area_id,
            period_key: opWeek.period_key || w.period_key,
            date: r.date,
            type: "out",
            movement_kind: "expense",
            finance_category_id: "",
            finance_source_id: sourceId,
            amount: r.amount,
            comment: r.payee || "",
            import_fingerprint: fp,
            import_batch_id: batchId,
            is_deleted: false,
            created_at: new Date().toISOString()
        });
        added++;
    });
    if (added > 0) {
        appData.financeRec.import_batches.push({
            import_batch_id: batchId,
            batch_key: batchKey,
            finance_area_id: area.finance_area_id,
            finance_source_id: sourceId,
            file_name: meta.file_name || "",
            rows_added: added,
            rows_dup: skippedDup,
            at: new Date().toISOString()
        });
        frAudit("expense_import", {
            finance_source_id: sourceId,
            detail: (meta.file_name || "paste") + " +" + added + " / dup " + skippedDup,
            new_value: added
        });
        saveApp();
        closeMgmtModal();
        frUi.workTab = "expense";
        renderFinanceRec();
        toast("Импортировано расходов: " + added
            + (skippedDup ? ("; пропущено дублей: " + skippedDup) : "")
            + (skippedNoAmt ? ("; без суммы: " + skippedNoAmt) : ""), "success");
    } else {
        toast(skippedDup ? "Все строки уже были загружены ранее (дубли)." : "Нечего импортировать.", "error");
    }
}

function financeRecFolderCardHtml() {
    return folderCardHtml("summary-folder", "financeRec", "Финконтроль", "Финансовая сверка поступлений, расходов и остатков", "");
}
