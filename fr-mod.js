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
    q: ""
};

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
    root.areas.forEach(function (a, i) {
        if (!a || typeof a !== "object") return;
        if (!a.finance_area_id) a.finance_area_id = "farea_" + (i + 1);
        if (a.is_active == null) a.is_active = true;
        if (a.sort_order == null) a.sort_order = (i + 1) * 10;
    });
    root.operations.forEach(function (o) {
        if (!o || typeof o !== "object") return;
        if (!o.movement_kind) {
            o.movement_kind = o.type === "in" ? "income" : "expense";
        }
        if (o.movement_kind === "income" || o.movement_kind === "non_income") o.type = "in";
        else o.type = "out";
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
    });
    return root;
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
        if (kind && frOpKind(o) !== kind) return false;
        return true;
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
    let income = 0, nonIncome = 0, expense = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        if (o.date !== date || o.finance_source_id !== sourceId) return;
        const amt = frNum(o.amount);
        const kind = frOpKind(o);
        if (kind === "income") income += amt;
        else if (kind === "non_income") nonIncome += amt;
        else expense += amt;
    });
    return {
        income: frRound(income),
        non_income: frRound(nonIncome),
        expense: frRound(expense)
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
    const prevActual = frDayActual(areaId, prev, sourceId);
    if (prevActual != null) return prevActual;
    const prevWeek = frWeek(prev);
    return frDayCalculated(areaId, prevWeek.period_key, prev, sourceId).calculated;
}

