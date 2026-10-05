# Sahneler, AR sahneleri, kesit düzlemi, ölçüler, figür ve karşılaştırma — plan

Branch: `v38`. Durum: plan, kod yok. Altı özellik, üç dalga halinde; her dalga
sonunda tam test paketi + tarayıcı (masaüstü/mobil) kontrolü, sonra v38'e push.
main'e yalnızca "taşı" denince.

## 0. Ortak ilkeler

- CLAUDE.md sınırları: ağır iş (GLB üretimi, ölçü hesabı, USDZ) yalnızca `worker.py`'de;
  web isteği sadece iş kuyruğa koyar.
- Görsel dil `DESIGN.md`; viewer'daki mevcut panel/araç çubuğu kalıpları kullanılır.
- Yetki: sahne/karşılaştırma oluşturma ve düzenleme sahip + editör
  (`require_model_editor` / `can_edit_project`); görüntüleme modelin mevcut erişim
  kuralları (özel proje → "private project" sayfası, süresi dolan model → 410 sayfası).
- Plan kapısı: her özellik `licensing.PLAN_FEATURES`'a bir anahtar olarak eklenir
  (admin fiyatlandırma ekranından açılıp kapatılabilir). Varsayılanlar §9'da karar.
- Kalıcı link ve QR: mevcut `QRLink` + `/m/<public_id>` deseni izlenir; sahne ve
  karşılaştırma QR'ları model değişse de bozulmaz, kaynak silinince zarif hata sayfası.
- model-viewer 4.3.0 sabit. Kesit düzlemi ve gizli katmanın tıklama dışı bırakılması
  three.js iç nesnesine (`scene` sembolü) erişir; bu tek bir yardımcıda toplanır,
  try/catch ile korunur, sürüm yükseltmesinde test edilir.

## 1. Kayıtlı görünümler (sahneler)

**Ne:** Sahip/editör o anki görünümü adlandırıp kaydeder: katman görünürlüğü ve
saydamlığı, kamera (orbit, hedef, görüş açısı), etiketlerin açık/kapalı oluşu,
kesit düzlemi (Faz 3), arka plan. Her sahnenin kendi linki ve QR'ı olur;
"sunum modu" sahneleri sırayla oynatır.

**Veri:** yeni tablo `model_scenes`
`id, model_id (CASCADE), public_id (unique), title (≤120), description (≤500),
order_index, state JSON, ar_status (none/queued/ready/failed), ar_glb_path,
ar_usdz_path, created_by_user_id, created_at, updated_at`. Migrasyon idempotent
(mevcut desen). Model başına üst sınır `MAX_SCENES_PER_MODEL` (öneri 12).

`state` şeması (sürümlü): `{"v":1,"layers":[{"name","visible","opacity"}],
"camera":{"orbit","target","fov"},"labels":bool,"section":null|{...},"bg":str}`.
Katmanlar ada göre eşlenir; model değiştirilince eşleşmeyen katman yok sayılır
ve sahne kartında "güncellenmeli" uyarısı çıkar.

**Rotalar:** `POST /models/<id>/scenes` (oluştur), `POST .../<scene_id>` (güncelle/
yeniden adlandır/sırala), `POST .../<scene_id>/delete`; okuma: viewer `?scene=<id>`;
kalıcı link `/s/<public_id>` → viewer'a yönlendirir; QR: mevcut `services/qr_assets`
ile SVG/PNG/etiket.

**Viewer:** "Sahneler" paneli (katman panelinin yanında): liste, tıkla-uygula,
"Bu görünümü kaydet" (editör), sürükle-sırala, link/QR kopyala. Katman panelinin
durumu tek bir `getViewState()/applyViewState()` arayüzüne taşınır; tur ve
sahneler bunu kullanır. Sunum modu: mevcut tur çubuğu sahne sırasıyla çalışır
(sahne yoksa bugünkü etiket turu).

**Testler:** CRUD + yetki (editör evet, yabancı 403), üst sınır, `/s/<public_id>`
yönlendirme ve özel/süresi dolmuş durumlar, `state` doğrulama (bilinmeyen alan
atılır, sınırlar), model değiştirme sonrası eşleşmeyen katman uyarısı, tarayıcıda
kaydet → yeniden yükle → aynı görünüm.

## 2. AR'da sahne görünümü

**Ne:** AR (Scene Viewer / Quick Look) katman kontrolü desteklemez. Sahne
kaydedilince worker, sadece görünen katmanlarla ve saydamlıkları uygulanmış bir
GLB + USDZ üretir; viewer bir sahnedeyken AR düğmesi bu dosyaları açar.

