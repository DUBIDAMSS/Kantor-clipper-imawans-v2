import os, json, subprocess, time, requests
from datetime import datetime, timezone, timedelta
from google import genai

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def update_visual(tahap, pesan):
    wita = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M WITA")
    with open("status_kantor.json", "w") as f:
        json.dump({"tahap": tahap, "pesan": pesan, "waktu": wita}, f)
    subprocess.run(["git", "config", "user.name", "Kantor AI Bot"])
    subprocess.run(["git", "config", "user.email", "bot@kantorai.local"])
    subprocess.run(["git", "add", "status_kantor.json"])
    subprocess.run(["git", "commit", "-m", f"Update visual: {tahap}"])
    subprocess.run(["git", "push"])

def kirim_telegram(video_path, caption):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo"
    with open(video_path, "rb") as vf:
        requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": caption}, files={"video": vf})

update_visual("RISET", "Agen Riset sedang membaca syarat campaign Konten.com...")
with open("syarat_harian.txt", "r") as f:
    syarat = f.read()

link_yt = [line.split(": ", 1)[1].strip() for line in syarat.splitlines() if line.startswith("LINK_YOUTUBE:")][0]
if "ISI_LINK" in link_yt or not link_yt.startswith("http"):
    update_visual("IDLE", "Menunggu link YouTube baru di syarat_harian.txt")
    exit(0)

update_visual("PRODUKSI", "Agen Clipper sedang mengunduh & memotong video materi...")
os.makedirs("output_klip", exist_ok=True)
subprocess.run(["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4", "-o", "source_video.mp4", link_yt], check=True)

update_visual("QC", "Agen QC memeriksa durasi, larangan, & kecocokan syarat Konten.com...")
video_file = client.files.upload(file="source_video.mp4")
while video_file.state.name == "PROCESSING":
    time.sleep(5)
    video_file = client.files.get(name=video_file.name)

prompt = f"""
Kamu adalah Tim Riset, Produksi, dan Quality Control (QC) Kantor AI Clipper untuk platform Konten.com.
Syarat campaign harian:
{syarat}

Pilih maksimal 3 segmen terbaik yang LOLOS QC (durasi wajib di antara DURASI_MINIMAL dan DURASI_MAKSIMAL, sesuai TOPIK_DICARI, dan bebas LARANGAN).
Tulis caption Instagram Reels menarik yang wajib menyertakan MENTION_WAJIB dan HASHTAG_WAJIB.

Berikan jawaban HANYA dalam format JSON array valid tanpa teks tambahan:
[
  {{
    "judul": "nama_klip_tanpa_spasi",
    "mulai": "00:01:15",
    "selesai": "00:01:50",
    "caption_ig": "Teks hook menarik + mention wajib + hashtag wajib"
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
    kirim_telegram(out_mp4, klip["caption_ig"])

update_visual("IDLE", "Selesai! Semua klip sudah dikirim ke Telegram. Agen kembali istirahat.")
