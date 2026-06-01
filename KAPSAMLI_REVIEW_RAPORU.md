# AcademicAR — Uçtan Uca Full-Stack Review Raporu

> Senior full-stack bakış açısıyla; güvenlik, backend, convert pipeline, viewer/AR,
> dashboard/profil/publication akışları, veri modeli, performans, frontend ve
> production hazırlık başlıklarında detaylı inceleme. Her bulgu **önem derecesi**,
> **konum** ve **öneri** ile listelenmiştir.
>
> İnceleme kapsamı: `app.py` (3384 satır), `config.py`, `models.py`, `auth.py`,
> `licensing.py`, `utils/security.py`, `services/storage_service.py`, `storage.py`,
> `converters/*`, `worker.py` ve `templates/*` (viewer, dashboard, profile,
> paper_new, paper_detail, base).

---

## 0. Yönetici Özeti

Proje **olgun ve iyi yapılandırılmış** bir MVP. Önceki review'da kapatılan kritik
maddeler (atomik job claim, CSP, dinamik Alembic stamp, dimensions cache, dev-payment
flag) yerinde. Mimari katmanları temiz, audit log kapsamlı, CSRF/oturum/OAuth
güvenliği büyük ölçüde doğru kurulmuş.

Buna karşılık **production'a çıkış öncesi mutlaka ele alınması gereken** birkaç konu var:

- **Kalıcı depolama yok** — dosyalar Railway/yerel diskte; yeniden deploy'da kaybolur. *(En kritik production riski.)*
- **Login brute-force koruması yok** — `/auth/login`'de rate limit / lockout yok.
- **Gizli (private) publication modelleri ve metadata'sı URL'i bilen herkese açık** — `view_model`/`serve_glb` yalnızca lisans/işleme durumunu kontrol ediyor, `paper.is_public`'i değil.
- **Profil istatistikleri ve plan mesajları gerçek davranışla tutarsız** — `package_type` her zaman `model_based` set ediliyor; "3 gün / 3 yıl link süresi" mesajları uygulanmıyor.

Aşağıda tüm bulgular önceliklendirilmiştir; Bölüm 12'de faz faz aksiyon planı var.

**Önem dağılımı:** Kritik: 1 · Yüksek: 4 · Orta: 8 · Düşük: 7 · İyi uygulamalar: Bölüm 11.

---

## 1. Mimari Genel Bakış

| Katman | Teknoloji | Not |
|---|---|---|
| Web framework | Flask (app factory `create_app`) | Tek dosya monolit (`app.py` 3384 satır) |
| ORM / DB | SQLAlchemy + Flask-Migrate; PostgreSQL (prod) / SQLite (dev) | `db.create_all()` baseline + Alembic artımlı |
| Auth | Flask-Login + Authlib (Google OAuth) | Email/şifre + Google |
| Asenkron iş | DB-backed `ConversionJob` + ayrı `worker.py` | Atomik claim (`FOR UPDATE SKIP LOCKED`) |
| Convert | trimesh (STL), Node CLIs (OBJ/FBX), pygltflib enrichment | USDZ opsiyonel (aspose-3d) |
| Görüntüleme | `@google/model-viewer` (unpkg CDN) + WebXR/SceneViewer/QuickLook | |
| Stil | Tailwind **play CDN** + `static/css/style.css` | Runtime CDN bağımlılığı |
| Rate limit | Flask-Limiter (Redis prod / memory dev) | Yalnızca upload uçlarında |

**Veri akışı (model upload):** `paper_new`/`upload_model` → `_create_model_for_paper`
(kaydet + arşivle + `ConversionJob`) → `enqueue_conversion_job` → (prod) worker
`run_next_conversion_job` → `process_model_upload_job` (convert + enrich + USDZ + QR
+ dimensions) → `paper_detail` polling ile durum güncelleme.

---

## 2. Güvenlik Bulguları

### 🔴 SEC-1 (Yüksek) — Login/auth uçlarında rate limit ve hesap kilidi yok
`Limiter(... default_limits=[])` (`app.py:76`) ve `auth.py`'deki `login`/`register`/
`google_callback` rotalarında hiçbir `@limiter.limit` yok. Yalnızca upload uçları
sınırlı.
- **Risk:** Şifre brute-force / credential stuffing sınırsız denenebilir. Başarısız
  login audit'leniyor (`auth.py:160`) ama engelleme yok.
