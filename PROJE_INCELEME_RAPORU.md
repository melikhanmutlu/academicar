# AcademicAR — Proje İnceleme Raporu

> Kapsam: Backend (`app.py`, `auth.py`, `models.py`, `config.py`, `licensing.py`, `converters/`, `services/`, `worker.py`) ve Frontend (`templates/`, `static/`).
> Amaç: Problemli noktaların tespiti + iyileştirme/öneri listesi + önceliklendirilmiş eylem planı.

---

## 1. Genel Değerlendirme

Proje olgun ve düşünülmüş bir MVP. Güçlü yanları:

- **Net mimari ayrım**: web (enqueue) + worker (conversion) ayrımı, storage soyutlaması (`storage.py`), lisans katmanı (`licensing.py`).
- **Güvenlik refleksleri iyi**: CSRF (Flask-WTF), session fixation koruması (`_rotate_session`), `secure_filename`, sahiplik decorator'ları (`utils/security.py`), güvenli redirect kontrolü, rate limiting, güvenlik header'ları.
- **Uyumluluk/denetim**: zorunlu onay kutuları + `AuditLog` + consent IP/timestamp.
- **Dosya işlemlerinde atomiklik**: replace akışında `.new` dosyası + `os.replace` ile eski GLB korunuyor.

Ancak production'a dair **ciddi bir mimari çelişki**, birkaç **kesin bug**, performans ve tutarlılık sorunları var. Aşağıda önceliklendirilmiş.

---

## 2. Kritik Sorunlar (öncelik: yüksek)

### 2.1. Production'da web süreci yine de conversion çalıştırıyor — worker mimarisini deliyor
`@/Users/.../academic_ar/app.py:1184-1196`

```python
if app.config.get("TESTING") or app.config.get("DEV_INLINE_JOBS"):
    process_model_upload_job(app, **job_kwargs)
else:
    import threading
    thread = threading.Thread(target=process_model_upload_job, ...)
    thread.start()
```

- `CLAUDE.md` ve `README.md` açıkça "production web süreci conversion çalıştırmaz, sadece `ConversionJob` yazar" diyor. Ancak production yolunda (TESTING değil, DEV_INLINE_JOBS kapalı) kod **daemon thread başlatıp conversion'ı web sürecinde çalıştırıyor**.
- Aynı anda `worker.py` de `run_next_conversion_job()` ile aynı `pending` job'u alabilir → **çift işleme / yarış durumu (race condition)**. Job claim'i atomik değil (kod yorumunda da kabul edilmiş: `app.py:1216-1217`).
- Sonuç: Railway web container'ında CPU/RAM ağır trimesh işi, gunicorn timeout riski, ve worker ile çakışma.

**Öneri:** Production'da thread başlatma; sadece `ConversionJob` yaz ve dön. Worker tarafında job claim'i `SELECT ... FOR UPDATE SKIP LOCKED` (PostgreSQL) ile atomikleştir. Inline çalışma yalnızca `TESTING`/`DEV_INLINE_JOBS` için kalsın.

### 2.2. `last_replaced_at` — var olmayan kolona yazılıyor (sessiz bug)
`@/Users/.../academic_ar/app.py:3082`

```python
model.last_replaced_at = datetime.now(UTC)
```

- `Model3D` modelinde böyle bir kolon yok; gerçek alan `replaced_at` (`@/Users/.../academic_ar/models.py:107`).
- SQLAlchemy bunu kalıcı olmayan geçici bir instance attribute olarak set eder → hata vermez ama **`replaced_at` hiçbir zaman güncellenmez**. Replace zaman damgası kayboluyor.

**Öneri:** `model.replaced_at = datetime.now(UTC)` olarak düzelt.

### 2.3. Şema yönetimi karmaşası: `create_all` + Alembic + elle `ALTER TABLE`
`@/Users/.../academic_ar/app.py:135-144`, `app.py:412-449`

