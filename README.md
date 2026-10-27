# Guard

Windows kilit ekranında kamera nöbeti tutar. Tanıdık yüzleri ayırt eder, yabancı yüz ve yanlış şifre denemelerini kaydeder, Telegram’a fotoğraf gönderir. Boşta kalınca kilitleme ve basit telemetri de var.

Bunu kendi makinemde, sınıf/ev bilgisayarında “biri oturdu mu” diye bakmak için yazdım. Ağır bir güvenlik ürünü değil; pratik bir nöbetçi.

## Kurulum

```bash
pip install -r requirements.txt
# YuNet + SFace onnx dosyalarını src/guard yanında modeller/ altına koy
python -m guard
```

Veriler `%APPDATA%\GuardSecurity` altında tutulur. İlk açılışta Telegram bot token / chat id istenir.

## Komutlar

| Komut | Ne yapar |
|-------|----------|
| `python -m guard` | Sistem tepsisi + arka plan nöbeti |
| `python -m guard test` | 15 saniyelik test nöbeti |
| `python -m guard kilit` | Sadece kilit nöbeti |
| `python -m guard kurulum` | Ayar penceresi |
| `python -m guard kisayol` | Masaüstü kısayolu |

Tek tık indirme için `installer/kurulum.py` (standart kütüphane, release’ten exe + modeller çeker).

## Not

Yüz modelleri depoda yok (boyut). OpenCV Zoo’dan indirip `modeller/` klasörüne koy.
