var rwState = { year: '', month: '', activity: '', tickerPage: 0, runs: [] };
var rwTickerRuns = [];
var rwAllRuns = [];

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
});

function rwLoadAllRuns() {
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_all_runs?filters=' + encodeURIComponent(JSON.stringify([])))
    .then(function(r) { return r.json(); })
    .then(function(data) {
        rwAllRuns = data.message || [];
        rwRenderRecords(rwAllRuns);
    });
}

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
    if (rwState.activity) filters.push(['activity_type', '=', rwState.activity]);
    return filters;
}

function rwSearch() {
    rwState.year = document.getElementById('rw-year').value;
    rwState.month = document.getElementById('rw-month').value;
    rwState.activity = document.getElementById('rw-activity').value;
    rwLoadStats();
}

function rwReset() {
    var now = new Date();
    rwState.year = now.getFullYear().toString();
    rwState.month = String(now.getMonth() + 1).padStart(2, '0');
    rwState.activity = '';
    document.getElementById('rw-year').value = rwState.year;
    document.getElementById('rw-month').value = rwState.month;
    document.getElementById('rw-activity').value = '';
    rwLoadStats();
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
    if (rwState.activity) parts.push(rwState.activity);
    var label = parts.length ? parts.join(' · ') : 'All Time';
    var el = document.getElementById('rw-section-label');
    if (el) el.textContent = label + ' · ' + rwState.runs.length + ' runs';
}

function rwRenderStats(runs) {
    var totalDist = runs.reduce(function(a,r) { return a + (r.distance_km||0); }, 0);
    var totalSec = runs.reduce(function(a,r) { return a + (r.duration_sec||0); }, 0);
    var totalElev = runs.reduce(function(a,r) { return a + (r.elevation_gain||0); }, 0);
    var totalCal = runs.reduce(function(a,r) { return a + (r.calories||0); }, 0);
    var avgDist = runs.length ? totalDist / runs.length : 0;
    var longest = runs.length ? Math.max.apply(null, runs.map(function(r) { return r.distance_km||0; })) : 0;
    var runsWithHR = runs.filter(function(r) { return r.avg_heart_rate > 0; });
    var avgHR = runsWithHR.length ? Math.round(runsWithHR.reduce(function(a,r) { return a + r.avg_heart_rate; }, 0) / runsWithHR.length) : null;
    var maxHR = runsWithHR.length ? Math.max.apply(null, runsWithHR.map(function(r) { return r.max_heart_rate||0; })) : null;

    var html = '';
    html += makeCard(runs.length, 'Total runs', 'click to view', true, 'rwShowModal()');
    html += makeCard(fmtDist(totalDist), 'Total distance', 'click to view', true, 'rwShowModal()');
    html += makeCard(fmtDur(totalSec), 'Total time', 'click to view', true, 'rwShowModal()');
    html += makeCard(fmtPace(totalDist, totalSec), 'Avg pace', '&nbsp;', false, null);
    html += makeCard(fmtDist(avgDist), 'Avg distance', 'click to view', true, 'rwShowModal()');
    html += makeCard(Math.round(totalElev) + ' m', 'Total elevation', '&nbsp;', false, null);
    html += makeCard(fmtDist(longest), 'Longest run', 'click to view', true, 'rwShowModal()');
    html += makeCard(Math.round(totalCal) + ' kcal', 'Total calories', '&nbsp;', false, null);
    html += makeCard(avgHR ? avgHR + ' bpm' : '--', 'Avg heart rate', '&nbsp;', false, null);
    html += makeCard(maxHR ? maxHR + ' bpm' : '--', 'Max heart rate', '&nbsp;', false, null);

    var el = document.getElementById('rw-cards');
    if (el) el.innerHTML = html;
    var sub = document.getElementById('rw-total-sub');
    if (sub) sub.textContent = runs.length + ' runs · ' + fmtDist(totalDist);
}

function rwBestMonth(runs) {
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0, 7);
        if (!map[key]) map[key] = { dist: 0, count: 0 };
        map[key].dist += r.distance_km || 0;
        map[key].count++;
    });
    var best = null, bestDist = -1;
    Object.keys(map).forEach(function(k) {
        if (map[k].dist > bestDist) { bestDist = map[k].dist; best = k; }
    });
    return best ? { key: best, dist: bestDist, count: map[best].count } : null;
}

function rwWorstMonth(runs) {
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0, 7);
        if (!map[key]) map[key] = { dist: 0, count: 0 };
        map[key].dist += r.distance_km || 0;
        map[key].count++;
    });
    var worst = null, worstDist = Infinity;
    Object.keys(map).forEach(function(k) {
        if (map[k].dist < worstDist) { worstDist = map[k].dist; worst = k; }
    });
    return worst ? { key: worst, dist: worstDist, count: map[worst].count } : null;
}

