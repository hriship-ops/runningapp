var params = new URLSearchParams(window.location.search);
var runName = params.get('name');

if (!runName) {
    document.getElementById('rd-title').textContent = 'No run specified';
} else {
    fetch('/api/method/runningapp.running_journal.doctype.run.run.get_run?name=' + encodeURIComponent(runName))
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.message) rdRender(data.message);
        else document.getElementById('rd-title').textContent = 'Run not found';
    })
    .catch(function() {
        document.getElementById('rd-title').textContent = 'Error loading run';
    });
}

function rdRender(run) {
    var pts = rdParsePoints(run.route_points);
    var splits = rdCalcSplits(pts, run.duration_sec, run.distance_km);
    var title = rdMakeTitle(run.date, run.location, run.activity_type);
    var dateStr = run.date ? new Date(run.date + 'T12:00:00').toLocaleDateString('en-US', {day:'numeric', month:'short', year:'numeric'}) : '';
    var hasAlt = pts.length > 0 && pts[0].ele != null;

    document.getElementById('rd-title').textContent = title;
    document.getElementById('rd-meta').textContent = dateStr + (run.location ? ' · ' + run.location : '');
    document.getElementById('rd-notes').value = run.notes || '';

    var dist = (run.distance_km || 0).toFixed(2);
    var dur = rdFmtDuration(run.duration_sec);
    var pace = rdFmtPace(run.distance_km, run.duration_sec);
    var elev = Math.round(run.elevation_gain || 0);
    var cal = Math.round(run.calories || 0);
    var avgHR = run.avg_heart_rate ? Math.round(run.avg_heart_rate) : '--';
    var maxHR = run.max_heart_rate ? Math.round(run.max_heart_rate) : '--';

    document.getElementById('rd-stats').innerHTML =
        rdStat('Distance', dist, 'km') +
        rdStat('Time', dur, 'h:m:s') +
        rdStat('Avg Pace', pace, '/km', true) +
        rdStat('Elevation', elev, 'm gain') +
        rdStat('Calories', cal, 'kcal') +
        rdStat('Avg / Max HR', avgHR + ' / ' + maxHR, 'bpm');

    if (pts.length > 0) rdInitMap(pts);
    if (hasAlt) rdDrawElevation(pts);
    if (splits.length > 0) rdRenderSplits(splits);
}

function rdStat(label, val, unit, accent) {
    return '<div class="rd-stat">' +
        '<div class="rd-stat-label">' + label + '</div>' +
        '<div class="rd-stat-val" style="' + (accent ? '' : 'color:#f1f5f9') + '">' + val + '</div>' +
        '<div class="rd-stat-unit">' + unit + '</div>' +
        '</div>';
}

function rdInitMap(pts) {
    var map = L.map('rd-map', {zoomControl:true, scrollWheelZoom:false});
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution:'© CartoDB', maxZoom:18
    }).addTo(map);
    var latlngs = pts.map(function(p) { return [p.lat, p.lon || p.lng]; });
    var poly = L.polyline(latlngs, {color:'#ea580c', weight:3, opacity:0.9}).addTo(map);
    map.fitBounds(poly.getBounds(), {padding:[30,30]});
    L.circleMarker(latlngs[0], {radius:7, color:'#fff', fillColor:'#22c55e', fillOpacity:1, weight:2}).addTo(map).bindTooltip('Start');
    L.circleMarker(latlngs[latlngs.length-1], {radius:7, color:'#fff', fillColor:'#ef4444', fillOpacity:1, weight:2}).addTo(map).bindTooltip('End');
}

function rdDrawElevation(pts) {
    var alts = pts.map(function(p) { return p.ele; });
    var mn = Math.min.apply(null, alts), mx = Math.max.apply(null, alts);
    var W = 820, H = 80, pad = 4;
    var sx = function(i) { return (i / (pts.length-1)) * W; };
    var sy = function(a) { return H - pad - ((a-mn)/(mx-mn||1)) * (H-pad*2); };
    var d = 'M0,' + sy(alts[0]);
    for (var i = 1; i < alts.length; i++) d += ' L' + sx(i).toFixed(1) + ',' + sy(alts[i]).toFixed(1);
    var fill = d + ' L' + W + ',' + H + ' L0,' + H + ' Z';
    var svg = document.getElementById('rd-elev');
    svg.innerHTML = '<defs><linearGradient id="eg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#ea580c" stop-opacity="0.35"/><stop offset="100%" stop-color="#ea580c" stop-opacity="0.03"/></linearGradient></defs>' +
        '<path d="' + fill + '" fill="url(#eg)"/>' +
        '<path d="' + d + '" fill="none" stroke="#ea580c" stroke-width="2"/>';
    document.getElementById('rd-elev-section').style.display = '';
}

