# AcademicAR — Büyüme, Satış ve Teknik Yol Haritası Raporu

*Hazırlanma tarihi: 21 Temmuz 2026 · Kapsam: B2C + B2B satış hazırlığı, otomasyon, araştırma ritmi, 90 günlük sprint planı*

---

## 1. Yönetici Özeti

AcademicAR bugün **satılabilir bir ürüne %80 mesafede**: konvertör pipeline'ı, model bazlı
lisanslama (Free / Academic $9.90 / Extended Archive $24.90 / Institutional), kalıcı QR
çözümleyici, kurumsal (B2B) kontrat modülü, analytics ve worker altyapısı yazılmış durumda.
Satışın önündeki gerçek engel pazarlama değil, **üç teknik launch blocker**:

1. **Ödeme henüz canlı değil** — `payments.py` içindeki LemonSqueezy `create_checkout`
   hâlâ TODO; tek bir kuruş bile tahsil edilemiyor.
2. **Dosya depolama ephemeral** — Railway'de container yeniden başladığında GLB/QR
   dosyaları kaybolur; "10 yıl arşiv" satan bir ürün için kabul edilemez. `services/r2_mirror`
   mevcut, uçtan uca bağlanmalı.
3. **Ölçülemeyen funnel** — `analytics.py` event altyapısı var ama kayıt → yükleme →
   yayınlama → ödeme dönüşüm hunisi tek ekranda izlenemiyor.

**Strateji tek cümleyle:** Ürünün kendisi dağıtım kanalıdır. Yayınlanan her QR kod bir
dergi sayfasında, poster panosunda veya sunum perdesinde **bizim reklamımızdır**; viewer
sayfasını okuyucuyu yükleyiciye çeviren bir dönüşüm makinesine dönüştürüp (B2C viral
döngü), oradan gelen kullanıcı yoğunluğunu kurum/dergi/konferans anlaşmalarına (B2B)
çevireceğiz.

**90 günlük hedefler (öneri):**

| Metrik | 30. gün | 60. gün | 90. gün |
|---|---|---|---|
| Canlı ödeme + kalıcı depolama | ✅ | — | — |
| Kayıtlı kullanıcı | 150 | 500 | 1.200 |
| Yayınlanan aktif QR model (North Star) | 60 | 250 | 700 |
| Ücretli model lisansı (kümülatif) | 5 | 40 | 120 |
| B2B pilot kurum/dergi | 1 görüşme | 2 pilot | 1 ücretli kontrat |

**En kritik 5 aksiyon (sıralı):** (1) LemonSqueezy'yi canlıya al, (2) R2 kalıcı depolamayı
bitir, (3) viewer sayfasına "kendi modelini yayınla" dönüşüm döngüsünü ekle, (4) yaşam
döngüsü e-posta otomasyonunu kur, (5) konferans-poster taktiğiyle ilk 100 gerçek
kullanıcıyı elle getir.

---

## 2. Mevcut Durum Tespiti (koddan doğrulanmış)

**Güçlü yanlar**
- Model bazlı lisanslama tamamen çalışıyor (`licensing.py`): Free (3 gün, watermark),
  Academic ($9.90 / 3 yıl), Extended Archive ($24.90 / 10 yıl), Institutional (kontrat).
  Fiyatlar `/admin/pricing` üzerinden DB'de düzenlenebilir.
- B2B modülü hazır (`institutions.py`, `institution_panel.py`): kota (model + depolama),
  davet linki, edu-domain kısıtı, `/i/<slug>` vitrin sayfası, aylık kullanım raporu ve
  30 gün kala yenileme hatırlatması worker'da mevcut.
- Kalıcı QR (`/m/<public_id>`) + model versiyonlama: "QR bir kez basılır, hep çalışır"
  vaadi teknik olarak sağlanmış — bu, satış konuşmasının bel kemiği.
- GLB/STL/OBJ/FBX pipeline'ı, Draco+webp optimizasyonu, USDZ (iOS AR) üretimi.
- Blog, disiplin sayfaları (programatik SEO iskeleti), pricing, institutional landing
  şablonları mevcut; `analytics.py` first-party event takibi yapıyor.

**Boşluklar**
- Ödeme: LemonSqueezy `create_checkout` TODO (`payments.py:198`), PayTR TRY akışı iskelet.
- Depolama: dosyalar lokal dizinlerde; R2 mirror uçtan uca devrede değil.
- Onboarding: kayıt sonrası kullanıcıyı "ilk modelini 5 dakikada yayınla"ya taşıyan
  güdümlü akış yok.
