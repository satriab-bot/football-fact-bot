"""
Generator fakta sepak bola harian (anti-halusinasi, multi-sumber).

Alur:
1. Kumpulkan kandidat peristiwa dari:
   a. Wikipedia "On This Day" (terstruktur: tahun + teks + ringkasan halaman)
   b. Portal berita/otoritas sepak bola dunia (BBC, Guardian, Sky Sports, ESPN, Goal, FIFA, UEFA, dst.)
      lewat pencarian per-domain dengan filter ketat (tanggal persis + tahun + sepak bola).
2. Kandidat portal yang cocok dengan kandidat Wikipedia (tahun sama + istilah kunci sama)
   digabung sebagai "konfirmasi silang". Kandidat yang terkonfirmasi diprioritaskan.
3. Gemini memilih satu kandidat dari daftar ringkas, lalu menulis HANYA dari isi lengkap sumbernya
   (untuk kelahiran/wafat: seluruh prosa halaman Wikipedia tokoh). Tahun diambil dari DATA, bukan dari AI.
   Panjang artikel = 20-50% dari panjang sumber, dipecah menjadi paragraf normal (~100 kata).
4. Cek deterministik: setiap angka di artikel harus ada di teks sumber.
5. Cek LLM kedua (verifier, temperature 0): mencari klaim yang tidak didukung sumber.
6. Jika gagal, today_fact.json LAMA dibiarkan (tidak ditimpa) dan skrip keluar dengan error.

Dependensi: pip install google-genai requests duckduckgo_search

Variabel lingkungan opsional:
  GEMINI_MODEL, GEMINI_VERIFIER_MODEL
  REQUIRE_CORROBORATION=1  -> hanya publikasikan jika ada >=1 portal berita yang mengonfirmasi
"""
import os
import re
import sys
import json
import html
import math
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from google import genai
from google.genai import types

try:
    from duckduckgo_search import DDGS
except ImportError:  # tanpa DDG, skrip tetap jalan hanya dengan Wikipedia
    DDGS = None

# ---------------------------------------------------------------- konfigurasi
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
VERIFIER_MODEL = os.environ.get("GEMINI_VERIFIER_MODEL", MODEL)
REQUIRE_CORROBORATION = os.environ.get("REQUIRE_CORROBORATION", "0") == "1"
MAX_ATTEMPTS = 5
MAX_CANDIDATES = 12

# Panjang artikel dihitung sebagai persentase dari panjang sumber (dalam jumlah kata).
LENGTH_MIN_RATIO = float(os.environ.get("LENGTH_MIN_RATIO", "0.20"))  # minimal 20% dari sumber
LENGTH_MAX_RATIO = float(os.environ.get("LENGTH_MAX_RATIO", "0.50"))  # maksimal 50% dari sumber
# Kalibrasi selisih bahasa (sumber Inggris vs artikel Indonesia). 1.0 = hitung apa adanya.
LENGTH_LANG_FACTOR = float(os.environ.get("LENGTH_LANG_FACTOR", "1.0"))
# Kandidat dibuang jika bagian yang boleh ditulis model kurang dari ini (terlalu sedikit untuk satu kalimat utuh).
MIN_GEN_WORDS = int(os.environ.get("MIN_GEN_WORDS", "20"))
# Bagian yang ditulis model minimal sekian kata, agar kalimat pembuka kode saja tidak cukup memenuhi 20%.
MIN_MODEL_WORDS = int(os.environ.get("MIN_MODEL_WORDS", "8"))

WIKI_API = "https://en.wikipedia.org/w/api.php"

# Paragraf "normal": sekitar 100 kata; paragraf pertama dihitung beserta kalimat pembuka sistem.
PARA_TARGET_WORDS = int(os.environ.get("PARA_TARGET_WORDS", "100"))
PARA_MIN_WORDS = int(os.environ.get("PARA_MIN_WORDS", "60"))    # paragraf terakhir boleh setengahnya
PARA_MAX_WORDS = int(os.environ.get("PARA_MAX_WORDS", "140"))
# Pengaman: halaman Wikipedia yang sangat panjang bisa menuntut ribuan kata dengan rasio 20%.
MAX_BODY_WORDS = int(os.environ.get("MAX_BODY_WORDS", "1500"))
# Berapa tokoh (kelahiran/wafat) yang isi lengkap halamannya diunduh per hari.
FULL_FETCH_LIMIT = int(os.environ.get("FULL_FETCH_LIMIT", "20"))
# Jika satu kandidat gagal semua percobaan, berapa kandidat berbeda yang dicoba.
MAX_CANDIDATE_TRIES = int(os.environ.get("MAX_CANDIDATE_TRIES", "2"))
OUTPUT_FILE = "today_fact.json"
HEADERS = {"User-Agent": "daily-football-fact/3.0 (https://eplsentris.blogspot.com)"}

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
             "Agustus", "September", "Oktober", "November", "Desember"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]

# Whitelist portal sepak bola/olahraga dunia: domain -> nama tampilan.
# Hasil pencarian dari domain di luar daftar ini DIABAIKAN.
PORTALS = {
    "bbc.com": "BBC Sport",
    "bbc.co.uk": "BBC Sport",
    "theguardian.com": "The Guardian",
    "skysports.com": "Sky Sports",
    "espn.com": "ESPN",
    "goal.com": "Goal",
    "fifa.com": "FIFA",
    "uefa.com": "UEFA",
    "planetsport.com": "Planet Sport",
    "reuters.com": "Reuters",
    "apnews.com": "AP News",
    "marca.com": "Marca",
    "as.com": "Diario AS",
    "gazzetta.it": "La Gazzetta dello Sport",
    "lequipe.fr": "L'Equipe",
    "kicker.de": "kicker",
    "90min.com": "90min",
    "onefootball.com": "OneFootball",
}
# Domain yang dicari satu per satu untuk kandidat harian (urutan = prioritas).
PORTAL_QUERY_DOMAINS = ["bbc.com", "theguardian.com", "skysports.com", "espn.com", "goal.com",
                        "fifa.com", "uefa.com", "planetsport.com", "marca.com", "90min.com"]
