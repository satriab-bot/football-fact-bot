import os
import json
from datetime import datetime
from google import genai
from duckduckgo_search import DDGS

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

client = genai.Client(api_key=api_key)

def search_football_archives(date_str):
    """Mencari data secara spesifik ke Wikipedia dan portal berita bola dunia via DuckDuckGo"""
    # Targetkan pencarian ke situs arsip dan berita bola terkemuka
    queries = [
        f"on this day football {date_str} site:en.wikipedia.org",
        f"football history match {date_str} site:espn.com OR site:goal.com OR site:skysports.com"
    ]
    
    results_text = ""
    try:
        with DDGS() as ddgs:
            for q in queries:
                print(f"Mencari target: {q}")
                for r in ddgs.text(q, max_results=3):
                    results_text += f"Sumber: {r.get('title')}\nInfo: {r.get('body')}\n\n"
    except Exception as e:
        print(f"Pencarian DuckDuckGo mengalami kendala: {e}")
        
    return results_text

def get_daily_football_fact():
    today = datetime.now()
    date_str = today.strftime("%d %B") # Contoh: 14 October / 14 Oktober
    
    # 1. Cari data dari sumber terarah (Wikipedia & Portal Bola)
    archive_data = search_football_archives(date_str)
    
    # Fallback jika data kosong (memperluas kueri umum khusus bola)
    if not archive_data.strip():
        with DDGS() as ddgs:
            for r in ddgs.text(f"historic football match event on {date_str}", max_results=4):
                archive_data += f"Info: {r.get('body')}\n"

    # 2. Berikan data spesifik tersebut ke Gemini untuk diverifikasi dan dijadikan narasi
    prompt = f"""
    Berdasarkan data arsip dan portal berita sepak bola dunia berikut ini untuk tanggal {date_str}:
    
    DATA ARSIP INTERNET:
    {archive_data}
    
    Instruksi Ketat:
    1. Pilih 1 peristiwa sejarah sepak bola yang PALING MENARIK dan VALID dari data di atas. 
    2. Pastikan tahun, klub/negara, dan fakta peristiwanya akurat berdasarkan data arsip tersebut (JANGAN MENGARANG FAKTA).
    3. Tulis dalam Bahasa Indonesia dengan gaya narasi storytelling yang memukau dan seru untuk pembaca blog.
    4. WAJIB dibagi menjadi 3 atau 4 paragraf pendek yang dipisahkan dengan tag HTML <p>...</p>.
    5. TAHUN KEJADIAN WAJIB DITULIS DI PARAGRAF PERTAMA! Kalimat pertama pada paragraf pertama HARUS diawali dengan tahun kejadian dan judul peristiwa dalam tanda tebal, contoh: <p><b>Tahun 1999 - Judul Peristiwa:</b> [isi cerita...]</p>
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
    )
    
    return response.text.strip()

if __name__ == "__main__":
    print("Mencari arsip bola terarah...")
    fact_content = get_daily_football_fact()
    
    data = {
        "date": datetime.now().strftime("%d %B"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json dengan arsip terarah!")
