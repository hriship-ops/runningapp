var rwState = { year: '', month: '', tickerPage: 0, runs: [] };
var rwTickerRuns = [];
var rwAllRuns = [];
var rwFocusActivity = 'Run';

document.addEventListener('DOMContentLoaded', function() {
    var now = new Date();
    rwState.year = now.getFullYear().toString();
    rwState.month = String(now.getMonth() + 1).padStart(2, '0');
    var yearSel = document.getElementById('rw-year');
    if (yearSel) {
        yearSel.innerHTML = '<option value="">All Years</option>';
        for (var y = now.getFullYear(); y >= 2010; y--) {
            var opt = document.createElement('option');
            opt.value = y; opt.textContent = y;
            if (y == rwState.year) opt.selected = true;
            yearSel.appendChild(opt);
        }
    }
    var monthSel = document.getElementById('rw-month');
    if (monthSel) monthSel.value = rwState.month;
    rwLoadAllRuns();
    rwSearch();
    rwLoadTicker();
    rwLoadGeoSummary();
});

/* ── LOCATION SUMMARY ── */
var rwGeoData = { countries: [], states: [], districts: [] };

function rwRenderGeoSummary(data) {
    rwGeoData.countries = data.countries || [];
    rwGeoData.states = data.states || [];
    rwGeoData.districts = data.districts || [];

    var cEl = document.getElementById('rw-geo-country-count');
    var sEl = document.getElementById('rw-geo-state-count');
    var dEl = document.getElementById('rw-geo-district-count');
    var text = document.getElementById('rw-geo-text');
    if (cEl) cEl.textContent = data.country_count != null ? data.country_count : rwGeoData.countries.length;
    if (sEl) sEl.textContent = data.state_count != null ? data.state_count : rwGeoData.states.length;
    if (dEl) dEl.textContent = data.district_count != null ? data.district_count : rwGeoData.districts.length;
    if (text) text.textContent = data.summary || '';
}

var RW_GEO_FIELD = { countries: 'country', states: 'state', districts: 'district' };
var RW_GEO_NEXT = { country: 'state', state: 'district', district: null };
var RW_GEO_TITLES = { country: 'Countries', state: 'States', district: 'Districts' };
var rwGeoFilters = {};

/* Distinct values for `field` among runs matching `filters`, computed
   client-side from the already-loaded rwAllRuns — no extra API calls
   needed for any level of the drill-down. */
function rwGeoDistinct(field, filters) {
    var seen = {};
    var out = [];
    rwAllRuns.forEach(function(r) {
        var val = r[field];
        if (!val) return;
        for (var k in filters) { if (r[k] !== filters[k]) return; }
        if (!seen[val]) { seen[val] = true; out.push(val); }
    });
    return out.sort();
}

