r"""Guard: kilit ekraninda kamera nobeti, yuz tanima, telemetri ve Telegram bildirimi.

Kullanim: guard.py [tray|kilit|test|kurulum|kisayol]   (bos = tray)
Veriler %APPDATA%\GuardSecurity altinda tutulur.
"""
import ctypes
from ctypes import wintypes
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

from guard.ayarlar import ESKI_KLASOR, kaynak_adaylari, paketlenmis, veri_klasoru
from guard.kamera import kamera_ac as _kamera_ac, kamera_engeli_var_mi
from guard.kisayol import kisayol_olustur

try:
    import psutil
except ImportError:
    psutil = None

try:
    from PIL import Image, ImageDraw
    import pystray
except ImportError:
    Image = None
    pystray = None


KLASOR = veri_klasoru()
OUT = KLASOR / "kayitlar"
YUZ_DIR = KLASOR / "yuzler"
FLAG = KLASOR / "failed.flag"
UNLOCK = KLASOR / "unlock.flag"
LOG_TXT = KLASOR / "log.txt"
LOG_JSON = KLASOR / "log.json"
TELEMETRI_JS = KLASOR / "telemetri_veri.js"
CONFIG_DOSYA = KLASOR / "config.json"
DASHBOARD_HTML = KLASOR / "dashboard.html"
ESKI_FLAG = ESKI_KLASOR / "failed.flag"
ESKI_UNLOCK = ESKI_KLASOR / "unlock.flag"

for d in (OUT, YUZ_DIR):
    d.mkdir(parents=True, exist_ok=True)

try:
    from guard.yuz import Taniyici, modelleri_hazirla
    modelleri_hazirla(KLASOR)
except Exception:
    Taniyici = None

# Kamera ve koruyucu sabitleri
FPS = 10
PIKSEL_ESIK = 25
ORAN_ESIK = 0.01
DURMA_SN = 8
ISINMA_KARE = 15
TEST_SN = 15
YABANCI_FOTO_ARALIK = 30
SAKLAMA_GUN = 14
MAKS_LOG_KAYIT = 1500
MAKS_TELEMETRI_GECMIS = 120
LOG_TXT_MAKS = 1_000_000

_log_kilit = threading.Lock()

# ---- ayarlar ----
VARSAYILAN_CONFIG = {
    "ilk_kurulum_tamam": False,
    "kullanici_adi": os.environ.get("USERNAME", "kullanici"),
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_aktif": False,
    "otomatik_kilit_nobeti": True,
    "bosta_islem_dk": 0,
    "bosta_islem_tipi": "kilitle",
    "telemetri_aralik_sn": 30,
    "kurulum_tarihi": "",
    "kisayol_eklendi": False,
}


def config_yukle() -> dict:
    if CONFIG_DOSYA.exists():
        try:
            with open(CONFIG_DOSYA, "r", encoding="utf-8") as f:
                veri = json.load(f)
            birlesik = dict(VARSAYILAN_CONFIG)
            birlesik.update(veri)
            return birlesik
        except Exception:
            pass
    return dict(VARSAYILAN_CONFIG)