- E-posta: transactional/lifecycle e-posta altyapısı yok (şifre sıfırlama hariç).
- Viewer sayfası dönüşüm için tasarlanmamış: okuyucu modeli görüyor ama "sen de yükle"
  teklifi yok.
- Funnel görünürlüğü: admin tarafında dönüşüm hunisi ekranı yok.

---

## 3. Konumlandırma ve Hedef Segmentler

**Tek cümlelik konumlandırma:** *"Makalene, posterine ve tezine — okuyucunun telefonuyla
30 saniyede açacağı — kalıcı bir 3D/AR modeli ekle."*

### B2C — bireysel araştırmacı (self-serve, PLG)
| Segment | Tetikleyici an | Mesaj |
|---|---|---|
| Tıp/diş/anatomi araştırmacısı & klinisyen | Makale kabulü, kongre posteri, tez savunması | "Segmentasyonun statik figür olarak ölmesin" |
| Yüksek lisans/doktora öğrencisi | Tez teslimi, poster günü | Free → tek seferlik $9.90 (düşük sürtünme) |
| Arkeoloji/paleontoloji/mühendislik | Buluntu/parça taraması, koleksiyon | "Modelini QR ile herkese aç" |

B2C'de fiyat abonelik değil **model başına tek seferlik** — akademisyenin "yıllık taahhüt"
alerjisine birebir uyuyor; bu yapıyı koru, aboneliğe çevirme.

### B2B — kurum, dergi, konferans (satış destekli)
1. **Üniversite/hastane departmanları** (anatomi, radyoloji, cerrahi eğitim): mevcut
   Institutional kontrat modeliyle kota bazlı yıllık anlaşma. Fiyat çıpası: model kotası
   × depolama; öneri $990–$4.900/yıl aralığında 2–3 kademe.
2. **Dergiler/yayıncılar**: "Bu dergi 3D destekler" — yazarlara upload akışı + derginin
   markalı vitrin sayfası. Küçük açık erişim dergileriyle başla (karar hızlı, kurul küçük).
3. **Konferanslar**: "AR-poster paketi" — organizasyona toplu kod, her postere QR.
   Tek etkinlikte yüzlerce yazar = en yoğun B2C edinim kanalı, B2B ambalajında.

B2B satışı B2C yoğunluğundan çıkar: aynı kurumdan 5+ bireysel kullanıcı görüldüğünde
otomatik sinyal üret (bkz. §6, lead scoring) ve o departmana kurumsal teklif götür.

---

## 4. Teknik Yol Haritası (satışı açan sıra ile)

### Faz 0 — Launch blocker'lar (Hafta 1–3)
1. **LemonSqueezy go-live**: `LemonSqueezyProvider.create_checkout` doldur (checkout API,
   `custom={payment_id, model_id, plan_key}`), webhook imza doğrulaması zaten var; sandbox
   uçtan uca test → prod. TR kullanıcılar için PayTR'ı Faz 1'e ertele — LemonSqueezy MoR
   olarak KDV/faturayı da çözer, tek sağlayıcıyla başla.
2. **R2 kalıcı depolama**: `services/r2_mirror`'ı upload/convert/serve akışına uçtan uca
   bağla; `worker.py` dönüşüm çıktısını R2'ye yazsın, `/files/...` route'ları R2'den
   (imzalı URL veya proxy) servis etsin. Mevcut lokal dosyalar için tek seferlik migrasyon
   script'i (`scripts/`).
3. **Funnel eventleri**: `track_event` ile `signup → paper_created → model_uploaded →
   conversion_done → published → checkout_started → paid` zincirini tamamla; admin'e
   basit huni ekranı (`analytics_snapshot` üstüne).

### Faz 1 — Dönüşüm makinesi (Hafta 3–6)
4. **Viewer viral döngüsü**: public viewer'a (tasarım diline sadık, ince bir bant)
   "Bu model AcademicAR ile yayınlandı — kendi modelini 5 dakikada yayınla" CTA'sı;
   free plan watermark'ı zaten var, tıklanabilir olsun. UTM'li link ile viewer→signup
   dönüşümünü ölç. **En yüksek kaldıraçlı tek geliştirme budur.**