- **Öneri:** `/auth/login` ve `/auth/register`'a IP + email bazlı rate limit
  (örn. `5/dakika`, `20/saat`). Tekrarlayan başarısızlıkta geçici lockout /
  exponential backoff. `papers/fetch-metadata` için de limit ekleyin (bkz. SEC-7).

### 🔴 SEC-2 (Yüksek / Tasarım kararı) — Private publication modelleri ve metadata'sı URL ile erişilebilir
`view_model` (`app.py:1602`), `model_resolver` (`app.py:1632`) ve `serve_glb`
(`app.py:1665`) yalnızca `model_access_status` / `model_is_accessible` (lisans +
processing durumu) kontrol ediyor; **`paper.is_public` kontrolü yok.**
- **Sonuç:** `is_public=False` bir publication'a ait modelin UUID'sini bilen herkes
  hem GLB'yi indirip görüntüleyebilir hem de viewer'ın Info panelinde **yazarlar,
  özet, DOI, kurum, PMID** gibi metadata'yı görebilir (`viewer.html:305-327`).
- **Not:** Bu, "QR ile paylaşım her zaman açık" tasarımı olabilir; ancak "private"
  etiketi kullanıcıya yanlış gizlilik beklentisi veriyor.
- **Öneri:** Bilinçli bir karar verin: (a) model erişimini `paper.is_public`'e
  bağlayın (private ise yalnızca sahip görür), veya (b) UI'da "modeller bağlantıyı
  bilen herkese açıktır" şeklinde net uyarı gösterip private'ın yalnızca dashboard
  listelemesini etkilediğini belirtin. En azından private paper'ların viewer'ında
  hassas metadata'yı gizleyin.