def config_kaydet(cfg: dict) -> None:
    try:
        with open(CONFIG_DOSYA, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


CONFIG = config_yukle()

# ---- win32 ----
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def bosta_gecen_sure_sn() -> float:
    """Kullanicinin klavye/fareye son dokunusundan itibaren gecen saniyeyi dondurur."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 0.0


def kilitli_mi() -> bool:
    """Ekranin kilitli olup olmadigini kontrol eder."""
    try:
        user32 = ctypes.windll.user32
        h = user32.OpenInputDesktop(0, False, 0x0100)
        if not h:
            return True
        user32.CloseDesktop(h)
        return False
    except Exception:
        return False


def ekrani_kilitle() -> None:
    """Windows kilit ekranina doner."""
    log_ekle("Ekrani kilitle komutu calistirildi.", kategori="SISTEM", olay="EKRAN_KILITLE")
    try:
        ctypes.windll.user32.LockWorkStation()
    except Exception as e:
        log_ekle(f"Ekran kilitlenemedi: {e}", kategori="HATA", olay="KILIT_HATA", seviye="HATA")


def bilgisayari_kapat(gecikme_sn: int = 0, zorla: bool = False) -> None:
    """Bilgisayari kapatir."""
    log_ekle(
        f"Bilgisayari kapatma komutu verildi (gecikme={gecikme_sn} sn).",
        kategori="SISTEM",
        olay="BILGISAYAR_KAPAT",
        seviye="UYARI",
    )
    telegram_mesaj_gonder(f"Bilgisayar kapatılıyor (Gecikme: {gecikme_sn} sn).")
    komut = ["shutdown", "/s", "/t", str(max(0, int(gecikme_sn)))]
    if zorla:
        komut.insert(2, "/f")
    try:
        subprocess.Popen(komut, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception as e:
        log_ekle(f"Kapatma komutu hatasi: {e}", kategori="HATA", olay="KAPATMA_HATA", seviye="HATA")


def kapatmayi_iptal_et() -> None:
    """Windows shutdown zamanlayicisini iptal eder."""
    try:
        subprocess.Popen(["shutdown", "/a"], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception:
        pass


# ---- donanim ve telemetri ----
_son_sicaklik_kontrol = 0.0
_onbellek_sicaklik = None


def islemci_sicakligi_cek() -> float | None:
    """Windows ve diger sistemlerde islemci sicakligini cekmeye calisir."""
    global _son_sicaklik_kontrol, _onbellek_sicaklik
    simdi = time.time()
    if simdi - _son_sicaklik_kontrol < 15.0:
        return _onbellek_sicaklik
    _son_sicaklik_kontrol = simdi

    if psutil and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for _, entries in temps.items():
                    for entry in entries:
                        if entry.current and 15.0 < entry.current < 115.0:
                            _onbellek_sicaklik = round(float(entry.current), 1)
                            return _onbellek_sicaklik
        except Exception:
            pass

    if os.name == "nt":
        ps_komut = (
            "$t = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue; "
            "if ($t) { ($t[0].CurrentTemperature - 2732) / 10.0 } else { "
            "$o = Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor -ErrorAction SilentlyContinue | Where-Object {$_.SensorType -eq 'Temperature'}; "
            "if ($o) { $o[0].Value } }"
        )
        try:
            sonuc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_komut],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            cikti = (sonuc.stdout or "").strip().replace(",", ".")
            if cikti:
                deger = float(cikti.splitlines()[0].strip())
                if 10.0 < deger < 120.0:
                    _onbellek_sicaklik = round(deger, 1)
                    return _onbellek_sicaklik
        except Exception:
            pass

    return _onbellek_sicaklik


def sistem_bilgisi_topla() -> dict:
    kayitli_yuzler = [f.stem for f in YUZ_DIR.glob("*.npy")]
    ram_toplam = 0.0
    cekirdek = os.cpu_count() or 1
    if psutil:
        try:
            ram_toplam = round(psutil.virtual_memory().total / (1024 ** 3), 2)
        except Exception:
            pass
    return {
        "bilgisayar_adi": socket.gethostname(),
        "kullanici": CONFIG.get("kullanici_adi") or os.environ.get("USERNAME", "kullanici"),
        "isletim_sistemi": f"{platform.system()} {platform.release()} ({platform.version()})",
        "islemci": platform.processor() or platform.machine(),
        "cekirdek_sayisi": cekirdek,
        "ram_toplam_gb": ram_toplam,
        "kayitli_yuzler": kayitli_yuzler,
        "veri_klasoru": str(KLASOR),
        "telegram_aktif": bool(CONFIG.get("telegram_aktif") and CONFIG.get("telegram_bot_token")),
        "calisma_modu": "EXE" if paketlenmis() else "Python",
    }


def anlik_telemetri_topla() -> dict:
    cpu_yuzde = 0.0
    ram_yuzde = 0.0
    ram_kullanilan_gb = 0.0
    ram_toplam_gb = 0.0
    disk_yuzde = 0.0
    disk_bos_gb = 0.0
    ag_gonderilen_mb = 0.0
    ag_alinan_mb = 0.0
    pil_yuzde = None
    sarjda_mi = None
    uptime_sn = 0.0

    if psutil:
        try:
            cpu_yuzde = round(psutil.cpu_percent(interval=None), 1)
            vm = psutil.virtual_memory()
            ram_yuzde = round(vm.percent, 1)
            ram_kullanilan_gb = round(vm.used / (1024 ** 3), 2)
            ram_toplam_gb = round(vm.total / (1024 ** 3), 2)

            sistem_surucu = os.environ.get("SystemDrive", "C:") + "\\"
            du = psutil.disk_usage(sistem_surucu)
            disk_yuzde = round(du.percent, 1)
            disk_bos_gb = round(du.free / (1024 ** 3), 2)

            net = psutil.net_io_counters()
            ag_gonderilen_mb = round(net.bytes_sent / (1024 ** 2), 1)
            ag_alinan_mb = round(net.bytes_recv / (1024 ** 2), 1)

            uptime_sn = round(time.time() - psutil.boot_time(), 0)

            if hasattr(psutil, "sensors_battery"):
                bat = psutil.sensors_battery()
                if bat is not None:
                    pil_yuzde = round(bat.percent, 1)
                    sarjda_mi = bool(bat.power_plugged)
        except Exception:
            pass

    bosta_sn = bosta_gecen_sure_sn()
    kilitli = kilitli_mi()
    if kilitli:
        durum = "KILITLI"
    elif bosta_sn > 60:
        durum = f"BOSTA ({int(bosta_sn)} sn)"
    else:
        durum = "AKTIF"

    return {
        "zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_yuzde": cpu_yuzde,
        "cpu_sicaklik_c": islemci_sicakligi_cek(),
        "ram_yuzde": ram_yuzde,
        "ram_kullanilan_gb": ram_kullanilan_gb,
        "ram_toplam_gb": ram_toplam_gb,
        "disk_yuzde": disk_yuzde,
        "disk_bos_gb": disk_bos_gb,
        "ag_gonderilen_mb": ag_gonderilen_mb,
        "ag_alinan_mb": ag_alinan_mb,
        "pil_yuzde": pil_yuzde,
        "sarjda_mi": sarjda_mi,
        "sistem_uptime_sn": uptime_sn,
        "bosta_gecen_sn": round(bosta_sn, 1),
        "kilitli_mi": kilitli,
        "kullanici_durumu": durum,
        "kamera_engeli_var": DURUM_TAKIP["son_kamera_engeli"],
        "aktif_zamanlayici": zamanlayici_metni_getir(),
    }


# ---- sure takibi ve log ----
DURUM_TAKIP = {
    "aktif_kullanim_sn": 0.0,
    "bosta_kalma_sn": 0.0,
    "kilitli_kalma_sn": 0.0,
    "uyku_suresi_sn": 0.0,
    "uyku_sayisi": 0,
    "yabanci_yuz_sayisi": 0,
    "yanlis_sifre_sayisi": 0,
    "kamera_engeli_sayisi": 0,
    "son_kamera_engeli": False,
    "zamanlayici_hedef_ts": 0.0,
    "zamanlayici_islem": "",
}

OZET_ANAHTARLARI = (
    "aktif_kullanim_sn", "bosta_kalma_sn", "kilitli_kalma_sn", "uyku_suresi_sn",
    "uyku_sayisi", "yabanci_yuz_sayisi", "yanlis_sifre_sayisi", "kamera_engeli_sayisi",
)
_veri = None
_ham_satirlar = deque(maxlen=250)
_anlik_onbellek = (0.0, {})
_sistem_ts = 0.0


def _veri_yukle() -> dict:
    """log.json'u ilk cagrida bir kez okur, sonrasinda bellekten calisir."""
    global _veri
    if _veri is not None:
        return _veri
    _veri = {
        "son_guncelleme": "",
        "sistem_bilgisi": {},
        "oturum_ozeti": {},
        "anlik_telemetri": {},
        "telemetri_gecmisi": [],
        "loglar": [],
    }
    try:
        kayit = json.loads(LOG_JSON.read_text(encoding="utf-8"))
        if isinstance(kayit, dict):
            _veri.update(kayit)
            for k in OZET_ANAHTARLARI:
                if k in _veri.get("oturum_ozeti", {}):
                    DURUM_TAKIP[k] = _veri["oturum_ozeti"][k]
    except Exception:
        pass
    try:
        _ham_satirlar.extend(LOG_TXT.read_text(encoding="utf-8", errors="replace").splitlines()[-250:])
    except Exception:
        pass
    return _veri


def _atomik_yaz(yol: Path, metin: str) -> None:
    gecici = yol.with_name(yol.name + ".tmp")
    gecici.write_text(metin, encoding="utf-8")
    os.replace(gecici, yol)


def _anlik_getir(max_yas: float = 5.0) -> dict:
    """Telemetriyi en fazla max_yas saniyede bir yeniden olcer."""
    global _anlik_onbellek
    ts, veri = _anlik_onbellek
    if not veri or time.time() - ts > max_yas:
        veri = anlik_telemetri_topla()
        _anlik_onbellek = (time.time(), veri)
    return veri


def zamanlayici_metni_getir() -> str:
    hedef = DURUM_TAKIP["zamanlayici_hedef_ts"]
    islem = DURUM_TAKIP["zamanlayici_islem"]
    if hedef > time.time() and islem:
        kalan = int(hedef - time.time())
        dk, sn = divmod(kalan, 60)
        etiket = "Bilgisayar kapatılacak" if islem == "kapat" else "Kilit ekranına dönülecek"
        return f"{etiket}: {dk} dk {sn} sn kaldı"
    return "Aktif zamanlayıcı yok"


def zamanlayici_ayarla(dakika: float, islem: str) -> None:
    """Belirtilen dakika sonunda 'kapat' veya 'kilitle' islemi uygulanmasini zamanlar."""
    if dakika <= 0:
        DURUM_TAKIP["zamanlayici_hedef_ts"] = 0.0
        DURUM_TAKIP["zamanlayici_islem"] = ""
        kapatmayi_iptal_et()
        log_ekle("Aktif zamanlayici iptal edildi.", kategori="SISTEM", olay="ZAMANLAYICI_IPTAL")
        return

    DURUM_TAKIP["zamanlayici_hedef_ts"] = time.time() + (dakika * 60.0)
    DURUM_TAKIP["zamanlayici_islem"] = islem
    aciklama = "Bilgisayarı Kapatma" if islem == "kapat" else "Kilit Ekranına Dönme"
    log_ekle(
        f"{dakika} dakika sonrasina '{aciklama}' zamanlayicisi kuruldu.",
        kategori="SISTEM",
        olay="ZAMANLAYICI_KURULDU",
        detay={"dakika": dakika, "islem": islem},
    )


def log_ekle(
    mesaj: str,
    kategori: str = "BILGI",
    olay: str = "GENEL",
    seviye: str = "BILGI",
    detay: dict | None = None,
    telemetri_ornegi_ekle: bool = False,
) -> None:
    """log.txt, log.json ve panelin okudugu telemetri_veri.js dosyalarini gunceller."""
    global _sistem_ts
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ek = " | " + ", ".join(f"{k}={v}" for k, v in detay.items()) if detay else ""
    satir = f"{simdi} [{kategori}/{olay}] {mesaj}{ek}"

    with _log_kilit:
        veri = _veri_yukle()
        _ham_satirlar.append(satir)
        try:
            if LOG_TXT.exists() and LOG_TXT.stat().st_size > LOG_TXT_MAKS:
                os.replace(LOG_TXT, LOG_TXT.with_name("log.eski.txt"))
            with open(LOG_TXT, "a", encoding="utf-8") as f:
                f.write(satir + "\n")
        except Exception:
            pass

        try:
            veri["son_guncelleme"] = simdi
            if time.time() - _sistem_ts > 30 or not veri["sistem_bilgisi"]:
                veri["sistem_bilgisi"] = sistem_bilgisi_topla()
                _sistem_ts = time.time()
            anlik = _anlik_getir()
            veri["anlik_telemetri"] = anlik
            veri["oturum_ozeti"] = {
                k: (round(DURUM_TAKIP[k], 1) if k.endswith("_sn") else DURUM_TAKIP[k])
                for k in OZET_ANAHTARLARI
            }

            if telemetri_ornegi_ekle:
                gecmis = veri["telemetri_gecmisi"]
                gecmis.append({
                    "zaman": simdi,
                    "cpu_yuzde": anlik["cpu_yuzde"],
                    "cpu_sicaklik_c": anlik["cpu_sicaklik_c"],
                    "ram_yuzde": anlik["ram_yuzde"],
                    "kullanici_durumu": anlik["kullanici_durumu"],
                })
                veri["telemetri_gecmisi"] = gecmis[-MAKS_TELEMETRI_GECMIS:]

            veri["loglar"].append({
                "tarih": simdi,
                "kategori": kategori,
                "olay": olay,
                "seviye": seviye,
                "mesaj": mesaj,
                "detay": detay or {},
            })
            veri["loglar"] = veri["loglar"][-MAKS_LOG_KAYIT:]

            _atomik_yaz(LOG_JSON, json.dumps(veri, ensure_ascii=False, separators=(",", ":")))
            js = dict(veri, ham_log_txt="\n".join(_ham_satirlar))
            _atomik_yaz(TELEMETRI_JS, "window.GUARD_DATA = " + json.dumps(js, ensure_ascii=False) + ";\n")
        except Exception:
            pass


# ---- telegram ----
def telegram_mesaj_gonder(metin: str, token: str | None = None, chat_id: str | None = None) -> bool:
    """Telegram Bot API uzerinden metin bildirimi gonderir."""
    tkn = (token if token is not None else CONFIG.get("telegram_bot_token", "")).strip()
    cid = (chat_id if chat_id is not None else CONFIG.get("telegram_chat_id", "")).strip()
    if not tkn or not cid:
        return False

    url = f"https://api.telegram.org/bot{tkn}/sendMessage"
    veri = urllib.parse.urlencode({"chat_id": cid, "text": metin}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=veri, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as e:
        log_ekle(f"Telegram mesaj gonderilemedi: {e}", kategori="HATA", olay="TELEGRAM_HATA", seviye="UYARI")
        return False


def telegram_foto_gonder(foto_yolu: str | Path, aciklama: str = "") -> bool:
    """Telegram Bot API uzerinden fotograf (multipart/form-data) gonderir."""
    tkn = CONFIG.get("telegram_bot_token", "").strip()
    cid = CONFIG.get("telegram_chat_id", "").strip()
    foto_path = Path(foto_yolu)
    if not tkn or not cid or not foto_path.exists():
        return False

    boundary = f"----GuardBoundary{int(time.time() * 1000)}"
    url = f"https://api.telegram.org/bot{tkn}/sendPhoto"

    try:
        foto_bytes = foto_path.read_bytes()
        govde = bytearray()

        govde.extend(f"--{boundary}\r\n".encode("utf-8"))
        govde.extend(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
        govde.extend(f"{cid}\r\n".encode("utf-8"))

        if aciklama:
            govde.extend(f"--{boundary}\r\n".encode("utf-8"))
            govde.extend(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
            govde.extend(f"{aciklama}\r\n".encode("utf-8"))

        govde.extend(f"--{boundary}\r\n".encode("utf-8"))
        govde.extend(
            f'Content-Disposition: form-data; name="photo"; filename="{foto_path.name}"\r\n'.encode("utf-8")
        )
        govde.extend(b"Content-Type: image/jpeg\r\n\r\n")
        govde.extend(foto_bytes)
        govde.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        req = urllib.request.Request(
            url,
            data=bytes(govde),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log_ekle(f"Telegram fotograf gonderilemedi: {e}", kategori="HATA", olay="TELEGRAM_FOTO_HATA", seviye="UYARI")
        return False


def telegram_asenkron_bildir(metin: str, foto_yolu: str | Path | None = None) -> None:
    """Ana donguyu bekletmeden arka planda Telegram bildirimi gonderir."""
    def _gonder():
        if foto_yolu and Path(foto_yolu).exists():
            ok = telegram_foto_gonder(foto_yolu, metin)
            if not ok:
                telegram_mesaj_gonder(metin)
        else:
            telegram_mesaj_gonder(metin)

    threading.Thread(target=_gonder, daemon=True).start()


# ---- kamera ----
def zaman() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def kamera_ac():
    def bildir(backend, idx):
        log_ekle(
            f"Kamera acildi (backend={backend}, index={idx})",
            kategori="GUVENLIK",
            olay="KAMERA_ACILDI",
            detay={"backend": backend, "index": idx},
        )
    return _kamera_ac(bildir)


def foto(frame, etiket: str, telegrama_gonder: bool = False, baslik: str = "") -> Path | None:
    if frame is None:
        return None
    yol = OUT / f"{etiket}_{zaman()}.jpg"
    cv2.imwrite(str(yol), frame)

    engel, parlaklik, std_deger = kamera_engeli_var_mi(frame)
    DURUM_TAKIP["son_kamera_engeli"] = engel

    if engel:
        DURUM_TAKIP["kamera_engeli_sayisi"] += 1
        uyari_msg = (
            f"UYARI: Kamera engeli var veya ekran kapkaranlik! "
            f"(Etiket={etiket}, Parlaklik={parlaklik}, Std={std_deger})"
        )
        log_ekle(
            uyari_msg,
            kategori="KAMERA_ENGELI",
            olay="KAMERA_ENGELI",
            seviye="KRITIK",
            detay={"parlaklik": parlaklik, "std": std_deger, "dosya": str(yol)},
        )
        telegram_asenkron_bildir(
            f"KAMERA ENGELİ ALGILANDI!\n"
            f"Kamera görüntüsü kapkaranlık (Parlaklık: {parlaklik}/255).\n"
            f"Fiziksel kamera kapağı kapalı veya kamera engellenmiş olabilir!\n"
            f"Olay: {etiket}",
            yol,
        )
    else:
        log_ekle(
            f"Fotograf kaydedildi: {yol.name}",
            kategori="GUVENLIK",
            olay=f"FOTO_{etiket.upper()}",
            detay={"dosya": str(yol), "parlaklik": parlaklik},
        )
        if telegrama_gonder:
            bildirim_metni = baslik or f"Guard: {etiket} ({datetime.now():%H:%M:%S})"
            telegram_asenkron_bildir(bildirim_metni, yol)

    return yol


def anlik_fotograf_cek_ve_kontrol_et(etiket: str = "ANLIK_KONTROL") -> tuple[bool, str]:
    """Kamerayi acar, isinma karelerini okur, fotograf ceker ve karanlik/engel kontrolu yapar."""
    cap = kamera_ac()
    if cap is None:
        msg = "Kamera açılamadı! Gizlilik izni kapalı veya başka bir uygulama kamerayı kullanıyor."
        log_ekle(msg, kategori="HATA", olay="KAMERA_YOK", seviye="HATA")
        telegram_asenkron_bildir(f"Guard uyarı: {msg}")
        return False, msg

    frame = None
    for _ in range(ISINMA_KARE):
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()

    if frame is None:
        return False, "Kameradan görüntü karesi okunamadı."

    engel, parlaklik, _ = kamera_engeli_var_mi(frame)
    yol = foto(
        frame,
        etiket,
        telegrama_gonder=True,
        baslik=f"Guard ({etiket}) - Bilgisayar: {socket.gethostname()}",
    )
    if engel:
        return True, f"Kamera engeli var: görüntü kapkaranlık (parlaklık {parlaklik}). Dosya: {yol.name}"
    return True, f"Kamera çalışıyor, fotoğraf çekildi (parlaklık {parlaklik}). Dosya: {yol.name}"


def temizle() -> None:
    """kayitlar klasorundeki SAKLAMA_GUN gunden eski dosyalari siler."""
    sinir = time.time() - SAKLAMA_GUN * 86400
    silinen = 0
    for ad in os.listdir(OUT):
        yol = OUT / ad
        try:
            if yol.is_file() and yol.stat().st_mtime < sinir:
                yol.unlink()
                silinen += 1
        except OSError:
            pass
    if silinen:
        log_ekle(f"Temizlik: {silinen} eski kayit dosyasi silindi.", kategori="SISTEM", olay="TEMIZLIK")


# ---- kilit nobeti ----
_aktif_nobet_kilit = threading.Lock()


def taniyici_baslat():
    if Taniyici is None:
        log_ekle("UYARI: yuz.py yuklenemedi, yuz tanima kapali.", kategori="HATA", olay="YUZ_MODUL_YOK", seviye="UYARI")
        return None
    try:
        t = Taniyici(KLASOR)
        log_ekle(
            f"Yuz tanima aktif. Kayitli kisiler: {list(t.kisiler)}",
            kategori="GUVENLIK",
            olay="YUZ_TANIMA_AKTIF",
            detay={"kisiler": list(t.kisiler)},
        )
        return t
    except Exception as e:
        log_ekle(f"Yuz tanima baslatilamadi: {e}", kategori="UYARI", olay="YUZ_TANIMA_KAPALI", seviye="UYARI")
        return None


def kilit_dongusu(test: bool = False, otomatik_mod: bool = False) -> None:
    """Kilit ekraninda kamerayi izler, hareket / yabanci yuz / yanlis sifre / kamera engeli kaydeder."""
    if not _aktif_nobet_kilit.acquire(blocking=False):
        return

    try:
        cap = kamera_ac()
        if cap is None:
            log_ekle(
                "HATA: Kamera acilamadi (gizlilik izni veya baska program kullaniyor olabilir)",
                kategori="HATA",
                olay="KAMERA_ACILAMADI",
                seviye="HATA",
            )
            telegram_asenkron_bildir("Guard kilit nöbeti: Kamera açılamadı!")
            return

        for _ in range(ISINMA_KARE):
            cap.read()

        taniyici = taniyici_baslat()
        bas = time.time()

        def devam_etsin_mi() -> bool:
            if test:
                return (time.time() - bas) < TEST_SN
            if os.path.exists(UNLOCK) or os.path.exists(ESKI_UNLOCK):
                return False
            if otomatik_mod and not kilitli_mi() and (time.time() - bas) > 3.0:
                return False
            return True

        onceki = None
        yazici = None
        son_hareket = 0.0
        ilk = True
        yuz_durum = "YUZ_YOK"
        son_yabanci_foto = 0.0
        son_engel_uyari = 0.0

        while devam_etsin_mi():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.5)
                continue

            simdi = time.time()

            engel, parlaklik, std_deger = kamera_engeli_var_mi(frame)
            DURUM_TAKIP["son_kamera_engeli"] = engel
            if engel and (simdi - son_engel_uyari > 180.0):
                son_engel_uyari = simdi
                foto(frame, "KAMERA_ENGELI")

            if ilk:
                foto(frame, "kilitlendi", telegrama_gonder=True, baslik="Bilgisayar Kilitlendi - Kamera Nöbette")
                ilk = False

            for flg in (FLAG, ESKI_FLAG):
                if os.path.exists(flg):
                    try:
                        os.remove(flg)
                    except OSError:
                        pass
                    DURUM_TAKIP["yanlis_sifre_sayisi"] += 1
                    foto(
                        frame,
                        "YANLIS_SIFRE",
                        telegrama_gonder=True,
                        baslik="UYARI: Kilit Ekranında YANLIŞ ŞİFRE Girildi!",
                    )

            gri = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
            hareket = False
            if onceki is not None:
                fark = cv2.absdiff(onceki, gri)
                hareket = (fark > PIKSEL_ESIK).mean() > ORAN_ESIK
            onceki = gri

            if taniyici is not None and not engel:
                try:
                    d = taniyici.bak(frame)
                except Exception:
                    log_ekle("HATA: Yuz tanima calisirken hata olustu.", kategori="HATA", olay="YUZ_HATA", seviye="HATA")
                    taniyici = None
                    d = None
                if d is not None:
                    if d != yuz_durum:
                        log_ekle(
                            f"Yuz durumu degisti: {d} (Kisi: {taniyici.son_isim if taniyici else '-'})",
                            kategori="GUVENLIK",
                            olay=f"YUZ_{d}",
                            detay={"kisi": taniyici.son_isim if taniyici else None},
                        )
                    yuz_durum = d
                if yuz_durum == "TANIDIK":
                    hareket = False
                elif yuz_durum == "YABANCI":
                    hareket = True
                    if simdi - son_yabanci_foto > YABANCI_FOTO_ARALIK:
                        DURUM_TAKIP["yabanci_yuz_sayisi"] += 1
                        foto(
                            frame,
                            "YABANCI",
                            telegrama_gonder=True,
                            baslik="UYARI: Bilgisayar Başında YABANCI YÜZ Tespit Edildi!",
                        )
                        son_yabanci_foto = simdi

            if hareket:
                son_hareket = simdi
            kaydet = (simdi - son_hareket) < DURMA_SN

            if kaydet and yazici is None:
                h, w = frame.shape[:2]
                yol = OUT / f"video_{zaman()}.mp4"
                yazici = cv2.VideoWriter(str(yol), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
                log_ekle(f"Video kaydi basladi: {yol.name}", kategori="GUVENLIK", olay="VIDEO_BASLADI", detay={"dosya": str(yol)})

            if yazici is not None:
                yazici.write(frame)
                if not kaydet:
                    yazici.release()
                    yazici = None
                    log_ekle("Video kaydi tamamlandi.", kategori="GUVENLIK", olay="VIDEO_BITTI")

            time.sleep(1.0 / FPS)

        if yazici is not None:
            yazici.release()

        ok, frame = cap.read()
        if ok and frame is not None:
            foto(frame, "ACILDI", telegrama_gonder=True, baslik="Kilit Açıldı - Kullanıcı Oturuma Döndü")
        cap.release()
    finally:
        _aktif_nobet_kilit.release()


# ---- arka plan servisi ----
_servis_calisiyor = True


def arka_plan_telemetri_dongusu() -> None:
    """Aktif/bosta/kilitli sure, uyku tespiti, otomatik nobet ve zamanlayici denetimi."""
    son_adim = time.time()
    son_telemetri_kayit = 0.0
    onceki_kilit_durumu = kilitli_mi()

    while _servis_calisiyor:
        time.sleep(1.0)
        simdi = time.time()
        gecen = simdi - son_adim
        son_adim = simdi

        if gecen > 15.0:
            DURUM_TAKIP["uyku_suresi_sn"] += gecen
            DURUM_TAKIP["uyku_sayisi"] += 1
            uyku_dk = round(gecen / 60.0, 1)
            log_ekle(
                f"Bilgisayar uykudan uyandi (Uyku suresi: {uyku_dk} dk / {int(gecen)} sn).",
                kategori="UYKU",
                olay="UYKU_UYANDI",
                detay={"uyku_sn": round(gecen, 1), "toplam_uyku_sayisi": DURUM_TAKIP["uyku_sayisi"]},
            )
            telegram_asenkron_bildir(f"Bilgisayar uykudan uyandı (Uyku süresi: {uyku_dk} dk).")
            gecen = 1.0

        kilitli = kilitli_mi()
        bosta_sn = bosta_gecen_sure_sn()

        if kilitli:
            DURUM_TAKIP["kilitli_kalma_sn"] += gecen
        elif bosta_sn >= 60.0:
            DURUM_TAKIP["bosta_kalma_sn"] += gecen
        else:
            DURUM_TAKIP["aktif_kullanim_sn"] += gecen

        if kilitli and not onceki_kilit_durumu and CONFIG.get("otomatik_kilit_nobeti", True):
            log_ekle("Ekran kilitlendi, otomatik kamera koruyucu devreye giriyor.", kategori="GUVENLIK", olay="OTOMATIK_KILIT_ALGILANDI")
            threading.Thread(target=kilit_dongusu, kwargs={"test": False, "otomatik_mod": True}, daemon=True).start()
        onceki_kilit_durumu = kilitli

        bosta_limit_dk = float(CONFIG.get("bosta_islem_dk") or 0)
        if bosta_limit_dk > 0 and not kilitli and bosta_sn >= (bosta_limit_dk * 60.0):
            islem_tipi = CONFIG.get("bosta_islem_tipi", "kilitle")
            if islem_tipi == "kapat":
                CONFIG["bosta_islem_dk"] = 0
                bilgisayari_kapat(gecikme_sn=10)
            else:
                ekrani_kilitle()
                time.sleep(2.0)

        hedef_ts = DURUM_TAKIP["zamanlayici_hedef_ts"]
        if hedef_ts > 0 and simdi >= hedef_ts:
            islem = DURUM_TAKIP["zamanlayici_islem"]
            DURUM_TAKIP["zamanlayici_hedef_ts"] = 0.0
            DURUM_TAKIP["zamanlayici_islem"] = ""
            if islem == "kapat":
                bilgisayari_kapat(gecikme_sn=5)
            elif islem == "kilitle":
                ekrani_kilitle()

        aralik = max(10, int(CONFIG.get("telemetri_aralik_sn", 30)))
        if simdi - son_telemetri_kayit >= aralik:
            son_telemetri_kayit = simdi
            anlik = _anlik_getir(0.0)
            log_ekle(
                f"Telemetri: CPU=%{anlik['cpu_yuzde']} ({anlik['cpu_sicaklik_c'] or 'N/A'}°C), "
                f"RAM=%{anlik['ram_yuzde']}, Durum={anlik['kullanici_durumu']}",
                kategori="TELEMETRI",
                olay="PERIYODIK_TELEMETRI",
                detay={
                    "cpu_yuzde": anlik["cpu_yuzde"],
                    "cpu_sicaklik_c": anlik["cpu_sicaklik_c"],
                    "ram_yuzde": anlik["ram_yuzde"],
                    "aktif_dk": round(DURUM_TAKIP["aktif_kullanim_sn"] / 60.0, 1),
                    "kilitli_dk": round(DURUM_TAKIP["kilitli_kalma_sn"] / 60.0, 1),
                },
                telemetri_ornegi_ekle=True,
            )


# ---- pencereler ----
def masaustu_kisayolu_ekle() -> tuple[bool, str]:
    if paketlenmis():
        hedef, arguman = Path(sys.executable), ""
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        hedef = pyw if pyw.exists() else Path(sys.executable)
        arguman = f'"{Path(__file__).resolve()}"'
    lnk = kisayol_olustur("Guard Security", hedef, arguman, Path(__file__).resolve().parent)
    if lnk:
        log_ekle(f"Masaustu kisayolu olusturuldu: {lnk}", kategori="SISTEM", olay="KISAYOL_OLUSTURULDU")
        return True, f"Kısayol masaüstüne eklendi: {lnk.name}"
    log_ekle("Masaustu kisayolu olusturulamadi.", kategori="HATA", olay="KISAYOL_HATA", seviye="UYARI")
    return False, "Kısayol oluşturulamadı."


RENK = {
    "zemin": "#eef1f4",
    "yuzey": "#fbfcfd",
    "yazi": "#16212c",
    "soluk": "#5b6a78",
    "cizgi": "#d3dae1",
    "vurgu": "#2b5d8c",
    "iyi": "#2e7d5b",
    "tehlike": "#b3261e",
}


def _pencere(baslik: str, boy: int):
    import tkinter as tk
    p = tk.Tk()
    p.title(baslik)
    p.geometry(f"470x{boy}")
    p.configure(bg=RENK["zemin"], padx=24, pady=20)
    p.resizable(False, False)
    p.attributes("-topmost", True)
    return tk, p


def _etiket(tk, ust, metin, boyut=10, renk="yazi", font="Segoe UI", **kw):
    return tk.Label(ust, text=metin, bg=RENK["zemin"], fg=RENK[renk], font=(font, boyut), **kw)


def _giris(tk, ust, satir, etiket, deger="", gizli=False):
    _etiket(tk, ust, etiket).grid(row=satir, column=0, sticky="w", pady=5)
    e = tk.Entry(
        ust,
        width=28,
        bg=RENK["yuzey"],
        fg=RENK["yazi"],
        relief="solid",
        bd=1,
        font=("Segoe UI", 10),
        show="*" if gizli else "",
    )
    e.insert(0, str(deger))
    e.grid(row=satir, column=1, padx=(12, 0), pady=5, ipady=3)
    return e


def _dugme(tk, ust, metin, komut, birincil=False):
    return tk.Button(
        ust,
        text=metin,
        command=komut,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=("Segoe UI", 10, "bold" if birincil else "normal"),
        padx=16,
        pady=6,
        bg=RENK["vurgu"] if birincil else RENK["cizgi"],
        fg="#ffffff" if birincil else RENK["yazi"],
    )


def _secenek(tk, ust, metin, degisken):
    return tk.Checkbutton(
        ust,
        text=metin,
        variable=degisken,
        bg=RENK["zemin"],
        fg=RENK["yazi"],
        selectcolor=RENK["yuzey"],
        activebackground=RENK["zemin"],
        font=("Segoe UI", 10),
        anchor="w",
    )


def kurulum_yardimcisi_ac(ilk_acilis: bool = False) -> None:
    try:
        from tkinter import messagebox
    except ImportError:
        return
    tk, p = _pencere("Guard ayarları", 460)

    _etiket(tk, p, "Guard ayarları", 15, font="Bahnschrift").pack(anchor="w")
    _etiket(tk, p, f"Veri klasörü: {KLASOR}", 8, "soluk").pack(anchor="w", pady=(2, 14))

    form = tk.Frame(p, bg=RENK["zemin"])
    form.pack(fill="x")
    ent_kullanici = _giris(tk, form, 0, "Kullanıcı adı", CONFIG.get("kullanici_adi", ""))
    ent_token = _giris(tk, form, 1, "Telegram bot token", CONFIG.get("telegram_bot_token", ""), gizli=True)
    ent_chat = _giris(tk, form, 2, "Telegram chat ID", CONFIG.get("telegram_chat_id", ""))
    ent_bosta = _giris(tk, form, 3, "Boşta kalınca kilitle (dk, 0 = kapalı)", CONFIG.get("bosta_islem_dk", 0))

    oto_kilit = tk.BooleanVar(value=bool(CONFIG.get("otomatik_kilit_nobeti", True)))
    kisayol = tk.BooleanVar(value=not CONFIG.get("kisayol_eklendi", False))
    _secenek(tk, p, "Ekran kilitlenince kamera nöbeti otomatik başlasın", oto_kilit).pack(fill="x", pady=(10, 0))
    _secenek(tk, p, "Masaüstüne kısayol ekle", kisayol).pack(fill="x")

    durum = _etiket(tk, p, "", 9)
    durum.pack(anchor="w", pady=(10, 0))

    def test_et():
        tkn, cid = ent_token.get().strip(), ent_chat.get().strip()
        if not tkn or not cid:
            messagebox.showwarning("Eksik bilgi", "Önce bot token ve chat ID girin.", parent=p)
            return
        durum.config(text="Telegram deneniyor...", fg=RENK["soluk"])
        p.update()
        ok = telegram_mesaj_gonder(
            f"Guard bağlantı testi başarılı.\nBilgisayar: {socket.gethostname()}\n{datetime.now():%Y-%m-%d %H:%M:%S}",
            token=tkn,
            chat_id=cid,
        )
        durum.config(
            text="Test mesajı gönderildi." if ok else "Gönderilemedi. Token veya chat ID hatalı olabilir.",
            fg=RENK["iyi"] if ok else RENK["tehlike"],
        )

    def kaydet():
        CONFIG["kullanici_adi"] = ent_kullanici.get().strip() or os.environ.get("USERNAME", "kullanici")
        CONFIG["telegram_bot_token"] = ent_token.get().strip()
        CONFIG["telegram_chat_id"] = ent_chat.get().strip()
        CONFIG["telegram_aktif"] = bool(CONFIG["telegram_bot_token"] and CONFIG["telegram_chat_id"])
        CONFIG["otomatik_kilit_nobeti"] = bool(oto_kilit.get())
        try:
            CONFIG["bosta_islem_dk"] = max(0, int(float(ent_bosta.get().strip() or 0)))
        except ValueError:
            CONFIG["bosta_islem_dk"] = 0

        ilk_kez = not CONFIG.get("ilk_kurulum_tamam", False)
        CONFIG["ilk_kurulum_tamam"] = True
        CONFIG["kurulum_tarihi"] = CONFIG.get("kurulum_tarihi") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kisayol_mesaji = ""
        if kisayol.get():
            ok, kisayol_mesaji = masaustu_kisayolu_ekle()
            CONFIG["kisayol_eklendi"] = ok
        config_kaydet(CONFIG)
        log_ekle(
            "Ayarlar kaydedildi.",
            kategori="SISTEM",
            olay="KURULUM_TAMAMLANDI",
            detay={"telegram_aktif": CONFIG["telegram_aktif"], "ilk_kurulum": ilk_kez},
        )

        if ilk_acilis or ilk_kez:
            durum.config(text="Kamera kontrol ediliyor...", fg=RENK["soluk"])
            p.update()
            _, mesaj = anlik_fotograf_cek_ve_kontrol_et("ILK_ACILIS")
            messagebox.showinfo("Hazır", "\n".join(m for m in (mesaj, kisayol_mesaji) if m), parent=p)
        elif kisayol_mesaji:
            messagebox.showinfo("Kısayol", kisayol_mesaji, parent=p)
        p.destroy()

    alt = tk.Frame(p, bg=RENK["zemin"])
    alt.pack(anchor="e", pady=(16, 0))
    _dugme(tk, alt, "Telegram'ı dene", test_et).pack(side="left", padx=(0, 8))
    _dugme(tk, alt, "Kaydet", kaydet, birincil=True).pack(side="left")
    p.mainloop()


def sure_ayar_penceresi_ac() -> None:
    try:
        from tkinter import messagebox
    except ImportError:
        return
    tk, p = _pencere("Zamanlayıcı", 330)

    _etiket(tk, p, "Zamanlayıcı", 15, font="Bahnschrift").pack(anchor="w")
    _etiket(tk, p, zamanlayici_metni_getir(), 9, "soluk").pack(anchor="w", pady=(2, 14))

    form = tk.Frame(p, bg=RENK["zemin"])
    form.pack(fill="x")
    ent_dk = _giris(tk, form, 0, "Süre (dakika)", 30)

    islem = tk.StringVar(value="kapat")
    for metin, deger in (
        ("Süre dolunca bilgisayarı kapat", "kapat"),
        ("Süre dolunca kilit ekranına dön", "kilitle"),
    ):
        tk.Radiobutton(
            p,
            text=metin,
            variable=islem,
            value=deger,
            bg=RENK["zemin"],
            fg=RENK["yazi"],
            selectcolor=RENK["yuzey"],
            activebackground=RENK["zemin"],
            anchor="w",
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=2)

    def baslat():
        try:
            dk = float(ent_dk.get().strip().replace(",", "."))
            if dk <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Süre hatalı", "0'dan büyük bir dakika girin.", parent=p)
            return
        zamanlayici_ayarla(dk, islem.get())
        messagebox.showinfo("Zamanlayıcı", zamanlayici_metni_getir(), parent=p)
        p.destroy()

    def iptal():
        zamanlayici_ayarla(0, "")
        messagebox.showinfo("Zamanlayıcı", "Aktif zamanlayıcı iptal edildi.", parent=p)
        p.destroy()

    alt = tk.Frame(p, bg=RENK["zemin"])
    alt.pack(anchor="e", pady=(16, 0))
    _dugme(tk, alt, "Zamanlayıcıyı iptal et", iptal).pack(side="left", padx=(0, 8))
    _dugme(tk, alt, "Başlat", baslat, birincil=True).pack(side="left")
    p.mainloop()


# ---- panel ve tepsi ----
def html_paneli_hazirla() -> Path:
    """Paketteki dashboard.html'i veri klasorune kopyalar."""
    for klasor in kaynak_adaylari():
        aday = klasor / "dashboard.html"
        if aday.exists() and aday.resolve() != DASHBOARD_HTML.resolve():
            try:
                shutil.copy2(aday, DASHBOARD_HTML)
                break
            except Exception:
                pass
    return DASHBOARD_HTML


def html_paneli_ac() -> None:
    html_yol = html_paneli_hazirla()
    log_ekle("HTML Telemetri ve Log paneli acildi.", kategori="SISTEM", olay="PANEL_ACILDI")
    if html_yol.exists():
        webbrowser.open(html_yol.as_uri())
    else:
        webbrowser.open(str(KLASOR))


def tepsi_ikonu_olustur():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=12, fill=(43, 93, 140, 255))
    d.ellipse((14, 14, 50, 50), outline=(255, 255, 255, 255), width=5)
    d.ellipse((26, 26, 38, 38), fill=(255, 255, 255, 255))
    return img


def sistem_tepsisi_baslat() -> None:
    """Sistem tepsisine ikonu ekler ve kontrol tuslarini sunar."""
    global _servis_calisiyor

    if pystray is None or Image is None:
        log_ekle(
            "UYARI: pystray veya Pillow kurulu degil! Sistem tepsisi yerine arka plan servisi olarak devam ediliyor.",
            kategori="UYARI",
            olay="TRAY_KUTUPHANE_YOK",
            seviye="UYARI",
        )
        while _servis_calisiyor:
            time.sleep(5)
        return

    def _panel_ac(icon, item):
        html_paneli_ac()

    def _simdi_kilitle(icon, item):
        threading.Thread(target=ekrani_kilitle, daemon=True).start()

    def _sure_ayarla(icon, item):
        threading.Thread(target=sure_ayar_penceresi_ac, daemon=True).start()

    def _hizli_zamanla(dk, islem):
        def _inner(icon, item):
            zamanlayici_ayarla(dk, islem)
        return _inner

    def _anlik_foto(icon, item):
        threading.Thread(target=anlik_fotograf_cek_ve_kontrol_et, args=("TEPSI_KONTROL",), daemon=True).start()

    def _yuz_kayit_ac(icon, item):
        def _calistir():
            try:
                from guard.yuz_kayit import yuz_kaydet
                yuz_kaydet(None, KLASOR)
            except Exception as e:
                log_ekle(f"Yuz kayit hatasi: {e}", kategori="HATA", olay="YUZ_KAYIT_HATA", seviye="HATA")
        threading.Thread(target=_calistir, daemon=True).start()

    def _ayarlar_ac(icon, item):
        threading.Thread(target=kurulum_yardimcisi_ac, kwargs={"ilk_acilis": False}, daemon=True).start()

    def _klasor_ac(icon, item):
        try:
            os.startfile(str(KLASOR))
        except Exception:
            webbrowser.open(str(KLASOR))

    def _kisayol(icon, item):
        threading.Thread(target=masaustu_kisayolu_ekle, daemon=True).start()

    def _hemen_kapat(icon, item):
        def _onayli_kapat():
            try:
                import tkinter as tk
                from tkinter import messagebox
                r = tk.Tk()
                r.withdraw()
                r.attributes("-topmost", True)
                onay = messagebox.askyesno(
                    "Bilgisayarı Kapat",
                    "Bilgisayarınız şimdi kapatılacak. Onaylıyor musunuz?",
                    parent=r,
                )
                r.destroy()
                if onay:
                    bilgisayari_kapat(gecikme_sn=0)
            except Exception:
                bilgisayari_kapat(gecikme_sn=0)
        threading.Thread(target=_onayli_kapat, daemon=True).start()

    def _cikis(icon, item):
        global _servis_calisiyor
        _servis_calisiyor = False
        log_ekle("Kullanici sistem tepsisinden cikis yapti.", kategori="SISTEM", olay="UYGULAMA_KAPANDI")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Paneli aç", _panel_ac, default=True),
        pystray.MenuItem("Ekranı kilitle", _simdi_kilitle),
        pystray.MenuItem("Zamanlayıcı ayarla...", _sure_ayarla),
        pystray.MenuItem(
            "Hızlı zamanlayıcı",
            pystray.Menu(
                pystray.MenuItem("15 dk sonra kilitle", _hizli_zamanla(15, "kilitle")),
                pystray.MenuItem("30 dk sonra kilitle", _hizli_zamanla(30, "kilitle")),
                pystray.MenuItem("15 dk sonra kapat", _hizli_zamanla(15, "kapat")),
                pystray.MenuItem("30 dk sonra kapat", _hizli_zamanla(30, "kapat")),
                pystray.MenuItem("60 dk sonra kapat", _hizli_zamanla(60, "kapat")),
                pystray.MenuItem("Zamanlayıcıyı iptal et", _hizli_zamanla(0, "")),
            ),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Anlık fotoğraf çek", _anlik_foto),
        pystray.MenuItem("Yüz kaydı ekle", _yuz_kayit_ac),
        pystray.MenuItem("Ayarlar", _ayarlar_ac),
        pystray.MenuItem("Masaüstü kısayolu oluştur", _kisayol),
        pystray.MenuItem("Veri klasörünü aç", _klasor_ac),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Bilgisayarı kapat", _hemen_kapat),
        pystray.MenuItem("Çıkış", _cikis),
    )

    ikon = pystray.Icon("GuardSecurity", tepsi_ikonu_olustur(), "Guard", menu)
    log_ekle("Sistem tepsisi (System Tray) ikonu aktif edildi.", kategori="SISTEM", olay="TRAY_AKTIF")
    ikon.run()


# ---- giris ----
def main() -> None:
    modu = sys.argv[1].lower() if len(sys.argv) > 1 else "tray"
    html_paneli_hazirla()
    temizle()

    log_ekle(
        f"Guard baslatildi (mod={modu}, klasor={KLASOR})",
        kategori="SISTEM",
        olay="BASLADI",
        detay={"mod": modu, "exe_mi": paketlenmis(), "klasor": str(KLASOR)},
        telemetri_ornegi_ekle=True,
    )

    try:
        if not CONFIG.get("ilk_kurulum_tamam", False) and modu in ("tray", "kurulum", ""):
            kurulum_yardimcisi_ac(ilk_acilis=True)

        if modu == "kurulum":
            kurulum_yardimcisi_ac(ilk_acilis=False)
        elif modu == "kisayol":
            print(masaustu_kisayolu_ekle()[1])
        elif modu == "test":
            kilit_dongusu(test=True)
            log_ekle("Test modu tamamlandi.", kategori="SISTEM", olay="TEST_BITTI")
        elif modu == "kilit":
            time.sleep(1)
            for f in (FLAG, UNLOCK, ESKI_FLAG, ESKI_UNLOCK):
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
            kilit_dongusu(test=False, otomatik_mod=False)
            log_ekle("Kilit modu tamamlandi.", kategori="SISTEM", olay="KILIT_BITTI")
        else:
            telemetri_thread = threading.Thread(target=arka_plan_telemetri_dongusu, daemon=True)
            telemetri_thread.start()
            sistem_tepsisi_baslat()
    except Exception:
        hata_metni = traceback.format_exc()
        log_ekle(f"Kritik hata:\n{hata_metni}", kategori="HATA", olay="KRITIK_HATA", seviye="KRITIK")