function frDayCalculated(areaId, periodKey, date, sourceId) {
    const opening = frDayOpening(areaId, date, sourceId);
    const mv = frDayMovements(areaId, periodKey, date, sourceId);
    const calculated = frRound(opening + mv.income + mv.non_income - mv.expense);
    const actual = frDayActual(areaId, date, sourceId);
    const discrepancy = actual == null ? null : frRound(actual - calculated);
    return {
        opening: opening,
        income: mv.income,
        non_income: mv.non_income,
        expense: mv.expense,
        calculated: calculated,
        actual: actual,
        discrepancy: discrepancy,
        status: actual == null ? "wait" : (Math.abs(discrepancy) < 0.005 ? "ok" : "bad")
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
    let income = 0, nonIncome = 0, expense = 0;
    frOps(areaId, periodKey).forEach(function (o) {
        if (o.finance_source_id !== sourceId) return;
        const amt = frNum(o.amount);
        const kind = frOpKind(o);
        if (kind === "income") income += amt;
        else if (kind === "non_income") nonIncome += amt;
        else expense += amt;
    });
    income = frRound(income);
    nonIncome = frRound(nonIncome);
    expense = frRound(expense);
    const inflow = frRound(income + nonIncome);
    const closing = frRound(opening + income + nonIncome - expense);
    return {
        opening: opening,
        income: income,
        non_income: nonIncome,
        expense: expense,
        inflow: inflow,
        outflow: expense,
        closing: closing
    };
}

function frAreaTotals(areaId, periodKey) {
    const acc = { opening: 0, income: 0, non_income: 0, expense: 0, inflow: 0, outflow: 0, closing: 0 };
    frSources(areaId, true).forEach(function (s) {
        const t = frSourceTotals(areaId, periodKey, s.finance_source_id);
        acc.opening += t.opening;
        acc.income += t.income;
        acc.non_income += t.non_income;
        acc.expense += t.expense;
        acc.inflow += t.inflow;
        acc.outflow += t.outflow;
        acc.closing += t.closing;
    });
    Object.keys(acc).forEach(function (k) { acc[k] = frRound(acc[k]); });
    return acc;
}

function frWeekActualClosing(areaId, periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    let sum = 0;
    let any = false;
    frSources(areaId, true).forEach(function (s) {
        const a = frDayActual(areaId, w.end_date, s.finance_source_id);
        if (a == null) {
            sum += frDayCalculated(areaId, periodKey, w.end_date, s.finance_source_id).calculated;
        } else {
            any = true;
            sum += a;
        }
    });
    return { value: frRound(sum), hasActual: any };
}

function frHasWeekFacts(areaId, periodKey) {
    const w = frWeek(String(periodKey || "").replace(/^week:/, ""));
    const days = frWeekDates(w);
    const sources = frSources(areaId, true);
    for (let i = 0; i < days.length; i++) {
        for (let j = 0; j < sources.length; j++) {
            if (frDayActual(areaId, days[i], sources[j].finance_source_id) != null) return true;
        }
    }
    return false;
}

function frWeekDiscrepancy(areaId, periodKey) {
    const tot = frAreaTotals(areaId, periodKey);
    const act = frWeekActualClosing(areaId, periodKey);
    return frRound(act.value - tot.closing);
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
    const ok = { income: 1, non_income: 1, expense: 1, days: 1, week: 1 };
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
        html += '<div class="fr-card ' + (balanced ? "fr-ok" : (hasFacts ? "fr-bad" : "")) + '"><span>Расхождение недели</span><b>'
            + (hasFacts ? frMoney(disc) : "—") + "</b><span>"
            + (hasFacts ? (balanced ? "✓ Сверено" : "⚠ Есть расхождение") : "Нет факта по дням") + "</span></div>";
        html += "</div>";

        html += '<div class="fr-work-tabs">';
        [
            ["income", "Доходы"],
            ["non_income", "Внедоходовые поступления"],
            ["expense", "Расходы"],
            ["days", "Остатки по дням"],
            ["week", "Сверка недели"]
        ].forEach(function (t) {
            html += '<button type="button" class="metrics-tab' + (frUi.workTab === t[0] ? " active" : "") + '" onclick="setFrWorkTab(\'' + t[0] + "')\">"
                + escapeHtml(t[1]) + "</button>";
        });
        html += "</div>";

        if (frUi.workTab === "income") html += renderFrMovementTab(area.finance_area_id, w, "income", canFill && !closed);
        else if (frUi.workTab === "non_income") html += renderFrMovementTab(area.finance_area_id, w, "non_income", canFill && !closed);
        else if (frUi.workTab === "expense") html += renderFrMovementTab(area.finance_area_id, w, "expense", canFill && !closed);
        else if (frUi.workTab === "days") html += renderFrDaysTab(area.finance_area_id, w, canFill && !closed);
        else html += renderFrWeekTab(area.finance_area_id, w, tot, disc, balanced, st, closed, hasFacts);

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
    return "Расходы";
}

function frKindNote(kind) {
    if (kind === "income") return "Ежедневные поступления выручки / доходов. Увеличивают остаток и входят в показатель «Доходы».";
    if (kind === "non_income") return "Поступления, которые увеличивают остаток денег, но НЕ являются доходом бизнеса (возвраты, внесения, перемещения и т.п.).";
    return "Расходы денежных средств. Статья расходов не обязательна — пояснение пишите в комментарии.";
}

function renderFrMovementTab(areaId, w, kind, canEdit) {
    const sources = frSources(areaId, false);
    const days = frWeekDates(w);
    let weekSum = 0;
    let html = '<div class="fr-box"><h3>' + escapeHtml(frKindTitle(kind)) + "</h3>";
    html += '<div class="fr-muted" style="margin-bottom:10px">' + escapeHtml(frKindNote(kind)) + "</div>";
    if (!sources.length) {
        html += '<div class="note">Сначала добавьте источники денежных средств во вкладке «Настройки».</div></div>';
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
        html += "<th>Источник</th><th class=\"fr-num\">Сумма</th><th>Комментарий</th>";
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
    if (!src) { toast("Выберите источник", "error"); return; }
    ensureFinanceRec();
    const movement_kind = kind === "non_income" ? "non_income" : (kind === "income" ? "income" : "expense");
    appData.financeRec.operations.push({
        finance_operation_id: nextPrefixedId("fop", appData.financeRec.operations.map(function (x) { return x && x.finance_operation_id; })),
        finance_area_id: area.finance_area_id,
        period_key: w.period_key,
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
    let html = '<div class="fr-box"><h3>Остатки по дням</h3>';
    html += '<div class="fr-muted" style="margin-bottom:10px">Контрольный экран. Доходы и расходы берутся из вкладок. '
        + "Введите фактический остаток на конец дня — система покажет расхождение сразу.</div>";
    if (!sources.length) {
        html += '<div class="note">Нет источников денежных средств.</div></div>';
        return html;
    }
    html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
    html += "<th>Дата</th><th>Источник</th><th class=\"fr-num\">Начало</th><th class=\"fr-num\">Доходы</th>"
        + "<th class=\"fr-num\">Внедоходовые</th><th class=\"fr-num\">Расходы</th>"
        + "<th class=\"fr-num\">Расчётный</th><th class=\"fr-num\">Факт</th>"
        + "<th class=\"fr-num\">Расхождение</th><th>Статус</th></tr></thead><tbody>";
    days.forEach(function (date) {
        sources.forEach(function (s, si) {
            const row = frDayCalculated(areaId, w.period_key, date, s.finance_source_id);
            const discClass = row.status === "bad" ? "is-bad" : (row.status === "ok" ? "is-ok" : "");
            const badge = row.status === "ok"
                ? '<span class="fr-badge fr-badge-ok">Сверено</span>'
                : (row.status === "bad"
                    ? '<span class="fr-badge fr-badge-bad">Есть расхождение</span>'
                    : '<span class="fr-badge fr-badge-wait">Нет факта</span>');
            html += "<tr>";
            html += "<td>" + (si === 0 ? escapeHtml(frFmtDate(date)) : "") + "</td>";
            html += "<td>" + escapeHtml(s.name) + "</td>";
            html += '<td class="fr-num">' + frMoney(row.opening) + "</td>";
            html += '<td class="fr-num fr-in">' + frMoney(row.income) + "</td>";
            html += '<td class="fr-num fr-non">' + frMoney(row.non_income) + "</td>";
            html += '<td class="fr-num fr-out">' + frMoney(row.expense) + "</td>";
            html += '<td class="fr-num">' + frMoney(row.calculated) + "</td>";
            html += '<td class="fr-num">';
            if (canEdit) {
                html += '<input style="width:110px;text-align:right" value="'
                    + escapeAttribute(row.actual == null ? "" : String(row.actual))
                    + "\" onchange=\"frSaveDayActual('" + s.finance_source_id + "','" + date + "',this.value)\" placeholder=\"факт\">";
            } else {
                html += row.actual == null ? "—" : frMoney(row.actual);
            }
            html += "</td>";
            html += '<td class="fr-num fr-disc-cell ' + discClass + '">'
                + (row.discrepancy == null ? "—" : frMoney(row.discrepancy)) + "</td>";
            html += "<td>" + badge + "</td></tr>";
        });
    });
    html += "</tbody></table></div></div>";
    return html;
}

function frSaveDayActual(sourceId, date, value) {
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
    if (raw === "") {
        row.actual_closing = null;
    } else {
        row.actual_closing = frNum(raw);
    }
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

function renderFrWeekTab(areaId, w, tot, disc, balanced, st, closed, hasFacts) {
    const sources = frSources(areaId, false);
    const badDay = frFirstBadDay(areaId, w.period_key);
    let html = '<div class="fr-layout"><div>';
    html += '<div class="fr-box"><h3>Сверка недели (чт–ср)</h3>';
    html += '<div class="fr-table-wrap"><table class="fr-table"><thead><tr>';
    html += "<th>Источник</th><th class=\"fr-num\">Начало</th><th class=\"fr-num\">Доходы</th>"
        + "<th class=\"fr-num\">Внедоходовые</th><th class=\"fr-num\">Расходы</th>"
        + "<th class=\"fr-num\">Расчётный</th><th class=\"fr-num\">Факт (конец ср.)</th>"
        + "<th class=\"fr-num\">Расхождение</th><th>Статус</th></tr></thead><tbody>";
    const acc = { opening: 0, income: 0, non_income: 0, expense: 0, calculated: 0, actual: 0, disc: 0 };
    sources.forEach(function (s) {
        const t = frSourceTotals(areaId, w.period_key, s.finance_source_id);
        const wed = frDayCalculated(areaId, w.period_key, w.end_date, s.finance_source_id);
        /* Факт среды, иначе цепочка через фактические остатки дней (если они есть). */
        const effectiveFact = wed.actual != null ? wed.actual : wed.calculated;
        const rowDisc = frRound(effectiveFact - t.closing);
        const hasAnyFact = frWeekDates(w).some(function (d) {
            return frDayActual(areaId, d, s.finance_source_id) != null;
        });
        const ok = Math.abs(rowDisc) < 0.005;
        acc.opening += t.opening;
        acc.income += t.income;
        acc.non_income += t.non_income;
        acc.expense += t.expense;
        acc.calculated += t.closing;
        acc.actual += effectiveFact;
        acc.disc += rowDisc;
        html += "<tr><td>" + escapeHtml(s.name) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.opening) + "</td>";
        html += '<td class="fr-num fr-in">' + frMoney(t.income) + "</td>";
        html += '<td class="fr-num fr-non">' + frMoney(t.non_income) + "</td>";
        html += '<td class="fr-num fr-out">' + frMoney(t.expense) + "</td>";
        html += '<td class="fr-num">' + frMoney(t.closing) + "</td>";
        html += '<td class="fr-num">' + frMoney(effectiveFact)
            + (wed.actual == null && hasAnyFact ? ' <span class="fr-muted">(цепочка)</span>' : "") + "</td>";
        html += '<td class="fr-num fr-disc-cell ' + (ok ? "is-ok" : "is-bad") + '">' + frMoney(rowDisc) + "</td>";
        html += "<td>" + (!hasAnyFact
            ? '<span class="fr-badge fr-badge-wait">Нет факта</span>'
            : (ok ? '<span class="fr-badge fr-badge-ok">Сверено</span>' : '<span class="fr-badge fr-badge-bad">Расхождение</span>'))
            + "</td></tr>";
    });
    html += '</tbody><tfoot><tr><td>ИТОГО</td>';
    html += '<td class="fr-num">' + frMoney(frRound(acc.opening)) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(acc.income)) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(acc.non_income)) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(acc.expense)) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(acc.calculated)) + "</td>";
    html += '<td class="fr-num">' + frMoney(frRound(acc.actual)) + "</td>";
    html += '<td class="fr-num fr-disc-cell ' + (hasFacts ? (balanced ? "is-ok" : "is-bad") : "") + '">'
        + (hasFacts ? frMoney(disc) : "—") + "</td>";
    html += "<td>" + (hasFacts ? (balanced ? "Сверено" : "Есть расхождение") : "Нет факта") + "</td></tr></tfoot></table></div>";
    if (badDay) {
        html += '<div class="note" style="margin-top:8px;color:#b91c1c">Первое расхождение по дням: <b>'
            + escapeHtml(frFmtDate(badDay)) + "</b>. Откройте «Остатки по дням».</div>";
    }
    html += "</div></div><div>";
    html += '<div class="fr-ctrl ' + (balanced ? "fr-ok" : (hasFacts ? "fr-bad" : "")) + '"><h3>Сверка недели</h3>';
    html += '<div class="fr-muted">Факт конца среды (или цепочка дней) − расчётный остаток</div>';
    html += '<div class="fr-disc">' + (hasFacts ? ((disc > 0 ? "+" : "") + frMoney(disc)) : "—") + "</div>";
    html += "<div>" + (hasFacts ? (balanced ? "✓ Сверено" : "⚠ Есть расхождение") : "Нет факта по дням") + "</div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Статус периода</h3>';
    html += "<p>" + escapeHtml(frStatusLabel(st.status)) + (closed ? " · только просмотр" : "") + "</p>";
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'filled\')">Отметить заполненным</button> ';
    if (frCan("check") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frSetStatus(\'review\')">На проверку</button> ';
    if (frCan("check") && !closed && balanced) html += '<button type="button" class="btn btn-primary btn-small" onclick="frSetStatus(\'reconciled\')">Сверено</button> ';
    if (frCan("close") && !closed) html += '<button type="button" class="btn btn-primary btn-small" onclick="frClosePeriod()">Закрыть период</button> ';
    if (closed && frCan("setup")) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frReopenPeriod()">Открыть закрытый период</button>';
    html += "</div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Перенос остатков</h3>';
    html += '<p class="fr-muted">Фактический остаток среды (или расчётный, если факта нет) станет началом следующей недели (четверг).</p>';
    if (frCan("fill") && !closed) html += '<button type="button" class="btn btn-secondary btn-small" onclick="frPushCarry()">Перенести в следующую неделю</button>';
    html += "</div></div></div>";
    return html;
}

function renderFrSettingsHtml(area) {
    let html = '<div class="fr-layout"><div class="fr-box"><h3>Источники средств</h3>';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'source\')">+ Источник</button>';
    html += '<table class="fr-table"><thead><tr><th>Название</th><th>Порядок</th><th>Статус</th><th></th></tr></thead><tbody>';
    frSources(area.finance_area_id, true).forEach(function (s) {
        html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(String(s.sort_order || "")) + "</td><td>"
            + (s.is_active === false ? "Неактивен" : "Активен") + '</td><td><button type="button" class="btn btn-small" onclick="openFrDictForm(\'source\',\'' + s.finance_source_id + "')\">✎</button></td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="fr-box"><h3>Статьи движения (необязательно)</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Для ежедневных расходов статья не обязательна — используйте комментарий. Справочник оставлен для совместимости.</div>';
    html += '<button type="button" class="btn btn-primary btn-small" onclick="openFrDictForm(\'cat\')">+ Статья</button>';
    html += '<table class="fr-table"><thead><tr><th>Название</th><th>Тип</th><th>Статус</th><th></th></tr></thead><tbody>';
    frCats(area.finance_area_id, true).forEach(function (s) {
        html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(s.default_type === "in" ? "Приход" : (s.default_type === "out" ? "Расход" : "Любой")) + "</td><td>"
            + (s.is_active === false ? "Неактивна" : "Активна") + '</td><td><button type="button" class="btn btn-small" onclick="openFrDictForm(\'cat\',\'' + s.finance_category_id + "')\">✎</button></td></tr>";
    });
    html += "</tbody></table></div></div>";
    html += '<div class="fr-box" style="margin-top:12px"><h3>Входящие остатки недели</h3>';
    html += '<div class="fr-muted" style="margin-bottom:8px">Остаток на начало четверга. Обычно переносится автоматически со среды предыдущей недели.</div>';
    html += '<table class="fr-table"><thead><tr><th>Источник</th><th class="fr-num">Остаток на начало недели</th></tr></thead><tbody>';
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
    html += '<p class="fr-muted">Показатели доступны по ID: finm_opening, finm_income, finm_non_income, finm_outflow, finm_closing, finm_discrepancy. Ежедневная сверка считается отдельно: факт − расчётный остаток.</p>';
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
                ["finm_actual_closing", "Фактический остаток"],
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
    const kind = rec ? frOpKind(rec) : (frUi.workTab === "non_income" ? "non_income" : (frUi.workTab === "expense" ? "expense" : "income"));
    let html = "<h3>" + (rec ? "Операция" : "Новая операция") + "</h3>";
    html += '<input type="hidden" id="frOpId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Дата</label><input id="frOpDate" type="date" value="' + escapeAttribute((rec && rec.date) || w.start_date) + '"></div>';
    html += '<div class="form-group"><label>Тип движения</label><select id="frOpKind">'
        + '<option value="income"' + (kind === "income" ? " selected" : "") + ">Доход</option>"
        + '<option value="non_income"' + (kind === "non_income" ? " selected" : "") + ">Внедоходовое поступление</option>"
        + '<option value="expense"' + (kind === "expense" ? " selected" : "") + ">Расход</option>"
        + "</select></div>";
    html += '<div class="form-group"><label>Источник</label><select id="frOpSrc">' + frSources(area.finance_area_id).map(function (c) {
        return '<option value="' + c.finance_source_id + '"' + (rec && rec.finance_source_id === c.finance_source_id ? " selected" : "") + ">" + escapeHtml(c.name) + "</option>";
    }).join("") + "</select></div>";
    html += '<div class="form-group"><label>Сумма</label><input id="frOpAmt" value="' + escapeAttribute(rec ? String(rec.amount) : "") + '"></div>';
    html += '<div class="form-group"><label>Комментарий</label><input id="frOpComment" value="' + escapeAttribute((rec && rec.comment) || "") + '"></div>';
    html += '<div class="note">Статья расходов не обязательна.</div>';
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
    rec.period_key = w.period_key;
    rec.date = ((document.getElementById("frOpDate") || {}).value || w.start_date);
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
    let html = "<h3>" + (kind === "source" ? "Источник средств" : "Статья движения") + "</h3>";
    html += '<input type="hidden" id="frDictKind" value="' + kind + '"><input type="hidden" id="frDictId" value="' + escapeAttribute(id || "") + '">';
    html += '<div class="form-group"><label>Название</label><input id="frDictName" value="' + escapeAttribute(rec ? rec.name : "") + '"></div>';
    html += '<div class="form-group"><label>Порядок</label><input id="frDictOrder" type="number" value="' + escapeAttribute(String(rec ? rec.sort_order : 10)) + '"></div>';
    if (kind === "cat") {
        html += '<div class="form-group"><label>Тип по умолчанию</label><select id="frDictType"><option value="any">Любой</option><option value="in"' + (rec && rec.default_type === "in" ? " selected" : "") + ">Приход</option><option value=\"out\"" + (rec && rec.default_type === "out" ? " selected" : "") + ">Расход</option></select></div>";
    }
    html += '<div class="form-group"><label>Статус</label><select id="frDictActive"><option value="1"' + (!rec || rec.is_active !== false ? " selected" : "") + ">Активен</option><option value=\"0\"" + (rec && rec.is_active === false ? " selected" : "") + ">Неактивен</option></select></div>";
    html += '<div class="note">Запись с историей не удаляется физически — только деактивируется.</div>';
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

function financeRecFolderCardHtml() {
    return folderCardHtml("summary-folder", "financeRec", "Финконтроль", "Финансовая сверка поступлений, расходов и остатков", "");
}
