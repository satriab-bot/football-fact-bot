import os
import json
from datetime import datetime
import time
from google import genai

# Ambil API Key dari GitHub Secrets
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

client = genai.Client(api_key=api_key)

def get_daily_football_fact():
    today = datetime.now()
    date_str = today.strftime("%d %B") # Contoh: 14 October
    
    prompt = f"""
    Cari di internet menggunakan Google Search tentang peristiwa atau fakta sejarah nyata dalam DUNIA SEPAK BOLA (Association Football/Soccer, BUKAN American Football/NFL) yang VALID terjadi tepat pada tanggal {date_str} di masa lalu.
    
    Instruksi Ketat:
    1. WAJIB lakukan verifikasi pencarian Google terlebih dahulu. Pastikan tanggal, tahun kejadian, nama klub/negara, nama pemain, dan skor pertandingan AKURAT dan nyata secara sejarah (JANGAN HALUSINASI / JANGAN MEMBUAT CERITA FIKSI).
    2. Hindari fakta yang hanya sekadar tanggal lahir/wafat pemain biasa, kecuali jika ada momen pertandingan monumental yang menyertainya.
    3. Tulis dalam Bahasa Indonesia dengan gaya narasi storytelling yang memukau, mendalam, dan seru.
    4. WAJIB dibagi menjadi 3 atau 4 paragraf pendek yang dipisahkan dengan tag HTML <p>...</p>.
    5. TAHUN KEJADIAN WAJIB DITULIS DI PARAGRAF PERTAMA! Kalimat pertama pada paragraf pertama HARUS diawali dengan tahun kejadian dan judul peristiwa dalam tanda tebal, contoh: <p><b>Tahun 1995 - Judul Peristiwa Singkat:</b> [isi cerita...]</p>
    """
    
    # Mengaktifkan Google Search Grounding via Tools untuk akurasi data
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash-lite', # Menggunakan model flash yang mendukung search tools
                contents=prompt,
                config={
                    'tools': [{'google_search': {}}] # LIVE GOOGLE SEARCH GROUNDING
                }
            )
            return response.text.strip()
        except Exception as e:
            print(f"Percobaan ke-{attempt+1} gagal, mencoba ulang... Error: {e}")
            time.sleep(5)
            
    raise Exception("Gagal mendapatkan fakta tervalidasi setelah 3 kali percobaan.")

if __name__ == "__main__":
    print("Sedang melakukan verifikasi fakta sepak bola via Google Search...")
    fact_content = get_daily_football_fact()
    
    data = {
        "date": datetime.now().strftime("%d %B"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json dengan data tervalidasi dan tahun yang jelas!")
