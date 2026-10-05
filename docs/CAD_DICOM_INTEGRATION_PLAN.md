# STEP / STP ve DICOM entegrasyon planı

Branch: `v38`. Durum: **uygulandı** (aşağıdaki "Uygulama durumu"). Plan metni karar kaydı olarak duruyor.

## Uygulama durumu

Alınan kararlar: STEP parçaları tek modelde ayrı katman; ham DICOM/segmentasyon işlemden sonra silinir; tüm planlara açık; ham tarama sınırı `MEDICAL_UPLOAD_MAX_BYTES` (500 MB), plan model sınırı yalnızca çıkan GLB için geçerli.

- **STEP/STP:** `converters/step_converter.py` (+ `step_cli.py`, alt süreç). Tolerans 0,05 mm / 0,2 rad (`STEP_TOL_LINEAR`, `STEP_TOL_ANGULAR`), üçgen tavanı `STEP_MAX_TRIANGLES` (3M). Z-up → Y-up kök düğümle. STEP renkleri korunur; renksizse kullanıcı rengi.
- **Katmanlar:** `converters/layers.py` (`normalize_layers`, 2–64 katman, parça başına adlı malzeme). Katmanlı modeller yalnızca Draco ile sıkıştırılır (`optimize_glb(keep_layers=True)`): `gltf-transform optimize` aynı renkli parçaları birleştiriyordu. Bilgi `Model3D.layer_info` (migrasyon `b5c6d7e8f9a0`). Viewer'da "Layers" paneli: göster/gizle, tek katman, saydamlık, hacim (mL).
- **DICOM:** `converters/medical/` (alt süreç). Hazır ayarlar: kemik ≥250 HU, deri ≥−300 HU, kontrast ≥150 HU (kemik dahil), otomatik (Otsu; MR'da HU ayarları otomatiğe düşer). Sınırlar: `MEDICAL_CONVERT_TIMEOUT` 900 sn, `MEDICAL_MAX_VOXELS` 40M, `MEDICAL_MAX_FACES` 1,5M, `MEDICAL_MAX_UNZIPPED_BYTES` 2 GiB, `MEDICAL_MAX_FILES` 5000.
- **Segmentasyon:** NIfTI, NRRD / Slicer .seg.nrrd (ad + renk), DICOM-SEG (ad + renk), ZIP içinde maske dosyaları (TotalSegmentator; dosya adı = yapı adı). Her etiket bir katman, hacmi mL.
- **Gizlilik:** ek `medical_confirm` onayı; ham veri arşivlenmez, R2'ye aynalanmaz, dönüşüm sonrası (başarılı/başarısız, takılan iş dahil) silinir; hasta bilgisi log/GLB'ye yazılmaz (testle doğrulandı). Viewer'da "tanı amaçlı değildir" notu.

Sıkıştırılmış DICOM (JPEG Lossless, JPEG-LS, JPEG 2000, RLE) python-gdcm ile okunur. Bilinen sınırlar: 12-bit JPEG Extended gibi nadir sıkıştırmalar, çok kareli (enhanced) DICOM, gantry tilt ve 4D NIfTI desteklenmez (anlaşılır hata). Katman kontrolleri yalnızca web viewer'da; AR modeli olduğu gibi gösterir.

## 0. Ortak ilkeler (CLAUDE.md'den)

- Web isteği sadece `ConversionJob` kuyruğa alır; dönüşüm `worker.py` içinde çalışır.
- Uyumluluk onayı (anonimleştirme + haklar + etik) zorunlu kalır, yükleme ve değiştirmede.
- Mevcut akışlar bozulmaz: yükleme, değiştirme (replace), QR, viewer, USDZ.
- Görsel dil değişmez (`DESIGN.md`); yalnızca format listeleri ve kısa bilgi metinleri güncellenir.
- Çıktı her zaman `finalize_converted_glb()` içinden geçer (doku gömme, PBR, optimize, doğrulama).

## 1. Hazırlık: doğrulanan bulgular (bu oturumda denendi)

| Konu | Sonuç |
|---|---|
| STEP → GLB | `cascadio 0.1.1` (OpenCascade) ile çalıştı. 10×20×30 mm'lik STEP kutusu 0,01 sn'de GLB'ye çevrildi. |
| STEP birimi | STEP dosyası birimi kendi içinde taşıyor. Kutu metre cinsinden (0,01 × 0,02 × 0,03) çıktı. Yani STL/OBJ'deki gibi kullanıcıya birim sormaya gerek yok (FBX gibi "embedded"). |
| DICOM kütüphaneleri | `pydicom 3.0.2` ve `scikit-image 0.26.0` (marching cubes) kuruldu ve içe aktarıldı. `numpy`, `scipy`, `trimesh` zaten bağımlılık. |
| Boyut | `cascadio` yaklaşık 28 MB (kurulu ~100 MB), `pydicom` ~15 MB, `scikit-image` ~32 MB. Konteyner imajı kabaca +150 MB büyür. |
| Python sürümü | Burada Python 3.11. CLAUDE.md'de 3.12 yazıyor. **Doğrulanmadı:** üretimde kullanılan sürüm için `cascadio` ve `scikit-image` wheel'leri var mı. İlk iş bu kontrol. |

Doğrulanmadı (planda test olarak yer alıyor): renkli/çok parçalı STEP'te renk aktarımı, büyük montajlarda süre ve bellek, sıkıştırılmış DICOM.

## 2. Faz A: STEP / STP

### Kapsam
`.step` ve `.stp` yüklenir, worker'da GLB'ye çevrilir, mevcut GLB akışıyla viewer ve AR'da görünür.

### Teknik tasarım
- **Bağımlılık:** `requirements.txt` içine `cascadio` (sabit sürüm). Dockerfile ve nixpacks için ek paket gerekmiyor (wheel kendi içinde geliyor); yine de ikisinde de imaj derlemesi denenecek.
- **`converters/step_converter.py`** (yeni): `BaseConverter` üzerinden `STEPConverter`. Dönüşüm **ayrı bir alt süreçte** çalışır (OpenCascade bellek hatasında web/worker sürecini düşürmesin), zaman aşımı `_safe_timeout()` mantığıyla.
- **Tessellation:** `cascadio`'nun doğrusal/açısal tolerans ayarları sabit varsayılan olarak seçilir. Amaç: pürüzsüz yüzey ama makul üçgen sayısı. Üçgen sayısı tavanı aşarsa kullanıcıya anlaşılır hata verilir.
- **Normalize:** `source_format` her zaman `step` (`.stp` dahil). Uzantı eşlemesi `allowed_model`, `SUPPORTED_MODEL_EXTENSIONS` ve yükleme/replace yollarında tek yerden yapılır.
- **Doğrulama (preflight):** dosya başlığı `ISO-10303-21` ile başlamalı; değilse "Geçersiz STEP dosyası" (STL/GLB doğrulamalarıyla aynı kalıp).
- **Renk:** STEP'te renk varsa korunur; yoksa mevcut kural (kullanıcı rengi, yoksa varsayılan gri PBR) uygulanır. "Renk kaybı olmasın" ilkesi için renkli bir STEP fixture'ı ile test edilir.
- **Birim:** `source_unit="embedded"` (FBX/GLB ile aynı); yükleme formunda birim sorulmaz.
- **Dokunulacak yerler:** `SUPPORTED_MODEL_EXTENSIONS`, `_get_converter_for_format`, `process_model_upload_job` (preflight/dispatch), `_create_model_for_paper` (birim kuralı, satır ~3258) ve replace yolu (satır ~8975), araç sağlık kontrolü (`_describe_cli_resolution` civarı, satır ~900).
- **Arayüz metinleri:** `accept=` ve "GLB, STL, OBJ, FBX" geçen 8 şablon (`paper_new`, `paper_detail`, `model_edit`, `faq`, `pricing`, `landing`, `about`, `discipline`). Tailwind sınıfı eklenirse `npm run build:css`.

### Test
- Küçük, commit edilebilir iki fixture: renksiz kutu ve renkli iki parçalı STEP (OCP ile üretilip depoya eklenir).
- Birim testi: dönüşüm başarılı, GLB geçerli, boyutlar doğru (mm → m), renk korunuyor, `.stp` uzantısı kabul.
- Akış testi: yükleme → job → worker → viewer erişilebilir; geçersiz STEP temiz hata veriyor.
- Gerçek dosya denemesi: birkaç gerçek CAD dosyası (parçalı montaj dahil) ile süre ve bellek ölçümü.

### Kabul ölçütü
`.step` ve `.stp` yüklenir, GLB çıkar, renk korunur, viewer ve QR çalışır, hatalı dosya anlaşılır hata verir, tüm test paketi ve `py_compile` temiz.

## 3. Faz B: DICOM

### Kapsam (ilk sürüm, bilinçli olarak dar)
Kullanıcı bir DICOM serisini **ZIP** olarak yükler, bir **hazır ayar** seçer (ör. kemik, yumuşak doku), sistem tek bir yüzey modelini GLB olarak üretir. Klinik tanı aracı değildir.

### Akış
1. **Yükleme:** ZIP dosyası kabul edilir (yalnızca DICOM seçeneği seçiliyken). Formda "DICOM serisi (ZIP)" ve hazır ayar seçimi.
2. **Güvenli açma:** worker'da; yol geçişi (`../`) engeli, toplam açılmış boyut ve dosya sayısı tavanı (zip bomb koruması).
3. **Okuma:** `pydicom` ile; tek seri seçilir (en çok kesitli `SeriesInstanceUID`), kesitler `ImagePositionPatient` ile sıralanır, `RescaleSlope/Intercept` ile HU değerine çevrilir, voksel aralığı `PixelSpacing` ve kesit aralığından hesaplanır.
4. **Yüzey çıkarma:** eşik (hazır ayar), hafif yumuşatma, `scikit-image` marching cubes, en büyük bağlı parçaları tut (gürültüyü at), üçgen sayısını sadeleştir.
5. **Çıktı:** mm → m, hasta koordinatı → Y-up, `trimesh` ile GLB, ardından `finalize_converted_glb()`. Renk: hazır ayara bağlı varsayılan (ör. kemik için bej).
6. **Ham veri:** dönüşüm biter bitmez **ham DICOM dosyaları silinir**; sadece GLB ve işleme parametreleri saklanır. `Model3D.source_format = "dicom"`.

### Anonimleştirme ve sorumluluk
- DICOM başlıkları hasta adı, kimlik no, doğum tarihi içerebilir. Ham veri kalıcı saklanmadığı için sızıntı riski düşer; yine de başlıklar hiçbir yerde gösterilmez veya loglanmaz.
- Uyumluluk onay metni DICOM için genişletilir: "Hasta kimliği içeren bilgi yüklemedim ve gerekli etik onay bende."
- Yükleme ve sonuç sayfasında: "Eğitim ve sunum amaçlıdır, klinik tanı için kullanılamaz."
- Başlıkta hasta kimliği alanı dolu görünüyorsa kullanıcı uyarılır (engellemeden, bilgilendirerek).

### Sınırlar
- Boyut: mevcut `MAX_CONTENT_LENGTH` (260 MB) ve plan başına model boyut sınırı geçerli; DICOM ZIP'leri bu tavana takılabilir (karar sorusu).
- Voksel sayısı ve süre için tavan; aşılırsa "serinizi küçültün veya kırpın" gibi anlaşılır hata.
- İlk sürümde yalnızca **sıkıştırılmamış** DICOM; JPEG/JPEG2000 sıkıştırmalı seriler net bir mesajla reddedilir (sonraki adım: `pylibjpeg`).
- Birden çok seri, eğimli kesit (gantry tilt) ve 4D seriler ilk sürümde desteklenmez, net hata verilir.

### Dokunulacak yerler
`SUPPORTED_MODEL_EXTENSIONS` ayrı bir DICOM yolu ister (ZIP genel olarak kabul edilmez); yükleme formu (hazır ayar seçici), `_create_model_for_paper` ve replace yolu, `process_model_upload_job` (yeni dal), `_get_converter_for_format`, yeni `converters/dicom_converter.py`, `models.py` (gerekirse işleme parametresi alanı + migrasyon), FAQ ve ilgili metinler.

### Test
- Pydicom ile sentetik DICOM serisi üretilir (içinde küre/silindir bulunan 3B hacim); beklenen model boyutu ve geçerli GLB doğrulanır.
- Zip bomb, yol geçişi, çok seri, sıkıştırılmış seri, eksik kesit, bozuk dosya için hata testleri.
- Ham DICOM'un işlemden sonra silindiği testi.
- Gerçek anonimleştirilmiş bir BT örneği ile elle kontrol (açık veri setlerinden).

### Kabul ölçütü
Sentetik ve gerçek bir seri ZIP olarak yüklenir, seçilen ayarla GLB üretilir, viewer ve AR'da görünür, ham veri kalmaz, sınır aşımları anlaşılır hata verir, test paketi temiz.

## 4. Sıra ve tahmini iş bölümü

1. **Hazırlık:** üretim Python sürümü için wheel kontrolü, Dockerfile/nixpacks derleme denemesi.
2. **Faz A (STEP/STP):** küçük ve düşük riskli; önce bu bitirilip v38'e commit edilir.
3. **Faz B (DICOM):** ayrı commit'ler: (a) okuma + hacim, (b) yüzey çıkarma + GLB, (c) yükleme arayüzü + onay metni, (d) sınırlar + testler.
4. **Metin ve belgeler:** FAQ, fiyat/özellik sayfaları, CLAUDE.md'deki "desteklenen formatlar" notu.
5. Her fazın sonunda: tam test paketi, `py_compile`, tarayıcıda masaüstü ve mobil kontrol; main'e yalnızca "taşı" denince.

## 5. Karar bekleyen sorular

1. **STEP:** Çok parçalı montajlarda her parça ayrı kalsın mı yoksa tek modele mi birleşsin? (Öneri: tek model, renkler korunur.)
2. **DICOM hazır ayarları:** hangi yapılar? (Öneri başlangıç: kemik, yumuşak doku, damar/kontrastlı.)
3. **DICOM ham veri:** işlemden sonra silinsin mi? (Öneri: evet, sil.)
4. **DICOM erişimi:** tüm planlara mı, yoksa belirli planlara mı açılsın?
5. **Boyut tavanı:** DICOM ZIP'leri için mevcut 260 MB yeterli mi?
6. **Hedef kullanıcı:** DICOM'u hangi branşlar kullanacak? Bu, hazır ayarları ve sıkıştırmalı DICOM desteğinin önceliğini belirler.