# Domain untuk query konfirmasi gabungan.
CORROBORATION_DOMAINS = ["bbc.com", "theguardian.com", "skysports.com", "espn.com", "goal.com"]

FOOTBALL_HINTS = ["football", "footballer", "soccer", "fifa", "uefa", "world cup",
                  "premier league", "football league", "f.c.", " fc ", "a.f.c.",
                  "copa", "serie a", "la liga", "bundesliga", "ligue 1", "libertadores",
                  "champions league", "fa cup", "europa league"]
NOT_SOCCER = ["american football", "canadian football", "australian rules", "gridiron",
              "nfl", "rugby", "gaelic football", "college football", "ncaa", "super bowl",
              "quarterback", "touchdown", "boxing", "boxer", "heavyweight", "jockey",
              "cricket", "grand prix", "formula one", "tennis", "golf", "athletics", "wimbledon"]

# Kata yang terlalu umum untuk dijadikan penanda kecocokan antar sumber.
GENERIC_TERMS = {
    "The", "This", "That", "These", "Those", "His", "Her", "Their", "They", "After", "Before",
    "During", "First", "Second", "Third", "Final", "Football", "Soccer", "League", "Cup", "Club",
    "Association", "National", "Team", "United", "City", "Championship", "Championships",
    "World", "European", "Europe", "International", "Match", "Game", "Season", "Premier",
    "English", "Scottish", "German", "Italian", "Spanish", "French", "Brazilian", "Argentine",
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Former", "Born", "Died", "Manager", "Captain", "Coach",
}

# Format daftar "1998: teks..." yang umum di halaman "on this day in sport".
ENTRY_RE = re.compile(
    r"\b(1[89]\d\d|20[0-2]\d)\s*[:\u2013\u2014-]\s+(.+?)"
    r"(?=\s+\b(?:1[89]\d\d|20[0-2]\d)\s*[:\u2013\u2014-]\s+|$)",
    re.S,
)

# Kata bernada kematian/duka (pakai batas kata agar "meninggalkan klub" tidak ikut terjaring).
HARD_DEATH_RES = [re.compile(p, re.I) for p in (
    r"\bmeninggal\b", r"\bwafat\b", r"\balmarhum\w*", r"\bmendiang\b", r"\bberpulang\b",
    r"\btutup usia\b", r"\bduka\b", r"\bin memoriam\b",
)]
SOFT_DEATH_RES = [re.compile(r"\bmengenang\b", re.I)]  # ambigu: wajar untuk peristiwa, janggal untuk kelahiran
SOURCE_DEATH_RE = re.compile(
    r"\b(died|death|dies|dead|killed|passed away|funeral|murdered|tragedy|disaster)\b", re.I)

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")
client = genai.Client(api_key=api_key)


# ---------------------------------------------------------------- utilitas teks
def looks_like_soccer(text: str) -> bool:
    t = " " + text.lower() + " "
    if any(bad in t for bad in NOT_SOCCER):
        return False
    return any(h in t for h in FOOTBALL_HINTS)


def key_terms(text: str) -> list[str]:
    """Kata berawalan huruf kapital (nama orang/klub/tempat) yang spesifik, urut kemunculan."""
    seen, out = set(), []
    for w in re.findall(r"\b[A-Z][A-Za-z'\u00C0-\u017F]{2,}\b", text):
        if w in GENERIC_TERMS or w.lower() in seen:
            continue
        seen.add(w.lower())
        out.append(w)
    return out[:10]


def host_label(href: str) -> str | None:
    """Nama portal jika domain ada di whitelist, selain itu None."""
    host = urlparse(href).netloc.lower().removeprefix("www.")
    for domain, label in PORTALS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return None


# ---------------------------------------------------------------- pencarian DDG
_ddg_failures = 0