5. **Güdümlü onboarding**: kayıt sonrası tek hedefli akış (proje aç → model yükle →
   QR'ını indir), boş dashboard yerine checklist; örnek model ile "önce dene" yolu
   (mevcut demo şablonundan).
6. **Paylaşım çıktıları**: model yayınlandığında hazır paket — yüksek çözünürlük QR
   (poster için vektörel), "makaleye eklenecek figür altyazısı" metni, DOI/atıf satırı.
   Akademisyenin işini bitiren çıktı = ağızdan ağıza yayılım.

### Faz 2 — B2B ve genişleme (Hafta 6–12)
7. **Self-serve kurumsal deneme**: "14 gün / 10 model" kurumsal pilot; mevcut kontrat
   modeline `trial` bayrağı + süre sonunda otomatik teklif e-postası. Satış görüşmesi
   beklemeden pilot başlatılabilmeli.
8. **Dergi/konferans vitrin modu**: `/i/<slug>` vitrinin dergi/etkinlik varyantı
   (logo, sayı/oturum listesi). Teknik olarak küçük iş, satışta büyük kapı açıcı.
9. **Basit REST API + toplu yükleme**: kurumların LMS/arşiv entegrasyonu için token'lı
   upload endpoint'i. (İstek gelmeden büyük API yatırımı yapma — YAGNI.)

> Kural: dönüşüm/ağır iş her zaman `worker.py`'de kalır; web süreçleri sadece
> `ConversionJob` kuyruğa yazar. Tüm yeni ekranlar DESIGN.md diline uyar.

---

## 5. Otomatikleştirilecek Sistemler

| Sistem | Ne yapar | Nasıl (mevcut altyapıyla) |
|---|---|---|
| **Yaşam döngüsü e-postaları** | D0 hoş geldin, D1 "modelin yüklenmedi", yayın sonrası "QR'ını postere ekle", free bitmeden 24s önce upgrade, satın alma sonrası makbuz+rehber | Resend/Postmark + worker'a e-posta kuyruğu tablosu (aylık rapor gönderimi zaten worker'da — aynı desen) |
| **Süre/yenileme otomasyonu** | Lisans bitmeden 30/7/1 gün kala hatırlatma + tek tıkla yenileme linki | Kurumsal 30-gün hatırlatıcı deseni bireysel lisanslara kopyalanır |
| **Dunning** | Başarısız ödeme → 3 denemelik e-posta dizisi | LemonSqueezy webhook eventleri |
| **Lead scoring (B2B sinyali)** | Aynı edu-domain'den ≥5 kullanıcı veya ≥10 model → admin'e/Slack'e "kurumsal fırsat" bildirimi | Günlük worker sorgusu; e-posta domain'i zaten elde |
| **Haftalık admin digest** | Funnel, gelir, en çok görüntülenen modeller, yeni edu-domain'ler tek e-postada | `analytics_snapshot` + worker cron |
| **Programatik SEO üretimi** | Disiplin × kullanım senaryosu sayfaları ("3D model in dental thesis" vb.), sitemap otomatik | Mevcut `discipline_content.py` deseni genişletilir |
| **Sosyal kanıt döngüsü** | Yeni yayınlanan (izinli) modellerden haftalık öne çıkanlar sayfası/feed | Yayın anında opt-in bayrağı |
| **Operasyonel alarmlar** | Conversion job hata oranı, kuyruk gecikmesi, webhook hataları → Slack | Worker heartbeat + basit eşikler |

E-posta için ilk günden ayrı bir marketing-automation SaaS'a bağlanma; worker + sağlayıcı
API'si ile başla, hacim 1.000+ kullanıcıya gelince değerlendir.

---

## 6. Düzenli Araştırma ve Öğrenme Ritmi

**Haftalık (60–90 dk, pazartesi):**
- Funnel raporu oku (otomatik digest): hangi adımda kayıp arttı → haftanın tek
  optimizasyon deneyi oraya.
- Rakip taraması: Sketchfab (fiyat/politika değişimi), MorphoSource, embed 3D çözümleri;
  değişiklikleri tek satırlık log dosyasına işle.
