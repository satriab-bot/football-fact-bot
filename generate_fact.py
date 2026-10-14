import os
import json
from datetime import datetime
from google import genai
from duckduckgo_search import DDGS

# Ambil API Key dari GitHub Secrets
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

client = genai.Client(api_key=api_key)

def search_duckduckgo(query):
    """Mencari informasi di internet menggunakan DuckDuckGo secara gratis"""
    print(f"Mencari data di DuckDuckGo: {query}")
    results = []
    try:
        with DDGS() as ddgs:
            # Mengambil 4 hasil teratas dari DuckDuckGo text search
            for r in ddgs.text(query, max_results=4):
                results.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}\n")
    except Exception as e:
        print(f"Gagal melakukan pencarian DuckDuckGo: {e}")
    
    return "\n".join(results)

def get_daily_football_fact():
    today = datetime.now()
    date_str = today.strftime("%d %B") # Contoh: 14 October / 14 Oktober
    
    # 1. Cari referensi sejarah sepak bola tanggal hari ini lewat DuckDuckGo
    search_query = f"football history match events on {date_str}"
    search_context = search_duckduckgo(search_query)
    
    # 2. Berikan hasil pencarian tersebut ke Gemini agar dirangkum dan diverifikasi
    prompt = f"""
    Berdasarkan data hasil pencarian internet berikut ini, pilih atau verifikasi 1 peristiwa sejarah nyata dalam DUNIA SEPAK BOLA (Association Football/Soccer) yang terjadi tepat pada tanggal {date_str} di masa lalu.
    
    DATA PENCARIAN DARI INTERNET:
    {search_context}
    
    Instruksi Ketat:
    1. Pastikan tanggal, tahun kejadian, klub/negara, dan fakta di dalam data pencarian tersebut AKURAT dan nyata (JANGAN HALUSINASI). Jika data dari internet kurang lengkap, gunakan pengetahuan sejarah sepak bola yang valid.
    2. Tulis dalam Bahasa Indonesia dengan gaya narasi storytelling yang memukau, mendalam, dan seru.
    3. WAJIB dibagi menjadi 3 atau 4 paragraf pendek yang dipisahkan dengan tag HTML <p>...</p>.
    4. TAHUN KEJADIAN WAJIB DITULIS DI PARAGRAF PERTAMA! Kalimat pertama pada paragraf pertama HARUS diawali dengan tahun kejadian dan judul peristiwa dalam tanda tebal, contoh: <p><b>Tahun 1995 - Judul Peristiwa Singkat:</b> [isi cerita...]</p>
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
    )
    
    return response.text.strip()

if __name__ == "__main__":
    print("Memulai proses pencarian & pembuatan fakta bola...")
    fact_content = get_daily_football_fact()
    
    data = {
        "date": datetime.now().strftime("%d %B"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json menggunakan DuckDuckGo Search!")