def ddg_text(query: str, max_results: int = 6) -> list[dict]:
    """Pencarian DDG yang tahan rate-limit: berhenti total setelah 3 kegagalan beruntun."""
    global _ddg_failures
    if DDGS is None or _ddg_failures >= 3:
        return []
    print(f"Mencari: {query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        _ddg_failures = 0
        time.sleep(1.5)
        return results
    except Exception as e:
        _ddg_failures += 1
        print(f"  DuckDuckGo kendala ({_ddg_failures}/3): {e}")
        time.sleep(3)
        return []


# ---------------------------------------------------------------- sumber 1: Wikipedia
# Bagian artikel Wikipedia yang bukan prosa (daftar, statistik, referensi) tidak dipakai sebagai sumber.
NON_PROSE_SECTIONS = {
    "references", "external links", "notes", "further reading", "see also", "honours", "honors",
    "career statistics", "statistics", "bibliography", "sources", "footnotes", "citations",
    "works cited", "gallery",
}
HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1$")
MIN_PROSE_LINE_WORDS = 8  # baris lebih pendek dianggap sisa tabel/daftar, bukan prosa


def clean_article_prose(text: str) -> str:
    """
    Dari teks polos artikel Wikipedia, ambil hanya PROSA: buang bagian non-prosa (referensi, tautan luar,
    statistik, daftar gelar, dsb.), buang judul bagian, dan buang baris pendek sisa tabel/daftar.
    """
    keep, drop_level = [], None
    for raw in text.splitlines():
        line = raw.strip()
        m = HEADING_RE.match(line)
        if m:
            level, name = len(m.group(1)), m.group(2).strip().lower()
            if drop_level is not None and level > drop_level:
                continue  # sub-bagian dari bagian yang sedang dibuang
            drop_level = level if name in NON_PROSE_SECTIONS else None
            continue
        if drop_level is None and count_words(line) >= MIN_PROSE_LINE_WORDS:
            keep.append(line)
    return "\n".join(keep)


def fetch_article_prose(title: str) -> str:
    """Isi LENGKAP satu artikel Wikipedia (teks polos, tanpa tabel/catatan kaki), hanya bagian prosa."""
    try:
        r = requests.get(WIKI_API, headers=HEADERS, timeout=30, params={
            "action": "query", "format": "json", "formatversion": 2,
            "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title,
        })
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", [])
        return clean_article_prose(pages[0].get("extract") or "") if pages else ""
    except Exception as e:
        print(f"  Ambil artikel Wikipedia '{title}' gagal: {e}")
        return ""


def enrich_with_article_prose(cands: list[dict]) -> None:
    """
    Ganti ringkasan satu paragraf dengan ISI LENGKAP halaman Wikipedia tokoh, khusus kelahiran/wafat
    (subjeknya jelas: halaman tokoh itu sendiri). Jika gagal, ringkasan lama tetap dipakai.
    Peristiwa tidak diubah: halaman pertama yang ditautkan belum tentu inti peristiwanya.
    """
    targets = [c for c in cands if c.get("wiki_title")][:FULL_FETCH_LIMIT]
    if not targets:
        return
    ok = 0
    for c in targets:
        prose = fetch_article_prose(c["wiki_title"])
        if count_words(prose) > count_words(c["context"]):
            c["context"] = prose
            ok += 1
        time.sleep(0.3)  # sopan terhadap server Wikimedia
    print(f"Isi lengkap artikel Wikipedia dimuat untuk {ok}/{len(targets)} tokoh.")


def get_wikipedia_candidates(month: int, day: int) -> list[dict]:
    url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{month:02d}/{day:02d}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Wikipedia On This Day gagal: {e}")
        return []

    candidates = []
    for kind in ("selected", "events", "births", "deaths"):
        for item in data.get(kind, []):
            text = item.get("text", "")
            year = item.get("year")
            pages = item.get("pages", [])
            # Kelahiran/wafat: hanya halaman tokohnya. Halaman kedua (klub/negara) tidak relevan
            # dan akan menggelembungkan panjang sumber.
            n_pages = 1 if kind in ("births", "deaths") else 2
            extracts = " ".join(p.get("extract", "") for p in pages[:n_pages])
            if year is None or not looks_like_soccer(text + " " + extracts):
                continue
            page_url = ""
            if pages:
                page_url = pages[0].get("content_urls", {}).get("desktop", {}).get("page", "")
            kind_id = {"selected": "peristiwa", "events": "peristiwa",
                       "births": "kelahiran", "deaths": "wafat"}[kind]
            first_extract = pages[0].get("extract", "") if pages else ""
            if kind_id == "kelahiran":
                status = life_status(first_extract)
            elif kind_id == "wafat":
                status = "meninggal"
            else:
                status = ""
            candidates.append({
                "year": int(year),
                "kind": kind_id,
                "text": text,
                "context": extracts,
                "url": page_url or "https://en.wikipedia.org/wiki/Main_Page",
                "source": "Wikipedia",
                "support": [],
                "subject": text.split(",")[0].strip() if kind_id != "peristiwa" else "",
                "status": status,
                # Judul halaman tokoh, dipakai untuk mengunduh isi lengkap artikelnya (hanya kelahiran/wafat).
                "wiki_title": ((pages[0].get("titles", {}).get("normalized")
                                or pages[0].get("titles", {}).get("canonical", "").replace("_", " "))
                               if (pages and kind_id != "peristiwa") else ""),
            })

    seen, unique = set(), []
    for c in candidates:
        key = (c["year"], c["text"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    enrich_with_article_prose(unique)
    return unique


# ---------------------------------------------------------------- sumber 2: portal berita
def get_portal_candidates(month: int, day: int) -> list[dict]:
    """
    Cari halaman 'on this day' di portal whitelist. Filter ketat:
    - tanggal persis muncul di judul/isi (atau di URL), DAN
    - entri berformat 'TAHUN: teks' atau kalimat dengan 'tanggal + tahun' yang eksplisit, DAN
    - teks entri itu sendiri tentang sepak bola (bukan rugby/American football).
    """
    month_en = MONTHS_EN[month - 1]
    date_res = [
        re.compile(rf"\b{day}(?:st|nd|rd|th)?\s+{month_en}\b", re.I),
        re.compile(rf"\b{month_en}\s+{day}(?:st|nd|rd|th)?\b", re.I),
    ]
    url_date_re = re.compile(rf"(?:19|20)\d\d[-/]?{month:02d}[-/]?{day:02d}")
    dated_year_res = [
        re.compile(rf"\b{day}(?:st|nd|rd|th)?\s+{month_en},?\s+((?:1[89]|20)\d\d)\b", re.I),
        re.compile(rf"\b{month_en}\s+{day}(?:st|nd|rd|th)?,?\s+((?:1[89]|20)\d\d)\b", re.I),
    ]

    out, seen = [], set()
    for domain in PORTAL_QUERY_DOMAINS:
        query = f'site:{domain} football "on this day" "{day} {month_en}"'
        for r in ddg_text(query, 6):
            href = r.get("href", "")
            source = host_label(href)
            if not source:
                continue
            title = r.get("title") or ""
            body = (r.get("body") or "").strip()
            blob = f"{title} {body}"
            date_in_text = any(p.search(blob) for p in date_res)
            date_in_url = bool(url_date_re.search(href))
            if not (date_in_text or date_in_url):
                continue

            found = []  # list of (year, text)
            for m in ENTRY_RE.finditer(body):
                text = re.sub(r"\s+", " ", m.group(2)).strip()
                if looks_like_soccer(text):
                    found.append((int(m.group(1)), text[:400]))
            if not found and date_in_text and looks_like_soccer(blob):
                for pat in dated_year_res:
                    m = pat.search(blob)
                    if m:
                        found.append((int(m.group(1)), re.sub(r"\s+", " ", body)[:400]))
                        break

            for year, text in found:
                key = (year, text[:60])
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "year": year, "kind": "peristiwa", "text": text,
                    "context": f"(dari halaman: {title})" if title else "",
                    "url": href, "source": source, "support": [],
                })
    return out


def merge_candidates(wiki: list[dict], portal: list[dict]) -> list[dict]:
    """Kandidat portal yang cocok dengan kandidat Wikipedia menjadi konfirmasi silang."""
    merged = list(wiki)
    for p in portal:
        p_terms = {t.lower() for t in key_terms(p["text"])}
        match = None
        for w in wiki:
            w_terms = {t.lower() for t in key_terms(w["text"] + " " + w["context"])}
            if w["year"] == p["year"] and len(p_terms & w_terms) >= 2:
                match = w
                break
        if match:
            match["support"].append({"url": p["url"], "source": p["source"], "text": p["text"]})
        else:
            merged.append(p)
    merged.sort(key=lambda c: -len(c["support"]))  # yang terkonfirmasi di depan (urutan lain stabil)
    return merged


def corroborate(c: dict) -> list[dict]:
    """Cari konfirmasi independen dari portal whitelist untuk kandidat terpilih."""
    terms = key_terms(c["text"])
    if len(terms) < 2:
        return []
    base = " ".join(terms[:6])
    sites = " OR ".join(f"site:{d}" for d in CORROBORATION_DOMAINS)
    queries = [f"{base} {c['year']} football", f"{base} {c['year']} ({sites})"]
    known_urls = {s["url"] for s in c["support"]} | {c["url"]}
    hits = []
    for q in queries:
        for r in ddg_text(q, 10):
            href = r.get("href", "")
            source = host_label(href)
            if not source or href in known_urls:
                continue
            blob = f"{r.get('title') or ''} {r.get('body') or ''}"
            if str(c["year"]) not in blob or not looks_like_soccer(blob):
                continue
            present = sum(1 for t in terms if t.lower() in blob.lower())
            if present >= min(3, len(terms)):
                known_urls.add(href)
                hits.append({"url": href, "source": source,
                             "text": re.sub(r"\s+", " ", (r.get("body") or ""))[:400]})
            if len(hits) >= 2:
                return hits
    return hits


def source_text_of(c: dict) -> str:
    # Tahun disertakan karena disimpan terpisah dari teks (data terstruktur), padahal sah disebut artikel.
    parts = [str(c["year"]), c.get("lead", ""), c["text"], c["context"]] + [s["text"] for s in c["support"]]
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- pemanggilan model
def call_json(model: str, prompt: str, max_tokens: int | None = None) -> dict:
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            max_output_tokens=max_tokens,
        ),
    )
    raw = re.sub(r"^```(?:json)?|```$", "", resp.text.strip(), flags=re.M).strip()
    return json.loads(raw)