- 2 kullanıcı görüşmesi (yeni kayıt + churn eden birer kişi, 15'er dk). Soru seti sabit:
  "hangi işin için geldin, nerede takıldın, kime tavsiye ederdin?"

**Aylık:**
- Konferans takvimi taraması (tıp/diş/anatomi/arkeoloji, TR + Avrupa, 3 ay ilerisi):
  poster oturumu olan etkinlikler = B2B "AR-poster paketi" hedef listesi.
- SEO/keyword incelemesi: "3D model in thesis", "QR code poster presentation",
  "interactive figure journal" ailesi; Search Console verisiyle içerik planı güncelle.
- Dergi politikaları taraması: multimedya/veri eki kabul eden dergiler listesine ekleme;
  her ay 5 dergi editörüne kişisel e-posta.
- Fiyat testi değerlendirmesi: `/admin/pricing` DB'den yönetildiği için A/B yerine
  dönemsel fiyat denemesi yapılabilir (ör. Extended $24.90 → $29.90 kohort karşılaştırması).

**Çeyreklik:** pazar boyu ve yatırımcı anlatısı güncellemesi (mevcut
`MVP_ANALYSIS_AND_ROADMAP.md` §6 üstüne), NPS ölçümü, yol haritası revizyonu.

---

## 7. Satış Motoru — Kanal Planı

**B2C edinim (maliyet sırasına göre):**
1. **Ürün içi viral döngü** (viewer CTA + watermark) — bedava, bileşik etki; §4/4 no'lu iş.
2. **Konferans taktiği**: bir sonraki yerel kongrede 10 yazara elden/e-postayla ücretsiz
   model yayınlama teklifi → poster panosunda 10 canlı QR → oradaki herkes potansiyel
   kullanıcı. İlk 100 kullanıcı buradan gelir; kurucu bunu bizzat yapmalı.
3. **İçerik/SEO**: roadmap'teki 27 blog konusu + programatik disiplin sayfaları;
   haftada 1 yazı disiplinli şekilde.
4. **Akademik Twitter/X + LinkedIn**: her hafta 1 etkileyici modelin 15 sn'lik AR
   videosu (izinli modellerden). Video içerik bu üründe kendini satar.

**B2B satış süreci (kurucu satışı, CRM: basit bir Notion/HubSpot free):**
- Kaynak: lead-scoring sinyali (§5) + konferans listesi + dergi taraması.
- Akış: 15 dk demo (canlı QR okutma — "wow" anı) → 14 gün self-serve pilot →
  kullanım raporu eşliğinde teklif → yıllık kontrat (mevcut offline ödeme akışı hazır).
- Hedef tempo: haftada 5 yeni kurumsal temas, ayda 2 pilot.

---

## 8. Metrikler

- **North Star: aktif yayınlanmış QR model sayısı** (görüntülenme değil — yayın, hem
  değer teslimi hem dağıtım demektir).
- Funnel: ziyaret → kayıt (%8+), kayıt → ilk model yayını (%40 hedef, aktivasyon),
  yayın → ücretli lisans (%15 hedef), viewer görüntüleme → kayıt (%1–2 viral katsayı).
- Gelir: aylık yeni lisans geliri, kurumsal ARR, ort. model başına gelir.
- Sağlık: conversion job başarı oranı ≥%98, medyan dönüşüm süresi, viewer p75 yüklenme.

---

## 9. 90 Günlük Sprint Planı (özet)

| Hafta | Teknik | Ticari |
|---|---|---|
| 1–2 | LemonSqueezy go-live, R2 uçtan uca | Konferans/dergi hedef listesi (20 kayıt) |
| 3–4 | Funnel eventleri + admin huni ekranı, viewer CTA | İlk konferans taktiği: 10 yazar, elle onboarding |
| 5–6 | Güdümlü onboarding, lifecycle e-posta v1 (hoş geldin + upgrade) | Haftalık blog + video ritmi başlar; 2 kullanıcı görüşmesi/hafta |
| 7–8 | Paylaşım paketi (vektörel QR, figür altyazısı), lisans yenileme otomasyonu | 5 dergi editörü outreach; lead-scoring sinyali devrede |
| 9–10 | Self-serve kurumsal pilot, dunning | İlk 2 kurumsal pilot başlat |
| 11–12 | Dergi/etkinlik vitrin modu, haftalık digest | Pilot → teklif dönüşümü; fiyat denemesi kararı |

**Riskler:** (1) Ödeme/depolama gecikirse her şey kayar — ilk 3 hafta başka iş alınmaz.
(2) Tıbbi veri hassasiyeti: uyum onayları zorunlu kalır, kurumsal satışta DPA/KVKK
dokümanı erken hazırlanır. (3) Tek kurucu kapasitesi: haftalık ritimdeki her şey
otomatik rapora bağlanır, elle veri toplama yasak.

---

*Bu doküman `docs/MVP_ANALYSIS_AND_ROADMAP.md` ile birlikte okunmalı; oradaki P0/P1/P2
listesi teknik detay kaynağıdır, bu rapor satış ve büyüme sıralamasını belirler.*
