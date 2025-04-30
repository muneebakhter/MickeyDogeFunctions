import azure.functions as func
import logging, os, datetime, json, re, requests
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from azure.data.tables import TableServiceClient, UpdateMode

app        = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
TABLE_NAME = "Agencies"

FEED_URL   = "https://www.ecfr.gov/api/admin/v1/agencies.json"
VER_URL    = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{title}.xml"
CHANGES_URL = "https://www.ecfr.gov/recent-changes"

svc  = TableServiceClient.from_connection_string(os.getenv("TABLE_CONN", ""))
tbl  = svc.get_table_client(TABLE_NAME)

def fmt_ref(r: dict) -> str:
    return ":".join(str(r.get(k, "")) for k in
                    ("title", "subtitle", "chapter", "subchapter", "part"))

def chunk(iterable, size=100):
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]

def extract_num(text, pattern):
    m = re.search(pattern, text)
    return m.group(1) if m else ""

def needs_fix_for_row(regs_str, hierarchies):
    for r in regs_str.split(";"):
        rp = tuple((r.split(":") + [""]*5)[:5])
        for sh in hierarchies:
            if all((not rp[i]) or (rp[i] == sh[i]) for i in range(5)):
                return True
    return False

def traverse_list(top_ol) -> set:
    hierarchies = set()
    def recurse(ol, title="", subtitle="", chapter="", subchapter="", depth=1):
        for li in ol.find_all("li", recursive=False):
            hdr = li.find(f"h{depth+1}")
            if not hdr:
                continue
            txt = hdr.get("data-inner-html", hdr.get_text(strip=True))

            if depth == 1:
                title = extract_num(txt, r"Title\s+(\d+)")
            elif depth == 2:
                subtitle = extract_num(txt, r"Subtitle\s+([A-Za-z])")
                if not subtitle:
                    chapter = extract_num(txt, r"Chapter\s+([IVXLCDM]+)")
            elif depth == 3:
                if txt.startswith("Chapter"):
                    chapter = extract_num(txt, r"Chapter\s+([IVXLCDM]+)")
                else:
                    subchapter = extract_num(txt, r"Subchapter\s+([A-Za-z])")
            elif depth == 4:
                part = extract_num(txt, r"Part\s+(\d+)")
                hierarchies.add((title, subtitle, chapter, subchapter, part))

            next_ol = li.find("ol", class_=f"level-{depth+1}")
            if next_ol:
                recurse(next_ol, title, subtitle, chapter, subchapter, depth+1)

    recurse(top_ol, depth=1)
    return hierarchies

def calc_size_mb(title: str,
                 subtitle: str = "", chapter: str = "", subchapter: str = "", part: str = "",
                 date: str     = "") -> float:
    if not date:
        date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    params = []
    if subtitle:   params.append(f"subtitle={quote_plus(subtitle)}")
    if chapter:    params.append(f"chapter={quote_plus(chapter)}")
    if subchapter: params.append(f"subchapter={quote_plus(subchapter)}")
    if part:       params.append(f"part={quote_plus(part)}")
    url = VER_URL.format(date=date, title=title)
    if params: url += "?" + "&".join(params)

    while True:
        r = requests.get(url, timeout=60)
        if r.status_code == 404 and "most recent issue date of" in r.text:
            m = re.search(r"most recent issue date of (\d{4}-\d{2}-\d{2})", r.text)
            if m:
                date = m.group(1)
                url  = VER_URL.format(date=date, title=title) + ("?" + "&".join(params) if params else "")
                continue
        r.raise_for_status()
        return int(r.headers.get("Content-Length", "0")) / 1_048_576

def update_agency_row(entity: dict, ydate: str) -> bool:
    regs = entity.get("Regulations", "")
    if not regs: 
        return False

    total_mb = 0.0
    for reg in regs.split(';'):
        title, subtitle, chapter, subchap, part, *_ = (reg.split(':') + [""]*5)[:5]
        if not title:
            continue
        total_mb += calc_size_mb(title, subtitle, chapter, subchap, part, date=ydate)

    entity["RegulationSizeMb"] = round(total_mb, 3)
    entity["LastUpdated"]      = datetime.datetime.utcnow().isoformat()
    tbl.update_entity(entity, mode=UpdateMode.REPLACE)
    return True

# ───────── LoadAgencies ─────────
@app.route(route="LoadAgencies", methods=["POST"])
def load_agencies(req: func.HttpRequest) -> func.HttpResponse:
    currtime = datetime.datetime.utcnow().isoformat()

    try:
        feed = requests.get(FEED_URL, timeout=30).json()["agencies"]
    except Exception as ex:
        logging.exception("Feed fetch failed")
        return func.HttpResponse(str(ex), status_code=502)

    entities = []
    def walk(nodes):
        for a in nodes:
            name = (a.get("name") or "").strip()
            if not name:
                continue
            regs = ";".join(fmt_ref(r) for r in a.get("cfr_references", []))
            entities.append({
                "PartitionKey": "AGENCY",
                "RowKey":       name,
                "Regulations":  regs,
                "RegulationSizeMb": 0.0,
                "LastUpdated":  currtime
            })
            walk(a.get("children", []))
    walk(feed)

    for batch in chunk(entities, 100):
        actions = [("create", e) for e in batch]
        tbl.submit_transaction(actions)

    return func.HttpResponse(
        json.dumps({"inserted": len(entities)}),
        mimetype="application/json",
        status_code=200
    )

# ───────── GetAgencies ─────────
@app.route(route="GetAgencies", methods=["GET"])
def get_agencies(req: func.HttpRequest) -> func.HttpResponse:
    rows = tbl.list_entities(filter="PartitionKey eq 'AGENCY'")
    payload = [{
        "AgencyName":       r["RowKey"],
        "RegulationsList":  r.get("Regulations","").split(";") if r.get("Regulations") else [],
        "RegulationSizeMb": r.get("RegulationSizeMb", 0.0),
        "LastUpdated":      r.get("LastUpdated")
    } for r in rows]
    return func.HttpResponse(json.dumps(payload, default=str),mimetype="application/json",status_code=200)

# ───────── UpdateAgencies ─────────
@app.timer_trigger(arg_name="mytimer", schedule="0 0 * * * *", run_on_startup=True)
def update_agencies_timer(mytimer: func.TimerRequest) -> None:
    ydate = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    custom_headers = {
        "Host": "www.ecfr.gov",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36",
        "Accept": ("text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;"
                "q=0.8,application/signed-exchange;v=b3;q=0.7"),
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "sec-ch-ua": "\"Google Chrome\";v=\"135\", \"Not-A.Brand\";v=\"8\", "
                    "\"Chromium\";v=\"135\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(CHANGES_URL, headers=custom_headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    hierarchies = set()
    top_ol = soup.select_one(f"ol.level-1#changes-for-{ydate}")
    if not top_ol:
        logging.info(f"No changes found for {ydate}")
    else:
        hierarchies = traverse_list(top_ol)

    updated = 0
    for row in tbl.list_entities(filter="PartitionKey eq 'AGENCY'"):
        needs_zero = row.get("RegulationSizeMb", 0.0) == 0.0
        needs_fix = needs_fix_for_row(row.get("Regulations", ""), hierarchies)
        if needs_zero or needs_fix:
            if update_agency_row(row, ydate):
                updated += 1

    logging.info(f"Updated {updated} rows")