def pick_candidate(candidates: list[dict], date_label: str) -> int:
    """
    Tahap 1: model memilih SATU kandidat dari daftar RINGKAS (cuplikan saja). Isi lengkap sumber (ribuan kata)
    baru dikirim pada tahap penulisan, hanya untuk kandidat terpilih.
    """
    lines = []
    for i, c in enumerate(candidates):
        extra = ""
        if c["kind"] == "kelahiran":
            extra = f", status hidup: {c.get('status') or 'tidak diketahui'}"
        conf = f", dikonfirmasi {', '.join(s['source'] for s in c['support'])}" if c["support"] else ""
        snippet = " ".join(c["context"].split()[:40])
        lines.append(
            f"[{i}] ({c['kind']}, tahun {c['year']}, {c['source']}{conf}{extra}) {c['text']}\n"
            f"    Cuplikan sumber: {snippet}\n"
            f"    Sumber {c['src_words']} kata, artikel akan {c['body_min']}-{c['body_max']} kata."
        )
    listing = "\n\n".join(lines)
    prompt = f"""
Kamu editor blog sejarah sepak bola. Tanggal hari ini: {date_label}.
Pilih SATU kandidat untuk artikel hari ini.

KRITERIA:
1. Benar-benar tentang sepak bola (association football/soccer), BUKAN American football, rugby, atau olahraga lain.
2. Menarik bagi pembaca sepak bola di seluruh dunia.
3. Utamakan kandidat yang dikonfirmasi portal berita dan yang sumbernya kaya.

KANDIDAT:
{listing}

KELUARKAN HANYA JSON: {{"candidate_index": <int>}}
"""
    try:
        idx = int(call_json(MODEL, prompt)["candidate_index"])
        if 0 <= idx < len(candidates):
            return idx
        print(f"Indeks kandidat di luar jangkauan ({idx}); pakai kandidat pertama.")
    except Exception as e:
        print(f"Pemilihan kandidat gagal ({e}); pakai kandidat pertama.")
    return 0


