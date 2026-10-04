#!/usr/bin/env python3
"""Hämtar ARWC2026 från follow.me.cz och skriver data.json (bana, checkpoints, lag)."""
import json, math, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
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

AJAX = BASE + "/wp-admin/admin-ajax.php"
RACER_KEYS = ["name","race_number","category_name","rank","photo","battery_level","battery_time","altitude","pace","speed","vam",
              "moving_time","standing_time","status","status_position","latitude","longitude"]

def ajax(**params):
    for attempt in range(3):
        try:
            return json.loads(get(AJAX + "?" + urllib.parse.urlencode(params)))["results"]
        except Exception:
            time.sleep(1 + attempt)
    return None

def decode_track(ta, tz):
    """track_addition = startpunkt (µ-grader) + deltor; returnerar [[lat,lng],..] glesad till ~30 m."""
    if not ta: return []
    lat, lng = ta["trackStart"]["pos"]["lat"], ta["trackStart"]["pos"]["lng"]
    pts = [[lat / 1e6, lng / 1e6]]
    for dl, dg in zip(ta["trackPoints"]["lats"], ta["trackPoints"]["lngs"]):
        lat += dl; lng += dg
        p = [lat / 1e6, lng / 1e6]
        if hav(pts[-1], p) >= 0.03: pts.append(p)
    if pts[-1] != [lat / 1e6, lng / 1e6]: pts.append([lat / 1e6, lng / 1e6])
    return [[round(a, 5), round(b, 5)] for a, b in pts]

def team_detail(args):
    rid, track_id, race_id, app_id, outdir = args
    rt = ajax(action="get_racer_times", id=rid, track_id=track_id, utc_replay_time=0)
    up = ajax(action="get_update", race_id=race_id, app_id=app_id, track_id=track_id, racer_id=rid,
              additional_racer_id=rid, from_datetime="2026-01-01 00:00:00")
    if not rt: return False
    racer = {k: rt["racer"].get(k) for k in RACER_KEYS}
    track = []
    if up:
        me = next((p for p in up["positions"] if str(p["id"]) == str(rid)), None)
        track = decode_track(me.get("track_addition") if me else None, None)
    times = [{"name": t["name"], "time": t.get("time") or ""} for t in rt["times"]]
    json.dump({"racer": racer, "times": times, "track": track},
              open(os.path.join(outdir, "teams", f"{rid}.json"), "w"), separators=(",", ":"), ensure_ascii=False)
    return True

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
    outdir = os.path.dirname(os.path.abspath(out))
    os.makedirs(os.path.join(outdir, "teams"), exist_ok=True)
    with ThreadPoolExecutor(4) as ex:
        ok = sum(ex.map(team_detail, [(t["id"], 0, td["raceId"], td["appId"], outdir) for t in teams]))
    print(f"{ok}/{len(teams)} lagdetaljer")
    print(f"{len(teams)} lag, {len(course)} bananpunkter, {len(cps)} checkpoints, {cum[-1]:.1f} km")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data.json")