function rwEsc(s) { return String(s).replace(/'/g, "\\'"); }

function rwOpenGeoModal(kind) {
    rwGeoFilters = {};
    rwGeoRenderLevel(RW_GEO_FIELD[kind]);
}

function rwGeoRenderLevel(field) {
    var list = rwGeoDistinct(field, rwGeoFilters);
    var crumbs = Object.keys(rwGeoFilters).map(function(k) { return rwGeoFilters[k]; }).join(' › ');
    document.getElementById('rw-geo-modal-title').textContent = (crumbs ? crumbs + ' › ' : '') + (RW_GEO_TITLES[field] || field);
    var ul = document.getElementById('rw-geo-modal-list');
    ul.innerHTML = list.length
        ? list.map(function(name) {
            return '<li onclick="rwGeoDrill(\'' + field + '\', \'' + rwEsc(name) + '\')">' + name + '</li>';
          }).join('')
        : '<li style="color:#6b7280">No runs here yet.</li>';
    document.getElementById('rw-geo-modal').style.display = 'flex';
}

function rwGeoDrill(field, value) {
    rwGeoFilters[field] = value;
    var next = RW_GEO_NEXT[field];
    if (next) {
        rwGeoRenderLevel(next);
    } else {
        rwShowRunsForGeoFilters(value);
    }
}

function rwCloseGeoModal(e) {
    if (!e || e.target === document.getElementById('rw-geo-modal'))
        document.getElementById('rw-geo-modal').style.display = 'none';
}

function rwShowRunsForGeoFilters(label) {
    var filters = rwGeoFilters;
    var runs = rwAllRuns.filter(function(r) {
        for (var k in filters) { if (r[k] !== filters[k]) return false; }
        return true;
    });
    var rows = runs.map(function(r) {
        return '<tr onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td><td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location||'--') + '</td><td>' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td><td>' + fmtPace(r.distance_km,r.duration_sec) + '</td></tr>';
    }).join('');
    document.getElementById('rw-modal-rows').innerHTML = rows;
    document.getElementById('rw-modal-title').textContent = label + ' · ' + runs.length + ' activities';
    document.getElementById('rw-geo-modal').style.display = 'none';
    document.getElementById('rw-modal').style.display = 'flex';
}

function rwLoadGeoSummary() {
    fetch('/api/method/runningapp.running_journal.location_summary.get_location_summary')
    .then(function(r) { return r.json(); })
    .then(function(data) { rwRenderGeoSummary(data.message || {}); })
    .catch(function() {});
}

function rwRefreshGeoSummary() {
    var btn = document.getElementById('rw-geo-refresh-btn');
    if (btn) btn.textContent = 'Summarizing...';
    fetch('/api/method/runningapp.running_journal.location_summary.refresh_location_summary', { method: 'GET' })
    .then(function(r) {
        if (!r.ok) {
            return r.json().catch(function() { return {}; }).then(function(body) {
                var msg = (body && body._server_messages) ? JSON.parse(JSON.parse(body._server_messages)[0]).message : (body && body.message) || ('Refresh failed (HTTP ' + r.status + ')');
                throw new Error(msg);
            });
        }
        return r.json();
    })
    .then(function(data) {
        if (btn) btn.textContent = '↻ Refresh Summary';
        rwRenderGeoSummary(data.message || {});
    })
    .catch(function(err) {
        if (btn) btn.textContent = '↻ Refresh Summary';
        alert('Location summary refresh failed: ' + (err && err.message ? err.message : 'unknown error'));
    });
}

function rwLoadAllRuns() {
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_all_runs?filters=' + encodeURIComponent(JSON.stringify([])))
    .then(function(r) { return r.json(); })
    .then(function(data) {
        rwAllRuns = data.message || [];
        var focused = rwFocusActivity ? rwAllRuns.filter(function(r) { return r.activity_type === rwFocusActivity; }) : rwAllRuns;
        rwRenderRecords(focused);
        rwRenderRecentRuns(focused);
        rwRenderWeeklyTable(focused);
        rwRenderMonthlyTable(focused);
        rwRenderYearlyTable(focused);
    });
}

/* â”€â”€ FORMAT HELPERS â”€â”€ */
function fmtDist(km) {
    if (!km) return '0 km';
    return km >= 1 ? km.toFixed(2) + ' km' : Math.round(km * 1000) + ' m';
}
function fmtDur(sec) {
    if (!sec) return '--';
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = Math.round(sec%60);
    return h ? h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0') : m + ':' + String(s).padStart(2,'0');
}
function fmtPace(km, sec) {
    if (!sec || !km) return '--';
    var sPerKm = sec / km, m = Math.floor(sPerKm/60), s = Math.round(sPerKm%60);
    return m + ':' + String(s).padStart(2,'0') + '/km';
}
function fmtDate(d) {
    return new Date(d + 'T12:00:00').toLocaleDateString('en-US', {day:'numeric', month:'short', year:'numeric'});
}
function fmtDateShort(d) {
    return new Date(d + 'T12:00:00').toLocaleDateString('en-US', {day:'numeric', month:'short'});
}
function fmtMonthKey(key) {
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var parts = key.split('-');
    return months[parseInt(parts[1]) - 1] + ' ' + parts[0];
}
function actPill(type) {
    var cls = type === 'Swimming' ? 'rw-act-swim' : type === 'Cycling' ? 'rw-act-cycle' : type === 'Walk' ? 'rw-act-walk' : 'rw-act-run';
    return '<span class="' + cls + '">' + type + '</span>';
}
function makeCard(val, lbl, sub, clickable, onclick) {
    return '<div class="rw-card' + (clickable ? '' : ' no-click') + '"' +
        (onclick ? ' onclick="' + onclick + '"' : '') + '>' +
        '<div class="rw-card-val">' + val + '</div>' +
        '<div class="rw-card-lbl">' + lbl + '</div>' +
        '<div class="rw-card-sub">' + (sub || '&nbsp;') + '</div>' +
        '</div>';
}

/* â”€â”€ FILTERS â”€â”€ */
function rwGetFilters() {
    var filters = [];
    if (rwState.year && rwState.month) {
        var start = rwState.year + '-' + rwState.month + '-01';
        var end = new Date(rwState.year, parseInt(rwState.month), 0);
        var endStr = rwState.year + '-' + rwState.month + '-' + String(end.getDate()).padStart(2,'0');
        filters.push(['date', '>=', start], ['date', '<=', endStr]);
    } else if (rwState.year) {
        filters.push(['date', '>=', rwState.year + '-01-01'], ['date', '<=', rwState.year + '-12-31']);
    }
    if (rwFocusActivity) filters.push(['activity_type', '=', rwFocusActivity]);
    return filters;
}

function rwSearch() {
    rwState.year = document.getElementById('rw-year').value;
    rwState.month = document.getElementById('rw-month').value;
    rwLoadStats();
}

function rwReset() {
    var now = new Date();
    rwState.year = now.getFullYear().toString();
    rwState.month = String(now.getMonth() + 1).padStart(2, '0');
    document.getElementById('rw-year').value = rwState.year;
    document.getElementById('rw-month').value = rwState.month;
    rwLoadStats();
}

function rwSetFocus(activity) {
    rwFocusActivity = activity;
    document.querySelectorAll('.rw-focus-btn').forEach(function(btn) { btn.classList.remove('active'); });
    event.target.classList.add('active');
    rwLoadStats();
    var filtered = rwFocusActivity ? rwAllRuns.filter(function(r) { return r.activity_type === rwFocusActivity; }) : rwAllRuns;
    rwRenderRecords(filtered);
    rwRenderRecentRuns(filtered);
    rwRenderWeeklyTable(filtered);
    rwRenderMonthlyTable(filtered);
    rwRenderYearlyTable(filtered);
}

function rwLoadStats() {
    var filters = rwGetFilters();
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_all_runs?filters=' + encodeURIComponent(JSON.stringify(filters)))
    .then(function(r) { return r.json(); })
    .then(function(data) {
        rwState.runs = data.message || [];
        rwRenderStats(rwState.runs);
        rwRenderLabel();
    });
}

function rwLoadTicker() {
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_all_runs?filters=' + encodeURIComponent(JSON.stringify([])))
    .then(function(r) { return r.json(); })
    .then(function(data) {
        rwTickerRuns = data.message || [];
        rwRenderTicker();
    });
}

function rwRenderLabel() {
    var parts = [];
    if (rwState.year) parts.push(rwState.year);
    if (rwState.month) {
        var months = ['','January','February','March','April','May','June','July','August','September','October','November','December'];
        parts.push(months[parseInt(rwState.month)]);
    }
    var label = parts.length ? parts.join(' Â· ') : 'All Time';
    var count = rwState.runs.length;
    var singular = rwFocusActivity ? rwFocusActivity.toLowerCase() : 'activity';
    var plural = rwFocusActivity ? rwFocusActivity.toLowerCase() + 's' : 'activities';
    var el = document.getElementById('rw-section-label');
    if (el) el.textContent = label + ' Â· ' + count + ' ' + (count === 1 ? singular : plural);
}

/* â”€â”€ STAT CARDS â”€â”€ */
function rwRenderStats(runs) {
    var totalDist = runs.reduce(function(a,r) { return a + (r.distance_km||0); }, 0);
    var totalSec = runs.reduce(function(a,r) { return a + (r.duration_sec||0); }, 0);
    var totalElev = runs.reduce(function(a,r) { return a + (r.elevation_gain||0); }, 0);
    var totalCal = runs.reduce(function(a,r) { return a + (r.calories||0); }, 0);
    var avgDist = runs.length ? totalDist / runs.length : 0;
    var longestRun = runs.length ? runs.reduce(function(a,r) { return (r.distance_km||0) > (a.distance_km||0) ? r : a; }, runs[0]) : null;
    var longest = longestRun ? longestRun.distance_km : 0;
    var runsWithHR = runs.filter(function(r) { return r.avg_heart_rate > 0; });
    var avgHR = runsWithHR.length ? Math.round(runsWithHR.reduce(function(a,r) { return a + r.avg_heart_rate; }, 0) / runsWithHR.length) : null;
    var maxHR = runsWithHR.length ? Math.max.apply(null, runsWithHR.map(function(r) { return r.max_heart_rate||0; })) : null;

    var singular = rwFocusActivity ? rwFocusActivity.toLowerCase() : 'activity';
    var plural = rwFocusActivity ? rwFocusActivity.toLowerCase() + 's' : 'activities';
    var countLabel = 'Total ' + (runs.length === 1 ? singular : plural);

    var html = '';
    html += makeCard(runs.length, countLabel, 'click to view', true, 'rwShowModal()');
    html += makeCard(fmtDist(totalDist), 'Total distance', 'click to view', true, 'rwShowModal()');
    html += makeCard(fmtDur(totalSec), 'Total time', 'click to view', true, 'rwShowModal()');
    html += makeCard(fmtPace(totalDist, totalSec), 'Avg pace', '&nbsp;', false, null);
    html += makeCard(fmtDist(avgDist), 'Avg distance', 'click to view', true, 'rwShowModal()');
    html += makeCard(Math.round(totalElev) + ' m', 'Total elevation', '&nbsp;', false, null);
    html += makeCard(fmtDist(longest), 'Longest run', longestRun ? fmtDate(longestRun.date) : '&nbsp;', true, longestRun ? 'rwOpenDetail(\'' + longestRun.name + '\')' : '');
    html += makeCard(Math.round(totalCal) + ' kcal', 'Total calories', '&nbsp;', false, null);
    html += makeCard(avgHR ? avgHR + ' bpm' : '--', 'Avg heart rate', '&nbsp;', false, null);
    html += makeCard(maxHR ? maxHR + ' bpm' : '--', 'Max heart rate', '&nbsp;', false, null);

    var el = document.getElementById('rw-cards');
    if (el) el.innerHTML = html;
    var sub = document.getElementById('rw-total-sub');
    if (sub) sub.textContent = runs.length + ' ' + (runs.length === 1 ? singular : plural) + ' Â· ' + fmtDist(totalDist);
}

/* â”€â”€ AGGREGATOR HELPERS â”€â”€ */
function rwBestMonth(runs) {
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0,7);
        if (!map[key]) map[key] = { dist:0, count:0 };
        map[key].dist += r.distance_km||0; map[key].count++;
    });
    var best = null, bestDist = -1;
    Object.keys(map).forEach(function(k) { if (map[k].dist > bestDist) { bestDist = map[k].dist; best = k; } });
    return best ? { key:best, dist:bestDist, count:map[best].count } : null;
}
function rwWorstMonth(runs) {
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0,7);
        if (!map[key]) map[key] = { dist:0, count:0 };
        map[key].dist += r.distance_km||0; map[key].count++;
    });
    var worst = null, worstDist = Infinity;
    Object.keys(map).forEach(function(k) { if (map[k].dist < worstDist) { worstDist = map[k].dist; worst = k; } });
    return worst ? { key:worst, dist:worstDist, count:map[worst].count } : null;
}
function rwBestYear(runs) {
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0,4);
        if (!map[key]) map[key] = { dist:0, count:0 };
        map[key].dist += r.distance_km||0; map[key].count++;
    });
    var best = null, bestDist = -1;
    Object.keys(map).forEach(function(k) { if (map[k].dist > bestDist) { bestDist = map[k].dist; best = k; } });
    return best ? { key:best, dist:bestDist, count:map[best].count } : null;
}
function rwFastest(runs, targetKm) {
    var lo = targetKm * 0.9, hi = targetKm * 1.1;
    var best = null, bestPace = Infinity;
    runs.forEach(function(r) {
        if (!r.distance_km || !r.duration_sec) return;
        if (r.distance_km < lo || r.distance_km > hi) return;
        var pace = r.duration_sec / r.distance_km;
        if (pace < bestPace) { bestPace = pace; best = r; }
    });
    return best;
}
function rwLongestStreak(runs) {
    if (!runs.length) return null;
    var dates = runs.map(function(r) { return r.date.slice(0,10); });
    dates = dates.filter(function(d,i,a) { return a.indexOf(d) === i; }).sort();
    var best = { days:1, start:dates[0], end:dates[0] };
    var cur = { days:1, start:dates[0], end:dates[0] };
    for (var i = 1; i < dates.length; i++) {
        var diff = (new Date(dates[i]+'T12:00:00') - new Date(dates[i-1]+'T12:00:00')) / 86400000;
        if (diff === 1) { cur.days++; cur.end = dates[i]; }
        else { cur = { days:1, start:dates[i], end:dates[i] }; }
        if (cur.days > best.days) best = { days:cur.days, start:cur.start, end:cur.end };
    }
    return best;
}
function rwBestWeek(runs) {
    if (!runs.length) return null;
    var weeks = {};
    runs.forEach(function(r) {
        var d = new Date(r.date+'T12:00:00');
        var mon = new Date(d); mon.setDate(d.getDate() - ((d.getDay()+6)%7));
        var key = mon.toISOString().slice(0,10);
        if (!weeks[key]) weeks[key] = { dist:0, count:0 };
        weeks[key].dist += r.distance_km||0; weeks[key].count++;
    });
    var best = null, bestDist = -1;
    Object.keys(weeks).forEach(function(k) { if (weeks[k].dist > bestDist) { bestDist = weeks[k].dist; best = k; } });
    if (!best) return null;
    var endD = new Date(best+'T12:00:00'); endD.setDate(endD.getDate()+6);
    var endDate = endD.toISOString().slice(0,10);
    var label = new Date(best+'T12:00:00').toLocaleDateString('en-US',{day:'numeric',month:'short'}) + ' â€“ ' + endD.toLocaleDateString('en-US',{day:'numeric',month:'short'});
    return { dist:bestDist, count:weeks[best].count, label:label, startDate:best, endDate:endDate };
}
function rwMostRunsWeek(runs) {
    if (!runs.length) return null;
    var weeks = {};
    runs.forEach(function(r) {
        var d = new Date(r.date+'T12:00:00');
        var mon = new Date(d); mon.setDate(d.getDate() - ((d.getDay()+6)%7));
        var key = mon.toISOString().slice(0,10);
        if (!weeks[key]) weeks[key] = { dist:0, count:0 };
        weeks[key].dist += r.distance_km||0; weeks[key].count++;
    });
    var best = null, bestCount = -1;
    Object.keys(weeks).forEach(function(k) { if (weeks[k].count > bestCount) { bestCount = weeks[k].count; best = k; } });
    if (!best) return null;
    var endD = new Date(best+'T12:00:00'); endD.setDate(endD.getDate()+6);
    var endDate = endD.toISOString().slice(0,10);
    var label = new Date(best+'T12:00:00').toLocaleDateString('en-US',{day:'numeric',month:'short'}) + ' â€“ ' + endD.toLocaleDateString('en-US',{day:'numeric',month:'short'});
    return { count:bestCount, dist:weeks[best].dist, label:label, startDate:best, endDate:endDate };
}
function rwBestEffort(runs, field, minSec) {
    var best = null, bestSec = Infinity;
    runs.forEach(function(r) {
        var sec = r[field];
        if (!sec || sec < minSec) return;
        if (sec < bestSec) { bestSec = sec; best = r; }
    });
    return best ? { run: best, sec: bestSec } : null;
}
function fmtSec(sec) {
    if (!sec) return '--';
    var m = Math.floor(sec/60), s = Math.round(sec%60);
    return m + ':' + String(s).padStart(2,'0');
}

