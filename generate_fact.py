"""
Generator fakta sepak bola harian (anti-halusinasi, multi-sumber).

Alur:
1. Kumpulkan kandidat peristiwa dari:
   a. Wikipedia "On This Day" (terstruktur: tahun + teks + ringkasan halaman)
   b. Portal berita/otoritas sepak bola dunia (BBC, Guardian, Sky Sports, ESPN, Goal, FIFA, UEFA, dst.)
      lewat pencarian per-domain dengan filter ketat (tanggal persis + tahun + sepak bola).
2. Kandidat portal yang cocok dengan kandidat Wikipedia (tahun sama + istilah kunci sama)
   digabung sebagai "konfirmasi silang". Kandidat yang terkonfirmasi diprioritaskan.
3. Gemini hanya boleh memilih & menulis ulang dari kandidat. Tahun diambil dari DATA, bukan dari AI.
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
MAX_ATTEMPTS = 4
MAX_CANDIDATES = 12
OUTPUT_FILE = "today_fact.json"
HEADERS = {"User-Agent": "daily-football-fact/3.0 (ganti-dengan-email-atau-url-blogmu)"}

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
            extracts = " ".join(p.get("extract", "") for p in pages[:2])
            if year is None or not looks_like_soccer(text + " " + extracts):
                continue
            page_url = ""
            if pages:
                page_url = pages[0].get("content_urls", {}).get("desktop", {}).get("page", "")
            candidates.append({
                "year": int(year),
                "kind": {"selected": "peristiwa", "events": "peristiwa",
                         "births": "kelahiran", "deaths": "wafat"}[kind],
                "text": text,
                "context": extracts,
                "url": page_url or "https://en.wikipedia.org/wiki/Main_Page",
                "source": "Wikipedia",
                "support": [],
            })

    seen, unique = set(), []
    for c in candidates:
        key = (c["year"], c["text"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
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
    parts = [str(c["year"]), c["text"], c["context"]] + [s["text"] for s in c["support"]]
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- pemanggilan model
def call_json(model: str, prompt: str) -> dict:
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    raw = re.sub(r"^```(?:json)?|```$", "", resp.text.strip(), flags=re.M).strip()
    return json.loads(raw)


def write_article(candidates: list[dict], date_label: str, feedback: list[str]) -> dict:
    blocks = []
    for i, c in enumerate(candidates):
        block = f"[{i}] ({c['kind']}, tahun {c['year']}) [{c['source']}] {c['text']}\n    Konteks: {c['context'] or '-'}"
        for s in c["support"]:
            block += f"\n    Sumber tambahan [{s['source']}]: {s['text']}"
        blocks.append(block)
    listing = "\n\n".join(blocks)

    feedback_block = ""
    if feedback:
        feedback_block = ("\nPERCOBAAN SEBELUMNYA DITOLAK KARENA:\n- " + "\n- ".join(feedback) +
                          "\nPerbaiki dengan membuang klaim tersebut, atau pilih kandidat lain.\n")

    prompt = f"""
Kamu penulis blog sejarah sepak bola yang SANGAT mengutamakan akurasi. Tanggal hari ini: {date_label}.

KANDIDAT (satu-satunya sumber fakta yang boleh kamu pakai):
{listing}
{feedback_block}
ATURAN MUTLAK:
1. Pilih SATU kandidat yang benar-benar tentang sepak bola (association football/soccer),
   BUKAN American football, rugby, atau olahraga lain. Utamakan kandidat yang punya "Sumber tambahan".
2. Gunakan HANYA fakta yang tertulis eksplisit di kandidat terpilih (teks, konteks, dan sumber tambahan).
   Tahun, nama pemain/klub/stadion, skor, angka, dan lokasi harus tertulis di sana.
3. Jika dua sumber berbeda pada suatu detail, JANGAN tulis detail itu. Tulis hanya yang disepakati.
4. DILARANG menambah detail yang tidak ada di sumber: suasana stadion, jumlah penonton,
   cuaca, kutipan, perasaan pemain, status "rival", julukan, atau latar sejarah dari ingatanmu.
5. Jika informasi di sumber sedikit, tulis lebih singkat dan sederhana.
   Lebih baik pendek tapi benar daripada panjang tapi mengarang.