- Her açılışta `db.create_all()` çalışıyor, ayrıca Flask-Migrate var, ayrıca SQLite için elle `ALTER TABLE` (`ensure_sqlite_schema`), ayrıca PostgreSQL için **hardcoded migration id** ile `stamp_alembic_version_if_needed` (`'669b2de1fcd7'`).
- Bu üçlü kombinasyon kırılgan: migration'lar ile `create_all` çakışabilir, hardcoded stamp ileride migration eklenince yanlış duruma yol açar.

**Öneri:** Tek kaynak seç — Alembic. Production'da `flask db upgrade` ile şema kur, `create_all` ve elle ALTER/stamp mantığını kaldır. (Geçiş dönemi için stamp mantığını en azından idempotent ve sürüm-bağımsız hale getir.)

---

## 3. Güvenlik

### 3.1. CSP yok + tüm CDN bağımlılıkları dış kaynaktan, SRI'sız
`@/Users/.../academic_ar/base.html:22`, `viewer.html:12-13`

- `cdn.tailwindcss.com` (üretim için önerilmez, runtime'da JIT derler), `unpkg.com/@google/model-viewer`, Google Fonts — hepsi SRI olmadan. CDN ele geçirilirse XSS riski. `after_request` header setinde **Content-Security-Policy bilinçli olarak yok** (`app.py:118-130`).
- Tailwind CDN ayrıca her sayfada konsola "production'da kullanmayın" uyarısı basar ve performansı düşürür.

**Öneri:** Tailwind'i build adımıyla (CLI/PostCSS) statik CSS'e derle. model-viewer'ı pinned sürümle self-host veya SRI ekle. En azından temel bir CSP uygula (model-viewer + inline style ihtiyaçları test edilerek).

### 3.2. Sahte/otomatik "ödeme" gerçek plan yükseltmesi yapıyor
`@/Users/.../academic_ar/app.py:2318-2335`

- `/profile` POST'unda kullanıcı plan seçince doğrudan `Payment(status="paid")` oluşturuluyor; gerçek bir ödeme sağlayıcısı yok. MVP için kabul edilebilir ama **canlıya çıkarsa ücretli planlar bedava** verilir.

**Öneri:** Gerçek ödeme entegrasyonu (Iyzico/Stripe) gelene kadar bu akışı admin-only veya feature-flag arkasına al; en azından "development" provider'ının production'da çalışmasını engelle.

### 3.3. `serve_glb` indirme caydırma teknikleri ve gizlilik
`@/Users/.../academic_ar/app.py:1581-1602`, `viewer.html:335-345`

- Sağ tık/kısayol engelleme tamamen kozmetik (kodda da kabul edilmiş). GLB herkese açık `/files/<uuid>/model.glb` üzerinden erişilebilir; UUID bilen herkes erişir. Lisans `active` ise korunuyor, bu makul; ancak "no-store" cache başlığı her görüntülemede yeniden indirme demek (bkz. Performans).

**Öneri:** Beklenti yönetimi: bu bir koruma değil. İstenirse imzalı/expire olan URL'ler veya token bazlı erişim.

### 3.4. `papers_fetch_metadata` request thread'ini bloklar
`@/Users/.../academic_ar/app.py:2530-2656`

- Crossref/PubMed'e `urllib` ile senkron 5 sn timeout'lu istek; web thread'ini bloklar. Yoğunlukta gthread havuzunu tüketebilir. Sabit endpoint olduğu için SSRF riski düşük ama timeout/retry/sınır yok.

**Öneri:** İstek başına kısa timeout korunsun; mümkünse client-side fetch veya ayrı bir hafif servis. En azından `requests` ile bağlantı+okuma timeout'unu netleştir.

### 3.5. Küçük güvenlik notları
- **Admin e-posta hardcoded**: `DEFAULT_ADMIN_EMAILS = {"melikhanmutlu@gmail.com"}` (`config.py:27`). Kaynak kodda kişisel admin tanımı; env'e taşınmalı.
- `X-Forwarded-For` doğru ele alınmış (ProxyFix hop sayısı), iyi.

---

## 4. Performans ve Ölçeklenebilirlik

### 4.1. Admin dashboard tüm tabloları belleğe çekiyor
`@/Users/.../academic_ar/app.py:1795,1842-1843,1866,1946`

- `sum(1 for model in Model3D.query.all() ...)`, `Model3D.query.all()` içinde dosya boyutu okuma, `QRLink.query.all()` üzerinden Python döngüleri, her model için `convert ... status` hesabı. Tablolar büyüdükçe **bellek + sorgu patlaması** (N+1).
- `model_access_status` her satır için Python'da; SQL'e taşınabilir.

**Öneri:** Sayımları `func.count` + `group_by` ile DB'de yap; "active models" gibi türev durumları materialize et veya indexli kolonlardan hesapla. Sayfalama ekle.

### 4.2. GLB için `Cache-Control: no-store`
`@/Users/.../academic_ar/app.py:1600`

- Her viewer açılışında büyük GLB yeniden indirilir. AR/3D dosyaları büyük olduğundan bant genişliği ve gecikme maliyeti yüksek.

**Öneri:** İçeriği immutable kabul edip (`model.glb` versiyonlanıyor) `private, max-age=...` veya ETag ile koşullu GET. İndirme caydırma amacı varsa bunu UX'ten ayır.

### 4.3. `format_model_dimensions_cm` context processor'da trimesh yüklüyor
`@/Users/.../academic_ar/app.py:232-251` (inject_globals üzerinden her template'e açık)

- Çağrıldığında her seferinde GLB'yi diskten trimesh ile parse eder. Liste/detay sayfalarında çok model varsa pahalı.

**Öneri:** Boyutu conversion sırasında hesaplayıp `Model3D`'de sakla (yeni kolon), template'te hazır oku.

### 4.4. Daemon thread + SQLite
- Local'de inline çalıştığı için sorun yok, ama thread yolunda SQLite yazma kilidi (`database is locked`) yaşanabilir.

---

## 5. Mimari / Kod Kalitesi

### 5.1. Ölü ve tutarsız kod
- `paper_is_expired` her zaman `False` döndürüyor (`licensing.py:151-152`) ama `app.py` içinde birçok yerde çağrılıyor (`qr_image`, `paper_public`, admin sayımları). Tüm bu kontroller ölü dal. Niyet "yayınlar expire olmasın" ise kontroller kaldırılmalı; değilse mantık eksik.
- `generate_qr` (`app.py:804`) artık `generate_model_qr` ile değiştirilmiş görünüyor — kullanılmıyorsa kaldır.
- `Paper.package_type`, `payment_status`, `expires_at` artık hep `model_based`/`None`'a sabitleniyor (`paper_new` ve `sync_paper_entitlements`); model-bazlı lisanslamaya geçişten kalan legacy alanlar. Şema/route'larda kafa karışıklığı yaratıyor.

### 5.2. Plan kümeleri tutarsız
- `licensing.LICENSE_PLANS`: `free / academic / extended_archive / institutional`.
- `User.plan` pratikte `free / academic` (admin update yalnız bunları kabul: `app.py:2154`).
- `/profile` ise `free / academic / extended_archive` kabul ediyor (`app.py:2310`).
- Bu üç farklı küme tek bir yerden (enum/sabit) beslenmeli.

### 5.3. `app.py` 3246 satır — tek dosya
- Route'lar, yardımcılar, conversion orkestrasyonu, admin, hepsi tek dosyada. Bakım ve test zorlaşıyor.

**Öneri:** Blueprint'lere böl (`auth` zaten ayrı): `papers`, `models`, `admin`, `viewer/public`, `account`. Conversion orkestrasyonunu `services/`'e taşı.

### 5.4. `aspose-3d` (USDZ) bağımlılığı `requirements.txt`'te yok
`@/Users/.../academic_ar/converters/stl_converter.py:64-91`

- Kod optional olarak yakalıyor (iyi), ama production'da USDZ hiçbir zaman üretilmeyecek → iOS Quick Look için davranışı netleştir. OBJ/FBX için Node CLI'lar da `package.json`'a bağlı; Railway nixpacks'te Node kurulumunu doğrula.

---

## 6. Frontend / UX

### 6.1. Flash mesajları kategoriye göre stillenmiyor
`@/Users/.../academic_ar/base.html:95-105`

- `danger`, `success`, `warning`, `info` hepsi aynı nötr gri kutuda render ediliyor. Hata ile başarı görsel olarak ayırt edilemiyor → kullanıcı hatayı fark etmez.

**Öneri:** `cat` değerine göre renk/ikon (kırmızı/yeşil/sarı) ekle.

### 6.2. Inline stil yoğunluğu
- `base.html` ve birçok template'te bol `style="..."`. Tasarım sistemi CSS değişkenleriyle var; inline stiller bakımı zorlaştırıyor ve CSP'yi (style-src) imkansızlaştırıyor.

### 6.3. Erişilebilirlik
- Viewer'da kontrol butonları iyi etiketli ama mobil menü/aria durumları kısmi. `aria-expanded` string set ediliyor (`base.html:169`) — `String(active)` daha güvenli.
- Renk kontrastı: `text-white/40` gibi düşük opaklıklar viewer metadata panelinde WCAG kontrastını zorlayabilir.

### 6.4. Tek noktadan hata (CDN)
- `unpkg`/`cdn.tailwindcss.com` erişilemezse viewer ve tüm sayfa stilleri çöker. Self-host önerilir (bkz. 3.1).

---

## 7. Test ve Operasyon

- `tests/` kapsamı iyi (auth, flows 56KB, converters, storage, security). Ancak:
  - **Production enqueue/worker yarış durumu** ve thread yolu test edilmiyor (TESTING hep inline).
  - `replaced_at` bug'ı test edilmediği için yakalanmamış.
- **Loglama**: `server.err.log` / `server.out.log` repoda. `.gitignore`'da `*.log` var ama dosyalar takip ediliyorsa temizlenmeli.
- **Repoda büyük binary'ler**: `AcademicAR_Sunumu.pdf` (~71MB), `thesis.pdf`, çeşitli PDF/HTML. Git geçmişini şişiriyor → LFS veya repo dışına.
- `.env` `.gitignore`'da (iyi). `DEFAULT_ADMIN_EMAILS` yine de koda gömülü.

---

## 8. Önceliklendirilmiş Eylem Planı

### Faz 1 — Kritik düzeltmeler (hemen) — ✅ TAMAMLANDI
- [x] **2.2** `last_replaced_at` → `replaced_at` düzeltildi (`app.py:3082`).
- [x] **2.1** Production'da conversion thread'i kaldırıldı; yalnızca enqueue + worker. Job claim PostgreSQL/MySQL'de `FOR UPDATE SKIP LOCKED` ile atomikleştirildi (`app.py` `enqueue_conversion_job` / `run_next_conversion_job`).
- [x] **3.2** Sahte ödeme akışı `Config.ALLOW_DEV_PAYMENTS` flag'ine bağlandı; production'da varsayılan KAPALI (`config.py`, `app.py` profile route).

### Faz 2 — Güvenlik & şema sağlamlaştırma — ✅ TAMAMLANDI (2.3 güvenli kısmi)
- [x] **2.3** *(güvenli kısmi yaklaşım — bkz. not)* `stamp_alembic_version_if_needed` hardcoded revizyon yerine **dinamik head**'e damgalıyor; `ensure_sqlite_schema` idempotent olarak belgelendi. `create_all` baseline olarak korundu. **Tam Alembic-only baseline ileri bir göç işi olarak bırakıldı** (mevcut "initial" migration tabloları oluşturmuyor, sadece index ekliyor).
- [x] **3.1** model-viewer tüm template'lerde tek sürüme sabitlendi (unpkg `4.2.0`); temel **CSP** eklendi (`Config.CSP_ENABLED`/`CSP_REPORT_ONLY`, `app.py`). *Tailwind self-host build'i takip işi olarak kaldı (build pipeline + tarayıcı doğrulaması gerektirir).*
- [x] **3.5** Admin e-postaları koddan kaldırıldı; yalnızca `ADMIN_EMAILS`/`DEFAULT_ADMIN_EMAILS` env'inden okunuyor (`config.py`, `.env.example`).

> **Doğrulama tavsiyesi (3.1):** Production'a çıkmadan önce 3D viewer + AR'ı tarayıcıda test edin. CSP bir şeyi kırarsa geçici olarak `CSP_REPORT_ONLY=1` (sadece raporla) veya `CSP_ENABLED=0` (devre dışı) ile esneyebilirsiniz.

### Faz 3 — Performans — ✅ TAMAMLANDI
- [x] **4.1** Admin dashboard'daki tam-tablo Python döngüleri SQL agregasyonuna çevrildi (`active_models`, `expired_qr_count`, `failed_format_counts`); orphan "expected files" yalnızca gerekli kolonları yüklüyor; pahalı dosya sistemi taraması (`os.walk` × 4 + orphan tespiti) yalnızca `overview`/`storage` sayfalarında çalışacak şekilde sınırlandırıldı. (Not: tam sayfalama UI'si yerine listeler zaten `.limit()` ile sınırlı; sayfalama ileri iyileştirme olarak kaldı. Günlük/aylık trend COUNT sorgularını sayfaya bağlamak da olası bir ek optimizasyon.)
- [x] **4.3** Model boyutları conversion sırasında bir kez ölçülüp `Model3D.dimensions_cm` kolonunda saklanıyor (`compute_glb_dimensions_cm`); listeleme/detay sayfaları artık her istekte trimesh ile GLB parse etmiyor. Eski kayıtlar için lazy fallback korundu; SQLite için idempotent ALTER eklendi.
- [x] **4.2** `serve_glb` artık koşullu GET destekliyor (`conditional=True`, ETag/Last-Modified) ve `Cache-Control: private, no-cache` ile revalidation yapıyor — tekrar görüntülemede 304 dönüp büyük GLB'yi yeniden indirmiyor; replace/appearance güncellemesi ETag'i değiştirip cache'i geçersiz kılıyor.

### Faz 4 — Bakım & kod kalitesi — kısmen ✅
- [x] **5.1** Ölü fonksiyonlar kaldırıldı: `generate_qr`, `sync_paper_entitlements`, `package_expires_at`. (Not: `paper_is_expired` guard'ları zararsız ileri-hook olarak korundu; legacy `Paper` kolonları DB migration gerektirdiği için kaldırılmadı.)
- [x] **5.2** Kullanıcı planları tek kaynaktan: `licensing.USER_SELECTABLE_PLAN_KEYS` + `is_valid_user_plan()`; profile, admin plan güncelleme ve admin filtre artık aynı kümeyi kullanıyor (admin artık `extended_archive` de atayabiliyor/filtreleyebiliyor).
- [x] **6.1** Flash mesajları kategoriye göre stillendi: `.flash-*` sınıfları (`style.css`) + `base.html` (success/danger/warning/info renkleri, `role="alert"`). Inline Jinja-in-CSS kaldırıldı.
- [x] **7** Log dosyaları zaten git'te takip edilmiyor (temiz). **Büyük PDF'ler (`AcademicAR_Sunumu.pdf` ~71MB vb.) takip ediliyor** — git history rewrite / LFS göçü force-push gerektiren bilinçli bir karar olduğundan otomatik yapılmadı; **öneri:** bu dosyaları `git rm --cached` + `.gitignore` ile takipten çıkarın veya Git LFS'e taşıyın.
- [ ] **5.3** `app.py`'yi blueprint'lere böl — *büyük ve riskli yapısal refactor (3373 satır); ayrı, odaklı bir çalışma olarak ele alınması önerilir.*

---

## 9. Hızlı Kazanımlar (tek satır / küçük dokunuş)
- `app.py:3082` `replaced_at` düzeltmesi.
- `base.html:95-105` flash renklendirme.
- `config.py:27` admin e-posta env'e.
- `app.py:804` kullanılmayan `generate_qr` kaldırma.

---

*Not: Bu rapor statik inceleme sonucudur; düzeltmeler uygulanırken mevcut testlerin (`python -m pytest tests`) yeşil kalması ve `DESIGN.md`'deki görsel dil kurallarının korunması önerilir.*