function rwBestPaceRun(runs, minKm) {
    var best = null, bestPace = Infinity;
    runs.forEach(function(r) {
        if (!r.distance_km || !r.duration_sec) return;
        if (r.distance_km < minKm) return;
        if (r.duration_sec < 600) return;
        var pace = r.duration_sec / r.distance_km;
        if (pace < 150) return;
        if (pace < bestPace) { bestPace = pace; best = r; }
    });
    return best;
}
function rwLowestHRRun(runs, minKm) {
    var best = null, bestHR = Infinity;
    runs.forEach(function(r) {
        if (!r.avg_heart_rate || r.distance_km < minKm) return;
        if (r.avg_heart_rate < bestHR) { bestHR = r.avg_heart_rate; best = r; }
    });
    return best;
}

/* â”€â”€ ALL-TIME RECORDS â”€â”€ */
function rwRenderRecords(runs) {
    var onlyRuns = runs.filter(function(r) { return r.activity_type === 'Run'; });
    var bMonth = rwBestMonth(runs);
    var wMonth = rwWorstMonth(runs);
    var bYear = rwBestYear(runs);
    var f5k = rwFastest(onlyRuns, 5);
    var f10k = rwFastest(onlyRuns, 10);
    var fHM = rwFastest(onlyRuns, 21.0975);
    var fFM = rwFastest(onlyRuns, 42.195);
    var streak = rwLongestStreak(onlyRuns);
    var bestWeek = rwBestWeek(onlyRuns);
    var mostRunsWeek = rwMostRunsWeek(onlyRuns);
    var bestPaceRun = rwBestPaceRun(onlyRuns, 5);
    var lowestHRRun = rwLowestHRRun(onlyRuns, 10);

    var act = rwFocusActivity ? rwFocusActivity.toLowerCase() : 'activity';
    var acts = rwFocusActivity ? rwFocusActivity.toLowerCase() + 's' : 'activities';

    function raceCard(label, run) {
        if (!run) return makeCard('--', label, '&nbsp;', false, null);
        var sub = fmtDur(run.duration_sec) + ' Â· ' + fmtDate(run.date);
        return makeCard(fmtPace(run.distance_km, run.duration_sec), label, sub, true, 'rwOpenDetail(\'' + run.name + '\')');
    }

    var html = '';
    html += makeCard(bMonth ? fmtMonthKey(bMonth.key) : '--', 'Best month', bMonth ? fmtDist(bMonth.dist) + ' Â· ' + bMonth.count + ' ' + (bMonth.count === 1 ? act : acts) : '&nbsp;', true, bMonth ? 'rwShowMonthModal(\'' + bMonth.key + '\')' : '');
    html += makeCard(wMonth ? fmtMonthKey(wMonth.key) : '--', 'Worst month', wMonth ? fmtDist(wMonth.dist) + ' Â· ' + wMonth.count + ' ' + (wMonth.count === 1 ? act : acts) : '&nbsp;', true, wMonth ? 'rwShowMonthModal(\'' + wMonth.key + '\')' : '');
    html += makeCard(bYear ? bYear.key : '--', 'Best year', bYear ? fmtDist(bYear.dist) + ' Â· ' + bYear.count + ' ' + (bYear.count === 1 ? act : acts) : '&nbsp;', false, null);
    html += raceCard('Fastest 5k', f5k);
    html += raceCard('Fastest 10k', f10k);
    html += raceCard('Fastest HM', fHM);
    html += raceCard('Fastest FM', fFM);
    html += makeCard(streak ? streak.days + ' days' : '--', 'Longest streak', streak ? fmtDateShort(streak.start) + ' â€“ ' + fmtDateShort(streak.end) : '&nbsp;', !!streak, streak ? 'rwShowRangeModal(\'' + streak.start + '\',\'' + streak.end + '\',\'Longest Streak\')' : '');
    html += makeCard(bestWeek ? fmtDist(bestWeek.dist) : '--', 'Best week (km)', bestWeek ? bestWeek.label + ' Â· ' + bestWeek.count + ' runs' : '&nbsp;', !!bestWeek, bestWeek ? 'rwShowRangeModal(\'' + bestWeek.startDate + '\',\'' + bestWeek.endDate + '\',\'Best Week\')' : '');
    html += makeCard(mostRunsWeek ? mostRunsWeek.count + ' runs' : '--', 'Most runs / week', mostRunsWeek ? mostRunsWeek.label : '&nbsp;', !!mostRunsWeek, mostRunsWeek ? 'rwShowRangeModal(\'' + mostRunsWeek.startDate + '\',\'' + mostRunsWeek.endDate + '\',\'Most Runs Week\')' : '');
    html += makeCard(bestPaceRun ? fmtPace(bestPaceRun.distance_km, bestPaceRun.duration_sec) : '--', 'Best pace run', bestPaceRun ? fmtDist(bestPaceRun.distance_km) + ' Â· ' + fmtDate(bestPaceRun.date) : 'min 5k', true, bestPaceRun ? 'rwOpenDetail(\'' + bestPaceRun.name + '\')' : '');
    html += makeCard(lowestHRRun ? Math.round(lowestHRRun.avg_heart_rate) + ' bpm' : '--', 'Lowest HR run', lowestHRRun ? fmtDist(lowestHRRun.distance_km) + ' Â· ' + fmtDate(lowestHRRun.date) : 'min 10k', true, lowestHRRun ? 'rwOpenDetail(\'' + lowestHRRun.name + '\')' : '');

    // Best efforts from pre-computed segment fields
    var e1k   = rwBestEffort(onlyRuns, "best_1k_sec",   210);
    var eMile = rwBestEffort(onlyRuns, "best_mile_sec",  330);
    html += makeCard(e1k ? fmtSec(e1k.sec) : "--", "Fastest 1k", e1k ? fmtDate(e1k.run.date) : "segment PR", !!e1k, e1k ? "rwOpenDetail(\"" + e1k.run.name + "\")" : "");
    html += makeCard(eMile ? fmtSec(eMile.sec) : "--", "Fastest mile", eMile ? fmtDate(eMile.run.date) : "segment PR", !!eMile, eMile ? "rwOpenDetail(\"" + eMile.run.name + "\")" : "");

    var el = document.getElementById('rw-records');
    if (el) el.innerHTML = html;
}