### 🟠 SEC-3 (Orta) — CSP `unsafe-inline` + `unsafe-eval` + Tailwind play CDN'e bağımlı
`CONTENT_SECURITY_POLICY` (`app.py:59-73`) `script-src`'de `'unsafe-inline'
'unsafe-eval'` içeriyor (Tailwind play CDN JIT gereği) ve `base.html:22` runtime'da
`cdn.tailwindcss.com` yüklüyor.
- **Risk:** `unsafe-inline`/`unsafe-eval` XSS korumasını büyük ölçüde etkisiz kılar.
  Ayrıca prod'da play CDN performans/kullanılabilirlik açısından önerilmez (CDN
  kesintisinde tüm stiller bozulur).
- **Öneri:** Tailwind'i build-time'da derleyip self-host edin; inline script'leri
  nonce'a taşıyın; `unsafe-eval`/`unsafe-inline`'ı kaldırın. `model-viewer`'ı da
  self-host edin (bkz. AR-2).

### 🟠 SEC-4 (Orta) — `X-Frame-Options: DENY`, viewer embed moduyla çelişiyor
`set_security_headers` her yanıta `X-Frame-Options: DENY` ekliyor (`app.py:147`).
Oysa `viewer.html` `?embed=true` modu (`viewer.html:1,194-206`) açıkça iframe'e
gömülmek için tasarlanmış.
- **Sonuç:** Embed modu cross-origin iframe'lerde **çalışmaz** (tarayıcı bloklar).
- **Öneri:** Viewer (özellikle embed) yanıtlarında `X-Frame-Options`'ı kaldırıp
  CSP `frame-ancestors` ile kontrollü bir allowlist tanımlayın; diğer sayfalarda
  DENY kalsın.

### 🟠 SEC-5 (Orta) — E-posta değişikliği doğrulamasız
`account_change_email` (`app.py:2575`) yeni e-postaya doğrulama maili göndermeden
hesabın e-postasını değiştiriyor (yalnızca mevcut şifreyi kontrol ediyor).
- **Risk:** Yanlış/sahip olunmayan adres atanabilir; hesap kurtarma ve bildirimler
  güvenilmez olur. `ADMIN_EMAILS` ile eşleşen bir adrese geçiş + sonraki login,
  istenmeyen admin yükseltmesine zemin hazırlayabilir.
- **Öneri:** E-posta değişiminde doğrulama token'lı onay akışı ekleyin.

### 🟠 SEC-6 (Orta) — Güvenilmeyen dosyaların 3D dönüşümü; kaynak/limit sertleştirmesi eksik
Convert pipeline güvenilmeyen kullanıcı dosyalarını trimesh ve **Node CLI'ları**
(`obj2gltf`, `fbx2gltf` — `external_converter.py`) ile işliyor. Doğrulama yalnızca
magic-byte düzeyinde (`validate_stl_file`/`validate_glb_file`, `app.py:564-606`).
- **Riskler:** (1) Geçerli ama devasa mesh → worker'da OOM (poligon/vertex/bellek
  limiti yok). (2) Node araçlarındaki olası açıklar RCE yüzeyi oluşturur.
- **İyi yanlar:** Dönüşüm izole worker'da çalışıyor; `MODEL_CONVERT_TIMEOUT`
  (`external_converter.py:53`) var; CLI çıktıları kullanıcıya ham sızdırılmıyor.
- **Öneri:** Vertex/poligon ve çözümlenmiş boyut üst sınırı; worker'da bellek/cgroup
  limiti; Node bağımlılıklarını pinleyip düzenli güncelleyin; mümkünse dönüşümü
  ayrı, ağ erişimi kısıtlı bir sandbox'ta çalıştırın.

### 🟡 SEC-7 (Düşük) — `papers/fetch-metadata` rate limit yok, dış istek proxy'si
`papers_fetch_metadata` (`app.py:2668`) Crossref/PubMed'e giden istekler yapıyor.
Host sabit olduğundan **SSRF değil**, ancak rate limit yok → upstream'i proxy'leyip
DoS aracı olarak kötüye kullanılabilir. Ayrıca User-Agent'ta placeholder e-posta
(`admin@academicar.com`, `app.py:2696`) var.
- **Öneri:** Login'li olsa da bu uca rate limit ekleyin; gerçek iletişim e-postası
  kullanın.

### 🟡 SEC-8 (Düşük) — PDF'ler aynı origin'de inline servis ediliyor
`paper_public_pdf_file` (`app.py:1773`) PDF'i inline döndürüyor. PDF'ler JS
içerebilir; aynı origin'de açılınca depolanmış-XSS yüzeyi oluşabilir.
- **İyi yan:** `X-Robots-Tag: noindex` set ediliyor.
- **Öneri:** PDF rotasına özel sıkı CSP (`sandbox`, `script-src 'none'`) veya
  ayrı/bağımsız bir görüntüleyici origin'i; alternatif olarak `Content-Disposition`.

### 🟡 SEC-9 (Düşük) — "Beni hatırla" çerezi ile oturum süresi tutarsız
`PERMANENT_SESSION_LIFETIME = 14 gün` (`config.py:56`) ama Flask-Login
`REMEMBER_COOKIE_DURATION` varsayılanı (365 gün) ezilmemiş; `login_user(..., remember=...)`
kullanılıyor (`auth.py:148`).
- **Öneri:** `REMEMBER_COOKIE_DURATION`, `REMEMBER_COOKIE_SECURE`,
  `REMEMBER_COOKIE_HTTPONLY`, `REMEMBER_COOKIE_SAMESITE` açıkça ayarlayın.

> **Güvenlik artıları (doğru yapılmış):** CSRF tüm formlarda + AJAX'ta
> `X-CSRFToken`; session fixation'a karşı `session.clear()` (`auth.py:82`); açık
> redirect koruması (`is_safe_redirect_url`); Google OAuth'ta şifreli hesaba
> oto-link yok + `email_verified` kontrolü; `secure_filename`; `serve_glb`'de
> `is_uuid` + sabit dosya adı whitelist (path traversal kapalı); admin backup'ta
> `os.path.basename`; prod'da HSTS + güvenli çerezler.

---

## 3. Backend Bulguları & Veri Modeli Tutarsızlıkları

### 🟠 BUG-1 (Orta — yanlış istatistik) — Profil `package_type` sayıları her zaman hatalı
`paper_new` her publication'ı `package_type="model_based"` ile oluşturuyor
(`app.py:2826`). Ancak profil sayfası academic/extended/temporary dağılımını
`package_type`'a göre hesaplıyor (`app.py:2507-2509`):
```python
academic_paper_count = sum(1 for p in user_papers if p.package_type == "academic")
extended_paper_count = sum(1 for p in user_papers if p.package_type == "extended_archive")
temporary_paper_count = paper_count - academic_paper_count - extended_paper_count
```
- **Sonuç:** `academic`/`extended` her zaman 0; tüm yayınlar "temporary" görünür.
- **Öneri:** Plan kavramı kullanıcı seviyesine taşındığına göre bu istatistikleri
  `User.plan`'a göre yeniden tanımlayın veya kaldırın.

### 🟠 BUG-2 (Orta — yanıltıcı mesaj) — Plan mesajları gerçek davranışı yansıtmıyor
Profil plan değişiminde "3 günlük linkler", "3 yıllık kalıcı linkler", "10 yıl"
gibi mesajlar gösteriliyor (`app.py:2484-2498`). Ancak:
- `paper_new` `expires_at=None` set ediyor (`app.py:2829`).
- `paper_is_expired` her zaman `False` döndürüyor (`licensing_paper_is_expired`).
- Yani **paper seviyesinde süre dolumu uygulanmıyor.** (Model seviyesi
  `access_expires_at` ise `model_access_status` ile uygulanıyor — bu çalışıyor.)
- **Öneri:** Mesajları gerçek davranışla hizalayın; ya paper expiry'yi uygulayın ya
  da süre vaadlerini kaldırın/yalnızca model lisansına bağlayın.

### 🟡 BUG-3 (Düşük) — `expiring_soon` sabit 0
`profile()` `expiring_soon = 0` sabit (`app.py:2520`) ve template'e geçiyor; "yakında
dolacak" göstergesi her zaman boş.

### 🟡 BUG-4 (Düşük — ölü özellik) — Onboarding kartı kapalı
`dashboard.html:14` `{% set show_onboarding = false %}` — 4 adımlı onboarding bloğu
hiçbir zaman gösterilmiyor. Ya etkinleştirin ya da ölü markup'ı kaldırın.

### Diğer backend notları
- **Legacy `Paper` alanları** (`package_type`, `payment_status`, `payment_provider`,
  `payment_reference`, `expires_at`) artık iş mantığında pasif; ileride migration
  ile sadeleştirme adayı (`models.py:63-70`).
- **`storage.py` soyutlaması kullanılmıyor** — kod doğrudan `os.path`/`shutil`
  kullanıyor (bkz. PERF-1).
- **`require_model_ownership`/`require_paper_ownership`** adminlere de 403 veriyor
  (`utils/security.py`); adminler için ayrı `/admin/...` rotaları mevcut, dolayısıyla
  fonksiyonel sorun değil ama bilinçli olun.

---

## 4. Convert Pipeline (STL/GLB/OBJ/FBX → GLB)

Genel olarak **sağlam ve iyi düşünülmüş**: tek-vertex topolojiyle flat shading
(`load_stl_mesh_without_normals`), Z-up→Y-up düzeltme, otomatik birim sezgisi,
origin'e ortalama, linear-space PBR `baseColorFactor` (sRGB→linear dönüşümü doğru),
triplanar UV üretimi, harici texture embed, kalite doğrulama, opsiyonel USDZ.

- **CONVERT-1 (Orta):** Mesh karmaşıklık limiti yok → büyük dosya worker'ı OOM
  edebilir (SEC-6 ile aynı kök).
- **CONVERT-2 (Düşük):** USDZ `aspose-3d`'ye bağlı (genelde kurulu değil,
  `stl_converter.py:74`); yoksa iOS Quick Look model-viewer'ın kayıplı
  USDZ üretimine düşer (kalite kaybı). Production'da iOS hedefliyorsanız net karar
  verin (aspose lisansı veya alternatif USDZ üretimi).
- **CONVERT-3 (Düşük):** OBJ/FBX dönüşümü deploy ortamında Node `node_modules`
  (`obj2gltf`, `fbx2gltf` binary) varlığına bağlı; yoksa `npx` ile ağdan çeker.
  Deploy gereksinimlerini (Dockerfile/nixpacks) net dokümante edin; aksi halde prod'da
  OBJ/FBX sessizce başarısız olur.
- **CONVERT-4 (Düşük):** İki ayrı GLB material enjektörü var (`enrich_glb_for_ar`
  pygltflib + `inject_pbr_material` raw struct fallback). Bakım yükü; fallback yolu
  daha az test edilmiş.

---

## 5. Viewer & AR

`viewer.html` model-viewer yapılandırması olgun: `ar-modes="webxr scene-viewer
quick-look"`, `ar-scale`, `ar-placement="floor"`, USDZ varsa `ios-src`, tone-mapping,
gölge, kamera kontrolleri, ekran görüntüsü (8 açı + ZIP), rotasyon, QR, fullscreen.

