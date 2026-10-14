import os
import json
from datetime import datetime, timezone, timedelta
from google import genai
from duckduckgo_search import DDGS

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

client = genai.Client(api_key=api_key)

def search_football_archives(date_str):
    """Mencari data pertandingan, hasil liga, atau catatan sejarah bola di seluruh dunia pada tanggal tersebut"""
    queries = [
        f"football match history on {date_str}",
        f"historic football match event on {date_str}",
        f"sejarah pertandingan sepak bola tanggal {date_str}"
    ]
    
    results_text = ""
    try:
        with DDGS() as ddgs:
            for q in queries:
                print(f"Mencari: {q}")
                for r in ddgs.text(q, max_results=4):
                    results_text += f"Info: {r.get('body')}\n"
    except Exception as e:
        print(f"Pencarian DuckDuckGo kendala: {e}")
        
    return results_text

def get_daily_football_fact():
    # Mengambil tanggal hari ini dalam format universal
    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%d %B") 
    
    archive_data = search_football_archives(date_str)
    
    # Prompt global tanpa batasan liga regional tertentu
    prompt = f"""
    Kamu adalah penulis narasi dan sejarawan sepak bola profesional. Tugasmu adalah memilih SATU momen atau peristiwa sepak bola yang menarik dari data internet di bawah ini untuk tanggal {date_str}.
    
    Catatan penting: Peristiwa tidak harus berupa tragedi atau rekor dunia besar. Pertandingan liga domestik masa lalu, hasil skor laga klasik, catatan performa klub, atau momen arsip sepak bola dari belahan dunia mana pun yang valid sudah lebih dari cukup untuk dijadikan cerita yang menarik.
    
    DATA PENDUKUNG DARI INTERNET:
    {archive_data}
    
    Instruksi Mutlak:
    1. Pilih satu peristiwa atau pertandingan dari seluruh dunia yang paling potensial untuk diangkat menjadi cerita berdasarkan data di atas.
    2. JANGAN MENGARANG FAKTA. Pertahankan keakuratan tahun, nama klub, atau skor berdasarkan data arsip.
    3. Tulis dalam Bahasa Indonesia dengan gaya narasi storytelling yang memukau, mendalam, dan seru untuk pembaca blog.
    4. WAJIB dibagi menjadi 3 atau 4 paragraf pendek yang dipisahkan dengan tag HTML <p>...</p>.
    5. TAHUN KEJADIAN WAJIB DITULIS DI PARAGRAF PERTAMA! Kalimat pertama pada paragraf pertama HARUS diawali dengan tahun kejadian dan judul peristiwa dalam tanda tebal, contoh: <p><b>Tahun 1999 - Ketatnya Persaingan Liga:</b> [isi cerita...]</p>
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
    )
    
    return response.text.strip()

if __name__ == "__main__":
    print("Memproses fakta bola berdasarkan arsip pertandingan global...")
    fact_content = get_daily_football_fact()
    
    # Tanggal untuk badge JSON
    now_utc = datetime.now(timezone.utc)
    date_badge_str = now_utc.strftime("%d %B")
    
    data = {
        "date": date_badge_str,
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json!")