/* â”€â”€ RECENT RUNS â”€â”€ */
function rwRenderRecentRuns(runs) {
    var el = document.getElementById('rw-recent-runs');
    if (!el) return;
    var recent = runs.slice(0, 10);
    var actLabel = rwFocusActivity ? rwFocusActivity + 's' : 'Activities';
    var titleEl = document.querySelector('.rw-recent-title');
    if (titleEl) titleEl.textContent = 'Recent ' + actLabel;
    var html = '<div class="rw-run-row header">' +
        '<span>Date</span><span>Activity</span><span style="text-align:right">Dist</span>' +
        '<span style="text-align:right">Pace</span><span style="text-align:right">HR</span>' +
        '<span style="text-align:right">Time</span></div>';
    recent.forEach(function(r) {
        var hr = r.avg_heart_rate ? Math.round(r.avg_heart_rate) + ' bpm' : '--';
        html += '<div class="rw-run-row" onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<span class="rw-run-date">' + fmtDateShort(r.date) + '</span>' +
            '<div><div class="rw-run-name">' + (r.run_name || 'Run') + '</div>' +
            '<div class="rw-run-location">' + (r.location || '') + '</div></div>' +
            '<span class="rw-run-dist">' + fmtDist(r.distance_km||0) + '</span>' +
            '<span class="rw-run-pace">' + fmtPace(r.distance_km, r.duration_sec) + '</span>' +
            '<span class="rw-run-hr">' + hr + '</span>' +
            '<span class="rw-run-time">' + fmtDur(r.duration_sec||0) + '</span>' +
            '</div>';
    });
    el.innerHTML = html;
}