- **AR-1 (Orta):** Embed modu `X-Frame-Options: DENY` ile çelişiyor (bkz. SEC-4) —
  gömme özelliği fiilen çalışmaz.
- **AR-2 (Orta):** `model-viewer` ve Tailwind **runtime'da CDN'den** yükleniyor
  (`viewer.html:12-13`). CDN kesintisinde viewer/AR tamamen kırılır. Self-host edin
  (CSP ve performans için de gerekli).
- **AR-3 (Düşük):** İndirme engelleme (contextmenu/keydown) kozmetik — kod yorumunda
  da kabul edilmiş; gerçek koruma sağlamaz. Beklenti yönetimi için yeterli.
- **AR-4 (Düşük / UX):** `viewer.toDataURL` ile ekran görüntüsü same-origin GLB
  gerektirir (mevcut durumda sağlanıyor); harici depolamaya geçilirse CORS'a dikkat.
- **AR-5 (Erişilebilirlik):** `alt`, `aria-label`'lar büyük ölçüde var; yükleme/hata
  durumları metinle bildiriliyor (`viewerStatus`). İyi.

---

## 6. Dashboard / Profile / Publication — Aksiyonlar & UX

### Dashboard (`dashboard.html`)
- ✅ Client-side arama/filtre/sıralama akıcı; metrik kartları net; boş durum ekranı iyi.
- 🟡 **UX-1:** Tüm satırlar render ediliyor, sunucu tarafı sayfalama yok → çok
  yayında yavaşlar (PERF-2/PERF-5).