def write_article(c: dict, date_label: str, feedback: list[str], previous: list[str] | None = None) -> dict:
    """Tahap 2: tulis artikel HANYA dari sumber lengkap kandidat terpilih."""
    kind_note = ""
    if c["kind"] == "kelahiran":
        kind_note = (f"\nPENTING: ini adalah KELAHIRAN tokoh pada tanggal hari ini. "
                     f"Status hidup: {c.get('status') or 'tidak diketahui'}.")
    elif c["kind"] == "wafat":
        kind_note = "\nPENTING: ini adalah WAFATNYA tokoh pada tanggal hari ini."
    if c.get("lead"):
        kind_note += f"\nKalimat pembuka yang SUDAH ditulis sistem (jangan diulang): \"{c['lead']}\""

    source_block = f"[{c['source']}] (tahun {c['year']}) {c['text']}\n\n{c['context'] or '-'}"
    for s in c["support"]:
        source_block += f"\n\n[Sumber tambahan: {s['source']}] {s['text']}"

    target = gen_target(c)
    n_par = paragraph_target(c)

    feedback_block = ""
    if feedback:
        feedback_block = "\nDRAFT SEBELUMNYA DITOLAK KARENA:\n- " + "\n- ".join(feedback) + "\n"
        if previous:
            feedback_block += ("\nDRAFT SEBELUMNYA:\n" + "\n\n".join(previous) +
                               "\n\nPerbaiki draft di atas: pertahankan bagian yang benar, buang atau koreksi klaim "
                               "yang bermasalah, dan sesuaikan panjangnya (tambahkan dari bagian sumber yang belum "
                               "dicakup jika kurang panjang). Jangan menulis ulang dari nol tanpa alasan.\n")

    prompt = f"""
Kamu penulis blog sejarah sepak bola yang SANGAT mengutamakan akurasi. Tanggal hari ini: {date_label}.
{kind_note}

SUMBER (satu-satunya sumber fakta yang boleh kamu pakai):
{source_block}
{feedback_block}
ATURAN MUTLAK:
1. Gunakan HANYA fakta yang tertulis eksplisit di SUMBER. Tahun, nama pemain/klub/stadion, skor, angka,
   dan lokasi harus tertulis di sana.
2. Jika dua sumber berbeda pada suatu detail, JANGAN tulis detail itu. Tulis hanya yang disepakati.
3. DILARANG menambah detail yang tidak ada di sumber: suasana stadion, jumlah penonton, cuaca, kutipan,
   perasaan pemain, status "rival", julukan, atau latar sejarah dari ingatanmu.
4. Jangan menyebut tanggal/tahun/angka apa pun yang tidak ada di sumber.
5. PANJANG (batas keras, dihitung otomatis dari panjang sumber {c['src_words']} kata): tulisanmu harus
   antara {c['gen_min']} dan {c['gen_max']} kata. Targetkan sekitar {target} kata. Untuk mencapainya, liput
   bagian-bagian sumber secara menyeluruh dan berimbang (masa awal, klub-klub utama, tim nasional,
   penghargaan, gaya bermain, kehidupan pribadi, bila ada di sumber) dengan urutan kronologis.
   Jangan mengulang-ulang dan jangan mengarang demi memenuhi panjang.
6. PARAGRAF: tulis sekitar {n_par} paragraf. Tiap paragraf sekitar {PARA_TARGET_WORDS} kata
   ({PARA_MIN_WORDS}-{PARA_MAX_WORDS} kata, kira-kira 4-8 kalimat) dan memuat SATU tema (misalnya: masa awal,
   satu klub, tim nasional, penghargaan). DILARANG menulis satu blok panjang tanpa jeda paragraf.
7. Bahasa Indonesia, gaya bercerita ringan tapi tetap faktual.
8. Jangan tulis tahun di judul; tahun akan ditambahkan sistem.
9. Jika berjenis "kelahiran": sistem SUDAH menulis judul dan kalimat pembuka yang menyatakan tokoh itu lahir
   pada tanggal ini. Jangan ulangi; lanjutkan dengan karier/prestasi dari sumber. Nada netral-positif.
   DILARANG memakai nada duka atau kata seperti "mengenang", "almarhum", "mendiang", "meninggal",
   "tutup usia" untuk tokoh yang tidak tercatat meninggal.
10. Jika berjenis "wafat": sistem sudah menulis judul dan kalimat pembuka. Gunakan nada hormat dan tenang,
    bukan perayaan.
11. Untuk judul "peristiwa": deskriptif dan netral. Jangan memakai kata "Mengenang".
12. Tulis tokoh yang masih hidup dengan kata kerja yang tepat; jangan menyiratkan bahwa ia sudah tiada.
13. DILARANG menuliskan tulisan "Sumber: ..." atau menyebutkan nama media/Wikipedia di dalam atau di akhir artikel.
14. Paragraf pertama HARUS diawali dengan tanggal dan tahun kejadian serta judul/pembuka peristiwa, tanpa mengulang kalimat yang sama di awal.

KELUARKAN HANYA JSON dengan format:
{{"title": "<judul singkat tanpa tahun>", "paragraphs": ["<paragraf 1>", "<paragraf 2>", "..."]}}
"""
    return call_json(MODEL, prompt, max_tokens=8192)


def llm_verify(article_text: str, source: str) -> list[str]:
    prompt = f"""
Kamu fact-checker ketat. Bandingkan ARTIKEL dengan SUMBER.
Daftar setiap klaim faktual di artikel (tahun, nama, klub, skor, angka, lokasi, hubungan antar pihak,
suasana, hasil pertandingan) yang TIDAK tertulis atau TIDAK bisa disimpulkan langsung dari SUMBER.
Embel-embel dramatis yang tidak ada di sumber juga dihitung tidak didukung.
Jika bagian-bagian SUMBER saling bertentangan pada suatu detail, klaim tentang detail itu dihitung tidak didukung.
Pastikan peristiwanya benar-benar sepak bola (association football), bukan American football atau rugby.

SUMBER:
{source}

ARTIKEL:
{article_text}

KELUARKAN HANYA JSON: {{"valid": <true/false>, "unsupported_claims": ["...", "..."]}}
"valid" = true hanya jika unsupported_claims kosong.
"""
    result = call_json(VERIFIER_MODEL, prompt)
    return [] if result.get("valid") else result.get("unsupported_claims") or ["klaim tidak terverifikasi"]