/* â”€â”€ WEEKLY TABLE â”€â”€ */
function rwRenderWeeklyTable(runs) {
    var el = document.getElementById('rw-weekly-table');
    if (!el) return;
    var now = new Date();
    var weeks = [];
    for (var w = 0; w < 8; w++) {
        var mon = new Date(now);
        mon.setDate(now.getDate() - ((now.getDay()+6)%7) - w*7);
        mon.setHours(0,0,0,0);
        var sun = new Date(mon); sun.setDate(mon.getDate()+6);
        sun.setHours(23,59,59,999);
        weeks.push({ start:mon, end:sun, runs:[], dist:0, sec:0, elev:0, hrSum:0, hrCount:0 });
    }
    runs.forEach(function(r) {
        var d = new Date(r.date+'T12:00:00');
        weeks.forEach(function(w) {
            if (d >= w.start && d <= w.end) {
                w.runs.push(r);
                w.dist += r.distance_km||0;
                w.sec += r.duration_sec||0;
                w.elev += r.elevation_gain||0;
                if (r.avg_heart_rate > 0) { w.hrSum += r.avg_heart_rate; w.hrCount++; }
            }
        });
    });
    var maxDist = Math.max.apply(null, weeks.map(function(w) { return w.dist; })) || 1;
    var fmt = function(d) { return d.toLocaleDateString('en-US',{day:'numeric',month:'short'}); };
    var html = '<table class="rw-sum-table"><thead><tr><th>Week</th><th>Runs</th><th>Distance</th><th>Pace</th><th>Avg HR</th><th>Elev</th></tr></thead><tbody>';
    weeks.forEach(function(w) {
        var avgHR = w.hrCount ? Math.round(w.hrSum/w.hrCount) + ' bpm' : '--';
        var barW = Math.round((w.dist/maxDist)*60);
        var distCell = w.dist > 0 ? '<div class="rw-bar-cell"><div class="rw-bar" style="width:' + barW + 'px"></div>' + fmtDist(w.dist) + '</div>' : '--';
        html += '<tr><td>' + fmt(w.start) + ' â€“ ' + fmt(w.end) + '</td><td>' + (w.runs.length||'--') + '</td><td>' + distCell + '</td><td>' + fmtPace(w.dist,w.sec) + '</td><td>' + avgHR + '</td><td>' + (w.elev ? Math.round(w.elev)+' m' : '--') + '</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

/* â”€â”€ MONTHLY TABLE â”€â”€ */
function rwRenderMonthlyTable(runs) {
    var el = document.getElementById('rw-monthly-table');
    if (!el) return;
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0,7);
        if (!map[key]) map[key] = { dist:0, sec:0, count:0, elev:0, hrSum:0, hrCount:0 };
        map[key].dist += r.distance_km||0; map[key].sec += r.duration_sec||0; map[key].count++;
        map[key].elev += r.elevation_gain||0;
        if (r.avg_heart_rate > 0) { map[key].hrSum += r.avg_heart_rate; map[key].hrCount++; }
    });
    var keys = [];
    var now = new Date();
    for (var i = 0; i < 12; i++) {
        var d = new Date(now.getFullYear(), now.getMonth()-i, 1);
        keys.push(d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0'));
    }
    var maxDist = Math.max.apply(null, keys.map(function(k) { return map[k] ? map[k].dist : 0; })) || 1;
    var html = '<table class="rw-sum-table"><thead><tr><th>Month</th><th>Runs</th><th>Distance</th><th>Pace</th><th>Avg HR</th><th>Elev</th></tr></thead><tbody>';
    keys.forEach(function(k) {
        var m = map[k] || { dist:0, sec:0, count:0, elev:0, hrSum:0, hrCount:0 };
        var avgHR = m.hrCount ? Math.round(m.hrSum/m.hrCount) + ' bpm' : '--';
        var barW = Math.round((m.dist/maxDist)*60);
        var distCell = m.dist > 0 ? '<div class="rw-bar-cell"><div class="rw-bar" style="width:' + barW + 'px"></div>' + fmtDist(m.dist) + '</div>' : '--';
        html += '<tr onclick="rwShowMonthModal(\'' + k + '\')">' +
            '<td>' + fmtMonthKey(k) + '</td><td>' + (m.count||'--') + '</td><td>' + distCell + '</td>' +
            '<td>' + (m.dist ? fmtPace(m.dist,m.sec) : '--') + '</td><td>' + avgHR + '</td>' +
            '<td>' + (m.elev ? Math.round(m.elev)+' m' : '--') + '</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

/* â”€â”€ YEARLY TABLE â”€â”€ */
function rwRenderYearlyTable(runs) {
    var el = document.getElementById('rw-yearly-table');
    if (!el) return;
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0,4);
        if (!map[key]) map[key] = { dist:0, sec:0, count:0, elev:0, hrSum:0, hrCount:0 };
        map[key].dist += r.distance_km||0; map[key].sec += r.duration_sec||0; map[key].count++;
        map[key].elev += r.elevation_gain||0;
        if (r.avg_heart_rate > 0) { map[key].hrSum += r.avg_heart_rate; map[key].hrCount++; }
    });
    var keys = Object.keys(map).sort(function(a,b) { return b-a; });
    var maxDist = Math.max.apply(null, keys.map(function(k) { return map[k].dist; })) || 1;
    var bestKey = keys.reduce(function(a,k) { return map[k].dist > map[a].dist ? k : a; }, keys[0]);
    var html = '<table class="rw-sum-table"><thead><tr><th>Year</th><th>Runs</th><th>Distance</th><th>Avg Pace</th><th>Avg HR</th><th>Elev</th></tr></thead><tbody>';
    keys.forEach(function(k) {
        var m = map[k];
        var avgHR = m.hrCount ? Math.round(m.hrSum/m.hrCount) + ' bpm' : '--';
        var barW = Math.round((m.dist/maxDist)*60);
        var distCell = '<div class="rw-bar-cell"><div class="rw-bar" style="width:' + barW + 'px"></div>' + fmtDist(m.dist) + '</div>';
        html += '<tr><td class="' + (k===bestKey ? 'rw-yr-best' : '') + '">' + k + (k===bestKey ? ' â˜…' : '') + '</td>' +
            '<td>' + m.count + '</td><td>' + distCell + '</td><td>' + fmtPace(m.dist,m.sec) + '</td>' +
            '<td>' + avgHR + '</td><td>' + Math.round(m.elev) + ' m</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

/* â”€â”€ MODALS â”€â”€ */
function rwShowMonthModal(monthKey) {
    var runs = rwAllRuns.filter(function(r) { return r.date.slice(0,7) === monthKey; });
    var rows = runs.map(function(r) {
        return '<tr onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td><td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location||'--') + '</td><td>' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td><td>' + fmtPace(r.distance_km,r.duration_sec) + '</td></tr>';
    }).join('');
    document.getElementById('rw-modal-rows').innerHTML = rows;
    document.getElementById('rw-modal-title').textContent = fmtMonthKey(monthKey) + ' Â· ' + runs.length + ' activities';
    document.getElementById('rw-modal').style.display = 'flex';
}

function rwShowModal() {
    var rows = rwState.runs.map(function(r) {
        return '<tr onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td><td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location||'--') + '</td><td>' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td><td>' + fmtPace(r.distance_km,r.duration_sec) + '</td></tr>';
    }).join('');
    document.getElementById('rw-modal-rows').innerHTML = rows;
    document.getElementById('rw-modal-title').textContent = rwState.runs.length + ' activities';
    document.getElementById('rw-modal').style.display = 'flex';
}

function rwShowRangeModal(startDate, endDate, title) {
    var runs = rwAllRuns.filter(function(r) {
        return r.date >= startDate && r.date <= endDate;
    });
    var rows = runs.map(function(r) {
        return '<tr onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td><td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location||'--') + '</td><td>' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td><td>' + fmtPace(r.distance_km,r.duration_sec) + '</td></tr>';
    }).join('');
    document.getElementById('rw-modal-rows').innerHTML = rows;
    document.getElementById('rw-modal-title').textContent = title + ' Â· ' + runs.length + ' activities';
    document.getElementById('rw-modal').style.display = 'flex';
}

function rwCloseModal(e) {
    if (!e || e.target === document.getElementById('rw-modal'))
        document.getElementById('rw-modal').style.display = 'none';
}

function rwOpenDetail(name) {
    window.open('/run-detail?name=' + name, '_blank');
}

/* â”€â”€ TICKER â”€â”€ */
function rwRenderTicker() {
    var page = rwState.tickerPage;
    var pageSize = 4;
    var start = page * pageSize;
    var items = rwTickerRuns.slice(start, start + pageSize);
    var totalPages = Math.ceil(rwTickerRuns.length / pageSize);
    var html = items.map(function(r) {
        var emoji = r.activity_type === 'Swimming' ? 'ðŸ„' : r.activity_type === 'Cycling' ? 'ðŸš´' : r.activity_type === 'Walk' ? 'ðŸš¶' : 'ðŸƒ';
        return '<div class="rw-ticker-item" onclick="rwOpenDetail(\'' + r.name + '\')">' +
            emoji + ' ' + fmtDate(r.date) + ' Â· ' + (r.location||'') +
            ' Â· ' + fmtDist(r.distance_km||0) +
            (r.duration_sec ? ' Â· ' + fmtPace(r.distance_km, r.duration_sec) : '') + '</div>';
    }).join('');
    document.getElementById('rw-ticker').innerHTML = html;
    document.getElementById('rw-ticker-page').textContent = (page+1) + '/' + Math.max(1,totalPages);
}

function rwTickerNext() {
    var totalPages = Math.ceil(rwTickerRuns.length / 4);
    if (rwState.tickerPage < totalPages - 1) { rwState.tickerPage++; rwRenderTicker(); }
}
function rwTickerPrev() {
    if (rwState.tickerPage > 0) { rwState.tickerPage--; rwRenderTicker(); }
}

/* ── GARMIN SYNC ── */
function rwSyncStravaOnce() {
    return fetch('/api/method/runningapp.running_journal.garmin_sync.sync_garmin_public', { method: 'GET' })
    .then(function(r) {
        if (!r.ok) {
            return r.json().catch(function() { return {}; }).then(function(body) {
                var msg = (body && body._server_messages) ? JSON.parse(JSON.parse(body._server_messages)[0]).message : (body && body.message) || ('Sync failed (HTTP ' + r.status + ')');
                throw new Error(msg);
            });
        }
        return r.json();
    })
    .then(function(data) { return data.message || data; });
}

function rwSyncStrava() {
    var btn = document.getElementById('rw-sync-btn');
    var totalImported = 0;

    function step() {
        if (btn) btn.textContent = totalImported > 0 ? ('Syncing... (' + totalImported + ' so far)') : 'Syncing...';
        return rwSyncStravaOnce().then(function(result) {
            totalImported += result.imported || 0;
            if (result.more_pending) {
                return step();
            }
        });
    }

    step()
    .then(function() {
        if (btn) btn.textContent = '↻ Sync Garmin';
        rwLoadAllRuns(); rwSearch(); rwLoadTicker();
    })
    .catch(function(err) {
        if (btn) btn.textContent = '↻ Sync Garmin';
        alert('Garmin sync failed: ' + (err && err.message ? err.message : 'unknown error') + (totalImported > 0 ? (' (' + totalImported + ' imported before the error)') : ''));
        rwLoadAllRuns(); rwSearch(); rwLoadTicker();
    });
}
