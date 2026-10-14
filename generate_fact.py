import os
import json
from datetime import datetime
import requests
from google import genai

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

client = genai.Client(api_key=api_key)

def fetch_wikipedia_events():
    """Mengambil data peristiwa sejarah terstruktur dari arsip resmi Wikipedia hari ini"""
    today = datetime.now()
    month = f"{today.month:02d}"
    day = f"{today.day:02d}"
    
    # Endpoint resmi arsip sejarah Wikipedia (berisi kejadian valid yang sudah dikurasi global)
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/all/{month}/{day}"
    
    try:
        response = requests.get(url)
        data = response.json()
        events = data.get("events", [])
        
        # Filter kata kunci sepak bola agar benar-benar akurat
        keywords = ['football', 'soccer', 'fifa', 'uefa', 'champions league', 'world cup', 'stadium', 'goal', 'match', 'club', 'league']
        football_events = []
        
        for e in events:
            text = e.get("text", "").lower()
            if any(kw in text for kw in keywords) and 'american football' not in text:
                football_events.append({
                    "year": e.get("year"),
                    "text": e.get("text")
                })
        return football_events
    except Exception as e:
        print(f"Gagal mengambil data dari Wikipedia API: {e}")
        return []

def get_daily_football_fact():
    # 1. Ambil data mentah yang dijamin valid dari arsip Wikipedia
    events = fetch_wikipedia_events()
    
    if not events:
        return "<p><b>Hari Ini Dalam Sejarah:</b> Belum ada catatan arsip pertandingan sepak bola besar yang terekam untuk tanggal hari ini.</p>"
    
    # Pilih salah satu peristiwa secara random dari arsip valid
    import random
    selected_event = random.choice(events)
    raw_year = selected_event["year"]
    raw_text = selected_event["text"]
    
    # 2. Suruh Gemini HANYA merapikan dan menceritakan ulang fakta yang SUDAH PASTI ini (Tanpa boleh mengubah tahun/fakta)
    prompt = f"""
    Kamu adalah penulis narasi sepak bola profesional. Di bawah ini adalah data peristiwa sejarah sepak bola yang VALID dan NYATA dari arsip:
    - Tahun: {raw_year}
    - Peristiwa: {raw_text}
    
    Tugasmu:
    1. Ubah data di atas menjadi narasi Bahasa Indonesia dengan gaya storytelling yang memukau, mendalam, dan seru untuk pembaca blog sepak bola.
    2. JANGAN MENGUBAH TAHUN, NAMA, ATAU FAKTA UTAMA DI ATAS. Tetap pertahankan keakuratan sejarahnya.
    3. WAJIB dibagi menjadi 3 atau 4 paragraf pendek yang dipisahkan dengan tag HTML <p>...</p>.
    4. Paragraf pertama HARUS diawali dengan tahun kejadian dan ringkasan peristiwa dalam tanda tebal, contoh: <p><b>Tahun {raw_year} - [Judul Menarik]:</b> [isi cerita...]</p>
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
    )
    
    return response.text.strip()

if __name__ == "__main__":
    print("Mengambil data arsip resmi dan merapikannya...")
    fact_content = get_daily_football_fact()
    
    data = {
        "date": datetime.now().strftime("%d %B"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json dengan data arsip tervalidasi!")
