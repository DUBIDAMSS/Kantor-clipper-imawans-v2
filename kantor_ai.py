import os, json, subprocess, time, requests, re, cv2
import numpy as np
from datetime import datetime, timezone, timedelta
from google import genai
import mediapipe as mp

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
    if TG_TOKEN and TG_CHAT_ID:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data={"chat_id": TG_CHAT_ID, "text": teks})

def kirim_video(video_path, caption):
    with open(video_path, "rb") as vf:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", data={"chat_id": TG_CHAT_ID, "caption": caption}, files={"video": vf})

def cek_perintah_telegram():
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    res = requests.get(url).json()
    if not res.get("ok") or not res.get("result"):
        return None
    pesan_terbaru = None
    update_id_terakhir = 0
    for item in res["result"]:
        update_id_terakhir = item["update_id"]
        msg = item.get("message", {})
        pengirim_id = str(msg.get("chat", {}).get("id", ""))
        teks_msg = msg.get("text", "")
        if pengirim_id == TG_CHAT_ID and "http" in teks_msg:
            pesan_terbaru = teks_msg
    if update_id_terakhir > 0:
        requests.get(f"{url}?offset={update_id_terakhir + 1}")
    return pesan_terbaru

def render_smart_speaker_zoom(input_mp4, output_mp4):
    """
    Mendeteksi semua wajah (2, 3, atau lebih orang), mengukur gerakan bukaan bibir
    untuk menentukan siapa yang sedang berbicara, lalu menggeser & nge-zoom kamera
    9:16 secara mulus mengikuti pembicara aktif.
    """
    cap = cv2.VideoCapture(input_mp4)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    temp_silent = output_mp4.replace(".mp4", "_silent.mp4")
    out_w, out_h = 1080, 1920
    writer = cv2.VideoWriter(temp_silent, cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_w, out_h))

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=6,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )

    # Ukuran crop dasar 9:16 dan faktor zoom saat seseorang berbicara
    base_crop_w = int(orig_h * (9 / 16))
    zoom_factor = 0.85  # Zoom in 15% lebih dekat ke pembicara aktif
    target_cx = orig_w / 2.0
    target_crop_w = float(base_crop_w)
    smooth_cx = target_cx
    smooth_crop_w = target_crop_w

    prev_mouth = {}
    speaker_hold_frames = 0
    current_speaker_x = target_cx

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Analisis setiap 2 frame agar cepat namun responsif
        if frame_idx % 2 == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)

            if results.multi_face_landmarks:
                kandidat_pembicara = []
                for idx_f, lm_list in enumerate(results.multi_face_landmarks):
                    lm = lm_list.landmark
                    # Titik tengah hidung/wajah (landmark 1)
                    cx = lm[1].x * orig_w
                    # Jarak bibir atas (13) dan bibir bawah (14) dinormalisasi tinggi wajah
                    face_h = abs(lm[152].y - lm[10].y) + 1e-6
                    mouth_open = abs(lm[14].y - lm[13].y) / face_h

                    # Cari kecocokan dengan posisi wajah frame sebelumnya untuk menghitung dinamika gerak bibir
                    key_pos = round(cx / (orig_w * 0.12))
                    delta_mouth = abs(mouth_open - prev_mouth.get(key_pos, mouth_open))
                    prev_mouth[key_pos] = mouth_open

                    # Skor bicara = kombinasi bukaan mulut + perubahan gerak bibir
                    skor_bicara = (mouth_open * 0.6) + (delta_mouth * 1.4)
                    kandidat_pembicara.append((skor_bicara, cx))

                kandidat_pembicara.sort(key=lambda x: x[0], reverse=True)
                best_score, best_x = kandidat_pembicara[0]

                # Tahan kamera minimal 15 frame (~0.5 detik) agar tidak loncat-loncat liar
                if best_score > 0.035 or speaker_hold_frames <= 0:
                    if abs(best_x - current_speaker_x) > orig_w * 0.08:
                        current_speaker_x = best_x
                        speaker_hold_frames = int(fps * 0.8)
                    else:
                        current_speaker_x = (current_speaker_x * 0.7) + (best_x * 0.3)

                target_cx = current_speaker_x
                target_crop_w = base_crop_w * zoom_factor
            else:
                # Jika tidak ada wajah terdeteksi, kembalikan ke tengah layar penuh
                target_cx = orig_w / 2.0
                target_crop_w = float(base_crop_w)

        if speaker_hold_frames > 0:
            speaker_hold_frames -= 1

        # Transisi kamera & zoom halus (cinematic pan & zoom)
        smooth_cx += (target_cx - smooth_cx) * 0.12
        smooth_crop_w += (target_crop_w - smooth_crop_w) * 0.08

        cw = int(min(orig_w, max(360, smooth_crop_w)))
        ch = int(min(orig_h, cw * (16 / 9)))
        cw = int(ch * (9 / 16))

        x1 = int(smooth_cx - cw / 2)
        x1 = max(0, min(orig_w - cw, x1))
        y1 = int((orig_h - ch) / 2)

        cropped = frame[y1:y1 + ch, x1:x1 + cw]
        resized = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        writer.write(resized)
        frame_idx += 1

    cap.release()
    writer.release()
    face_mesh.close()

    # Gabungkan kembali dengan audio asli dari input_mp4
    subprocess.run([
        "ffmpeg", "-y", "-i", temp_silent, "-i", input_mp4,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest",
        output_mp4
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if os.path.exists(temp_silent):
        os.remove(temp_silent)

tahap_sekarang = "RISET"

try:
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
        update_visual("IDLE", "Menunggu perintah & link video dikirim lewat chat Telegram...")
        exit(0)

    urls = re.findall(r'(https?://[^\s]+)', syarat)
    if not urls:
        update_visual("IDLE", "Link video tidak ditemukan di pesan Telegram.")
        exit(0)
    link_video = urls[0]

    tahap_sekarang = "PRODUKSI"
    update_visual("PRODUKSI", "Agen Clipper sedang mengunduh video materi...")
    os.makedirs("output_klip", exist_ok=True)
    if os.path.exists("source_video.mp4"):
        os.remove("source_video.mp4")

    if "drive.google.com" in link_video:
        hasil_unduh = subprocess.run(
            ["gdown", link_video, "-O", "source_video.mp4"],
            capture_output=True, text=True
        )
        if hasil_unduh.returncode != 0 or not os.path.exists("source_video.mp4"):
            raise RuntimeError(f"Gagal unduh Google Drive: {hasil_unduh.stderr[-250:]}")
    else:
        yt_cookies = os.environ.get("YT_COOKIES", "").strip()
        yt_cmd = [
            "yt-dlp",
            "--js-runtimes", "node",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", "source_video.mp4"
        ]
        if yt_cookies and ("youtube.com" in link_video or "youtu.be" in link_video):
            with open("cookies.txt", "w") as cf:
                cf.write(yt_cookies)
            yt_cmd.extend(["--cookies", "cookies.txt"])
        yt_cmd.append(link_video)

        hasil_unduh = subprocess.run(yt_cmd, capture_output=True, text=True)
        if hasil_unduh.returncode != 0:
            baris_err = [b for b in hasil_unduh.stderr.splitlines() if "ERROR:" in b]
            detail_err = baris_err[-1] if baris_err else hasil_unduh.stderr[-250:]
            raise RuntimeError(f"Gagal mengunduh video: {detail_err}")

    tahap_sekarang = "QC"
    update_visual("QC", "Agen QC memeriksa segmen & mendeteksi subtitle bawaan...")
    video_file = client.files.upload(file="source_video.mp4")
    while video_file.state.name == "PROCESSING":
        time.sleep(5)
        video_file = client.files.get(name=video_file.name)

    prompt = f"""
Kamu adalah Tim Riset, Produksi, dan Quality Control (QC) Kantor AI Clipper untuk platform Konten.com.
Berikut instruksi & syarat campaign harian yang dikirim bos lewat Telegram:
{syarat}

Tugasmu:
1. Periksa apakah video sumber ini SUDAH memiliki subtitle/teks ucapan bawaan di layar (hardcoded subtitle). Isi "sudah_ada_subtitle": true jika sudah ada teks terjemahan/ucapan bawaan di video, atau false jika video bersih tanpa teks ucapan.
2. Pilih maksimal 3 segmen terbaik yang LOLOS QC (mematuhi durasi, topik, dan larangan yang diminta). Jika durasi tidak disebut, gunakan standar 20-55 detik.
3. Tulis caption menarik yang menyertakan mention dan hashtag wajib yang diminta (jika ada).

Berikan jawaban HANYA dalam format JSON objek valid tanpa teks tambahan:
{{
  "sudah_ada_subtitle": true,
  "klip": [
    {{
      "judul": "nama_klip_tanpa_spasi",
      "mulai": "00:01:15",
      "selesai": "00:01:50",
      "caption_ig": "Teks hook menarik + mention/hashtag relevan"
    }}
  ]
}}
"""

    model_kandidat = [
        m.name.replace("models/", "")
        for m in client.models.list()
        if "generateContent" in (m.supported_actions or []) and "gemini" in m.name
    ]
    prioritas = ["gemini-3-flash-preview", "gemini-3-flash", "gemini-2.5-flash-preview", "gemini-2.5-pro", "gemini-2.0-flash"]
    urutan_uji = [p for p in prioritas if p in model_kandidat] + [m for m in model_kandidat if m not in prioritas]

    response = None
    error_terakhir = None
    for nama_model in urutan_uji:
        try:
            response = client.models.generate_content(model=nama_model, contents=[video_file, prompt])
            if response and response.text:
                break
        except Exception as err_m:
            error_terakhir = err_m
            continue

    if not response or not response.text:
        raise RuntimeError(f"Semua model Gemini gagal diakses: {error_terakhir}")

    raw_json = response.text.strip().removeprefix("```json").removesuffix("```").strip()
    hasil_ai = json.loads(raw_json)
    sudah_ada_sub = hasil_ai.get("sudah_ada_subtitle", False)
    klip_list = hasil_ai.get("klip", [])

    tahap_sekarang = "POSTING"
    for idx, klip in enumerate(klip_list, 1):
        update_visual("PRODUKSI", f"Agen Clipper merender zoom pembicara aktif klip {idx}...")
        raw_cut = f"raw_cut_{idx}.mp4"
        tracked_cut = f"tracked_{idx}.mp4"
        out_mp4 = f"output_klip/{idx}_{klip['judul']}.mp4"

        # 1. Potong segmen waktu asli dulu (masih landscape 16:9 penuh)
        subprocess.run([
            "ffmpeg", "-y", "-i", "source_video.mp4",
            "-ss", klip["mulai"], "-to", klip["selesai"],
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", raw_cut
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. Jalankan pelacak wajah & gerak bibir otomatis (zoom mengikuti pembicara aktif)
        render_smart_speaker_zoom(raw_cut, tracked_cut)

        # 3. Tambahkan subtitle Whisper HANYA jika video belum punya subtitle bawaan
        if not sudah_ada_sub:
            subprocess.run(["whisper", tracked_cut, "--model", "base", "--language", "Indonesian", "--output_format", "srt"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            srt_file = f"tracked_{idx}.srt"
            if os.path.exists(srt_file):
                subprocess.run([
                    "ffmpeg", "-y", "-i", tracked_cut,
                    "-vf", f"subtitles={srt_file}:force_style='FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=55'",
                    "-c:a", "copy", out_mp4
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                os.remove(srt_file)
            else:
                os.rename(tracked_cut, out_mp4)
        else:
            os.rename(tracked_cut, out_mp4)

        for tmp in [raw_cut, tracked_cut]:
            if os.path.exists(tmp):
                os.remove(tmp)

        update_visual("POSTING", f"Agen Kurir mengirim klip {idx} ke Telegram...")
        kirim_video(out_mp4, klip["caption_ig"])

    update_visual("IDLE", "Selesai! Semua klip sudah dikirim ke Telegram. Agen kembali istirahat.")

except Exception as e:
    ringkasan_error = str(e).strip()[:300]
    pesan_visual = f"GAGAL di tahap {tahap_sekarang}: {ringkasan_error}"
    update_visual("ERROR", pesan_visual)
    kirim_teks(f"❌ PROSES KANTOR AI GAGAL!\n\n📍 Tahap: {tahap_sekarang}\n⚠️ Penyebab:\n{ringkasan_error}")
    raise
