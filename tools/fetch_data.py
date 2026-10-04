#!/usr/bin/env python3
"""Hämtar ARWC2026 från follow.me.cz och skriver data.json (bana, checkpoints, lag)."""
import json, math, re, sys, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = "https://en.follow.me.cz"
PAGE = BASE + "/tracking-en/ARWC2026/"
UA = {"User-Agent": "Mozilla/5.0 (tracking-demo)"}
ISO3 = {"FRA":"FR","EST":"EE","SWE":"SE","NOR":"NO","FIN":"FI","DEN":"DK","DNK":"DK","GER":"DE","DEU":"DE","ESP":"ES","ITA":"IT","CZE":"CZ","SVK":"SK","POL":"PL","NED":"NL","NLD":"NL","BEL":"BE","SUI":"CH","CHE":"CH","AUT":"AT","GBR":"GB","GRB":"GB","USA":"US","CAN":"CA","AUS":"AU","NZL":"NZ","BRA":"BR","ARG":"AR","CHL":"CL","CHI":"CL","JPN":"JP","CHN":"CN","POR":"PT","PRT":"PT","HUN":"HU","LAT":"LV","LVA":"LV","LTU":"LT","LIT":"LT","RSA":"ZA","ZAF":"ZA","IRL":"IE","ISL":"IS","SLO":"SI","SVN":"SI","CRO":"HR","HRV":"HR","UKR":"UA","RUS":"RU","MEX":"MX","COL":"CO","ECU":"EC","PER":"PE"}

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode()

def decode_poly(s):
    pts, i, lat, lng = [], 0, 0, 0
    while i < len(s):
        for k in (0, 1):
            shift = res = 0
            while True:
                b = ord(s[i]) - 63; i += 1
                res |= (b & 31) << shift; shift += 5
                if b < 32: break
            d = ~(res >> 1) if res & 1 else res >> 1
            if k == 0: lat += d
            else: lng += d
        pts.append([lat / 1e5, lng / 1e5])
    return pts

def hav(a, b):
    r = math.pi / 180
    x = math.sin((b[0]-a[0])*r/2)**2 + math.cos(a[0]*r)*math.cos(b[0]*r)*math.sin((b[1]-a[1])*r/2)**2
    return 12742 * math.asin(math.sqrt(x))

def project(course, cum, p):
    """Närmaste punkt på banan -> (km längs banan, avstånd från banan i km)."""
    best = (1e9, 0)
    kx = math.cos(p[0] * math.pi / 180)
    for i in range(1, len(course)):
        a, b = course[i-1], course[i]
        dx, dy = (b[1]-a[1])*kx, b[0]-a[0]
        L2 = dx*dx + dy*dy
        t = 0 if L2 == 0 else max(0, min(1, (((p[1]-a[1])*kx)*dx + (p[0]-a[0])*dy) / L2))
        q = [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t]
        d = hav(p, q)
        if d < best[0]: best = (d, cum[i-1] + (cum[i]-cum[i-1])*t)
    return best[1], best[0]

def main(out):
    html = get(PAGE)
    m = re.search(r"var track_data = (\{.*?\n\t\t\});", html, re.S)
    td = json.loads(m.group(1))
    track = td["tracks"][0]
    course = decode_poly(track["polyline"])
    cum = [0.0]
    for i in range(1, len(course)): cum.append(cum[-1] + hav(course[i-1], course[i]))
    cps = []
    for c in list(td["checkpoints"].values())[0]:
        if c.get("display") == "0": continue
        p = [float(c["location_lat"]), float(c["location_lng"])]
        km, _ = project(course, cum, p)
        cps.append({"name": c["name"], "code": c["code"], "lat": p[0], "lng": p[1], "km": round(km, 2)})
    cps.sort(key=lambda c: c["km"])

    q = urllib.parse.urlencode({"action":"get_update","race_id":td["raceId"],"app_id":td["appId"],"track_id":0,"racer_id":0,"from_datetime":""})
    live = json.loads(get(BASE + "/wp-admin/admin-ajax.php?" + q))["results"]
    paris = ZoneInfo("Europe/Paris")
    now = datetime.now(paris)
    teams = []
    for r in live["positions"]:
        if r.get("display") == "0" or r.get("lat") in (None, ""): continue
        pos = [float(r["lat"]), float(r["lng"])]
        km, off = project(course, cum, pos)
        seen = None
        mm = re.match(r"(\d+)\.(\d+)\.\s+(\d+):(\d+)", r.get("utc") or "")
        if mm:
            d, mo, h, mi = map(int, mm.groups())
            seen = datetime(now.year, mo, d, h, mi, tzinfo=paris).isoformat()
        iso = ISO3.get((r.get("nationality") or "").upper(), (r.get("nationality") or "")[:2].upper())
        teams.append({"id": int(r["id"]), "name": r["name"], "no": r["race_number"], "country": r.get("nationality") or "",
            "flag": "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso) if len(iso) == 2 and iso.isalpha() else "",
            "category": r.get("category_name") or "", "rank": int(r["rank"]) if str(r.get("rank","")).isdigit() else None,
            "lat": pos[0], "lng": pos[1], "km": round(km, 2), "off": round(off, 2), "seen": seen,
            "lost": bool(r.get("lost_signal")), "status": int(r.get("status_id") or 0), "finish": r.get("finish_time") or ""})
    teams.sort(key=lambda t: -t["km"])
    json.dump({"race": td["raceData"]["name"], "generated": datetime.now(paris).isoformat(timespec="seconds"),
        "server_time_utc": live["server_time_utc"], "total_km": round(cum[-1], 2),
        "course": [[round(a, 5), round(b, 5)] for a, b in course], "checkpoints": cps, "teams": teams},
        open(out, "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"{len(teams)} lag, {len(course)} bananpunkter, {len(cps)} checkpoints, {cum[-1]:.1f} km")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data.json")
