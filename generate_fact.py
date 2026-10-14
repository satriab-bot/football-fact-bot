import os
import json
from datetime import datetime
import google.generativeai as genai  # Gunakan library yang saat ini sudah sukses berjalan di repo Anda

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

genai.configure(api_key=api_key)

def get_daily_football_fact():
    today = datetime.now()
    date_str = today.strftime("%d %B")
    
    # Prompt disempurnakan agar narasinya lebih panjang, mendalam, dan memukau
    prompt = f"""
    Cari dan berikan 1 fakta sejarah/momen ikonik yang sangat menarik dalam DUNIA SEPAK BOLA yang terjadi tepat pada tanggal {date_str} di masa lalu.
    
    Ketentuan:
    1. Hindari yang hanya sekadar tanggal lahir/wafat. Pilih pertandingan epik, gol legendaris, atau drama historis yang memukau.
    2. Tulis dalam Bahasa Indonesia dengan gaya narasi yang bercerita (storytelling), mendalam, panjang (sekitar 3-4 paragraf pendek), agar pembaca blog sepak bola merasa excited dan tercerahkan.
    3. Paragraf pertama sebutkan tahun kejadian dan konteks dramatisnya, paragraf berikutnya jelaskan detail unik/dampaknya.
    4. Awali teks persis dengan format: "<b>[Tahun Kejadian] - [Judul Singkat Menarik]</b><br><br>" diikuti isi narasinya.
    """
    
    # Gunakan model yang sudah terbukti berhasil di environment Anda
    model = genai.GenerativeModel('gemini-3.5-flash-lite') 
    response = model.generate_content(prompt)
    return response.text.strip()

if __name__ == "__main__":
    print("Membuat fakta sepak bola mendalam...")
    fact_content = get_daily_football_fact()
    
    data = {
        "date": datetime.now().strftime("%d %B %Y"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json!")