# ---------------------------------------------------------------- pengecekan deterministik
def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def deterministic_check(article_text: str, source: str, today_day: int) -> list[str]:
    """Setiap angka di artikel harus muncul di sumber (kecuali tanggal hari ini)."""
    allowed = numbers_in(source) | {str(today_day)}
    missing = sorted(numbers_in(article_text) - allowed)
    return [f"angka '{n}' tidak ada di sumber" for n in missing]


def life_status(extract: str) -> str:
    """
    Tentukan 'hidup' / 'meninggal' / 'tidak diketahui' dari tanda kurung di awal ringkasan Wikipedia:
      "(born 15 October 1977)"           -> hidup
      "(15 October 1977 - 3 June 2020)"  -> meninggal
    Jika ragu, hasilnya 'tidak diketahui' (aman: tidak ada klaim usia atau nada duka).
    """
    inners = re.findall(r"\(([^)]{0,160})\)", extract[:300])
    for inner in inners:
        if re.search(r"\bdied\b", inner) or re.search(r"\d{4}\s*[\u2013\u2014-]\s*[^)]*?\d{4}", inner):
            return "meninggal"
    for inner in inners:
        if re.search(r"\bborn\b", inner):
            return "hidup"
    return "tidak diketahui"


def framing(c: dict, now: datetime) -> tuple[str, str]:
    """
    Menyerahkan pembuatan kalimat pembuka sepenuhnya ke Gemini 
    agar tidak terjadi pengulangan kalimat lahir/wafat.
    """
    return "", ""
    name = c.get("subject") or c["text"].split(",")[0].strip()
    date_txt = f"{now.day} {MONTHS_ID[now.month - 1]} {c['year']}"
    if c["kind"] == "kelahiran":
        lead = f"Pada {date_txt}, {name} lahir."
        if c.get("status") == "hidup":  # usia hanya disebut jika terkonfirmasi masih hidup
            lead += f" Hari ini, ia genap berusia {now.year - c['year']} tahun."
        return f"Kelahiran {name}", lead
    return f"Wafatnya {name}", f"Pada {date_txt}, {name} meninggal dunia."


def tone_check(article_text: str, source: str, c: dict) -> list[str]:
    """Tolak nada duka/kematian yang tidak sesuai dengan jenis peristiwa dan sumbernya."""
    if c["kind"] == "wafat":
        return []
    hard = [p.pattern for p in HARD_DEATH_RES if p.search(article_text)]
    soft = [p.pattern for p in SOFT_DEATH_RES if p.search(article_text)]
    if c["kind"] == "kelahiran" and c.get("status") != "meninggal":
        found = hard + soft
        if found:
            return ["nada duka/kematian ('mengenang', 'meninggal', dsb.) tidak pantas: ini kabar kelahiran "
                    "tokoh yang tidak tercatat meninggal. Tulis dengan nada netral-positif"]
    elif hard and not SOURCE_DEATH_RE.search(source):
        return ["kata bernada kematian dipakai padahal sumber tidak menyebut kematian"]
    return []


# ---------------------------------------------------------------- panjang artikel = % dari sumber
def count_words(text: str) -> int:
    return len(re.findall(r"\w+(?:['\u2019-]\w+)*", text))


def source_word_count(c: dict) -> int:
    """
    Panjang SUMBER dalam kata: teks kandidat + ringkasan + sumber tambahan portal.
    Tidak dihitung: tahun, kalimat pembuka buatan kode, dan label metadata '(dari halaman: ...)'.
    """
    parts = [c["text"]]
    if c["context"] and not c["context"].startswith("(dari halaman"):
        parts.append(c["context"])
    parts += [s["text"] for s in c["support"]]
    return count_words(" ".join(parts))


def set_budget(c: dict) -> None:
    """
    Hitung rentang panjang. 'body' = isi artikel akhir (kalimat pembuka kode + paragraf model).
    'gen'  = bagian yang boleh ditulis model (body dikurangi kalimat pembuka).
    Batas atas dijepit ke MAX_BODY_WORDS; jika batas BAWAH saja sudah melewatinya (halaman sangat panjang),
    kandidat ditandai 'too_long' dan dibuang karena tidak mungkin ditulis dengan andal.
    """
    src_w = source_word_count(c) * LENGTH_LANG_FACTOR
    lead_w = count_words(c.get("lead", ""))
    raw_min = math.ceil(src_w * LENGTH_MIN_RATIO)
    raw_max = math.floor(src_w * LENGTH_MAX_RATIO)
    c["src_words"] = round(src_w)
    c["too_long"] = raw_min > MAX_BODY_WORDS
    c["body_min"] = raw_min
    c["body_max"] = min(raw_max, MAX_BODY_WORDS)
    c["gen_min"] = max(MIN_MODEL_WORDS, c["body_min"] - lead_w)
    c["gen_max"] = c["body_max"] - lead_w


def gen_target(c: dict) -> int:
    """
    Sasaran panjang tulisan model: titik tengah rentang (sekitar 35% dari sumber untuk rasio 20-50%).
    Jika model menulis kurang dari batas bawah, percobaan ulang memakai draf sebelumnya untuk ditambah.
    """
    return c["gen_min"] + round(0.5 * (c["gen_max"] - c["gen_min"]))


def paragraph_target(c: dict) -> int:
    """Jumlah paragraf = panjang sasaran dibagi panjang paragraf normal (minimal 1)."""
    return max(1, round(gen_target(c) / PARA_TARGET_WORDS))