**Uygulama:** yeni iş tipi `scene_ar` (ConversionJob, mevcut `usdz_regen` deseni).
`converters/scene_variant.py`: kaynak GLB'den (Draco açılmış kopya üzerinden,
pygltflib) görünmeyen katman düğümlerini çıkarır, saydam katmanları BLEND +
alfa ile yazar, Draco ile sıkıştırır; USDZ mevcut `convert_glb_to_usdz` ile.
Dosyalar `converted/<model_id>/scenes/<scene_id>.glb|usdz`, R2'ye aynalanır,
sahne/model silinince ve model değiştirilince temizlenir (değiştirmede yeniden
üretim kuyruğa alınır). Boyut plan depolamasına sayılır (karar §9).

**Testler:** varyant GLB'de yalnızca seçili katmanlar ve alfa doğru; iş kuyruğa
web isteğinde değil sahne kaydında girer; silme/değiştirme temizliği; Quick Look'ta
saydamlık için gerçek cihaz kontrolü (manuel, iPhone).

## 3. Kesit düzlemi

**Ne:** Modeli bir düzlemle kesip içini görmek: hazır eksenler (aksiyal /
koronal / sagital — tıbbi modellerde hasta eksenleri, diğerlerinde X/Y/Z), konum
kaydırıcısı, ters çevirme, serbest açı (sürükle). Katmanlarla birlikte çalışır,
sahneye kaydedilir.