function rwBestYear(runs) {
    var map = {};
    runs.forEach(function(r) {
        var key = r.date.slice(0, 4);
        if (!map[key]) map[key] = { dist: 0, count: 0 };
        map[key].dist += r.distance_km || 0;
        map[key].count++;
    });
    var best = null, bestDist = -1;
    Object.keys(map).forEach(function(k) {
        if (map[k].dist > bestDist) { bestDist = map[k].dist; best = k; }
    });
    return best ? { key: best, dist: bestDist, count: map[best].count } : null;
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

function rwRenderRecords(runs) {
    var onlyRuns = runs.filter(function(r) { return r.activity_type === 'Run'; });
    var bMonth = rwBestMonth(onlyRuns);
    var wMonth = rwWorstMonth(onlyRuns);
    var bYear = rwBestYear(onlyRuns);
    var f5k = rwFastest(onlyRuns, 5);
    var f10k = rwFastest(onlyRuns, 10);
    var fHM = rwFastest(onlyRuns, 21.0975);
    var fFM = rwFastest(onlyRuns, 42.195);

    function raceCard(label, run) {
        if (!run) return makeCard('--', label, '&nbsp;', false, null);
        var pace = fmtPace(run.distance_km, run.duration_sec);
        var time = fmtDur(run.duration_sec);
        var sub = time + ' · ' + fmtDate(run.date);
        return makeCard(pace, label, sub, true, 'rwOpenDetail(\'' + run.name + '\')');
    }

    var html = '';
    html += makeCard(bMonth ? fmtMonthKey(bMonth.key) : '--', 'Best month', bMonth ? fmtDist(bMonth.dist) + ' · ' + bMonth.count + ' runs' : '&nbsp;', false, null);
    html += makeCard(wMonth ? fmtMonthKey(wMonth.key) : '--', 'Worst month', wMonth ? fmtDist(wMonth.dist) + ' · ' + wMonth.count + ' runs' : '&nbsp;', false, null);
    html += makeCard(bYear ? bYear.key : '--', 'Best year', bYear ? fmtDist(bYear.dist) + ' · ' + bYear.count + ' runs' : '&nbsp;', false, null);
    html += raceCard('Fastest 5k', f5k);
    html += raceCard('Fastest 10k', f10k);
    html += raceCard('Fastest HM', fHM);
    html += raceCard('Fastest FM', fFM);

    var el = document.getElementById('rw-records');
    if (el) el.innerHTML = html;
}

function rwShowModal() {
    var rows = rwState.runs.map(function(r) {
        return '<tr onclick="rwOpenDetail(\'' + r.name + '\')">' +
            '<td>' + fmtDate(r.date) + '</td>' +
            '<td>' + actPill(r.activity_type) + '</td>' +
            '<td>' + (r.location || '--') + '</td>' +
            '<td>' + fmtDist(r.distance_km||0) + '</td>' +
            '<td>' + fmtDur(r.duration_sec||0) + '</td>' +
            '<td>' + fmtPace(r.distance_km, r.duration_sec) + '</td>' +
            '</tr>';
    }).join('');
    document.getElementById('rw-modal-rows').innerHTML = rows;
    document.getElementById('rw-modal-title').textContent = rwState.runs.length + ' runs';
    document.getElementById('rw-modal').style.display = 'flex';
}

function rwCloseModal(e) {
    if (!e || e.target === document.getElementById('rw-modal')) {
        document.getElementById('rw-modal').style.display = 'none';
    }
}

function rwOpenDetail(name) {
    window.open('/run-detail?name=' + name, '_blank');
}

function rwRenderTicker() {
    var page = rwState.tickerPage;
    var pageSize = 4;
    var start = page * pageSize;
    var items = rwTickerRuns.slice(start, start + pageSize);
    var totalPages = Math.ceil(rwTickerRuns.length / pageSize);
    var html = items.map(function(r) {
        var emoji = r.activity_type === 'Swimming' ? '\uD83C\uDFC4' : r.activity_type === 'Cycling' ? '\uD83D\uDEB4' : r.activity_type === 'Walk' ? '\uD83D\uDEB6' : '\uD83C\uDFC3';
        return '<div class="rw-ticker-item" onclick="rwOpenDetail(\'' + r.name + '\')">' +
            emoji + ' ' + fmtDate(r.date) + ' · ' + (r.location || '') +
            ' · ' + fmtDist(r.distance_km||0) +
            (r.duration_sec ? ' · ' + fmtPace(r.distance_km, r.duration_sec) : '') +
            '</div>';
    }).join('');
    document.getElementById('rw-ticker').innerHTML = html;
    document.getElementById('rw-ticker-page').textContent = (page+1) + '/' + Math.max(1, totalPages);
}

function rwTickerNext() {
    var totalPages = Math.ceil(rwTickerRuns.length / 4);
    if (rwState.tickerPage < totalPages - 1) { rwState.tickerPage++; rwRenderTicker(); }
}
function rwTickerPrev() {
    if (rwState.tickerPage > 0) { rwState.tickerPage--; rwRenderTicker(); }
}