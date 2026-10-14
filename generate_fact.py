
import os
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from google import genai

# =========================================================
# KONFIGURASI
# =========================================================

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

MODEL = "gemini-3.5-flash-lite"
TIMEZONE = ZoneInfo("Asia/Jakarta")

client = genai.Client(api_key=API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
}

MAX_SEARCH_RESULTS = 5
MAX_PAGES_TO_FETCH = 10
MIN_INDEPENDENT_DOMAINS = 2

# =========================================================
# UTILITAS
# =========================================================

def clean_json(text):
    """Mengambil JSON jika model membungkusnya dengan code fence."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def get_domain(url):
    """Normalisasi domain untuk mengurangi sumber duplikat."""
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def search_football_archives(date_obj):
    """Cari arsip untuk tanggal dan bulan yang sedang diproses."""
    day_month = date_obj.strftime("%d %B")
    month_day = date_obj.strftime("%B %d")
    day = date_obj.day
    month = date_obj.strftime("%B")

    queries = [
        f"football historical events {month_day} results archive",
        f"football matches played on {day_month} historical results",
        f"site:worldfootball.net football {day_month} historical matches",
        f"football club official history {day} {month}",
        f"sejarah pertandingan sepak bola {day_month}",
    ]

    found = {}
    try:
        with DDGS() as ddgs:
            for query in queries:
                print(f"Mencari: {query}")
                try:
                    results = ddgs.text(
                        query,
                        max_results=MAX_SEARCH_RESULTS
                    )
                    for result in results:
                        url = result.get("href", "")
                        if not url.startswith("http"):
                            continue

                        # Jangan mengumpulkan URL yang sama berulang kali.
                        if url not in found:
                            found[url] = {
                                "url": url,
                                "title": result.get("title", ""),
                                "snippet": result.get("body", "")
                            }
                except Exception as exc:
                    print(f"Pencarian dilewati: {exc}")

                time.sleep(1)

    except Exception as exc:
        print(f"Pencarian gagal: {exc}")

    return list(found.values())


def fetch_page(source):
    """Ambil teks halaman asli, bukan hanya snippet mesin pencari."""
    try:
        response = requests.get(
            source["url"],
            headers=HEADERS,
            timeout=12
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return None

        # Hindari memproses halaman yang terlalu besar.
        if len(response.content) > 3_000_000:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup([
            "script", "style", "nav", "footer",
            "header", "form", "noscript", "svg"
        ]):
            tag.decompose()

        text = " ".join(soup.stripped_strings)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 250:
            return None

        return {
            "url": source["url"],
            "title": source["title"] or soup.title.get_text(" ", strip=True)
            if soup.title else source["title"],
            "domain": get_domain(source["url"]),
            "content": text[:7000]
        }

    except Exception as exc:
        print(f"Tidak dapat membaca {source['url']}: {exc}")
        return None


def collect_sources(search_results):
    """Ambil halaman dari beberapa domain berbeda."""
    sources = []
    used_domains = set()

    for result in search_results:
        domain = get_domain(result["url"])
        if not domain or domain in used_domains:
            continue

        page = fetch_page(result)
        if not page:
            continue

        page["id"] = f"S{len(sources) + 1}"
        sources.append(page)
        used_domains.add(domain)

        print(f"Sumber berhasil dibaca: {domain}")

        if len(sources) >= MAX_PAGES_TO_FETCH:
            break

    return sources


# =========================================================
# TAHAP 1: PILIH PERISTIWA BERDASARKAN BUKTI
# =========================================================

def select_candidate(date_obj, sources):
    day_month = date_obj.strftime("%d %B")
    source_data = json.dumps(sources, ensure_ascii=False)

    prompt = f"""
Kamu adalah peneliti sejarah sepak bola yang sangat berhati-hati.

Tanggal target: {day_month}.
Tahun kejadian boleh tahun berapa pun.

SUMBER YANG BERHASIL DIBACA:
{source_data}

TUGAS:
Pilih satu peristiwa sepak bola yang benar-benar didukung oleh
sumber di atas dan terjadi pada tanggal serta bulan target.

