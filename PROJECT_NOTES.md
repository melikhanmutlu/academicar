# AcademicAR — Proje Notları & Yapılandırma Rehberi

> Bu dosya projeyi devralan herhangi bir geliştirici veya yapay zeka aracının
> sistemi hızla anlayabilmesi için tutulur. Kritik bir değişiklik yapıldığında
> ilgili bölümü güncelleyin ve git'e commit edin.
>
> **Son güncelleme:** 2026-06-02
> **Güncelleyen:** melikhanmutlu

---

## 1. Proje Kimliği

| Alan | Değer |
|------|-------|
| Proje adı | AcademicAR |
| Amaç | Araştırmacıların 3D/AR modellerini akademik yayınlarla paylaşması |
| Sahip / Ana geliştirici | Melikhan Mutlu (`melikhanmutlu@gmail.com`) |
| GitHub repo | `https://github.com/melikhanmutlu/academicar` |
| Ana branch | `main` |
| Production branch | `v5` (Railway bu branch'i izliyor) |
| Canlı domain | `https://academicar.com` |
| Admin e-posta | `melikhanmutlu@gmail.com` (kod: `config.py → DEFAULT_ADMIN_EMAILS`) |

---

## 2. Hosting & Altyapı

### Railway (ana platform)
- **Platform:** [railway.app](https://railway.app)
- **Organizasyon:** melikhanmutlu (kişisel)
- **Proje:** `academicar` (Railway projesi)
- **Servisler:**
  - `web` — Gunicorn Flask uygulaması (`app.py`)
  - `worker` — Arka plan dönüşüm işçisi (`worker.py`)
- **Veritabanı:** Railway PostgreSQL eklentisi (aynı proje içinde)
- **Volume:** Railway Volume → `STORAGE_ROOT` env ile mount edilmiş
  (yüklenen dosyalar, GLB'ler, PDF'ler, QR kodları burada saklanır)

> ⚠️ Volume mount edilmezse her deploy'da tüm kullanıcı dosyaları silinir.
> Bu projenin en kritik production riskidir.

### Domain
- **Kayıt yeri:** *(buraya domain registrar'ı yazın, örn. Namecheap / GoDaddy)*
- **DNS:** Railway'e yönlendirilmiş (CNAME)
- **SSL:** Railway otomatik sağlıyor (Let's Encrypt)

---

## 3. Ortam Değişkenleri (Environment Variables)

Tüm değişkenler **Railway → Servis → Variables** sekmesinde tanımlanır.
Yerel geliştirme için `.env` dosyası kullanılır (`.env.example` şablondur).

### Zorunlu Değişkenler

| Değişken | Açıklama | Örnek / Not |
|----------|----------|-------------|
| `APP_ENV` | Ortam | `production` |
| `SECRET_KEY` | Flask session/CSRF imza anahtarı | Uzun rastgele string, **asla paylaşma** |
| `DATABASE_URL` | PostgreSQL bağlantı URL'i | Railway otomatik inject eder |
| `SITE_URL` | Sitenin genel erişim URL'i | `https://academicar.com` |
| `STORAGE_ROOT` | Yüklenen dosyaların disk yolu | `/data` (Railway Volume mount noktası) |

### Google OAuth

| Değişken | Açıklama |
|----------|----------|
| `GOOGLE_CLIENT_ID` | Google Cloud Console → AcademicAR OAuth client |
| `GOOGLE_CLIENT_SECRET` | Aynı client'ın secret'ı |

**Google Cloud Console bilgileri:**
- Proje adı: `AcademicAR`
- Console URL: [console.cloud.google.com](https://console.cloud.google.com) → Proje: `AcademicAR`
- OAuth client adı: `AcademicAR`
- Authorized JavaScript origins: `https://academicar.com`
- Authorized redirect URI: `https://academicar.com/auth/google/callback`
- Consent screen durumu: **Testing** → herkese açmak için "Publish" gerekir
  (Google Cloud → **Audience** → Publishing status → Production)

#### 🔄 Google OAuth bilgilerini değiştirmeniz gerekirse:
1. [console.cloud.google.com](https://console.cloud.google.com) → `AcademicAR` projesi
2. **Google Auth Platform → Clients → AcademicAR** (kalem ikonu)
3. Authorized origins / redirect URI'leri yeni domain ile güncelleyin
4. Gerekirse yeni Client Secret oluşturun (`Add secret`)
5. Railway'de `GOOGLE_CLIENT_ID` ve `GOOGLE_CLIENT_SECRET` değişkenlerini güncelleyin
6. `SITE_URL` değişkenini de yeni domain ile güncelleyin

### Rate Limiting
| Değişken | Açıklama | Varsayılan |
|----------|----------|-----------|
| `RATELIMIT_STORAGE_URI` | Redis URL (prod'da gerekli) | Memory (tek instance için yeterli) |
| `REDIS_URL` | Railway Redis eklentisi URL'i | Railway otomatik inject eder |

### E-posta (İsteğe Bağlı)
E-posta değişimi doğrulaması için SMTP ayarları. Ayarlanmazsa link log'a yazılır.

| Değişken | Açıklama |
|----------|----------|
| `MAIL_SERVER` | SMTP sunucusu (örn. `smtp.gmail.com`) |
| `MAIL_PORT` | Port (varsayılan: `587`) |
| `MAIL_USE_TLS` | TLS (varsayılan: `1`) |
| `MAIL_USERNAME` | SMTP kullanıcı adı |
| `MAIL_PASSWORD` | SMTP şifresi / uygulama şifresi |
| `MAIL_DEFAULT_SENDER` | Gönderici adresi (örn. `noreply@academicar.com`) |
| `CONTACT_EMAIL` | Crossref/PubMed API User-Agent'ında kullanılır |

### Diğer Önemli Değişkenler
| Değişken | Açıklama | Varsayılan |
|----------|----------|-----------|
| `MAX_MESH_FACES` | STL dönüşümünde izin verilen max triangle sayısı | `2000000` |
| `MAX_MESH_VERTICES` | STL dönüşümünde izin verilen max vertex sayısı | `2000000` |
| `EMBED_FRAME_ANCESTORS` | Viewer'ın iframe'e gömülmesine izin verilen originler | `*` |
| `SESSION_LIFETIME_DAYS` | Oturum süresi (gün) | `14` |
| `PASSWORD_MIN_LENGTH` | Minimum şifre uzunluğu | `8` |
| `CSP_ENABLED` | Content Security Policy aktif mi | `1` |

---

## 4. Veritabanı

- **Production:** PostgreSQL (Railway managed)
- **Development:** SQLite (`academic_ar_dev.db`, git'e dahil değil)
- **Migration aracı:** Alembic + Flask-Migrate
- **Migration komutları:**
  ```bash
  flask db migrate -m "açıklama"
  flask db upgrade
  ```
- Migration dosyaları: `migrations/versions/`

---

## 5. Branch Stratejisi

| Branch | Amaç |
|--------|------|
| `main` | Stabil referans |
| `v4` | Önceki stabil versiyon (yedek) |
| `v5` | **Aktif production branch** — Railway bu branch'i izliyor |

> Yeni özellik geliştirirken `v5`'ten dal açın, test edip `v5`'e merge edin.
> Railway otomatik deploy eder.

---

## 6. Admin Paneli

- **URL:** `https://academicar.com/admin/`
- **Erişim:** Sadece `DEFAULT_ADMIN_EMAILS` listesindeki kullanıcılar
- **Kod konumu:** `config.py → DEFAULT_ADMIN_EMAILS`
- **Şu anki admin:** `melikhanmutlu@gmail.com`

#### 🔄 Admin e-postasını değiştirmeniz gerekirse:
1. `config.py` → `DEFAULT_ADMIN_EMAILS` setini güncelleyin
2. Commit + push yapın
3. Railway otomatik deploy eder
4. Yeni e-posta ile giriş yapıldığında admin yetkisi otomatik atanır

---

## 7. 3D Model Dönüşüm Pipeline

| Format | Yöntem | Gereksinim |
|--------|--------|-----------|
| STL → GLB | Pure Python (`converters/stl_converter.py`) | Sadece `trimesh` |
| GLB | Doğrudan kabul | — |
| OBJ → GLB | Node CLI `obj2gltf` | `npm install` gerekli |
| FBX → GLB | Node CLI `fbx2gltf` | `npm install` gerekli |
| GLB → USDZ (iOS) | `aspose-3d` (opsiyonel) | Lisanslı paket, kurulu değilse model-viewer fallback |

Worker servisi (`worker.py`) Railway'de ayrı bir servis olarak çalışır.
Dönüşüm işleri `ConversionJob` tablosunda takip edilir.

---

## 8. Ödeme & Plan Sistemi

- Şu an aktif bir ödeme entegrasyonu **yok** (Iyzico/Stripe planlanıyor)
- `ALLOW_DEV_PAYMENTS=1` ile geliştirme ortamında test ödemeleri simüle edilebilir
- Plan seviyeleri: `free` → `academic` → `extended_archive`
- Plan yönetimi: Admin paneli → kullanıcı düzenleme

---

## 9. Bilinen Açık Maddeler

> Ayrıntılı bulgu listesi: `KAPSAMLI_REVIEW_RAPORU.md`

| Öncelik | Madde | Durum |
|---------|-------|-------|
| 🔴 Kritik | Object storage (S3/R2/GCS) — dosyalar şu an Railway Volume'da | Açık |
| 🟠 Yüksek | Tailwind play CDN → build-time derlemeye geçiş | Açık |
| 🟠 Yüksek | model-viewer CDN → self-host | Açık |
| 🟡 Orta | `app.py` monolitini Blueprint'lere bölme | Açık |
| 🟡 Orta | Dashboard sunucu tarafı pagination | Açık |
| 🟡 Düşük | Büyük PDF'leri Git LFS'e taşıma | Açık |

---

## 10. Sık Yapılan Değişiklikler — Hızlı Kılavuz

### Domain değiştirme
1. Yeni domain'i Railway'de tanımlayın (Settings → Domains)
2. DNS kayıtlarını güncelleyin
3. Railway env: `SITE_URL=https://yenidomain.com`
4. Google Cloud Console → OAuth client → Authorized origins + redirect URI güncelle
5. `config.py → DEFAULT_ADMIN_EMAILS` etkilenmez (e-posta bazlı)

### Google OAuth e-postasını / hesabını değiştirme
1. Google Cloud Console → `AcademicAR` projesi → oturum açan hesabı değiştirmek için proje sahipliğini devredin
2. Yeni `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` oluşturun
3. Railway env'i güncelleyin

### Yeni admin kullanıcı ekleme
1. `config.py` → `DEFAULT_ADMIN_EMAILS = {"melikhanmutlu@gmail.com", "yeni@email.com"}`
2. Commit + push → Railway deploy

### Şifre / Secret Key sıfırlama
1. Railway env: `SECRET_KEY` değerini değiştirin
2. ⚠️ Tüm aktif kullanıcı oturumları geçersiz olur

---

## 11. Geliştirme Ortamı Kurulumu

```bash
# Repo'yu klonla
git clone https://github.com/melikhanmutlu/academicar.git
cd academicar
git checkout v5

# Python ortamı
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Bağımlılıklar
pip install -r requirements.txt
npm install

# Ortam değişkenleri
cp .env.example .env
# .env dosyasını düzenleyin

# Uygulamayı başlat
python run_local_server.py
# → http://localhost:5000

# Testler
python -m pytest tests/ -v
```

---

*Bu dosya `PROJECT_NOTES.md` olarak repo kökünde tutulur ve git'e commit edilir.*
*Credential değerleri buraya yazılmaz — sadece nerede bulunacağı belirtilir.*