def prepare_candidates(cands: list[dict], now: datetime) -> list[dict]:
    """Pasang judul/kalimat pembuka + anggaran panjang. Buang kandidat yang sumbernya terlalu pendek/panjang."""
    ready, too_short, too_long = [], 0, 0
    for c in cands:
        c["title_fixed"], c["lead"] = framing(c, now)
        set_budget(c)
        if c["too_long"]:
            too_long += 1
            continue
        if c["gen_max"] < MIN_GEN_WORDS:
            too_short += 1
            continue
        ready.append(c)
    if too_short:
        print(f"{too_short} kandidat dibuang karena sumbernya terlalu pendek untuk rentang "
              f"{LENGTH_MIN_RATIO:.0%}-{LENGTH_MAX_RATIO:.0%}.")
    if too_long:
        print(f"{too_long} kandidat dibuang karena artikelnya akan melebihi {MAX_BODY_WORDS} kata "
              f"(naikkan MAX_BODY_WORDS jika ingin menyertakannya).")
    return ready


# ---------------------------------------------------------------- paragraf normal
# Singkatan/inisial yang diakhiri titik tetapi bukan akhir kalimat.
ABBREV = {"dr", "mr", "mrs", "ms", "st", "jr", "sr", "vs", "no", "prof", "ir", "drs", "hj", "h"}