SYARAT MUTLAK:
1. Tanggal dan bulan kejadian harus cocok dengan tanggal target.
2. Jangan menyimpulkan tanggal kejadian dari tanggal publikasi artikel.
3. Tahun, klub, lawan, skor, pencetak gol, dan lokasi hanya boleh
   digunakan jika didukung oleh sumber.
4. Bedakan tanggal pertandingan dari tanggal pembukaan stadion,
   tanggal pendirian klub, atau tanggal peristiwa lain.
5. Gunakan setidaknya dua sumber dari domain berbeda yang secara
   nyata mendukung fakta inti yang sama.
6. Dua situs yang hanya menyalin berita identik bukan bukti
   independen yang memadai.
7. Jika tidak ada peristiwa yang cukup kuat, nyatakan TIDAK LAYAK.
8. Jangan mengarang fakta atau mengisi informasi yang tidak tersedia.
9. Prioritaskan arsip resmi klub, liga, federasi, dan catatan
   pertandingan yang dapat diverifikasi.

KELUARAN HARUS BERUPA JSON VALID SAJA:
{{
  "layak": true,
  "judul": "Judul singkat peristiwa",
  "tahun": 1900,
  "tanggal": "03 September",
  "jenis": "pertandingan",
  "fakta_inti": [
    "Fakta yang didukung sumber"
  ],
  "source_ids": ["S1", "S2"],
  "alasan": "Alasan kelayakan berdasarkan bukti"
}}

Jika tidak layak, gunakan:
{{
  "layak": false,
  "judul": "",
  "tahun": null,
  "tanggal": "",
  "jenis": "",
  "fakta_inti": [],
  "source_ids": [],
  "alasan": "Penjelasan singkat"
}}

Jangan pernah mengisi layak=true hanya untuk memenuhi tugas.
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )
    return clean_json(response.text)


# =========================================================
# TAHAP 2: AUDIT FAKTA DAN PENULISAN
# =========================================================

