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

DEADLINE = time.time() + 6 * 60  # efter detta görs inga fler omförsök (jobbet ska aldrig hänga)

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def ajax(retries=4, delay=1, **params):
    """admin-ajax-anrop med omförsök. Källan svarar ibland HTTP 400 eller {"success":false} slumpmässigt (ca hälften av get_update-anropen)."""
    why = "ok"
    for attempt in range(retries):
        if attempt and time.time() > DEADLINE:
            why = "deadline"; break
        try:
            j = json.loads(get(AJAX + "?" + urllib.parse.urlencode(params)))
            if j.get("success") and j.get("results"): return j["results"]
            why = "success=false"
        except Exception as e:
            why = type(e).__name__ + " " + str(e)[:60]
        if params.get("action") == "get_update":
            log(f"get_update försök {attempt + 1}/{retries} misslyckades: {why}")
        time.sleep(delay)
    if params.get("action") != "get_update":
        log(f"{params.get('action')} id={params.get('id')} gav upp: {why}")
    return None

TRACKS = {}  # racer-id -> track_addition (fylls av main från ett enda get_update-anrop)

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

def bearing(a, b):
    r = math.pi / 180
    y = math.sin((b[1]-a[1])*r) * math.cos(b[0]*r)
    x = math.cos(a[0]*r)*math.sin(b[0]*r) - math.sin(a[0]*r)*math.cos(b[0]*r)*math.cos((b[1]-a[1])*r)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def motion(ta):
    """Riktning (grader) och fart (km/h, senaste ~10 min) ur råa spårpunkter."""
    if not ta: return None, None
    lat, lng = ta["trackStart"]["pos"]["lat"], ta["trackStart"]["pos"]["lng"]
    t = 0; pts = [(0, [lat/1e6, lng/1e6])]
    for dl, dg, dt in zip(ta["trackPoints"]["lats"], ta["trackPoints"]["lngs"], ta["trackPoints"]["timestamps"]):
        lat += dl; lng += dg; t += dt
        pts.append((t, [lat/1e6, lng/1e6]))
    end_t, end_p = pts[-1]
    heading = None
    for tt, pp in reversed(pts):
        if hav(pp, end_p) >= 0.1: heading = round(bearing(pp, end_p)); break
    speed = 0.0
    for tt, pp in reversed(pts):
        if end_t - tt >= 600 or (tt, pp) == pts[0]:
            if end_t > tt: speed = hav(pp, end_p) / ((end_t - tt) / 3600)
            break
    return heading, round(speed, 1)

def parse_times(times):
    """'Th, 06:00:00' / '06:20:32' / '-' -> absolutsekunder (dag räknas upp vid dagsprefix eller när klockan går bakåt)."""
    out, day, prev = [], -1, None
    for t in times:
        raw = (t.get("time") or "").strip()
        m = re.match(r"(?:([A-Za-z]{2}),\s*)?(\d{1,2}):(\d{2}):(\d{2})", raw)
        if not m: out.append(None); continue
        sec = int(m.group(2))*3600 + int(m.group(3))*60 + int(m.group(4))
        if m.group(1) or prev is None or sec < prev: day += 1
        prev = sec
        out.append(day*86400 + sec)
    return out

def team_detail(args):
    rid, track_id, race_id, app_id, outdir = args
    rt = ajax(action="get_racer_times", id=rid, track_id=track_id)
    if not rt: return False
    racer = {k: rt["racer"].get(k) for k in RACER_KEYS}
    ta = TRACKS.get(str(rid))
    track = decode_track(ta, None)
    heading, speed = motion(ta)
    times = [{"name": t["name"], "time": t.get("time") or ""} for t in rt["times"]]
    return {"id": rid, "racer": racer, "times": times, "track": track, "heading": heading, "speed": speed}

def main(out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    log("hämtar sidan")
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

    paris = ZoneInfo("Europe/Paris")
    # Källan kräver from_datetime >= racestart (UTC). Racestart som from_datetime ger positioner + hela spåren för alla lag i ett anrop.
    start_utc = datetime.fromisoformat(td["raceData"]["real_race_start"]).replace(tzinfo=paris).astimezone(ZoneInfo("UTC"))
    live = ajax(retries=8, delay=3, action="get_update", race_id=td["raceId"], app_id=td["appId"], track_id=0, racer_id=0,
                from_datetime=start_utc.strftime("%Y-%m-%d %H:%M:%S"))
    if not live:
        print("::warning::get_update misslyckades efter omförsök – behåller senast publicerade data", flush=True)
        sys.exit(0)  # ingen data.json skrivs => workflowet hoppar över publiceringen
    log(f"get_update ok: {len(live['positions'])} lag")
    TRACKS.update({str(p["id"]): p.get("track_addition") for p in live["positions"]})
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
    doc = {"race": td["raceData"]["name"], "generated": datetime.now(paris).isoformat(timespec="seconds"),
        "server_time_utc": live["server_time_utc"], "total_km": round(cum[-1], 2),
        "course": [[round(a, 5), round(b, 5)] for a, b in course], "checkpoints": cps,
        "start": datetime.fromisoformat(td["raceData"]["real_race_start"]).replace(tzinfo=paris).isoformat()}
    json.dump(doc | {"teams": teams}, open(out, "w"), separators=(",", ":"), ensure_ascii=False)
    outdir = os.path.dirname(os.path.abspath(out))
    os.makedirs(os.path.join(outdir, "teams"), exist_ok=True)
    with ThreadPoolExecutor(4) as ex:
        details = [d for d in ex.map(team_detail, [(t["id"], 0, td["raceId"], td["appId"], outdir) for t in teams]) if d]
    # delsträckor: tid sedan senast passerade checkpoint; snabbaste per exakt sträcka markeras
    best = {}
    for d in details:
        ab = parse_times(d["times"]); prev_i = None
        for i, a in enumerate(ab):
            d["times"][i]["split"] = None; d["times"][i]["leg"] = None
            if a is None: continue
            if prev_i is not None and a > ab[prev_i]:
                sp = a - ab[prev_i]; key = f"{prev_i}-{i}"
                d["times"][i]["split"] = sp; d["times"][i]["leg"] = key
                if key not in best or sp < best[key]: best[key] = sp
            prev_i = i
    for d in details:
        for t in d["times"]:
            t["best"] = bool(t["leg"] and best.get(t["leg"]) == t["split"]); t.pop("leg", None)
        json.dump({"racer": d["racer"], "times": d["times"], "track": d["track"]},
                  open(os.path.join(outdir, "teams", f"{d['id']}.json"), "w"), separators=(",", ":"), ensure_ascii=False)
    mo = {d["id"]: d for d in details}
    for t in teams:
        d = mo.get(t["id"])
        t["heading"] = d["heading"] if d else None
        t["spd"] = d["speed"] if d else None
    json.dump(doc | {"teams": teams}, open(out, "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"{len(details)}/{len(teams)} lagdetaljer, {len(best)} sträckor")
    print(f"{len(teams)} lag, {len(course)} bananpunkter, {len(cps)} checkpoints, {cum[-1]:.1f} km")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data.json")