function rdCalcSplits(pts, totalSec, totalKm) {
    if (!pts.length || !totalSec || !totalKm) return [];
    var hasTime = pts[0].t != null;
    var hasHR = pts[0].hr != null;
    var MIN_MOVING_SPEED = 0.5;
    var cumDist = [0], cumActive = [0];
    for (var i = 1; i < pts.length; i++) {
        var segDist = rdHaversine(pts[i-1], pts[i]);
        var dt = hasTime ? (pts[i].t - pts[i-1].t) : 0;
        var speed = (dt > 0) ? segDist / dt : 0;
        var activedt = (dt > 0 && speed >= MIN_MOVING_SPEED) ? dt : 0;
        cumDist.push(cumDist[i-1] + segDist);
        cumActive.push(cumActive[i-1] + activedt);
    }
    var totalDist = cumDist[cumDist.length - 1];
    var numSplits = Math.min(Math.floor(totalKm), Math.floor(totalDist / 1000));
    if (numSplits < 1) return [];
    var splits = [], prevSec = 0, hrBuf = [], ptIdx = 0;
    for (var km = 1; km <= numSplits; km++) {
        var boundary = km * 1000;
        var j = ptIdx;
        while (j < cumDist.length - 1 && cumDist[j+1] < boundary) j++;
        if (j >= cumDist.length - 1) break;
        var frac = (cumDist[j+1] - cumDist[j]) > 0
            ? (boundary - cumDist[j]) / (cumDist[j+1] - cumDist[j]) : 1;
        frac = Math.max(0, Math.min(1, frac));
        var secAtBoundary = hasTime
            ? cumActive[j] + frac * (cumActive[j+1] - cumActive[j])
            : (boundary / totalDist) * totalSec;
        while (ptIdx <= j) {
            if (hasHR && pts[ptIdx].hr) hrBuf.push(pts[ptIdx].hr);
            ptIdx++;
        }
        var avgHR = hrBuf.length ? Math.round(hrBuf.reduce(function(a,b){return a+b;},0) / hrBuf.length) : null;
        splits.push({ km: km, sec: secAtBoundary - prevSec, hr: avgHR });
        prevSec = secAtBoundary;
        hrBuf = [];
    }
    return splits;
}

function rdRenderSplits(splits) {
    var secs = splits.map(function(s) { return s.sec; });
    var best = Math.min.apply(null, secs);
    var avg = secs.reduce(function(a,b) { return a+b; }, 0) / secs.length;
    var hasHR = splits.some(function(s) { return s.hr != null; });
    var html = splits.map(function(s) {
        var cls = s.sec === best ? 'rd-split rd-split-best' : s.sec < avg ? 'rd-split rd-split-fast' : 'rd-split';
        return '<div class="' + cls + '">' +
            '<div class="rd-split-km">km ' + s.km + '</div>' +
            '<div class="rd-split-pace">' + rdFmtPaceSec(s.sec) + '</div>' +
            (hasHR && s.hr ? '<div class="rd-split-hr">' + s.hr + '</div>' : '') +
            '</div>';
    }).join('');
    document.getElementById('rd-splits').innerHTML = html;
    document.getElementById('rd-splits-section').style.display = '';
}

function rdParsePoints(raw) {
    if (!raw) return [];
    try {
        var data = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (!Array.isArray(data)) return [];
        return data.filter(function(p) { return p.lat != null; });
    } catch(e) { return []; }
}

function rdHaversine(a, b) {
    var R = 6371000, toR = function(x) { return x * Math.PI / 180; };
    var dLat = toR(b.lat-a.lat), dLon = toR((b.lon||b.lng)-(a.lon||a.lng));
    var x = Math.sin(dLat/2)*Math.sin(dLat/2) + Math.cos(toR(a.lat))*Math.cos(toR(b.lat))*Math.sin(dLon/2)*Math.sin(dLon/2);
    return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1-x));
}

function rdMakeTitle(date, location, actType) {
    if (!date) return 'Run';
    var d = new Date(date + 'T12:00:00');
    var h = d.getHours();
    var tod = h < 11 ? 'Morning' : h < 14 ? 'Midday' : h < 18 ? 'Afternoon' : 'Evening';
    var act = actType || 'Run';
    var loc = location ? ' — ' + location.split(',')[0] : '';
    return tod + ' ' + act + loc;
}

function rdFmtDuration(sec) {
    if (!sec) return '0:00:00';
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = Math.floor(sec%60);
    return h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
}

function rdFmtPace(km, sec) {
    if (!km || !sec) return '--:--';
    return rdFmtPaceSec(sec/km);
}

function rdFmtPaceSec(sec) {
    var m = Math.floor(sec/60), s = Math.round(sec%60);
    return m + ':' + String(s).padStart(2,'0');
}