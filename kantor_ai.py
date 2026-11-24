import os, json, subprocess, time, requests, re
from datetime import datetime, timezone, timedelta
from google import genai

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

def update_visual(tahap, pesan):
    wita = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M WITA")
    with open("status_kantor.json", "w") as f:
        json.dump({"tahap": tahap, "pesan": pesan, "waktu": wita}, f)
    subprocess.run(["git", "config", "user.name", "Kantor AI Bot"])
    subprocess.run(["git", "config", "user.email", "bot@kantorai.local"])
    subprocess.run(["git", "add", "status_kantor.json", "syarat_harian.txt"])
    subprocess.run(["git", "commit", "-m", f"Update via Telegram: {tahap}"])
    subprocess.run(["git", "push"])

def kirim_teks(teks):
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data={"chat_id": TG_CHAT_ID, "text": teks})

def kirim_video(video_path, caption):
    with open(video_path, "rb") as vf:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", data={"chat_id": TG_CHAT_ID, "caption": caption}, files={"video": vf})

def cek_perintah_telegram():
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    res = requests.get(url).json()
    print("DEBUG TELEGRAM RESPONSE:", json.dumps(res))
    
    if not res.get("ok"):
        print("ERROR TOKEN TELEGRAM:", res)
        return None
    
    if not res.get("result"):
        kirim_teks("ℹ️ Server mengecek Telegram, tapi antrean pesan baru kosong. Kirim link YouTube sekarang lalu Run Workflow lagi.")
        return None
    
    pesan_terbaru = None
    update_id_terakhir = 0
    for item in res["result"]:
        update_id_terakhir = item["update_id"]
        msg = item.get("message", {})
        pengirim_id = str(msg.get("chat", {}).get("id", ""))
        teks_msg = msg.get("text", "")
        print(f"Pesan dari ID {pengirim_id}: {teks_msg}")
        
        if pengirim_id == TG_CHAT_ID and "http" in teks_msg:
            pesan_terbaru = teks_msg
        elif pengirim_id != TG_CHAT_ID and pengirim_id != "":
            print(f"PERINGATAN: ID pengirim ({pengirim_id}) beda dengan Secret TELEGRAM_CHAT_ID ({TG_CHAT_ID})")
    
    if update_id_terakhir > 0:
        requests.get(f"{url}?offset={update_id_terakhir + 1}")
    return pesan_terbaru

update_visual("RISET", "Agen Riset memeriksa perintah terbaru di chat Telegram...")
perintah_tg = cek_perintah_telegram()

if perintah_tg:
    with open("syarat_harian.txt", "w") as f:
        f.write(perintah_tg)
    syarat = perintah_tg
    kirim_teks("✅ Perintah baru dari Telegram diterima! Tim Kantor AI mulai memproses video...")
else:
    with open("syarat_harian.txt", "r") as f:
        syarat = f.read()

if "http" not in syarat or "ISI_LINK" in syarat:
    update_visual("IDLE", "Menunggu perintah & link YouTube dikirim lewat chat Telegram...")
    exit(0)

urls = re.findall(r'(https?://[^\s]+)', syarat)
if not urls:
    update_visual("IDLE", "Link YouTube tidak ditemukan di pesan Telegram.")
    exit(0)
link_yt = urls[0]

update_visual("PRODUKSI", "Agen Clipper sedang mengunduh video materi...")
os.makedirs("output_klip", exist_ok=True)
subprocess.run(["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4", "-o", "source_video.mp4", link_yt], check=True)

update_visual("QC", "Agen QC memeriksa durasi, larangan, & syarat Konten.com...")
video_file = client.files.upload(file="source_video.mp4")
while video_file.state.name == "PROCESSING":
    time.sleep(5)
    video_file = client.files.get(name=video_file.name)

prompt = f"""
Kamu adalah Tim Riset, Produksi, dan Quality Control (QC) Kantor AI Clipper untuk platform Konten.com.
Berikut instruksi & syarat campaign harian yang dikirim bos lewat Telegram:
{syarat}

Tugasmu:
1. Analisis video ini dan pilih maksimal 3 segmen terbaik yang LOLOS QC (mematuhi durasi, topik, dan larangan yang diminta). Jika durasi tidak disebut, gunakan standar 20-55 detik.
2. Tulis caption menarik yang menyertakan mention dan hashtag wajib yang diminta (jika ada).

Berikan jawaban HANYA dalam format JSON array valid tanpa teks tambahan:
[
  {{
    "judul": "nama_klip_tanpa_spasi",
    "mulai": "00:01:15",
    "selesai": "00:01:50",
    "caption_ig": "Teks hook menarik + mention/hashtag relevan"
  }}
]
"""

response = client.models.generate_content(model="gemini-2.5-flash", contents=[video_file, prompt])
raw_json = response.text.strip().removeprefix("```json").removesuffix("```").strip()
klip_list = json.loads(raw_json)

for idx, klip in enumerate(klip_list, 1):
    raw_cut = f"temp_{idx}.mp4"
    out_mp4 = f"output_klip/{idx}_{klip['judul']}.mp4"
    
    subprocess.run([
        "ffmpeg", "-y", "-i", "source_video.mp4",
        "-ss", klip["mulai"], "-to", klip["selesai"],
        "-vf", "crop=ih*(9/16):ih,scale=1080:1920",
        "-c:v", "libx264", "-c:a", "aac", raw_cut
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    subprocess.run(["whisper", raw_cut, "--model", "base", "--language", "Indonesian", "--output_format", "srt"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    srt_file = f"temp_{idx}.srt"
    if os.path.exists(srt_file):
        subprocess.run([
            "ffmpeg", "-y", "-i", raw_cut,
            "-vf", f"subtitles={srt_file}:force_style='FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=50'",
            "-c:a", "copy", out_mp4
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(srt_file)
    else:
        os.rename(raw_cut, out_mp4)

    if os.path.exists(raw_cut):
        os.remove(raw_cut)

    update_visual("POSTING", f"Agen Kurir mengirim klip {idx} lolos QC ke Telegram...")
    kirim_video(out_mp4, klip["caption_ig"])

update_visual("IDLE", "Selesai! Semua klip sudah dikirim ke Telegram. Agen kembali istirahat.")
