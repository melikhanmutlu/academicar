# AcademicAR

AcademicAR, akademik araştırmacıların tez/makale çalışmalarına 3D model ekleyebilmesi için geliştirilmiş bir Flask uygulamasıdır. Kullanıcı STL model yükler, sistem modeli GLB formatına dönüştürür, kalıcı bir görüntüleme bağlantısı ve QR kodu üretir. Okuyucu bu bağlantıdan modeli 3D olarak inceleyebilir ve destekleyen cihazlarda AR modunda açabilir.

## Mevcut Özellikler

- E-posta/şifre ile kayıt ve giriş
- Opsiyonel Google OAuth girişi
- Kullanıcı paneli
- Tez/makale oluşturma ve silme
- Opsiyonel PDF yükleme
- PDF dosyaları için kullanıcıya özel erişim
- STL doğrulama ve STL → GLB dönüşümü
- Her model için kalıcı public viewer linki
- QR kodu üretme, yazdırma ve PNG indirme
- Model adı/açıklaması düzenleme
- CSRF koruması, güvenli redirect, hata sayfaları
- Upload rate limit
- Pytest test paketi

## Lokal Kurulum

Python 3.12 önerilir.

```bash
cd academic_ar
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

## Ortam Değişkenleri

```bash
cp .env.example .env
```

Geliştirme ortamında `SECRET_KEY` boş bırakılırsa varsayılan dev anahtarı kullanılır. `APP_ENV=pilot`, `APP_ENV=production` veya `FLASK_ENV=production` kullanıldığında gerçek bir `SECRET_KEY` zorunludur.

Örnek güvenli anahtar:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Google OAuth kullanmak için:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Google Cloud Console tarafında redirect URI:

```text
http://localhost:5000/auth/google/callback
```

## Çalıştırma

Tek komut:

```bash
python app.py
```

Tarayıcıdan:

```text
http://127.0.0.1:5000/
```

## Test

```bash
python -m py_compile app.py auth.py models.py config.py converters/base_converter.py converters/stl_converter.py
python -m pytest tests -p no:cacheprovider
```

Windows üzerinde bazı kilitli pytest geçici klasörleri oluşursa `.gitignore` bunları dışarıda bırakır. Test runtime verileri `tests_runtime/` altında üretilir.

## Kullanıcı Akışı

1. Kullanıcı landing page’den kayıt olur veya giriş yapar.
2. Panelden yeni tez/makale oluşturur.
3. Başlık, yazarlar, yıl, alan, kurum, DOI, özet ve opsiyonel PDF girer.
4. Tez detayında STL model yükler.
5. Sistem dosyayı doğrular, GLB’ye dönüştürür ve QR kodu üretir.
6. Kullanıcı public viewer linkini açar veya QR sayfasını yazdırır.
7. Dış kullanıcı QR/viewer linkiyle modele giriş yapmadan erişir.
8. PDF dosyası yalnızca tez sahibi tarafından indirilebilir.

## Proje Yapısı

```text
academic_ar/
├── app.py              # Flask app ve route'lar
├── auth.py             # Auth blueprint
├── models.py           # User, Paper, Model3D modelleri
├── config.py           # Ortam ve uygulama ayarları
├── converters/         # STL → GLB dönüştürücü
├── templates/          # Jinja2 şablonları
├── static/             # CSS / JS
├── tests/              # Pytest testleri
├── uploads/            # Runtime STL geçici dosyaları
├── converted/          # Runtime GLB dosyaları
├── qr_codes/           # Runtime QR görselleri
└── pdfs/               # Runtime PDF dosyaları
```

## Sonraki Faz

- Async dönüşüm job sistemi
- Upload progress yüzdesi
- Model thumbnail/preview
- Draco mesh compression
- Docker/Nginx/HTTPS deployment
- Redis tabanlı production rate limit