def verify_and_write(date_obj, candidate, sources):
    source_map = {s["id"]: s for s in sources}

    selected_sources = [
        source_map[sid]
        for sid in candidate.get("source_ids", [])
        if sid in source_map
    ]

    domains = {
        s["domain"] for s in selected_sources if s.get("domain")
    }

    if len(domains) < MIN_INDEPENDENT_DOMAINS:
        return None

    day_month = date_obj.strftime("%d %B")

    prompt = f"""
Kamu bertugas sebagai editor verifikasi fakta sepak bola.

Tanggal target: {day_month}

KANDIDAT PERISTIWA:
{json.dumps(candidate, ensure_ascii=False)}

SUMBER YANG HARUS DIPERIKSA:
{json.dumps(selected_sources, ensure_ascii=False)}

PERIKSA ULANG SEBELUM MENULIS:
- Apakah tanggal dan bulan peristiwa benar-benar cocok?
- Apakah tahun kejadian didukung sumber?
- Apakah semua fakta inti dapat ditelusuri ke sumber?
- Apakah ada kontradiksi mengenai lawan, skor, stadion, atau hasil?
- Apakah sumber benar-benar mendukung klaim, bukan sekadar
  menyebut nama klub atau peristiwa yang mirip?
- Apakah ada detail narasi yang hanya merupakan dugaan?

Jangan menganggap keputusan tahap sebelumnya otomatis benar.
Jika ada keraguan penting atau kontradiksi yang belum terselesaikan,
verifikasi harus gagal.

Jika lolos, tulis artikel Bahasa Indonesia:
- 3 paragraf pendek dengan tag HTML <p>...</p>.
- Paragraf pertama diawali dengan tahun dan judul dalam format:
  <p><b>Tahun 1900 - Judul:</b> ...</p>
- Gaya storytelling menarik, tetapi tidak sensasional.
- Semua detail harus bersumber.
- Dilarang mengarang suasana stadion, reaksi penonton, motif,
  jumlah penonton, pencetak gol, atau detail lain.
- Jangan mengklaim pertandingan berlangsung sengit atau dramatis
  tanpa bukti yang mendukung.
- Jangan menggunakan informasi yang tidak tersedia hanya demi
  membuat tulisan lebih panjang.
- Pertahankan ketidakpastian jika sumber tidak sepakat.
- Tidak perlu mengulang semua sumber di dalam artikel.

KELUARAN JSON VALID SAJA:
{{
  "lolos": true,
  "tanggal_terverifikasi": "{day_month}",
  "tahun_terverifikasi": 1900,
  "fakta_terverifikasi": [
    "Fakta inti yang telah diperiksa"
  ],
  "fact": "<p><b>Tahun 1900 - Judul:</b> ...</p><p>...</p><p>...</p>",
  "alasan": "Ringkasan hasil pemeriksaan"
}}

Jika gagal, gunakan:
{{
  "lolos": false,
  "tanggal_terverifikasi": "",
  "tahun_terverifikasi": null,
  "fakta_terverifikasi": [],
  "fact": "",
  "alasan": "Alasan kegagalan"
}}

Jangan pernah mengembalikan lolos=true jika bukti tidak memadai.
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )

    audit = clean_json(response.text)

    if not audit.get("lolos"):
        print("Verifikasi gagal:", audit.get("alasan"))
        return None

    # Pemeriksaan tambahan oleh program, bukan hanya oleh AI.
    if audit.get("tanggal_terverifikasi") != day_month:
        print("Ditolak: tanggal hasil audit tidak cocok.")
        return None

    if audit.get("tahun_terverifikasi") != candidate.get("tahun"):
        print("Ditolak: tahun hasil audit tidak cocok.")
        return None

    fact = audit.get("fact", "").strip()

    if not fact.startswith("<p><b>Tahun "):
        print("Ditolak: format artikel tidak sesuai.")
        return None

    if fact.count("<p>") != 3 or fact.count("</p>") != 3:
        print("Ditolak: artikel harus terdiri dari 3 paragraf.")
        return None

    return {
        "fact": fact,
        "audit": audit,
        "sources": selected_sources
    }


# =========================================================
# PROSES UTAMA
# =========================================================

def get_daily_football_fact():
    now = datetime.now(TIMEZONE)
    day_month = now.strftime("%d %B")

    print(f"Mencari fakta sepak bola untuk {day_month}...")

    search_results = search_football_archives(now)

    if not search_results:
        raise RuntimeError("Tidak ada hasil pencarian. Artikel dibatalkan.")

    sources = collect_sources(search_results)

    if len(sources) < MIN_INDEPENDENT_DOMAINS:
        raise RuntimeError(
            "Sumber yang berhasil dibaca tidak mencukupi. "
            "Artikel dibatalkan demi menjaga akurasi."
        )

    print(f"Total halaman yang berhasil dibaca: {len(sources)}")

    candidate = select_candidate(now, sources)

    if not candidate.get("layak"):
        raise RuntimeError(
            "Tidak ditemukan peristiwa dengan bukti memadai: "
            + candidate.get("alasan", "")
        )

    allowed_ids = {s["id"] for s in sources}
    candidate_ids = candidate.get("source_ids", [])

    if (
        len(set(candidate_ids)) < MIN_INDEPENDENT_DOMAINS
        or not set(candidate_ids).issubset(allowed_ids)
    ):
        raise RuntimeError(
            "Referensi kandidat tidak valid. Artikel dibatalkan."
        )

    result = verify_and_write(now, candidate, sources)

    if not result:
        raise RuntimeError(
            "Artikel tidak lolos audit fakta. Tidak ada artikel baru."
        )

    data = {
        "date": day_month,
        "fact": result["fact"],
        "verified": True,
        "event_year": result["audit"]["tahun_terverifikasi"],
        "sources": [
            {
                "title": s["title"],
                "url": s["url"],
                "domain": s["domain"]
            }
            for s in result["sources"]
        ],
        "verification_notes": result["audit"]["alasan"]
    }

    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print("Artikel lolos pemeriksaan dan today_fact.json diperbarui.")
    print("Tahun kejadian:", data["event_year"])
    print("Jumlah sumber:", len(data["sources"]))


if __name__ == "__main__":
    try:
        get_daily_football_fact()
    except Exception as exc:
        print(f"PROSES DIBATALKAN: {exc}")
        raise
