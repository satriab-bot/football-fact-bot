import os
import json
import random
from datetime import datetime
import google.generativeai as genai

# Konfigurasi API Key dari Environment Variable GitHub
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY belum diset di GitHub Secrets!")

genai.configure(api_key=api_key)

def get_daily_football_fact():
    # Ambil tanggal dan bulan hari ini secara dinamis
    today = datetime.now()
    date_str = today.strftime("%d %B") # Contoh: 14 October / 14 Oktober
    
    # Prompt instruksi ketat untuk AI
    prompt = f"""
    Cari dan berikan 1 fakta unik, sejarah penting, rekor, atau momen ikonik dalam DUNIA SEPAK BOLA (Association Football/Soccer, BUKAN American Football/NFL) yang terjadi tepat pada tanggal {date_str} di masa lalu (bisa 1 tahun lalu, 10 tahun lalu, hingga 100 tahun lalu).
    
    Ketentuan Penting:
    1. Jangan memilih fakta yang HANYA menyebutkan tanggal lahir atau kematian pemain, KECUALI jika ada momen atau rekor pertandingan yang sangat monumental di hari tersebut.
    2. Utamakan peristiwa pertandingan epik, gol legendaris, momen transfer kontroversial, insiden unik di lapangan, atau rekor turnamen (Piala Dunia, Liga Champions, Liga Top Eropa, dll).
    3. Tulis dalam bahasa Indonesia yang santai, menarik, dan mengalir seperti gaya pembawa acara bola (panjang sekitar 2-3 kalimat).
    4. Awali teks dengan format tahun kejadian dalam tanda tebal, contoh: "<b>Tahun 2006:</b> [isi fakta...]"
    """
    
    # Menggunakan model Gemini yang cepat dan stabil
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt)
    
    return response.text.strip()

if __name__ == "__main__":
    print("Sedang meminta fakta sepak bola dari AI...")
    fact_content = get_daily_football_fact()
    
    # Simpan ke dalam file JSON
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "fact": fact_content
    }
    
    with open("today_fact.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    print("Berhasil memperbarui today_fact.json!")
