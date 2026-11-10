#!/usr/bin/env python -X utf8
# -*- coding: utf-8 -*-
"""rea_site/build.py —— 把本地全部研究数据编译成一个自包含静态站

输出 rea_site/site/index.html（数据内联，离线可看，可直接丢 GitHub Pages / 任意静态托管）
     rea_site/site/data.json（同一份数据，给机器/别的页面用）
每天 20:40 出票后由定时任务跑一次即可（也可手动 python -X utf8 build.py）。
"""
import os, re, csv, json, glob, datetime

BASE = os.environ.get("SFC_BASE") or os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("SFC_ROOT") or os.path.dirname(BASE)
SITE = os.environ.get("SFC_OUT") or os.path.join(BASE, "site")
os.makedirs(SITE, exist_ok=True)

S500 = os.path.join(ROOT, "rea_500", "cache")
SDJY = os.path.join(ROOT, "rea_djyy", "cache")
ARCH = os.path.join(ROOT, "rea_zgzcw", "archive")

BOOKS = {"2": "威廉", "3": "澳门", "5": "bet365", "6": "立博", "293": "立明"}


def jload(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def latest(d, pat):
    fs = [f for f in glob.glob(os.path.join(d, pat)) if not os.path.basename(f).startswith("_")]
    fs.sort(key=lambda f: (os.path.getmtime(f), os.path.basename(f)))   # git checkout 会把 mtime 拉平，同分就按文件名（我们文件名都带日期）
    return fs[-1] if fs else None


def newest_n(d, pat, n):
    fs = [f for f in glob.glob(os.path.join(d, pat)) if not os.path.basename(f).startswith("_")]
    fs.sort(key=lambda f: (os.path.getmtime(f), os.path.basename(f)), reverse=True)
    return fs[:n]


D = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds")}
log = []

# 对外不出现第三方站点品牌名（含运行时抓来的文本，如卡片"未收录"提示）
SCRUB = [(re.compile(r"djyy\s*live\.com", re.I), "外部数据源"),
         (re.compile(r"djyy\s*live", re.I), "外部数据源"),
         (re.compile(r"djyy\s*data\.com", re.I), "回测库"),
         (re.compile(r"djyy\s*data", re.I), "回测库"),
         (re.compile(r"djyy", re.I), "外部数据源"),
         (re.compile(r"策略实验室", re.I), "回测库"),
         (re.compile(r"Pinnacle", re.I), "主流机构")]


def scrub(o):
    if isinstance(o, str):
        for rx, rep in SCRUB:
            o = rx.sub(rep, o)
        return o
    if isinstance(o, list):
        return [scrub(x) for x in o]
    if isinstance(o, dict):
        return {scrub(k): scrub(v) for k, v in o.items()}
    return o

# ---------- 1. 当期信号（500 四层）----------
sig_f = latest(S500, "signals_*.json")
sig = jload(sig_f) or {}
if sig:
    rows = sig.get("rows") or []
    zx = sig.get("zx") or {}
    so = sig.get("same_odds") or {}
    # 开赛时间优先用本次抓取（rows 自带 time）；那份手工 rj_times.json 换期就会套错，只在没得选时用
    times = {str(r["no"]): r["time"] for r in rows if r.get("time")}
    if not times:
        _rt = os.path.join(S500, "rj_times.json")
        if os.path.exists(_rt) and os.path.getmtime(_rt) >= os.path.getmtime(sig_f):
            times = {str(r.get("no")): r.get("time") for r in (jload(_rt) or [])}
    for r in rows:
        k = str(r["no"])
        p = r.get("prob") or {}
        pv = {kk: vv for kk, vv in p.items() if vv is not None}
        z = zx.get(k)
        if z and pv:
            z["mark"] = "同" if z.get("agree") == max(pv, key=pv.get) else "⚠背离"
        elif z:
            z["mark"] = "--"
    D["signals"] = {"issue": sig.get("issue"), "rows": rows, "zx": zx, "same_odds": so,
                    "times": times, "book_names": BOOKS,
                    "deadline": sig.get("deadline") or "", "fetched": sig.get("fetched") or "",
                    "sp_cmp": sig.get("sp_cmp") or [], "official": sig.get("official") or {},
                    "sp_open": any((r.get("sp") or {}).get("3") is not None for r in rows)}
    D["book_names"] = BOOKS
    log.append(f"signals {sig.get('issue')} {len(rows)}场 zx={len(zx)} 同赔={len(so)}")

# ---------- 2. 终盘参考（djyylive）----------
card_f = latest(SDJY, "card_*.json")
card = jload(card_f)
if card:
    # 接口回传的是浮点原值（如 1.7100000000000002、3.8000000000000003），入站前统一收成 2 位小数
    for _r in (card.get("rows") or []):
        _p = _r.get("pinn") or {}
        for _side in ("opening", "odds"):
            _d = _p.get(_side) or {}
            for _k in ("home", "draw", "away"):
                if isinstance(_d.get(_k), (int, float)):
                    _d[_k] = round(_d[_k], 2)
        _t = _r.get("totals") or {}
        if isinstance(_t.get("line"), (int, float)):
            _t["line"] = round(_t["line"], 2)
    D["card"] = card
    log.append(f"card {os.path.basename(card_f)} 覆盖 {card.get('coverage')}")

# ---------- 3. 票面（betslip 落盘文件）----------

def rows_from_push(issue):
    """从 push_<期>_定稿.md 的票面块取 01~14 全序 14 行（含弃场写 `-`）。
    betslip 的主表只列入选的 9 行，首屏「票面正在落定」要 14 行才完整。"""
    p = os.path.join(ARCH, "push_%s_定稿.md" % issue)
    if not os.path.exists(p) or not re.search(r"```", open(p, encoding="utf-8").read()):
        return []
    txt = open(p, encoding="utf-8").read()
    m = re.search(r"```[\s\S]*?```", txt)
    if not m:
        return []
    out = []
    for ln in m.group(0).splitlines():
        g = re.match(r"^(\d{2})\s+(\d{2}:\d{2})\s+(.+?)\s+([013](?:/[013])?|-)\s*$", ln.strip())
        if g:
            out.append({"no": int(g.group(1)), "vs": g.group(3).strip(),
                        "pick": "-" if g.group(4) == "-" else g.group(4), "tag": ""})
    return out if len(out) == 14 else []


def parse_betslip(path):
    txt = open(path, encoding="utf-8").read()
    issue = os.path.basename(path).split("_")[1].split(".")[0]
    t = re.search(r"（(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 截止）", txt)
    m = re.search(r"·\s*(\d+)注\s*·\s*(\d+)元", txt)
    pk = {}
    mm = re.search(r"310 记法速写：([^）]+)", txt)
    if mm:
        for a in mm.group(1).split(","):
            a = a.strip()
            g = re.match(r"(\d+)=([\d/]+)", a)
            if g:
                pk[int(g.group(1))] = re.sub(r"/{2,}", "/", g.group(2).strip())   # 「0/1」按字符 join 会变成 0//1
    for no in range(1, 15):
        if no not in pk:
            pk[no] = "弃"
    drops = "、".join(re.findall(r"放弃场次：(.*)", txt)[0].split("、")) if "放弃场次" in txt else ""
    alerts = []
    blk = re.search(r"## 定稿依据\n(.*?)(\n##|\Z)", txt, re.S)
    if blk:
        alerts = [re.sub(r"\*\*", "", l).lstrip("- ").strip()
                  for l in blk.group(1).splitlines() if l.strip().startswith("-")][:6]
    note = ""
    n = re.search(r"\*\*本期特殊：([^*]+)\*\*", txt)
    if n:
        note = n.group(1).strip()
    # 票面自带对阵：停售/开奖后 signals 会切到下一期，选号绝不能套到新期的比赛上
    # 选列容错：允许 **3** 后面跟括注（如「**3**（压缩胆）」）再收尾 —— 2026-11-06 就因
    # 严格要求「**3**|」把带括注的 04、07 两行整行丢掉，票面只剩 7 行
    rows = []
    for ln in txt.splitlines():
        if ln.startswith("## ") and rows:      # 第二张小节（B 组/任14）的表不算主票，否则命中数会混成 17/18
            break
        g = re.match(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*\*{0,2}\s*"
                     r"([013](?:\s*/\s*[013])?)\s*\*{0,2}\s*(?:[（(]([^）)]*)[）)])?\s*\|", ln)
        if g:
            rows.append({"no": int(g.group(2)), "vs": g.group(3).strip(),
                         "pick": re.sub(r"\s+", "", g.group(4)),
                         "tag": (g.group(5) or "").strip()})
    _full = rows_from_push(issue)
    if _full:
        rows = _full
    return {"issue": issue, "deadline": t.group(1) if t else "", "bets": int(m.group(1)) if m else None,
            "cost": int(m.group(2)) if m else None, "picks": pk, "drops": drops,
            "alerts": alerts, "note": note, "rows": rows}


bf = latest(ARCH, "betslip_*.md")
if bf:
    D["ticket"] = parse_betslip(bf)
    log.append(f"ticket {D['ticket']['issue']} {D['ticket']['bets']}注 {len(D['ticket']['picks'])}行")
    # 已停售 → 拉官方开奖结果，标成「已开奖」，并提示下一期什么时候出定稿
    _T = D["ticket"]
    try:
        _ddl = datetime.datetime.strptime(_T["deadline"], "%Y-%m-%d %H:%M")
    except Exception:
        _ddl = None
    _T["settled"] = bool(_ddl and _ddl < datetime.datetime.now())
    if _T["settled"]:
        _off = None
        for _f in sorted(glob.glob(os.path.join(S500, "sfc_official_*.json")), key=os.path.getmtime):
            for _r in ((jload(_f) or {}).get("games") or {}).get("14场") or []:
                if str(_r.get("issue")) == _T["issue"] and (_r.get("result") or []):
                    _off = _r
                    break
            if _off:
                break
        if _off:
            _res = [str(x) for x in _off["result"]]
            _rw = _T.get("rows") or []
            _sel = [r for r in _rw if r.get("pick") not in (None, "弃", "-")]
            for r in _rw:
                r["res"] = _res[int(r["no"]) - 1] if 1 <= int(r["no"]) <= len(_res) else ""
            _hit = sum(1 for r in _sel if r["res"] and r["res"] in r["pick"])
            _T.update({"result": " ".join(_res), "hit": _hit, "hit_n": len(_sel),
                       "won": bool(_sel) and _hit == len(_sel),
                       "rj_count": _off.get("rj_count"), "rj_prize": _off.get("rj_prize"),
                       "g1_count": _off.get("g1_count"), "g1_prize": _off.get("g1_prize"),
                       "g2_count": _off.get("g2_count"), "g2_prize": _off.get("g2_prize")})
            log.append(f"ticket {_T['issue']} 已开奖 310={''.join(_res)} 我方 {_hit}/{len(_sel)}"
                       + ("中奖" if _hit == len(_sel) else "未中"))
        else:
            log.append(f"ticket {_T['issue']} 已停售，官方开奖记录还没取到")
    _nx = str((D.get("signals") or {}).get("issue") or "")
    if _T["settled"] and _nx and _nx != str(_T["issue"]):
        _T["next"] = {"issue": _nx, "at": "20:40"}

# ---------- 4. 四场进球 ----------
jf = latest(S500, "jqc_*.json")
jq = jload(jf)
if jq:
    rows = jq.get("rows") or []
    clear = sum(1 for r in rows if r.get("odds") and min(float(r["odds"][0]), float(r["odds"][2])) <= 1.60)
    jq["clear"] = clear
    jq["min_clear"] = 2
    jq["skipped"] = clear < 2
    D["jqc"] = jq
    log.append(f"jqc {jq.get('issue')} 强弱分明 {clear}/{len(rows)} → {'跳过' if jq['skipped'] else '出票'}")

# ---------- 5. 近 10 期奖金行情（kaijiang 缓存）----------
prizes = []
for f in sorted(glob.glob(os.path.join(S500, "kj_*.html")), reverse=True):
    iss = os.path.basename(f)[3:-5]
    txt = re.sub(r"<[^>]+>", " ", open(f, encoding="utf-8", errors="ignore").read())
    txt = re.sub(r"[ \t\r\n]+", " ", txt)
    m = re.search(r"任九\s*([\d,]+)\s+([\d,]+)", txt)
    s = re.search(r"本期任九销量[：:]\s*([\d,.]+)", txt)
    if m:
        sales = None
        if s:
            try:
                sales = int(float(s.group(1).replace(",", "")))
            except Exception:
                sales = None
        prizes.append({"issue": iss, "rj_n": m.group(1), "rj_prize": m.group(2), "rj_sales": sales})
prizes = prizes[:10]
if prizes:
    vals = sorted(int(p["rj_prize"].replace(",", "")) for p in prizes)
    D["prizes"] = prizes
    D["prize_median"], D["prize_min"], D["prize_max"] = vals[len(vals) // 2], vals[0], vals[-1]
    log.append(f"prizes {len(prizes)} 期 中位 {D['prize_median']}")

# ---------- 6. 我方战绩 ----------
lg = {"rows": [], "stake": 0, "return": 0, "pnl": 0}
lp = os.path.join(ARCH, "我方战绩.md")
if os.path.exists(lp):
    in_rj = False
    for ln in open(lp, encoding="utf-8"):
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip().replace("*", "") for c in s.strip("|").split("|")]
        if cells[0] == "期号":          # 表头说了算：只有任九那张表（含"命中/净额"）进这里的账
            in_rj = "命中" in cells and "净额" in cells
            continue                    # 任14 台账（覆盖/奖项）与漏班记录结构不同，不混算
        if not in_rj or len(cells) < 7 or not re.fullmatch(r"26\d{3}", cells[0]):
            continue
        hit = re.search(r"(\d)/9", cells[3])
        nc = re.search(r"(\d+)\s*注\s*/\s*(\d+)\s*元", cells[2])
        win = re.search(r"(\d+)\s*元", cells[5])
        pnl = re.search(r"([+-]?\d+)\s*元", cells[-1])
        row = {"issue": cells[0], "ticket": cells[1],
               "hit": (hit.group(1) + "/9") if hit else "--",
               "bets": int(nc.group(1)) if nc else None,
               "cost": int(nc.group(2)) if nc else None,
               "result": cells[4],
               "win": int(win.group(1)) if win else 0,
               "pnl": int(pnl.group(1)) if pnl else 0}
        lg["rows"].append(row)
    for r in lg["rows"]:
        lg["stake"] += r.get("cost") or 0
        lg["return"] += r.get("win") or 0
        lg["pnl"] += r.get("pnl") or 0
    lg["rows"].sort(key=lambda r: r["issue"], reverse=True)
    # 已出票但官方还没开奖的最新一期：也要出现在这张表里（标待开奖，不计入盈亏合计）
    # —— 用户点名要的：「最新这一期的数据还看不到」，2026-11-07 加
    tk = D.get("ticket") or {}
    iss = str(tk.get("issue") or "")
    lg["settled_n"] = len({r["issue"] for r in lg["rows"]})   # 按**期号**去重：一期出 A/B 两组时不能算成两期
    if iss and re.fullmatch(r"26\d{3}", iss) and tk.get("rows") and not any(r["issue"] == iss for r in lg["rows"]):
        pk = tk.get("picks") or {}
        dan = sum(1 for v in pk.values() if "/" not in str(v) and "弃" not in str(v))
        dbl = sum(1 for v in pk.values() if "/" in str(v))
        drp = sum(1 for v in pk.values() if "弃" in str(v))
        lg["rows"].insert(0, {"issue": iss,
                              "ticket": ("%d胆+%d双" % (dan, dbl)) + (("（弃%d场）" % drp) if drp else ""),
                              "hit": "--", "bets": tk.get("bets"), "cost": tk.get("cost"),
                              "result": "待开奖", "win": None, "pnl": None, "pending": True})
        lg["pending"] = {"issue": iss, "cost": tk.get("cost") or 0, "deadline": tk.get("deadline") or ""}
        log.append("ledger 追加待开奖行 第%s期 %s注/%s元" % (iss, tk.get("bets"), tk.get("cost")))
    D["ledger"] = lg
    log.append(f"ledger {len(lg['rows'])} 期 投入 {lg['stake']} 回收 {lg['return']} 盈亏 {lg['pnl']}")

# ---------- 7. 实验室 ----------
lab = {}
# ---------- 本季进球缺口 → 首页「高节奏」免费提示条（同时给进球彩当依据）----------
gap_f = latest(SDJY, "gap_*.json")
gap = jload(gap_f) or {}
lgap = gap.get("leagues") or []
mgap = [m for m in (gap.get("matches") or []) if m.get("gap") is not None]
mgap.sort(key=lambda m: -(m.get("gap") or 0))
log_n = 0
try:
    with open(os.path.join(SDJY, "tempo_log.jsonl"), encoding="utf-8") as f:
        log_n = sum(1 for _ in f)
except OSError:
    pass
D["tempo"] = {"at": gap.get("generated_at"), "min_gap": gap.get("min_gap") or 0.35,
              "leagues": lgap[:16],
              "lagging": [x for x in lgap if (x.get("gap") or 0) >= (gap.get("min_gap") or 0.35)],
              "top": mgap[:36], "summary": gap.get("summary") or {}, "logged": log_n}
log.append(f"tempo 滞后联赛 {len(D['tempo']['lagging'])} 个 / 可测 {(gap.get('summary') or {}).get('n_leagues', 0)} 个，"
           f"高节奏场次 {len(mgap)} 场，样本账本 {log_n} 场")

lab["calib"] = jload(os.path.join(SDJY, "calib.json")) or {}
lab["formtest"] = jload(os.path.join(SDJY, "formtest.json")) or {}
lcal = jload(os.path.join(SDJY, "league-calibration.json")) or {}
if lcal:
    lab["league_cal"] = {"x2": lcal.get("x2"), "corners": lcal.get("corners"), "cards": lcal.get("cards")}
cu = jload(os.path.join(SDJY, "cuptest.json"))
if cu:
    lab["cuptest"] = cu

csvp = os.path.join(SDJY, "league_calibration_matches.csv")
if os.path.exists(csvp):
    band = {}
    tot = [0, 0]
    try:
        for r in csv.DictReader(open(csvp, encoding="utf-8-sig")):
            try:
                ph, pd, pa = float(r["模型主胜概率"]), float(r["模型平局概率"]), float(r["模型客胜概率"])
            except Exception:
                continue
            res = (r.get("赛果") or "").strip()
            idx = {"主胜": ph, "平局": pd, "客胜": pa}.get(res)
            if idx is None:
                continue
            top = max(ph, pd, pa)
            side = "主胜" if top == ph else ("平局" if top == pd else "客胜")
            for lo, hi in ((0, .35), (.35, .45), (.45, .55), (.55, .65), (.65, .75), (.75, 1.01)):
                if lo <= top < hi:
                    k = (lo, hi)
                    band.setdefault(k, [0, 0])
                    band[k][0] += 1
                    band[k][1] += 1 if side == res else 0
                    break
            tot[0] += 1
            tot[1] += 1 if side == res else 0
        lab["model_bands"] = [{"lo": k[0], "hi": k[1], "n": v[0], "hit": round(v[1] / v[0] * 100, 1)}
                              for k, v in sorted(band.items()) if v[0]]
        lab["model_overall"] = {"n": tot[0], "hit": round(tot[1] / max(tot[0], 1) * 100, 1)}
        # 角球方向正确率
        cc = [0, 0]
        for r in csv.DictReader(open(csvp, encoding="utf-8-sig")):
            try:
                p, line, cor = float(r["模型大球概率"]), float(r["角球盘口"]), int(r["实际角球数"])
            except Exception:
                continue
            cc[0] += 1
            cc[1] += 1 if ((p >= 0.5) == (cor > line)) else 0
        if cc[0]:
            lab["corners_dir"] = {"角球": round(cc[1] / cc[0] * 100, 1), "n": cc[0]}
        log.append(f"csv model_bands {len(lab['model_bands'])} 档 总体 {lab['model_overall']}")
    except Exception as e:
        log.append(f"csv 失败 {repr(e)[:40]}")

olf = jload(os.path.join(SDJY, "odds_lab_full.json"))
if olf:
    pts = ((olf.get("curve") or {}).get("points") or [])
    step = max(1, len(pts) // 400)
    lab["odds_lab"] = {"kpi": olf.get("kpi"), "curve": {"points": pts[::step]}}
    log.append(f"odds_lab kpi n={(olf.get('kpi') or {}).get('n')} curve {len(pts)}→{len(pts[::step])}")
D["lab"] = lab

# ---------- 7b. 首页全量比赛 + 任14 推荐 + 付费墙 ----------
SCACHE = os.path.join(BASE, "cache")
daily = jload(latest(SCACHE, "daily_leagues_*.json")) or {}
upf = latest(SDJY, "upcoming_*.json")
up = jload(upf) or {}
today = datetime.date.today().isoformat()
jc = jload(latest(os.path.join(ROOT, "rea_500", "cache"), "jc_*.json"))
hraw = jload(latest(SCACHE, "season_overview_*.json")) or {}


def age_h(p):
    """缓存距今的小时数，用于首页标注各数据源的刷新时效"""
    try:
        return round((datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 3600.0, 1)
    except Exception:
        return None


D["refresh"] = {"upcoming_h": age_h(upf),
                "jc_h": age_h(latest(os.path.join(ROOT, "rea_500", "cache"), "jc_*.json")),
                "daily_h": age_h(latest(SCACHE, "daily_leagues_*.json"))}

leagues = daily.get("leagues") or []
lg_by_zh = {l.get("zh"): l for l in leagues if l.get("zh")}
team_idx = {}
for l in leagues:
    for nm, t in (l.get("teams") or {}).items():
        team_idx.setdefault(nm, (l, t))


def norm(s):
    return re.sub(r"[\s\.\-·'（）()]", "", str(s or "")).lower()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.86
    k = 0
    for x, y in zip(a, b):
        if x != y:
            break
        k += 1
    return k / max(len(a), len(b))


OFFICIAL_KEYS = {"jc": ("code", "num", "give", "spf", "rqspf", "pool_status",
                            "plays", "ttg", "crs", "hafu"),
                 "bc": ("code", "num", "give", "spf", "rqspf", "pool_status",
                        "ttg", "crs", "fid", "ddl")}


def join_official(rows, src, tag):
    """官方场次表（竞彩/北单）按 同名+同日 贴到赛程行上。
    官方简称与全称是两套写法（帕梅拉斯 / 帕尔梅拉斯），两个都试，取更高的那个分。"""
    hit = 0
    for it in (src or []):
        d = (it.get("at") or "")[:10]
        pairs = [(it.get("home_full") or it.get("home"), it.get("away_full") or it.get("away")),
                 (it.get("home"), it.get("away"))]
        best, bs = None, 0.0
        for r in rows:
            if (r.get("at") or "")[:10] != d:
                continue
            s = max((sim(r.get("home"), hh) + sim(r.get("away"), aa)) / 2 for hh, aa in pairs)
            if s > bs:
                best, bs = r, s
        if best is not None and bs >= 0.6:
            best[tag] = {k: it.get(k) for k in OFFICIAL_KEYS.get(tag, ())}
            it["_used"] = True
            hit += 1
    return hit


def official_only(it, tag):
    """官方在售、但赛程表里没有的比赛：用官方SP去水给出方向，单独成卡"""
    sp = it.get("spf") or it.get("rqspf") or {}
    inv = {k: 1.0 / v for k, v in sp.items() if isinstance(v, (int, float)) and v > 1.0}
    tot = sum(inv.values())
    probs = {k: v / tot for k, v in inv.items()} if tot else {}
    top = max(probs, key=probs.get) if probs else None
    pow_ = probs.get(top) or 0
    nm = {"3": "主胜", "1": "平局", "0": "客胜"}
    tier, cls = (("强胆", "g") if pow_ >= .65 else ("稳胆", "g") if pow_ >= .55
                 else ("双选更稳", "y") if pow_ >= .45 else ("均势/冷门", "r"))
    r = {"no": 9000 + int(it.get("num") or 0), "at": it.get("at"), "lg": it.get("lg"),
         "country": it.get("country"), "season": None,
         "home": it.get("home") or it.get("home_full"), "away": it.get("away") or it.get("away_full"),
         "probs": {"home": probs.get("3"), "draw": probs.get("1"), "away": probs.get("0")},
         "odds": {"home": sp.get("3"), "draw": sp.get("1"), "away": sp.get("0")},
         "open": {}, "side": nm.get(top), "pow": pow_, "tier": tier, "tier_cls": cls,
         "draw_risk": ("高" if (probs.get("1") or 0) >= .31 and pow_ < .55 else None),
         "ou_tone": None, "cup": False, "gap": 0, "rankgap": "", "szc": False,
         "jc": ({k: it.get(k) for k in OFFICIAL_KEYS["jc"]} if tag == "jc" else None),
         "bc": ({k: it.get(k) for k in OFFICIAL_KEYS["bc"]} if tag == "bc" else None),
         "official_only": True,
         "note": "此场未进我们的赛程抓取，方向取自官方" + ("胜平负SP" if it.get("spf") else "让球SP") + "去水"}
    return r


szc_keys = set()
for r in (card or {}).get("rows", []):
    if r.get("home"):
        szc_keys.add((norm(r["home"]), norm(r["away"]), (r.get("at") or r.get("date") or "")[:10]))

POOL = []
for i, m in enumerate(up.get("all") or [], 1):
    lgz = m.get("lg") or ""
    L = lg_by_zh.get(lgz) or {}
    th = (team_idx.get(m.get("home")) or (None, {}))[1]
    ta = (team_idx.get(m.get("away")) or (None, {}))[1]
    allb = m.get("all") or []
    if not allb:
        continue
    pr = {r["side"]: r.get("market") for r in allb}
    md = {r["side"]: r.get("model") for r in allb}
    od = {r["side"]: r.get("odds") for r in allb}
    top = max(pr.items(), key=lambda kv: kv[1] or 0)[0]
    pow_ = pr.get(top) or 0
    best = m.get("best") or {}
    tier, cls = (("强胆", "g") if pow_ >= .65 else ("稳胆", "g") if pow_ >= .55
                 else ("双选更稳", "y") if pow_ >= .45 else ("均势/冷门", "r"))
    dr = pr.get("平局") or 0
    draw_risk = "高" if (dr >= .31 and pow_ < .55) else ("中" if dr >= .28 else None)
    ov = m.get("ou_over")
    oo = m.get("odds_open") or {}
    at = m.get("at") or ""
    POOL.append({
        "no": i, "at": at, "lg": lgz, "country": L.get("country"), "season": L.get("season"),
        "home": m.get("home"), "away": m.get("away"), "fid": m.get("fid"),
        "logo_h": th.get("logo"), "logo_a": ta.get("logo"),
        "pos_h": th.get("pos"), "pos_a": ta.get("pos"), "pts_h": th.get("pts"), "pts_a": ta.get("pts"),
        "form_h": th.get("form"), "form_a": ta.get("form"),
        "probs": {"home": pr.get("主胜"), "draw": pr.get("平局"), "away": pr.get("客胜")},
        "odds": {"home": od.get("主胜"), "draw": od.get("平局"), "away": od.get("客胜")},
        "open": {"home": oo.get("home"), "draw": oo.get("draw"), "away": oo.get("away")},
        "mp_h": md.get("主胜"), "mp_d": md.get("平局"), "mp_a": md.get("客胜"),
        "edge": best.get("edge"), "edge_side": best.get("side"),
        "ou_line": m.get("ou_line"), "over": ov, "ou_open": m.get("ou_open"),
        "ou_tone": ("大球倾向" if ov and ov >= .58 else ("小球倾向" if ov and ov <= .42 else None)),
        "btts": m.get("btts_yes"), "ah_line": m.get("ah_line"), "ah_home": m.get("ah_home"),
        "side": top, "pow": pow_, "tier": tier, "tier_cls": cls, "draw_risk": draw_risk,
        "cup": any(k in lgz for k in ("欧冠", "欧联", "欧协联")),
        "gap": (abs(th["pos"] - ta["pos"]) if isinstance(th.get("pos"), int) and isinstance(ta.get("pos"), int) else 0),
        "rankgap": (f"{th.get('pos')}名 vs {ta.get('pos')}名" if th.get("pos") and ta.get("pos") else ""),
        "jc": None, "bc": None,
        "szc": (norm(m.get("home")), norm(m.get("away")), at[:10]) in szc_keys})

n_jc = join_official(POOL, (jc or {}).get("jc"), "jc")
n_bc = join_official(POOL, (jc or {}).get("bc"), "bc")
n_extra = 0
for tag, src in (("jc", (jc or {}).get("jc")), ("bc", (jc or {}).get("bc"))):
    for it in (src or []):
        if it.get("_used"):
            continue
        POOL.append(official_only(it, tag))
        n_extra += 1
POOL.sort(key=lambda r: r.get("at") or "")

# 主任的全部玩法：竞彩(胜平负/让球胜平负/比分/总进球数/半全场胜平负 + 单关/混合过关)、
# 北京单场(让球胜平负/总进球数/比分/胜平负)、传统足彩(胜负彩14场/任选9场/4场进球/6场半全场)
JQC_ISSUE = jload(latest(os.path.join(ROOT, "rea_500", "cache"), "jqc_*.json")) or {}
SFC_ROWS = ((D.get("signals") or {}).get("rows") or [])
JQC_ROWS = JQC_ISSUE.get("rows") or []


def _vs_pair(vs):
    h, _, a = str(vs or "").partition("vs")
    return norm(h), norm(a)


def _member(rows, it):
    """按 500 fid 精确认，认不到再按 同队名+同日 兜底（返回期内的场次序号）"""
    fid = str(it.get("fid") or (it.get("bc") or {}).get("fid") or "")
    for r in rows:
        if fid and str(r.get("fid") or "") == fid:
            return rows.index(r) + 1
    hn, an = norm(it.get("home")), norm(it.get("away"))
    for r in rows:
        vh, va = _vs_pair(r.get("vs"))
        if sim(it.get("home"), vh) >= .6 and sim(it.get("away"), va) >= .6:
            return rows.index(r) + 1
    return None


PLAY_CT = {}
for it in POOL:
    jcd, bcd = it.get("jc") or {}, it.get("bc") or {}
    pl = [[u"竞彩", p.get("name"), int(p.get("single") or 0)] for p in (jcd.get("plays") or [])]
    for nm, has in ((u"让球胜平负", bcd.get("rqspf")), (u"总进球数", bcd.get("ttg")),
                    (u"比分", bcd.get("crs")), (u"胜平负", bcd.get("spf"))):
        if has and not any(x[0] == u"竞彩" and x[1] == nm for x in pl):
            pl.append([u"北单", nm, 0])
    sfc_no = _member(SFC_ROWS, it)
    if sfc_no:
        pl.append([u"足彩", u"胜负彩14场", 0])
        pl.append([u"足彩", u"任选9场", 0])
    jqc_no = _member(JQC_ROWS, it)
    if jqc_no:
        pl.append([u"足彩", u"4场进球", 0])
    it["plays"] = pl
    it["allup"] = 1 if any(int(p.get("allup") or 0) for p in (jcd.get("plays") or [])) else 0
    it["single_any"] = 1 if any(int(p.get("single") or 0) for p in (jcd.get("plays") or [])) else 0
    it["sfc_no"], it["jqc_no"] = sfc_no, jqc_no
    for src_name, play, sg in pl:
        k = src_name + u"·" + play
        PLAY_CT[k] = PLAY_CT.get(k, 0) + 1
    if it["allup"]:
        PLAY_CT[u"竞彩·混合过关"] = PLAY_CT.get(u"竞彩·混合过关", 0) + 1
    if it["single_any"]:
        PLAY_CT[u"竞彩·单关"] = PLAY_CT.get(u"竞彩·单关", 0) + 1

D["play_meta"] = {"counts": PLAY_CT,
                  "sfc_issue": (D.get("signals") or {}).get("issue"),
                  "jqc_issue": JQC_ISSUE.get("issue"),
                  "jc_play": bool((jc or {}).get("jc")),
                  "note": u"6场半全场胜负游戏暂未接入（只影响这一个筛选，其余玩法都是官方在售口径）"}
if POOL:
    D["pool"] = {"generated_at": up.get("generated_at") or daily.get("generated_at"),
                 "window": up.get("window"), "n": len(POOL), "matches": POOL,
                 "leagues": [{"id": l.get("id"), "zh": l.get("zh"), "country": l.get("country"),
                              "logo": l.get("logo"), "season": l.get("season"),
                              "overview": l.get("overview"), "teams": l.get("teams")} for l in leagues]}
    D["jc_meta"] = {"available": bool(jc), "matched_jc": n_jc, "matched_bc": n_bc,
                    "total_jc": len((jc or {}).get("jc") or []), "total_bc": len((jc or {}).get("bc") or []),
                    "note": "竞彩官方接口只挂「当前投注日」的在售场次（每天中午前后才放出下一日），"
                            "所以下方竞彩数会少；北单在售场次取自公开列表，其 SP 为彩池参考值、随时变动，"
                            "最终以体彩中心公布为准。没被贴标的比赛仍然照常推荐，只是没有官方编号可抄。"}
    D["season_hist"] = [{"zh": v.get("zh"), "seasons": v.get("seasons")}
                        for v in (hraw.get("hist") or {}).values()][:60]
    log.append(f"pool {len(POOL)}场 / 联赛{len(leagues)} / 竞彩贴标{n_jc} / 北单贴标{n_bc} / 官方补卡{n_extra} / 往季{len(D['season_hist'])}")

def ai_ref(issue, market_picks):
    """读 AI 方向账本里 DeepSeek 那行，只作**参考展示**（2026-11-08 用户要求：任九/任14 都带上 DeepSeek 参考）。
    永不参与选号：市场方向优先，这是铁律。"""
    try:
        path = os.path.join(S500, "ai_picks_ledger.jsonl")
        best = None
        fb = None
        NAME = {"deepseek": "DeepSeek", "grok": "Grok", "gpt-pollinations": "GPT", "self": "我方AI"}
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if str(r.get("issue")) != str(issue):
                    continue
                s = str(r.get("src") or "")
                if s.startswith("deepseek"):
                    best = r                      # 用户指定优先看 DeepSeek
                elif s != "self" and fb is None:
                    fb = r                        # 没有 DeepSeek 时退回其它外部源（self 是我们的自评，不当参考）
        best = best or fb
        if not best:
            return None
        who = NAME.get(str(best.get("src") or ""), str(best.get("src") or "AI"))
        pk = best.get("picks") or {}
        dl = [str(((pk.get(str(i)) or {}).get("dir") or "-")).replace("/", "") for i in range(1, 15)]
        # 「与百家首选不一致」= AI 自己首选的方向 ≠ 百家均概率最高的方向
        # （不是「我们票面有没有覆盖」——那样会因为双选第二格正好是平而漏报，抢双选名额时吃亏）
        diff = [i for i in range(1, 15)
                if dl[i - 1][:1] not in ("", "-") and dl[i - 1][:1] != (market_picks[i - 1] or "")[:1]]
        zz = lambda ns: "、".join(str(x).zfill(2) for x in ns)
        return {"src": who, "dirs": " ".join(dl), "diff": diff, "draws": sum(1 for x in dl if "1" in x),
                "blind": not best.get("saw_our_picks"),
                "note": ("%s 赛前参考（%s盲写，押平 %d 场）：%s" % (
                    who, "" if not best.get("saw_our_picks") else "见票后", sum(1 for x in dl if "1" in x), " ".join(dl)))
                    + ("；与百家首选不一致 %d 场：%s" % (len(diff), zz(diff)) if diff else "；与百家首选无冲突")}
    except Exception:
        return None


# 任14（14场全包）：同一套市场优先规则，**双选压到 4 个 = 10胆+4双 = 16注/32元**（2026-11-08 用户定，每天随任九一起推送）
sig_rows = (D.get("signals") or {}).get("rows") or []
if sig_rows:
    p14 = []
    for r in sig_rows:
        p = {k: v for k, v in (r.get("prob") or {}).items() if v is not None}
        if not p:
            p14.append([r["no"], "31", 0, 0, 3])
            continue
        order = sorted(p.items(), key=lambda kv: -kv[1])
        (top, ptop), sec_pair = order[0], order[1]
        pdraw = p.get("1") or 0            # 平局隐含（去水归一）
        # v3 口径：第二选项 = 热门 ≥45% 配平、<45% 配反向（79,577 场实测分界 44~45%）
        second = "1" if ptop >= 45 else sec_pair[0]
        if second == top:
            second = order[1][0] if order[1][0] != top else order[2][0]
        # 胆分级：一级胆=均概率≥57%（实测命中 57%+）；二级胆=52~57% 且 平局隐含<28%；其余=该双选
        tier = 1 if ptop >= 57 else (2 if (ptop >= 52 and pdraw < 28) else 3)
        p14.append([r["no"], top + second, ptop, pdraw, tier])
    # v6（2026-11-08 17:15 用户定：「任14 默认 16 注/32 元，双选名额参考 DeepSeek 来配合选择」）
    #   名额数不变（4 个 = 16注/32元，上限内取低值），只改**分配**：
    #   ① 先算 DeepSeek 赛前盲写参考；② 它与百家首选分歧的中游场先占名额（按平隐含，最多 2 个）；
    #   ③ 它与某个**二级胆**分歧（它认为会平）→ 该二级胆降级为双选（最多 1 场，二级胆本就是勉强做胆）；
    #   ④ 其余名额仍按「平局隐含最高」补满（45~55% 档实测平局率 27.0%，全场最高）；
    #   ⑤ **方向永远是市场首选 + 第二选项**，DeepSeek 不覆盖方向（铁律）。
    ref = ai_ref((D.get("signals") or {}).get("issue"), [x[1] for x in p14])
    diff = set((ref or {}).get("diff") or [])
    demote = [x for x in p14 if x[4] == 2 and x[0] in diff][:1]
    for x in demote:
        x[4] = 3
    upgraded = set()          # 被迫单选的场（二级胆/名额不够），站点与票面都要单独标注
    need = sorted([x for x in p14 if x[4] == 3], key=lambda x: (-x[3], x[2]))
    seat = [x[0] for x in need if x[0] in diff][:2]
    for x in need:
        if len(seat) >= 4:
            break
        if x[0] not in seat:
            seat.append(x[0])
    seat = set(seat)
    for x in demote:
        if x[0] not in seat:                  # DeepSeek 想降级但抢不到名额 → 恢复二级胆身份（仍是胆，只是不双选）
            x[4] = 2
    for x in need:
        if x[0] not in seat:
            x[1] = x[1][0]
            upgraded.add(x[0])
    # 丙（2026-11-08 17:47 用户定）：**AI 押平 且 平局隐含 ≥24% 的分歧场**，第二格改成配平——
    # 否则会出现「名额是 AI 抢的、保险却按市场买了反向」的空转（26162 期 11 佛罗伦vs尤文 就是这例）。
    # 只在这一种情况下覆盖市场口径的第二格；第一格（方向）永远不动，45% 交叉点对其它场照旧。
    ai_draw_seats = []
    if ref:
        ad = (ref.get("dirs") or "").split()
        for x in p14:
            if x[0] in seat and x[0] in diff and len(x[1]) > 1 and x[3] >= 24:
                d = ad[x[0] - 1] if 0 < x[0] <= len(ad) else ""
                if "1" in d:
                    parts = [c for c in x[1] if c in "310"]
                    if parts and parts[0] != "1" and parts[-1] != "1":   # 第二格本来不是平才算「丙口径生效」
                        x[1] = parts[0] + "1"
                        ai_draw_seats.append(x[0])
    for x in p14:
        if x[4] in (1, 2):            # 一级/二级胆一律单选：≥60% 深让盘也做胆（v3 规则 c 让位）
            x[1] = x[1][0]
            if x[4] == 2:
                upgraded.add(x[0])
    dbl = sum(1 for x in p14 if len(x[1]) > 1)
    bets = 2 ** dbl                     # 注数 = 2^(双选场数)；成本另乘 2 元
    rows14 = [{"no": r["no"], "vs": r.get("vs"),
               "pick": "/".join(next(x[1] for x in p14 if x[0] == r["no"])),
               "prob": [((r.get("prob") or {}).get(k)) for k in ("3", "1", "0")],
               "asian": r.get("asian") or []} for r in sig_rows]
    z2 = lambda ns: "、".join(str(x).zfill(2) for x in sorted(ns))
    dan = [x[0] for x in p14 if len(x[1]) == 1]
    dan_ok = [x for x in dan if x not in upgraded]      # 真达胆资格的胆
    dan_up = sorted(upgraded)                            # 被压成单选的，未达胆资格
    duu = [x[0] for x in p14 if len(x[1]) > 1]
    why14 = []
    dan1 = [x[0] for x in p14 if x[4] == 1]
    dan2 = [x[0] for x in p14 if x[4] == 2]
    seat_d = sorted(n for n in duu if n in diff)                 # 靠 DeepSeek 分歧拿到双选名额的场
    demote_in = sorted(x[0] for x in demote if x[0] in duu)      # 被 DeepSeek 判平而降级成双选的二级胆
    if dan1:
        why14.append(f"一级胆 {z2(dan1)}：均概率 ≥57%（79,577 场实测这一档热门命中 57%~80%，≥60% 的深让盘也直接做胆）")
    if dan2:
        why14.append(f"二级胆 {z2(dan2)}：均概率 52~57% 但平局隐含 <28% —— 是「伪胆区」里平局风险最低的一批，"
                     f"<b>本票最大的风险点就在这里</b>（14场全包，错一场即全盘皆输）")
    if duu:
        why14.append(f"双选 {z2(duu)}：名额 4 个（v8 默认取低值 = 16注/32元，上限 32注/64元），"
                     f"**分配参考 DeepSeek 分歧**："
                     + (f"它与百家分歧的中游场 {z2(seat_d)} 先占名额；" if seat_d else "")
                     + (f"它判平的二级胆 {z2(demote_in)} 降级为双选；" if demote_in else "")
                     + f"其余名额按平局隐含最高补满（实测 45~55% 档实际平局率 27.0%，≥60% 档只有 19.4%）。"
                     f"第二格按热门 ≥45% 配平、<45% 配反向——<b>方向一律取市场首选，DeepSeek 只决定哪几场加保险，不改方向</b>"
                     + (f"；<b>丙口径</b>：AI 押平且平局隐含 ≥24% 的分歧场 {z2(ai_draw_seats)}，第二格改配平——"
                        f"不然名额是它抢来的、保险却买了反向，等于白花这一票" if ai_draw_seats else ""))
    why14.append(f"{dbl} 个双选 → 2^{dbl} = {bets} 注 / {bets * 2} 元；14场全包，一张复式最多中 1 注")
    why14.append("口径与任九完全一致（v3）：市场优先；AI/专家/模型永不覆盖市场方向；欧冠/欧联/欧协联场次不采信模型与专家")
    # ref 已在分配双选名额前算好（DeepSeek 分歧参与分配），此处不再重复取
    if ref:
        why14.append(ref["note"] + "（**仅供参考，不改任何选号**）")
    _iss14 = (D.get("signals") or {}).get("issue")
    _tk = D.get("ticket") or {}
    if _tk.get("issue") == _iss14:
        _tk["ai_ref"] = ref                     # 任九卡也带上同一份参考（站点模板暂未渲染，推送会读它）
    D["rq14"] = {"issue": (D.get("signals") or {}).get("issue"),
                 # deadline 必须先取 signals（当期）：以前取 ticket.deadline，而 rows 取的是 signals，
                 # 定稿前就会出现「第26160期 · 停售 2026-11-05 22:00」这种日期与对阵不同期的错（2026-11-06 用户指出）
                 "deadline": (D.get("signals") or {}).get("deadline") or (D.get("ticket") or {}).get("deadline"),
                 "bets": bets, "cost": bets * 2, "rows": rows14,
                 "ai_ref": ref, "why": why14}
    log.append(f"rq14 {len(rows14)}行 {dbl}双 {bets}注/{bets * 2}元")
    # 任14 选号留档：data.json 每小时会被下一期覆盖，第二天结算要按当期取票面，
    # 所以按期号另存一份历史（cache\rq14_history.jsonl）。同一期反复重建时**保留最后一次**
    # （越接近停售越接近终盘，结算认的就是这张）。rea_500\sfc14_check.py 读这份核奖。
    try:
        _iss = str(D["rq14"].get("issue") or "")
        if _iss:
            os.makedirs(os.path.join(BASE, "cache"), exist_ok=True)
            _hp = os.path.join(BASE, "cache", "rq14_history.jsonl")
            hist = {}
            if os.path.exists(_hp):
                for _ln in open(_hp, encoding="utf-8"):
                    try:
                        _o = json.loads(_ln)
                        if _o.get("issue"):
                            hist[str(_o["issue"])] = _o
                    except Exception:
                        continue
            hist[_iss] = {"issue": _iss, "deadline": D["rq14"].get("deadline"),
                          "bets": bets, "cost": bets * 2,
                          "rows": [{"no": x["no"], "vs": x["vs"], "pick": x["pick"]}
                                   for x in rows14],
                          "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
            with open(_hp, "w", encoding="utf-8") as _f:
                for _k in sorted(hist)[-200:]:
                    _f.write(json.dumps(hist[_k], ensure_ascii=False) + "\n")
            log.append(f"rq14 留档：{_iss} 期已写入 rq14_history.jsonl（共 {len(hist)} 期）")
    except Exception as _e:
        log.append(f"rq14 留档失败（不影响发布）：{_e!r}")
D["paywall"] = {"issue_price": 0, "month_price": 0, "beta": True,
                "codes": ["DEMO2026"],
                "note": "这里不收费，今后也不收费"}


# 收款码：图片放进 site\ 就行，后缀 jpg/jpeg/png/webp 都认，取最近改动的一个
#   微信码 → donate_wx.jpg      支付宝码 → donate_alipay.jpg
#   两个都没有时，退回老的一张 donate.jpg
def _qr(*pats):
    for pat in pats:
        fs = sorted(glob.glob(os.path.join(SITE, pat)), key=os.path.getmtime)
        if fs:
            return os.path.basename(fs[-1])
    return None


D["donate"] = {"wx": _qr("donate_wx.*", "donate_weixin.*", "donate_wechat.*"),
               "ali": _qr("donate_alipay.*", "donate_ali.*", "donate_zfb.*")}
if not (D["donate"]["wx"] or D["donate"]["ali"]):
    D["donate"]["legacy"] = _qr("donate.jpg", "donate.jpeg", "donate.png", "donate.webp")

# 联系方式（2026-11-05）：邮箱是 Cloudflare Email Routing 的域名信箱
#   （301392.xyz 的 MX 指向 route*.mx.cloudflare.net，SPF=_spf.mx.cloudflare.net，
#    账号里有一条"全部收信→Worker cloud-mail"的启用规则，能收信），
#   不是挂着好看的假地址。CF 登录在调试 Chrome 里，可用 cf_api.py 查。
#   加微信好友的码：手机截图存成 site\wx.png 就行（jpg/jpeg/png/webp 都认）。
#   截图通常带一大圈白边，构建时自动裁掉并缩到 560px 宽，另存 contact_wx.png；
#   源图更新了会重裁（按修改时间判断），没装 Pillow 就退回原图。
CONTACT_MAIL = "admin@301392.xyz"


def _qr_tight(out_name, src_pats, maxw=560):
    """裁掉二维码截图四周留白后另存一份，返回要发布的文件名"""
    src = _qr(*src_pats)
    if not src:
        return None
    sp = os.path.join(SITE, src)
    op = os.path.join(SITE, out_name)
    if os.path.exists(op) and os.path.getmtime(op) >= os.path.getmtime(sp):
        return out_name
    try:
        from PIL import Image
        im = Image.open(sp)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im.convert("RGBA"), (0, 0), im.convert("RGBA"))
            im = bg
        else:
            im = im.convert("RGB")
        # 阈值 230：连浅灰的说明小字也算"内容"，不会被裁掉
        m = im.convert("L").point(lambda v: 0 if v > 230 else 255)
        box = m.getbbox() or (0, 0, im.size[0], im.size[1])
        pad = 14
        box = (max(0, box[0] - pad), max(0, box[1] - pad),
               min(im.size[0], box[2] + pad), min(im.size[1], box[3] + pad))
        im = im.crop(box)
        if im.size[0] > maxw:
            im = im.resize((maxw, max(1, int(im.size[1] * maxw / float(im.size[0])))),
                           Image.LANCZOS)
        # 二维码本质是几块平色，量化到 64 色后体积约 1/3，实测清晰度不掉（灰阶占比 16.9% vs 原图 16.7%）
        try:
            im = im.convert("P", palette=Image.ADAPTIVE, colors=64)
        except Exception:
            pass
        im.save(op, "PNG", optimize=True)
        return out_name
    except Exception:
        return src        # 没 Pillow / 裁图失败就直接用原图，别把联系方式弄没了


D["contact"] = {"email": CONTACT_MAIL,
                "wx": _qr_tight("contact_wx.png", ("wx.png", "wx.jpg", "wx.jpeg", "wx.webp",
                                                   "wechat.png", "weixin.png", "wx_*.png"))}

# ---------- 8. 接口自检 ----------
# 对外文案不点任何数据公司的名字（用户 2026-11-08 定）：接口内部叫什么只管我们自己看，
# 站上统一显示成「抓取/官方」这类中性说法，说明里也把接口代号抹掉。
NEUT = [("zgzcw_hongdan", "抓取模块"), ("调试Chrome", "抓取环境"), ("模块导入", "抓取模块"),
        ("红单方案列表 hdapi", "方案列表"), ("红单方案列表", "方案列表"), ("hdapi", ""),
        ("方案详情 zjtj", "方案详情"), ("zjtj", ""),
        ("赛果/赔率 getMatchinfo", "赛果与赔率"), ("getMatchinfo", ""), ("playId", "玩法"),
        ("trade/rj 官方SP层", "官方开奖 SP"), ("trade/rj", ""), ("kaijiang sfc 开奖", "官方开奖记录"),
        ("kaijiang", ""), ("jqc 四场进球赛程", "进球游戏赛程"), ("jq4 四场进球开奖", "进球游戏开奖"),
        ("jqc", ""), ("jq4", ""),
        ("zx 指数中心", "指数与盘口"), ("zx", ""), ("odds 欧赔平均", "收盘赔率均值"), ("odds", ""),
        ("同赔历史 ajax", "同赔历史"), ("ajax", ""), ("fixture", "在售"),
        ("avwinc2 在", "字段齐全"), ("DOM直读", "页面直读"), ("DOM拉取失败/被拦", "页面读取被拦"),
        ("fid=", "样本 "), ("code=", ""), ("planId=", "编号 "), ("resultCode=", "回执 "),        ("(已购单兜底)", "（登录态正常）"), ("（已购单兜底）", "（登录态正常）"),        ("接口可 POST（counts 可能为 null=无历史）", "通道可用（无历史时返回空值）"),        ("接口可 POST", "通道可用"), ("counts", "计数字段")]


def neut(s):
    s = str(s or "")
    for a, b in NEUT:
        s = s.replace(a, b)
    s = re.sub(r"\s{2,}", " ", s).strip()
    # ｜ 后面是我们自己的登录/购单细节（账号、isBuy、正文字数…），站上不需要看见
    s = s.split("｜")[0].strip()
    s = re.sub(r"planInfo=\S*", "", s)
    for rx, rep in SCRUB:
        s = rx.sub(rep, s)
    return s.strip()


hf = latest(S500, "health_*.json")
hraw = jload(hf) or {}
if hraw.get("rows"):
    items = [{"name": neut(r.get("name", "")), "ok": r.get("status") == "✓",
              "warn": r.get("status") == "⚠", "info": neut(r.get("msg") or "")[:100]}
             for r in hraw["rows"]]
    D["health"] = {"date": str(hraw.get("ts") or "")[:10],
                   "total": len(items),
                   "ok": sum(1 for i in items if i["ok"]),
                   "degraded": sum(1 for i in items if i["warn"]),
                   "abnormal": sum(1 for i in items if not i["ok"] and not i["warn"]),
                   "items": items}
    log.append(f"health {D['health']['ok']}/{D['health']['total']} 异常 {D['health']['abnormal']}")

# ---------- 9. 固定文案（免费对照表 / 规则 / 改写记录）----------
D["compare_free"] = [
    ["官方SP/凯利/平均概率/亚盘（14场）", "也放", "照样给"],
    ["5 公司初盘共识与分歧", "也放", "直接给（本站读DOM，不怕WAF）"],
    ["相同欧指历史发生率", "也放（还被当卖点）", "已用万级样本证明它只是噪声 → 仅作提示"],
    ["主流机构初盘→终盘 去水概率", "也放（公开盘口）", "直接给，并标出初→终盘方向"],
    ["统计模型 1x2 概率", "只放这一个，其余 8 个玩法锁着", "一并给出，并附对照实验证明它不比市场准"],
    ["一万次蒙特卡洛比分模拟", "专业版（members_only）", "无；用比分分布 + 大小/BTTS 概率替代，结论一致"],
    ["AI 赛事前瞻正文", "英超放 / 其余锁", "英超同样能读，嵌在终盘参考里"],
    ["预测首发11人 / 裁判 / 天气", "也放", "直接给"],
    ["每日完整价值场次 screen-upcoming", "Max 版锁", "本站自己用赛程+终盘概率算一份（upcoming.py）"],
    ["赛后 xG / 直播实时预测", "专业版锁", "赛前用不上，不做"],
    ["校准表 / 效率实验 / 价值台账", "分散在各页", "一页全公开，含方法与样本量"],
    ["会员费 / 月费 / 单场推荐费", "有（含已过期订阅）", "一律 0 元，以后也不收费"],
]
D["rules"] = [
    ["a) 胆资格", "仅当官方SP热门方向 ≤1.60 或 均概率 ≥60% 才可单选；40%~59% 一律双选"],
    ["b) 凯利与背离", "凯利 ≥0.99 → 禁单选；5公司共识与SP概率背离 → 双选或弃场"],
    ["c) 深让盘", "SP胜 <1.5 且 亚盘让球 ≥一球/球半 → 必双选或弃场（含受让方）"],
    ["d) 面热（已降级）", "同赔历史与当前概率差 ≥8pt 只作提示记录，不再覆盖选号、不再禁做胆"],
    ["e) 平赔顶高", "平赔被五家一致顶到最高 + 共识背离 → 直接弃场"],
    ["f) 形态冲突", "主/客场分项进失球极端却与赔率反向 → 禁做胆（只保留极端情形）"],
    ["g) 市场优先", "模型/专家/实票永不覆盖市场方向；跨国杯赛场次模型信号一律作废"],
    ["h) 注数", "注数 = 2^(双选场数) × 2元；一张复式票最多中 1 注"],
    ["仓位", "默认 32注/64元；只有「深让 + 背离 + 弃场」合计 ≥6 场才升到 64注/128元"],
    ["进球彩", "8注/16元；期內强弱分明场（热门≤1.60）不足 2 场 → 本期直接跳过"],
]
D["rule_changes"] = [
    ["2026-11-04：面热从「强制覆盖」降级为「仅提示」",
     "过去两期（26156 亚特兰大 0:3、26157 博德 0:0）两注失手都栽在「市场大热门 + 面热」上，直觉是要防面热。"
     "但把 13.9 万场公开回测数据拉下来按价格档复核后：同赔面热度与实际发生率的差只有 0~2pt，是抽样噪声；"
     "而热门方向的实际发生率与赔率隐含概率吻合到 ±1pt。防面热= 用噪声改信号，长期必亏。"],
    ["2026-11-05：跨国杯赛场次的模型信号一律作废（两轮实验 n=260）",
     "自造对照实验（已结束比赛，市场取机构终盘）。第二轮各 120 场：杯赛里模型首选命中 60.0% vs 市场 65.8%，"
     "Brier 0.5739 vs 0.4775（模型差 +0.096）；「模型比市场高 ≥5pt」的所谓价值方向 105 次只打出 17.1%（≥10pt 的 78 次同样差）。"
     "同一实验在国内联赛组（英西德意法）差距只有 +0.019，价值信号 26.9% —— 说明模型是国内联赛拟合出来的，"
     "遇到跨国实力差没有先验。第一轮 n=140 得到 +0.073 的同向结论。"
     "这也解释了付费站自家 1x2 价值策略长期 +9.8%、但 2026-07-17 起 349 注亏 ¥5,433：夏天的价值池被欧冠/欧联资格赛填满。"],
]
D["about"] = [
    ["刷新频率", "每天每小时第 1 分自动重建并发布一次：竞彩官方场次与北京单场每 1 小时抓一次；全量未开赛比赛与赔率在每天 11 点 / 17 点 / 21 点那一次顺带做（竞彩官方每天上午 11 点放出次日场次）；联赛积分榜与画像在每天 08 点那次；任九 / 任14 定稿每天 20:40 出票后更新。页面顶部标的是最近一次构建时间与各数据源距今多久"],
    ["打赏", "这里不收钱。觉得有用就顺手赏一点，收款码在页尾；打赏不改变任何选号，也不构成任何投注建议"],
    ["口径", "任九以 90 分钟赛果为准；概率均为去水后市场概率；「均概率」指多家欧赔汇总的平均隐含概率"],
    ["装成 App", "安卓：用 Chrome 打开本页 → 菜单里点「安装应用」（或「添加到主屏幕」）；苹果：用 Safari 打开 → 分享 → 添加到主屏幕。装完是独立图标、没有地址栏，用的还是同一份数据，每次打开都是最新的"],
    ["消息提醒", "定稿消息走企业微信群（每天 20:40 前后发定稿，停售 22:00 前），网页里不做弹窗打扰；手机上没开网页通知也不影响正常使用"],
    ["联系方式", "邮箱 " + CONTACT_MAIL + "（真有人在看的域名信箱，不是自动回复，一般一个工作日内回）；加微信好友的二维码在页尾「联系方式」卡片里。数据纠错、页面故障、规则口径都可以直说；投注明细自己操作，本站不代售彩票"],
    ["免责", "只提供概率与历史统计，不预测单场结果，不代售彩票。这里不收钱，以后也不收费；打赏不构成服务承诺，也不影响选号。历史回测不代表未来"],
    ["可复算", "本页全部数据都来自公开接口，样本量与筛选条件都写在表头，欢迎挑错"],
]

# ---------- 10. 写文件 ----------
D = scrub(D)   # 对外文案去品牌名（含运行时抓来的提示文本）
data_js = json.dumps(D, ensure_ascii=False)          # 明文整站数据，只留本地核对（不发布）
# ---- 反爬加固（2026-11-05）：定稿类数据打散后再内联 ----------------------------
# 免费部分（全部比赛/积分榜/联赛库…占 86% 字节）保持明文，利于首屏与收录；
# 定稿/信号/账本这些"别人最想抄"的切片走 XOR+base64，密钥前后两半分别放在
# #ds 的 data-k 与 <html> 的 data-k 上，查看源码不再是现成 JSON。
import base64 as _b64
import hashlib as _hl
import random as _rnd
SENS = ("ticket", "rq14", "signals", "jqc", "ledger", "card")
D["wm"] = {"i": _hl.sha1(data_js.encode("utf-8")).hexdigest()[:10],
           "b": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}      # 隐形水印/查重指纹
data_js = json.dumps(D, ensure_ascii=False)
sens = {k: D[k] for k in SENS if k in D}
free = {k: v for k, v in D.items() if k not in sens}
kb = [_rnd.randint(1, 255) for _ in range(4)]
blob = _b64.b64encode(bytes(b ^ kb[i % 4] for i, b in
                            enumerate(json.dumps(sens, ensure_ascii=False).encode("utf-8")))).decode()
tpl = open(os.path.join(BASE, "template.html"), encoding="utf-8").read()
if "__DATA_FREE__" in tpl:
    html = tpl.replace("__DATA_FREE__", json.dumps(free, ensure_ascii=False))
    html = html.replace("__BLOB__", blob)
    html = html.replace("__KA__", "%02x%02x" % (kb[0], kb[1])).replace("__KB__", "%02x%02x" % (kb[2], kb[3]))
    log.append("反爬：定稿切片已打散（%d 字节 → 密文 %d 字节，明文 %d 字节）" %
               (len(json.dumps(sens, ensure_ascii=False)), len(blob), len(json.dumps(free, ensure_ascii=False))))
else:                                   # 兼容旧模板
    html = tpl.replace("__DATA_JSON__", data_js)
# ---------- PWA：manifest + service worker（让用户「装成 App」）----------
# 图标由 make_icons.py 生成后常驻 site/，这里只管清单与缓存策略。
bid = datetime.datetime.now().strftime("%Y%m%d%H%M")
manifest = {
    "name": "小九足球实验室 · 每日全量足球比赛推荐", "short_name": "小九足球",
    "description": "每日全量足球比赛的概率、定稿参考与奖金行情，不收钱，以后也不收费。",
    "id": "/", "start_url": "/", "scope": "/", "display": "standalone",
    "orientation": "portrait", "background_color": "#0f1115", "theme_color": "#0f1115",
    "lang": "zh-CN", "categories": ["sports", "news"],
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
               "purpose": "maskable"}]}
open(os.path.join(SITE, "manifest.webmanifest"), "w", encoding="utf-8").write(
    json.dumps(manifest, ensure_ascii=False, indent=1))
sw = """/* 小九足球 service worker —— 构建于 %s（内容一变浏览器就自动换版） */
var CACHE = "xj9-%s";
var PRE = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/favicon.ico"];
self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(PRE); })
    .then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var r = e.request;
  if (r.method !== "GET") return;
  try { if (new URL(r.url).origin !== location.origin) return; } catch (err) { return; }  // 第三方计数器不插手
  if (r.mode === "navigate") {          // 页面：先联网（每小时重建的数据要立刻见效），断网才回缓存
    e.respondWith(fetch(r).then(function (res) {
      if (res && res.ok) { var cp = res.clone(); caches.open(CACHE).then(function (c) { c.put("/", cp); }); }
      return res;
    }).catch(function () { return caches.match("/"); }));
    return;
  }
  e.respondWith(caches.match(r).then(function (hit) {   // 图标等静态件：先缓存，缺了再联网回填
    return hit || fetch(r).then(function (res) {
      if (res && res.ok) { var cp = res.clone(); caches.open(CACHE).then(function (c) { c.put(r, cp); }); }
      return res;
    });
  }));
});
""" % (bid, bid)
open(os.path.join(SITE, "sw.js"), "w", encoding="utf-8").write(sw)
log.append("PWA：manifest.webmanifest + sw.js（缓存版本 %s）已生成" % bid)

open(os.path.join(SITE, "index.html"), "w", encoding="utf-8").write(html)
open(os.path.join(SITE, "data.json"), "w", encoding="utf-8").write(data_js)
print("=== 建站完成 ===")
for l in log:
    print("  ", l)
print(f"   index.html {os.path.getsize(os.path.join(SITE,'index.html')):,}B  data.json {len(data_js):,}B")
print("   本地预览：python -m http.server 8788 --directory site")


# ---------- 10. 同步 site\_lab：GitHub 端保底重建用的源数据 ----------
# 抓取必须在这台电脑做：实测同一个请求走境外出口，体彩官方回 418、500 回 403，
# 所以 GitHub Actions 只能负责「数据传上去之后」的重建与发布（电脑关着，站点也不会停在昨天）。
# _lab 里只保留**每种缓存的最新一份**（合计约 2.5MB），每次 build 自动刷新，Actions 直接拿它跑同一个 build.py。
LAB = os.path.join(SITE, "_lab")
if os.path.isdir(LAB):
    import shutil
    KEEP = [("rea_500", "cache", ["signals_*.json", "jc_*.json", "jqc_*.json", "card_*.json", "kj_*.html",
                                 "health_*.json", "sfc_official_*.json", "rj_times.json"]),
            ("rea_djyy", "cache", ["upcoming_*.json", "gap_*.json", "card_*.json", "tempo_log.jsonl"]),
            ("rea_zgzcw", "archive", ["betslip_*.md", "push_*_定稿.md", "*战绩*.md"]),
            (None, "cache", ["rq14_history.jsonl", "season_overview_*.json"])]
    nb = 0
    for root_, sub, pats in KEEP:
        src_dir = os.path.join(ROOT, root_, sub) if root_ else os.path.join(BASE, sub)
        dst_dir = os.path.join(LAB, root_, sub) if root_ else os.path.join(LAB, sub)
        os.makedirs(dst_dir, exist_ok=True)
        for pat in pats:
            fs = sorted(glob.glob(os.path.join(src_dir, pat)), key=lambda f: (os.path.getmtime(f), os.path.basename(f)))
            if not fs:
                continue
            try:
                shutil.copy2(fs[-1], os.path.join(dst_dir, os.path.basename(fs[-1])))
                nb += 1
                keep_name = os.path.basename(fs[-1])
                for old in glob.glob(os.path.join(dst_dir, pat)):          # 只留最新一份，别让仓库无限膨胀
                    if os.path.basename(old) != keep_name:
                        os.remove(old)
            except Exception as e:
                log.append("_lab 同步失败 %s：%s" % (pat, repr(e)[:60]))
    for f in ("build.py", "template.html"):
        try:
            shutil.copy2(os.path.join(BASE, f), os.path.join(LAB, f))
            nb += 1
        except Exception:
            pass
    log.append("_lab 已同步 %d 个文件（GitHub 端保底重建用）" % nb)