def split_sentences(text: str) -> list[str]:
    """Pecah teks menjadi kalimat; tidak memecah setelah singkatan (Dr., vs., U.S.) atau inisial satu huruf."""
    parts, start = [], 0
    for m in re.finditer(r"[.!?]+[\"'\u201d\u2019)]?\s+(?=[\"'\u201c\u2018(]?[A-Z0-9])", text):
        before = text[start:m.start()].split()
        last = before[-1].lower().strip("\"'\u201c\u2018(") if before else ""
        if last in ABBREV or len(last) == 1 or re.fullmatch(r"(?:[a-z]\.)+[a-z]?", last):
            continue
        parts.append(text[start:m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _eff_words(p: str, index: int, lead_w: int) -> int:
    """Panjang paragraf sebagaimana tampil; paragraf pertama dihitung beserta kalimat pembuka sistem."""
    return count_words(p) + (lead_w if index == 0 else 0)


def _split_into(p: str, k: int, offset: int) -> list[str]:
    """Pecah satu paragraf panjang menjadi ~k paragraf berukuran seimbang, hanya di batas kalimat."""
    sents = split_sentences(p)
    if len(sents) < 2:
        return [p]
    per = (offset + sum(count_words(s) for s in sents)) / k
    groups, cur, cur_w = [], [], offset
    for j, s in enumerate(sents):
        cur.append(s)
        cur_w += count_words(s)
        if cur_w >= per and len(groups) < k - 1 and j < len(sents) - 1:
            groups.append(" ".join(cur))
            cur, cur_w = [], 0
    if cur:
        groups.append(" ".join(cur))
    if len(groups) > 1 and count_words(groups[-1]) < PARA_MIN_WORDS // 2:  # sisa terlalu kecil -> gabung
        groups[-2] += " " + groups.pop()
    return groups


def normalize_paragraphs(paragraphs: list[str], lead_w: int = 0) -> list[str]:
    """
    Pastikan hasil berupa paragraf NORMAL (sekitar PARA_TARGET_WORDS kata), apa pun format keluaran model:
    (a) paragraf yang terlalu pendek digabung ke tetangganya;
    (b) paragraf yang terlalu panjang dipecah di batas kalimat.
    Jumlah kata total tidak berubah; hanya penempatan jeda paragraf.
    """
    paras = [" ".join(p.split()) for p in paragraphs if p and p.strip()]

    i = 0
    while len(paras) > 1 and i < len(paras):
        threshold = PARA_MIN_WORDS if i < len(paras) - 1 else PARA_MIN_WORDS // 2
        if _eff_words(paras[i], i, lead_w) < threshold:
            if i > 0:
                paras[i - 1] += " " + paras[i]
                del paras[i]
            else:
                paras[1] = paras[0] + " " + paras[1]
                del paras[0]
            continue
        i += 1

    out = []
    for i, p in enumerate(paras):
        offset = lead_w if i == 0 else 0
        w = count_words(p) + offset
        if w <= PARA_MAX_WORDS:
            out.append(p)
        else:
            out.extend(_split_into(p, max(2, math.ceil(w / PARA_TARGET_WORDS)), offset))
    return out


def fit_length(paragraphs: list[str], c: dict) -> list[str]:
    """
    Jika terlalu panjang, buang paragraf dari belakang (hanya mengurangi isi, tidak menambah klaim).
    Jika terlalu pendek tidak bisa diperbaiki di sini; ditangani lewat length_check + tulis ulang.
    """
    paras = [p for p in paragraphs if p.strip()]
    lead_w = count_words(c.get("lead", ""))
    while len(paras) > 1 and lead_w + sum(count_words(p) for p in paras) > c["body_max"]:
        paras.pop()
    return paras


def length_check(c: dict, paragraphs: list[str]) -> list[str]:
    lead_w = count_words(c.get("lead", ""))
    gen_w = sum(count_words(p) for p in paragraphs)
    if c["gen_min"] <= gen_w <= c["gen_max"]:
        return []
    detail = (f"(total artikel {lead_w + gen_w} kata termasuk kalimat pembuka sistem {lead_w} kata; rentang total "
              f"{c['body_min']}-{c['body_max']} kata = {LENGTH_MIN_RATIO:.0%}-{LENGTH_MAX_RATIO:.0%} dari sumber "
              f"{c['src_words']} kata)")
    if gen_w < c["gen_min"]:
        return [f"tulisanmu terlalu pendek: {gen_w} kata, minimal {c['gen_min']} {detail}. Tambahkan sekitar "
                f"{gen_target(c) - gen_w} kata dari bagian sumber yang belum kamu cakup (tanpa mengarang)"]
    return [f"tulisanmu terlalu panjang: {gen_w} kata, maksimal {c['gen_max']} {detail}. "
            f"Ringkas hingga sekitar {gen_target(c)} kata"]


# ---------------------------------------------------------------- output
def build_html(candidate: dict, draft: dict) -> str:
    # Kelahiran/wafat: judul & kalimat pembuka dari kode. Peristiwa: judul dari AI.
    title = html.escape((candidate.get("title_fixed") or draft["title"]).strip().rstrip(":"))
    lead = html.escape(candidate.get("lead", ""))
    paragraphs = [html.escape(p.strip()) for p in draft["paragraphs"] if p.strip()]
    first = f"{lead} {paragraphs[0]}".strip()
    parts = [f"<p><b>Tahun {candidate['year']} - {title}:</b> {first}</p>"]
    parts += [f"<p>{p}</p>" for p in paragraphs[1:]]

    return "\n".join(parts)


# ---------------------------------------------------------------- main
def select_candidate(candidates: list[dict], date_label: str) -> dict | None:
    """Pilih kandidat (LLM), lalu cari konfirmasi portal. Mode ketat: kandidat tanpa konfirmasi dibuang."""
    while candidates:
        idx = pick_candidate(candidates, date_label)
        chosen = candidates[idx]
        print(f"Kandidat terpilih: [{chosen['year']}] {chosen['text'][:80]}")

        if not chosen["support"] and not chosen.get("_corroboration_tried"):
            chosen["_corroboration_tried"] = True
            chosen["support"] = corroborate(chosen)
            set_budget(chosen)  # sumber bertambah -> rentang panjang ikut dihitung ulang
            if chosen["support"]:
                print(f"  Terkonfirmasi oleh: {', '.join(s['source'] for s in chosen['support'])}")

        if chosen["too_long"]:
            print("  Dibuang: artikelnya akan melebihi batas panjang.")
            candidates.pop(idx)
            continue
        if REQUIRE_CORROBORATION and not chosen["support"]:
            print("  Dibuang: tidak ada konfirmasi dari portal berita.")
            candidates.pop(idx)
            continue
        return chosen
    return None


def try_write(chosen: dict, date_label: str, now: datetime) -> dict | None:
    """Tulis + verifikasi untuk SATU kandidat. Percobaan ulang memakai draf sebelumnya sebagai dasar perbaikan."""
    feedback: list[str] = []
    previous: list[str] | None = None
    lead_w = count_words(chosen.get("lead", ""))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"Percobaan {attempt}/{MAX_ATTEMPTS}...")
        try:
            draft = write_article(chosen, date_label, feedback, previous)
            paras = [p for p in draft.get("paragraphs", []) if isinstance(p, str) and p.strip()]
            if not paras:
                raise ValueError("paragraf kosong")
        except Exception as e:
            print(f"Output model tidak valid: {e}")
            feedback = [f"format output salah ({e})"]
            continue

        paras = normalize_paragraphs(paras, lead_w)  # pastikan paragraf normal, bukan satu blok
        paras = fit_length(paras, chosen)
        draft["paragraphs"] = paras
        previous = paras

        article_text = f"{chosen['title_fixed'] or draft.get('title', '')} " + " ".join(paras)
        source = source_text_of(chosen)

        problems = length_check(chosen, paras)
        problems += deterministic_check(article_text, source, now.day)
        problems += tone_check(article_text, source, chosen)
        if not problems:
            try:
                problems = llm_verify(article_text, source)
            except Exception as e:
                problems = [f"verifier gagal dijalankan ({e})"]

        if problems:
            print("Ditolak:", problems)
            feedback = problems
            continue
        return draft
    return None


def main() -> int:
    now = datetime.now(timezone.utc)  # UTC: acuan universal untuk pembaca di seluruh dunia
    date_label = f"{now.day} {MONTHS_ID[now.month - 1]}"
    print(f"Memproses fakta bola untuk {date_label} (UTC)...")

    wiki = get_wikipedia_candidates(now.month, now.day)
    print(f"Wikipedia: {len(wiki)} kandidat sepak bola.")
    portal = get_portal_candidates(now.month, now.day)
    print(f"Portal berita: {len(portal)} kandidat.")

    ready = prepare_candidates(merge_candidates(wiki, portal), now)
    ready.sort(key=lambda c: (-len(c["support"]), -c["src_words"]))  # terkonfirmasi & terkaya di depan
    candidates = ready[:MAX_CANDIDATES]
    if not candidates:
        print("Tidak ada kandidat dengan panjang sumber yang sesuai. File lama dipertahankan.")
        return 1
    confirmed = sum(1 for c in candidates if c["support"])
    print(f"Total {len(candidates)} kandidat ({confirmed} sudah terkonfirmasi silang).")

    for _ in range(MAX_CANDIDATE_TRIES):
        chosen = select_candidate(candidates, date_label)
        if chosen is None:
            break
        draft = try_write(chosen, date_label, now)
        if draft:
            data = {"date": date_label, "fact": build_html(chosen, draft)}
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            body_w = count_words(chosen.get("lead", "")) + sum(count_words(p) for p in draft["paragraphs"])
            print(f"Panjang artikel {body_w} kata dalam {len(draft['paragraphs'])} paragraf = "
                  f"{body_w / max(chosen['src_words'], 1):.0%} dari sumber ({chosen['src_words']} kata).")
            used = [chosen["url"]] + [s["url"] for s in chosen["support"]]
            print(f"Berhasil memperbarui {OUTPUT_FILE}. Sumber: {', '.join(used)}")
            return 0
        print("Kandidat ini gagal semua percobaan; mencoba kandidat lain.")
        candidates.remove(chosen)

    print("Semua percobaan gagal verifikasi. File lama dipertahankan.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
