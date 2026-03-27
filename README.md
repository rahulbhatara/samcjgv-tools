# SAMC JGV Tools

Kumpulan tools pendukung untuk [samcjgv-raceroom](https://github.com/rahulbhatara/samcjgv-raceroom) Race Control System.

---

## 🗺️ track-scraper

Tool untuk merekam track definition (centerline & pit lane) dari dalam game GTA V melalui CEF (Chromium Embedded Framework).

### Cara Kerja
1. **Server** (`server.py`) — HTTP bridge yang menerima data posisi XY dari game
2. **CEF Client** (`client.html`) — Diload di dalam game, menangkap posisi dari `updateScoreboard` API
3. **Dashboard** (`dashboard.html`) — Kontrol recording dan visualisasi track yang sedang direkam

### Cara Pakai
```bash
# Jalankan server
python3 track-scraper/server.py

# Buka dashboard di browser
# http://localhost:8899/

# Load CEF client di game
# http://localhost:8899/client
```

### Alur Recording
1. Buka **Dashboard** → klik **Record Track** atau **Record Pit**
2. Jalan satu lap penuh di game (CEF client akan mengirim posisi XY secara otomatis)
3. Klik **Stop** → export sebagai `.trackdef.json`
4. Upload file ke raceroom via `/api/race/upload_trackdef`

### Output
File `*.trackdef.json` berisi:
- `centerline` — Array koordinat XY jalur utama
- `pit_lane` — Array koordinat XY jalur pit
- `bounding_box` — Min/max koordinat untuk rendering
- `finish_line` — Koordinat garis finish

---

## 📦 Tools Lainnya

> Tools tambahan akan ditambahkan seiring kebutuhan pengembangan samcjgv-raceroom.
