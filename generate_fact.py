import os
import json
from datetime import datetime
from google import genai

# Ambil API Key dari GitHub Secrets
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

# Inisialisasi client menggunakan SDK resmi terbaru
client = genai.Client(api_key=api_key)

def get_daily_football_fact():
    today = datetime.now()
    date_str = today.strftime("%d %B")
    
    prompt = f"""
    Cari dan berikan 1 fakta sejarah/momen ikonik yang sangat menarik dalam DUNIA SEPAK BOLA yang terjadi tepat pada tanggal {date_str} di masa lalu.
    
    Ketentuan:
    1. Hindari yang hanya sekadar tanggal lahir/wafat. Pilih pertandingan epik, gol legendaris, atau drama historis yang memukau.
    2. Tulis dalam Bahasa Indonesia dengan gaya narasi yang bercerita (storytelling), mendalam, panjang (sekitar 3-4 paragraf), agar pembaca blog sepak bola merasa excited dan tercerahkan.
    3. Paragraf pertama sebutkan tahun kejadian dan konteks dramatisnya, paragraf berikutnya jelaskan detail unik/dampaknya.
    4. Awali teks persis dengan format: "<b>[Tahun Kejadian] - [Judul Singkat Menarik]</b><br><br>" diikuti isi narasinya.
    """
    
    # Menggunakan model standar yang didukung penuh oleh SDK google-genai
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite', 
        contents=prompt,
    )
    
    return response.text.strip()

if __name__ == "__main__":
    print("Mengeksekusi permintaan fakta sepak bola ke Gemini API...")
    fact_content = get_daily_football_fact()
    
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json!")