- 🟡 **UX-2:** PDF yükleme sonrası `window.location.reload()` (`dashboard.html:389`) —
  çalışır ama ağır; satır içi güncelleme daha akıcı olur.
- 🟡 **UX-3:** Hatalar `alert()` ile gösteriliyor (`dashboard.html:364,391`) —
  yeni kategorili flash stiliyle tutarsız.

### Publication detay (`paper_detail.html`)
- ✅ İşlenmekte olan modeller için `model_status` polling (2.5sn, `paper_detail.html:488-510`)
  — async dönüşüm için doğru UX.
- ✅ Renk değişimi/replace/QR/edit aksiyonları model_id & QR'ı koruyacak şekilde
  tasarlanmış (atomik swap, appearance backup) — sağlam.
- 🟡 **UX-4:** Polling sonsuz değil ama hata durumunda kullanıcı yalnızca
  `processing_error` metnini görüyor; "tekrar dene" aksiyonu yok — replace ile
  dolaylı çözülüyor.

### Profile (`profile.html` + route)
- 🔴 BUG-1/BUG-2 burada görünür (yanlış istatistik + yanıltıcı plan mesajları).
- 🟠 **UX-5:** Plan değişimi prod'da `ALLOW_DEV_PAYMENTS` kapalıyken "iletişime geçin"
  mesajı veriyor (doğru), fakat gerçek ödeme entegrasyonu yok — ücretli planlar
  fiilen kullanılamıyor. Ürün kararı netleştirilmeli (Iyzico/Stripe).
- ✅ Şifre/e-posta/profil/hesap silme akışları doğrulamalı ve audit'li; hesap silmede
  dosya temizliği yapılıyor (`app.py:2632-2666`).

---

## 7. Performans

- 🔴 **PERF-1 (Kritik, production):** **Kalıcı depolama yok.** Yüklenen modeller,
  GLB'ler, PDF'ler, QR'lar Railway efemeral diskinde/yerelde tutuluyor; `storage.py`
  soyutlaması tanımlı ama **kullanılmıyor**. Yeniden deploy / container yeniden
  başlatmada **tüm kullanıcı dosyaları kaybolur** (config yorumunda da uyarılmış,
  `config.py:91-92`). **S3/R2/GCS gibi object storage'a geçiş şart.**
- 🟠 **PERF-2 (Orta):** Dashboard ve profil `paper.models`'a lazy erişimle **N+1
  sorgu** üretiyor (`dashboard.html` her satırda `p.models`; `profile()` Python
  döngüleri, `app.py:2505-2513`). `selectinload`/`joinedload` veya tek COUNT
  agregasyonu kullanın.
- 🟠 **PERF-3 (Orta):** Mesh boyut/poligon limiti yok → büyük dosyalar worker'ı OOM
  edebilir (SEC-6/CONVERT-1).
- 🟡 **PERF-4 (Düşük):** Admin dashboard günlük/aylık trend COUNT sorguları her sayfa
  yüklemesinde çalışıyor (yalnızca ilgili sayfada gösterilse de) — önceki raporda not
  edilmişti; sayfaya bağlamak ek iyileştirme.