6. Jangan menyebut tanggal/tahun/angka apa pun yang tidak ada di sumber.
7. Bahasa Indonesia, gaya bercerita ringan tapi tetap faktual. 3 atau 4 paragraf, boleh lebih jika sumber sangat kaya informasi.
8. Jangan tulis tahun di judul; tahun akan ditambahkan sistem.

KELUARKAN HANYA JSON dengan format:
{{"candidate_index": <int>, "title": "<judul singkat tanpa tahun>", "paragraphs": ["<p1>", "<p2>", "<p3>"]}}
"""
    return call_json(MODEL, prompt)


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


# ---------------------------------------------------------------- output
def build_html(candidate: dict, draft: dict) -> str:
    title = html.escape(draft["title"].strip().rstrip(":"))
    paragraphs = [html.escape(p.strip()) for p in draft["paragraphs"] if p.strip()]
    parts = [f"<p><b>Tahun {candidate['year']} - {title}:</b> {paragraphs[0]}</p>"]
    parts += [f"<p>{p}</p>" for p in paragraphs[1:]]

    sources, seen_urls = [], set()
    for label, url in [(candidate["source"], candidate["url"])] + \
                      [(s["source"], s["url"]) for s in candidate["support"]]:
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append(f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(label)}</a>')
    if sources:
        parts.append(f"<p><small>Sumber: {', '.join(sources)}</small></p>")
    return "\n".join(parts)


# ---------------------------------------------------------------- main
def main() -> int:
    now = datetime.now(timezone.utc)  # UTC: acuan universal untuk pembaca di seluruh dunia
    date_label = f"{now.day} {MONTHS_ID[now.month - 1]}"
    print(f"Memproses fakta bola untuk {date_label} (UTC)...")

    wiki = get_wikipedia_candidates(now.month, now.day)
    print(f"Wikipedia: {len(wiki)} kandidat sepak bola.")
    portal = get_portal_candidates(now.month, now.day)
    print(f"Portal berita: {len(portal)} kandidat.")

    candidates = merge_candidates(wiki, portal)[:MAX_CANDIDATES]
    if not candidates:
        print("Tidak ada kandidat sumber yang valid. File lama dipertahankan.")
        return 1
    confirmed = sum(1 for c in candidates if c["support"])
    print(f"Total {len(candidates)} kandidat ({confirmed} sudah terkonfirmasi silang).")

    feedback: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not candidates:
            break
        print(f"Percobaan {attempt}/{MAX_ATTEMPTS}...")
        try:
            draft = write_article(candidates, date_label, feedback)
            idx = int(draft["candidate_index"])
            chosen = candidates[idx]
            if not draft.get("paragraphs"):
                raise ValueError("paragraf kosong")
        except Exception as e:
            print(f"Output model tidak valid: {e}")
            feedback = [f"format output salah ({e})"]
            continue

        # Cari konfirmasi portal untuk kandidat terpilih jika belum ada.
        if not chosen["support"] and not chosen.get("_corroboration_tried"):
            chosen["_corroboration_tried"] = True
            chosen["support"] = corroborate(chosen)
            if chosen["support"]:
                print(f"  Terkonfirmasi oleh: {', '.join(s['source'] for s in chosen['support'])}")

        if REQUIRE_CORROBORATION and not chosen["support"]:
            print("  Ditolak: tidak ada konfirmasi dari portal berita.")
            candidates.pop(idx)
            feedback = ["kandidat sebelumnya tidak punya konfirmasi dari portal berita; pilih kandidat lain"]
            continue

        article_text = f"{draft['title']} " + " ".join(draft["paragraphs"])
        source = source_text_of(chosen)

        problems = deterministic_check(article_text, source, now.day)
        if not problems:
            try:
                problems = llm_verify(article_text, source)
            except Exception as e:
                problems = [f"verifier gagal dijalankan ({e})"]

        if problems:
            print("Ditolak:", problems)
            feedback = problems
            continue

        data = {"date": date_label, "fact": build_html(chosen, draft)}
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        used = [chosen["url"]] + [s["url"] for s in chosen["support"]]
        print(f"Berhasil memperbarui {OUTPUT_FILE}. Sumber: {', '.join(used)}")
        return 0

    print("Semua percobaan gagal verifikasi. File lama dipertahankan.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
