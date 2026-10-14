"""
Generator fakta sepak bola harian (versi anti-halusinasi).

Alur:
1. Ambil kandidat peristiwa dari Wikipedia "On This Day" (terstruktur: tahun + teks + ringkasan halaman).
   Jika kosong, fallback ke DuckDuckGo dengan filter ketat (harus memuat tanggal DAN tahun).
2. Gemini hanya boleh memilih & menulis ulang dari kandidat. Tahun diambil dari DATA, bukan dari AI.
3. Cek deterministik: setiap angka di artikel harus ada di teks sumber.
4. Cek LLM kedua (verifier, temperature 0): mencari klaim yang tidak didukung sumber.
5. Jika gagal setelah beberapa percobaan, today_fact.json LAMA dibiarkan (tidak ditimpa)
   dan skrip keluar dengan error, jadi lebih baik tidak update daripada update yang keliru.

Dependensi: pip install google-genai requests duckduckgo_search
"""
import os
import re
import sys
import json
import html
from datetime import datetime, timezone

import requests
from google import genai
from google.genai import types

try:
    from duckduckgo_search import DDGS
except ImportError:  # DDG hanya fallback, jadi tidak wajib
    DDGS = None

# ---------------------------------------------------------------- konfigurasi
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
VERIFIER_MODEL = os.environ.get("GEMINI_VERIFIER_MODEL", MODEL)
MAX_ATTEMPTS = 3
MAX_CANDIDATES = 12
OUTPUT_FILE = "today_fact.json"
HEADERS = {"User-Agent": "daily-football-fact/2.0 (ganti-dengan-email-atau-url-blogmu)"}

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
             "Agustus", "September", "Oktober", "November", "Desember"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]

FOOTBALL_HINTS = ["football", "footballer", "soccer", "fifa", "uefa", "world cup",
                  "premier league", "football league", "f.c.", " fc ", "a.f.c.",
                  "copa", "serie a", "la liga", "bundesliga", "ligue 1", "libertadores"]
NOT_SOCCER = ["american football", "canadian football", "australian rules", "gridiron",
              "nfl", "rugby", "gaelic football", "college football", "ncaa"]

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")
client = genai.Client(api_key=api_key)


# ---------------------------------------------------------------- pengumpulan sumber
def looks_like_soccer(text: str) -> bool:
    t = " " + text.lower() + " "
    if any(bad in t for bad in NOT_SOCCER):
        return False
    return any(h in t for h in FOOTBALL_HINTS)


def get_wikipedia_candidates(month: int, day: int) -> list[dict]:
    """Sumber utama: Wikipedia On This Day (tahun & teks sudah terstruktur)."""
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
            })

    # hilangkan duplikat (selected & events sering sama)
    seen, unique = set(), []
    for c in candidates:
        key = (c["year"], c["text"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def get_ddg_candidates(month: int, day: int) -> list[dict]:
    """Fallback: hanya terima snippet yang memuat tanggal persis DAN tahun 4 digit."""
    if DDGS is None:
        return []
    month_en = MONTHS_EN[month - 1]
    date_patterns = [rf"\b{day}\s+{month_en}\b", rf"\b{month_en}\s+{day}\b"]
    queries = [
        f'"{day} {month_en}" football match history',
        f'"{month_en} {day}" football on this day',
    ]
    out, seen = [], set()
    try:
        with DDGS() as ddgs:
            for q in queries:
                print(f"Mencari (fallback): {q}")
                for r in ddgs.text(q, max_results=8):
                    body = (r.get("body") or "").strip()
                    if not body or body in seen:
                        continue
                    if not any(re.search(p, body, re.I) for p in date_patterns):
                        continue
                    years = re.findall(r"\b(1[89]\d\d|20[0-2]\d)\b", body)
                    if not years or not looks_like_soccer(body):
                        continue
                    seen.add(body)
                    out.append({
                        "year": int(years[0]),
                        "kind": "peristiwa",
                        "text": body,
                        "context": "",
                        "url": r.get("href", ""),
                        "source": r.get("title", "Web"),
                    })
    except Exception as e:
        print(f"Pencarian DuckDuckGo kendala: {e}")
    return out


def source_text_of(c: dict) -> str:
    return f"{c['text']} {c['context']}"


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
    listing = "\n\n".join(
        f"[{i}] ({c['kind']}, tahun {c['year']}) {c['text']}\n    Konteks: {c['context'] or '-'}"
        for i, c in enumerate(candidates)
    )
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
1. Pilih SATU kandidat yang benar-benar tentang sepak bola (association football/soccer).
2. Gunakan HANYA fakta yang tertulis eksplisit di kandidat terpilih (teks + konteks).
   Tahun, nama pemain/klub/stadion, skor, angka, dan lokasi harus tertulis di sana.
3. DILARANG menambah detail yang tidak ada di sumber: suasana stadion, jumlah penonton,
   cuaca, kutipan, perasaan pemain, status "rival", julukan, atau latar sejarah dari ingatanmu.
4. Jika informasi di sumber sedikit, tulis lebih singkat dan sederhana.
   Lebih baik pendek tapi benar daripada panjang tapi mengarang.
5. Jangan menyebut tanggal/tahun/angka apa pun yang tidak ada di sumber.
6. Bahasa Indonesia, gaya bercerita ringan tapi tetap faktual. 3 atau 4 paragraf pendek.
7. Jangan tulis tahun di judul; tahun akan ditambahkan sistem.

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


# ---------------------------------------------------------------- main
def build_html(candidate: dict, draft: dict) -> str:
    title = html.escape(draft["title"].strip().rstrip(":"))
    paragraphs = [html.escape(p.strip()) for p in draft["paragraphs"] if p.strip()]
    parts = [f"<p><b>Tahun {candidate['year']} - {title}:</b> {paragraphs[0]}</p>"]
    parts += [f"<p>{p}</p>" for p in paragraphs[1:]]
    if candidate.get("url"):
        parts.append(f'<p><small>Sumber: <a href="{html.escape(candidate["url"])}" '
                     f'target="_blank" rel="noopener">{html.escape(candidate["source"])}</a></small></p>')
    return "\n".join(parts)


def main() -> int:
    now = datetime.now(timezone.utc)  # UTC: acuan universal untuk pembaca di seluruh dunia
    date_label = f"{now.day} {MONTHS_ID[now.month - 1]}"
    print(f"Memproses fakta bola untuk {date_label}...")

    candidates = get_wikipedia_candidates(now.month, now.day)
    if not candidates:
        candidates = get_ddg_candidates(now.month, now.day)
    if not candidates:
        print("Tidak ada kandidat sumber yang valid. File lama dipertahankan.")
        return 1
    candidates = candidates[:MAX_CANDIDATES]
    print(f"{len(candidates)} kandidat ditemukan.")

    feedback: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
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
        print(f"Berhasil memperbarui {OUTPUT_FILE} (sumber: {chosen['url']})")
        return 0

    print("Semua percobaan gagal verifikasi. File lama dipertahankan.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