**Uygulama:** three.js `renderer.localClippingEnabled` + materyallere
`clippingPlanes` (ortak iç-erişim yardımcısı). v1'de kesit yüzeyi doldurulmaz
(kapak/“cap” yok); iç yüzeyler çift taraflı gösterilir ve hafif farklı tonla
boyanır. Kapak (stencil ile dolu kesit) v2'ye bırakılır. Ölçüm/etiket tıklaması
kesilen tarafa denk gelmez (raycast düzlemle süzülür). AR'a kesit taşınmaz
(sahne AR'ı kesitsiz üretilir, panelde not).

**Testler:** tarayıcıda düzlem konumuna göre görünen yüzey ve
`positionAndNormalFromPoint` sonucu; mobil dokunmatik kaydırıcı; sahneye
kaydet/uygula; iç erişim başarısızsa düğme gizlenir, hata yok.

## 4. Yapı ölçüleri ve yapılar arası mesafe

**Ne:** Her katman için boyutlar (yönlendirilmiş kutu kenarları), en büyük çap,
hacim (tıbbi modellerde var). Seçilen iki katman arası en kısa mesafe ve iki en
yakın nokta; viewer'da çizgiyle gösterilir. Katman panelinde ve model sayfasında
"Ölçüler" tablosu; CSV dışa aktarma.

**Uygulama:** `converters/layer_metrics.py`, worker'da `normalize_layers` sonrası,
Draco'dan önce: katman başına köşe noktaları; boyut = PCA ile yönlendirilmiş
kutu; en büyük çap = dışbükey zarf üzerinde en uzak nokta çifti; mesafe =
`scipy.spatial.cKDTree` ile yüzey örnekleri arası en kısa (yüzey örneklemesiyle
köşe-köşe yaklaşımının hatası testle sınırlanır). Çiftler: kutuları birbirine
`METRIC_PAIR_RADIUS` (öneri 50 mm) yakın olanlar, en fazla 64 katman → hesap
sınırlı. Sonuç `layer_info["metrics"]`. Mevcut katmanlı modeller için bir kerelik
geri doldurma işi (worker, admin tetikler; Draco GLB'yi gltf-transform ile açıp
hesaplar). Tıbbi uyarı: "eğitim/sunum amaçlıdır".

**Testler:** bilinen geometri (iki küre arası mesafe, kutu boyutları) ±%2;
64 katmanda süre sınırı; geri doldurma; viewer'da iki katman seçimi ve çizgi.

## 5. Yayın kalitesinde figür dışa aktarma

**Ne:** O anki görünüm (sahne, katmanlar, kesit) yüksek çözünürlükte PNG:
seçilebilir genişlik (ör. 2000/3000/4000 px), şeffaf ya da beyaz arka plan,
ölçek çubuğu, görünen katmanların renk açıklaması, isteğe bağlı başlık ve QR
(sahnenin kalıcı linki).

**Uygulama:** istemci tarafı: model-viewer `toBlob` ile hedef çözünürlükte
render, canvas'ta bileşim (mevcut "combined view" kodundan yararlanılır).
Ölçek çubuğu: kamera hedefinden ekrana paralel 10 mm'lik iki noktanın
`updateHotspot` + `queryHotspot` ekran konumlarından piksel/mm; perspektif
nedeniyle "model merkezinde geçerli" notu figüre yazılır. Sunucu yükü yok.

**Testler:** üretilen PNG boyutu ve DPI meta verisi; ölçek çubuğu doğruluğu
(bilinen ölçülü modelde ±%3); QR'ın sahne linkini çözmesi; mobilde paylaşım.

## 6. Önce / sonra karşılaştırma

**Ne:** İki modeli yan yana (mobilde üst-alt ya da sürgülü geçiş), kamera senkron;
her biri kendi katman paneliyle. Etiketler: "Önce / Sonra" ya da serbest. Kalıcı
link + QR.

**Veri:** `model_comparisons`: `id, public_id, owner_user_id, title, left_model_id,
right_model_id, left_label, right_label, sync_camera, created_at`. Kural v1: iki
model de oluşturan kişinin sahip/editör olduğu projelerden. Görüntüleme: her
model kendi erişim kuralına tabi; biri erişilemezse o yarıda zarif durum.

**Uygulama:** `/compare/<public_id>` (yeni şablon, viewer bileşenleri yeniden
kullanılır), oluşturma model sayfasından ("Karşılaştır…" → ikinci modeli seç).
Kamera senkronu `camera-change` olayıyla, döngüye girmeyecek şekilde.
Birim farkı yok (tüm GLB'ler metre); ölçek farklıysa uyarı.

**Testler:** oluşturma/yetki, özel proje yarısı, senkron (tarayıcıda), QR, mobil.

## 7. Uygulama dalgaları ve iş bölümü

Planlama, gözden geçirme ve entegrasyon Opus 5.5; uygulama Sonnet 5.5 alt
ajanları, çakışmasın diye dosya sahipliği ayrılmış olarak.

| Dalga | Ajan | İş | Dosyalar |
|---|---|---|---|
| 1 | A | Sahneler (veri, rotalar, viewer paneli, sunum modu) + ortak `getViewState/applyViewState` + iç-erişim yardımcısı | models, migrasyon, yeni `scenes.py` blueprint, viewer.html |
| 1 | B | Katman ölçüleri + geri doldurma | `converters/layer_metrics.py`, worker kancası, testler |
| 1 | C | Karşılaştırma sayfası | models, migrasyon, yeni `comparisons.py`, yeni şablon |
| 2 | A | Kesit düzlemi + figür dışa aktarma (viewer) | viewer.html |
| 2 | B | Sahne AR varyantları (worker) | `converters/scene_variant.py`, worker, `scenes.py` kancası |
| 2 | C | Ölçüler arayüzü (panel, çizgi, model sayfası tablosu, CSV) | model_edit.html, viewer.html'in ölçü bölümü (A ile sıralı) |
| 3 | Lider | Birleştirme, tam test, tarayıcı QA (masaüstü + mobil), iPhone AR kontrol listesi, belgeler (FAQ, CLAUDE.md), v38 push | — |

Migrasyonlar tek başlık (head) olacak şekilde sıralanır (A → C), `test_migration_heads` korur.

## 8. Riskler

- **model-viewer iç erişimi:** kesit ve raycast süzme sürüm bağımlı → tek yardımcı,
  özellik tespiti, başarısızsa özelliği gizle; sürüm sabit kalır.
- **Quick Look saydamlık:** USDZ'de alfa desteği sınırlı olabilir → gerçek cihazda
  doğrulanmazsa saydam katmanlar AR varyantında tam görünür/gizli olarak yazılır.
- **Depolama:** sahne başına AR varyantı (GLB + USDZ) → model başına sahne sınırı,
  plan depolamasına sayım, disk güvenlik kontrolleri (yeni eklenen boş alan
  denetimi) worker işinde de uygulanır.
- **Ölçü doğruluğu:** yüzey örneklemesi + yumuşatılmış tıbbi yüzeyler → değerler
  "yaklaşık" ve eğitim amaçlı etiketlenir.
- **Performans:** iki model yan yana mobilde ağır olabilir → küçük poster önizleme,
  dokununca yükleme.

## 9. Karar bekleyen sorular

1. Plan kapısı: hangi özellikler Free'de de açık? Öneri: sahneler (en fazla 2)
   ve kesit düzlemi herkese; AR sahneleri, ölçüler, figür dışa aktarma,
   karşılaştırma Academic ve üstü.
2. Model başına sahne sınırı: öneri 12.
3. AR sahne varyantları plan depolamasına sayılsın mı? Öneri: evet.
4. Karşılaştırma farklı sahiplerin modelleri arasında olabilsin mi? Öneri: v1'de
   hayır (yalnızca kişinin sahip/editör olduğu projeler).
5. Ölçü tablosu herkese mi, yalnızca sahip/editöre mi görünsün? Öneri: herkese
   (sahip kapatabilir).