- 🟡 **PERF-5 (Düşük):** Dashboard sunucu sayfalaması yok (tüm satırlar DOM'da).

---

## 8. Frontend & Kod Kalitesi

- 🟡 **CODE-1:** `app.py` 3384 satırlık monolit — blueprint'lere bölme önceki
  oturumda ertelendi; bakım/okunabilirlik için hâlâ önerilir.
- 🟡 **CODE-2:** Tüm JS template'lere inline gömülü (`static/js` boş) — CSP
  sertleştirmesini ve yeniden kullanımı zorlaştırıyor; ortak JS'i harici dosyalara
  taşıyın.
- 🟡 **CODE-3:** Yoğun inline `style="..."` kullanımı (base.html, viewer.html) —
  CSP `style-src 'unsafe-inline'` gerektiriyor; kademeli olarak sınıflara taşıyın
  (flash mesajlarında zaten yapıldı).
- 🟡 **CODE-4:** İki GLB material enjektörü (CONVERT-4); legacy `Paper` alanları;
  `storage.py` ölü soyutlama — sadeleştirme adayları.
- ✅ Tutarlı tasarım dili (DESIGN.md), kategorili flash mesajları, reveal animasyonları,
  mobil hamburger nav, erişilebilirlik etiketleri mevcut.

---

## 9. DevOps / Production Hazırlık

- 🔴 Kalıcı depolama (PERF-1) — çıkış öncesi blocker.
- 🟠 Redis rate-limit storage prod'da gerçekten ayarlı mı doğrulayın (`REDIS_URL`);
  yoksa `memory://`'ye düşüyor ve çok-instance'ta rate limit paylaşılmaz.
- 🟠 Self-host: Tailwind build + model-viewer (SEC-3/AR-2).
- 🟡 Büyük PDF'ler git'te takip ediliyor (`AcademicAR_Sunumu.pdf` ~71MB) — LFS/repo
  dışına (önceki raporda da not edildi).
- 🟡 Worker servisinin (`worker.py`) prod'da ayrı bir Railway servisi olarak
  çalıştığını ve sağlık kontrolü/yeniden başlatma politikasının olduğunu doğrulayın.
- ✅ 50 test geçiyor; CSRF/oturum/OAuth/güvenlik başlıkları yerinde.

---

## 10. Bölüm Bazlı Bulgu Tablosu

| ID | Önem | Alan | Konum | Özet |
|----|------|------|-------|------|
| SEC-1 | Yüksek | Auth | `auth.py` login, `app.py:76` | Login rate-limit/lockout yok |
| SEC-2 | Yüksek | Yetki | `app.py:1602,1632,1665` | Private model/metadata URL ile açık |
| SEC-3 | Orta | XSS/CSP | `app.py:59-73`, `base.html:22` | CSP unsafe-inline/eval + play CDN |
| SEC-4 | Orta | Embed | `app.py:147`, `viewer.html:1` | X-Frame-Options embed'i kırıyor |
| SEC-5 | Orta | Hesap | `app.py:2575` | E-posta değişimi doğrulamasız |
| SEC-6 | Orta | Convert | `external_converter.py`, `app.py:564` | Güvenilmeyen dosya işleme limiti yok |
| SEC-7 | Düşük | DoS | `app.py:2668` | metadata fetch rate-limit yok |
| SEC-8 | Düşük | XSS | `app.py:1773` | PDF inline same-origin |
| SEC-9 | Düşük | Oturum | `config.py:56`, `auth.py:148` | remember-cookie süresi tanımsız |
| BUG-1 | Orta | İstatistik | `app.py:2507-2509,2826` | package_type sayıları hep 0 |
| BUG-2 | Orta | Ürün | `app.py:2484-2498` | Plan süre mesajları uygulanmıyor |
| BUG-3 | Düşük | UI | `app.py:2520` | expiring_soon sabit 0 |
| BUG-4 | Düşük | Ölü kod | `dashboard.html:14` | Onboarding kapalı |
| PERF-1 | Kritik | Storage | `storage.py`, `config.py:91` | Efemeral disk → veri kaybı |
| PERF-2 | Orta | DB | `dashboard.html`, `app.py:2505` | N+1 sorgu |
| PERF-3 | Orta | Worker | convert pipeline | Mesh boyut limiti yok |
| CONVERT-2 | Düşük | iOS | `stl_converter.py:74` | USDZ aspose'a bağlı |
| CONVERT-3 | Düşük | Deploy | `external_converter.py` | Node bağımlılığı |
| AR-2 | Orta | Bağımlılık | `viewer.html:12-13` | Runtime CDN bağımlılığı |
| UX-1..5 | Düşük | UX | dashboard/profile | Sayfalama, alert(), plan |
| CODE-1..4 | Düşük | Bakım | `app.py`, templates | Monolit, inline JS/CSS |

---

## 11. İyi Uygulamalar (Korunmalı)

- App factory + blueprint (auth) yapısı; ProxyFix doğru hop ile.
- CSRF her formda + AJAX `X-CSRFToken`; session fixation rotasyonu; açık redirect koruması.
- Google OAuth: şifreli hesaba oto-link yok, `email_verified` kontrolü.
- Atomik job claim (`FOR UPDATE SKIP LOCKED`), replace'te atomik GLB swap + appearance backup.
- Kapsamlı `AuditLog`; soft-delete + restore; dosya temizliği.
- `is_uuid` + sabit dosya whitelist'iyle path traversal kapalı; `secure_filename`.
- Dimensions cache; GLB koşullu GET/ETag; admin sorgularında SQL agregasyon (önceki faz).
- Convert'te linear-space PBR, doubleSided, triplanar UV — AR uyumluluğu için doğru detaylar.

---

## 12. Önceliklendirilmiş Aksiyon Planı

### Faz A — Production blocker'ları (çıkış öncesi)
1. **PERF-1:** Object storage'a (S3/R2/GCS) geçiş; `storage.py` soyutlamasını gerçekten kullan.
2. **SEC-1:** Login/register rate-limit + lockout.
3. **SEC-2:** Private model erişim politikası kararı + uygulama (veya net UI uyarısı + metadata gizleme).
4. **BUG-2/BUG-1:** Plan mesajlarını ve profil istatistiklerini gerçek davranışla hizala.

### Faz B — Güvenlik & sağlamlaştırma
5. **SEC-3 / AR-2:** Tailwind + model-viewer self-host; CSP'den `unsafe-eval`/`unsafe-inline` kaldır (nonce).
6. **SEC-4 / AR-1:** Embed için `X-Frame-Options` yerine `frame-ancestors` allowlist.
7. **SEC-5:** E-posta değişimi doğrulama akışı.
8. **SEC-6 / PERF-3 / CONVERT-1:** Mesh boyut/poligon limiti + worker bellek limiti.
9. **SEC-7, SEC-8, SEC-9:** metadata fetch rate-limit, PDF CSP/sandbox, remember-cookie ayarları.

### Faz C — Performans & UX
10. **PERF-2:** Dashboard/profil N+1 → eager load / agregasyon.
11. **PERF-5 / UX-1:** Dashboard sunucu sayfalaması.
12. **UX-2/UX-3:** PDF upload'da satır içi güncelleme; `alert()` yerine flash/toast.
13. **CONVERT-2/3:** USDZ ve Node bağımlılıkları için deploy kararları + dokümantasyon.

### Faz D — Bakım & kod kalitesi
14. **CODE-1:** `app.py`'yi blueprint'lere böl.
15. **CODE-2/3:** Inline JS/CSS'i harici dosyalara taşı.
16. **CODE-4 / BUG-3 / BUG-4:** Ölü kod/alan temizliği (legacy Paper alanları, storage.py, onboarding, expiring_soon).
17. Büyük PDF'leri LFS/repo dışına.

---

## 13. Hızlı Kazanımlar (tek/az satır)
- `expiring_soon` ve onboarding ölü kodunu kaldır veya etkinleştir.
- `papers_fetch_metadata` User-Agent'taki placeholder e-postayı düzelt + rate-limit ekle.
- `REMEMBER_COOKIE_*` ayarlarını `config.py`'ye ekle.
- Profil plan mesajlarını gerçek davranışa göre güncelle (yanlış süre vaatlerini kaldır).
- Login rotasına `@limiter.limit("5 per minute")` ekle.

---

*Not: Bu rapor statik inceleme sonucudur. Faz A maddeleri production'a çıkış için
blocker niteliğindedir. Değişiklik uygularken `python -m pytest tests`'in yeşil
kalmasına ve `DESIGN.md` görsel diline sadık kalınmasına dikkat edin.*
