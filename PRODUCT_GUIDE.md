# AcademicAR — Ürün Rehberi

## Ürün Nedir? (30 Saniyede)

Hayal edin: Bir doktor, mühendis veya araştırmacı bilimsel bir dergi makalesine 3D bir anatomik model eklemek isterse ne olur? Normalde imkansız. AcademicAR bunu mümkün kılıyor.

Kullanıcı STL formatında 3D modelini yükler, sistem onu web ve mobil tarayıcılara uygun hale getirir, otomatik olarak QR kodu üretir. Okuyucu bu QR kodunu tarardığında veya linki açtığında modeli 3D olarak inceleyebilir, hatta destekleyen telefonunda artırılmış gerçeklik (AR) modunda görebilir.

---

## Uygulamanın Akışı (Halktan Basit Bir İnsanın Bakış Açısından)

### 1. Giriş: Kim Hoşgeldiniz Diye Sorması
Kullanıcı siteye gelir. Eğer hesabı yoksa, e-posta ve şifreyle kayıt olur (veya Google ile kolayca giriş yapar). Zaten hesabı varsa, sadece giriş yapar. Bu kadar basit.

### 2. Kontrol Paneli: Çalışmalarını Düzenlemek
Giriş yaptıktan sonra, kullanıcı özel bir panele ulaşır. Burası onun çalışma alanı. Tüm tez ve makaleleri listeleyen bir sayfa. "Yeni makale ekle" butonuna bastığında form açılır.

### 3. Makale Bilgilerini Doldurması
Formu doldururken:
- Makale/tez başlığı
- Yazarlar
- Yayın yılı
- Alan (tıp, mühendislik, vb.)
- Kurumu
- DOI veya PMID (varsa)
- Özet
- PDF dosyası (isteğe bağlı)

Tüm bunları yazıp kaydettikten sonra, makale oluşturulur. Makale "boş" durumdadır — henüz modeli yok.

### 4. 3D Model Yüklenmesi
Makale detay sayfasında, kullanıcı "Model Yükle" butonunu tıklar. STL ya da GLB dosyasını seçer. Ancak yükleme öncesinde bir önemli kontrol sorusu sorulur:

> "Bu model anonimleştirilmiş ve hastaya ait değil mi? Paylaşma hakkı var mı? Etik onaylar aldınız mı?"

Bu sorulara evet demek zorunda. İşte bu, hukuki sorumluluk mesajı.

### 5. Sistem Dosyayı İşliyor
- Eğer STL yüklenmişse, sistem onu GLB formatına dönüştürür (bu web tarayıcılarında daha hızlı çalışır).
- Dosya doğrulanır. Eğer bozuk ise, hata mesajı verilir.
- Dönüştürme başarılı olursa, sistem otomatik olarak bir QR kodu üretir.

### 6. Linki Alma ve Kullanması
Artık makale sahibi:
- Modeli 3D olarak görüntülemek için **Public Link** adresi alır
- Makaleye yapıştıracağı **QR Kodu** indirir
- QR kodunu bastırır ve PDF makalesine, posterine veya sunumuna yapıştırır

### 7. Okuyucu Bağlantıya Giriyor
Makaleyi okuyan biri QR kodunu tarardığında (telefonu ile) veya linki açtığında:
- Giriş yapmaya gerek yok
- 3D modeli görselleri döndürebilir, yakınlaştırabilir, döndürebilir
- Eğer telefonunda AR destekleniyorsa, "AR'da Aç" butonu ile modeli gerçek ortamına 3D olarak yansıtabilir

---

## Ürün Özellikleri

### Ana Özellikler

**1. Model Dönüştürme**
- STL dosyaları otomatik olarak GLB'ye dönüştürülür
- GLB dosyaları doğrudan desteklenir
- Dönüştürme hızlı ve sunucu tarafında gerçekleşir

**2. Web Tabanlı 3D Görüntüleme**
- Tarayıcıdan açılır, yazılım yüklenmesine gerek yok
- Fare ile döndürülebilir, yakınlaştırılabilir
- Otomatik döndürme özelliği var

**3. Artırılmış Gerçeklik (AR)**
- Destekleyen Android/iOS cihazlarda "AR Modunda Aç" seçeneği
- Model, gerçek dünya üzerine hologram gibi yansıtılır

**4. QR Kodu Üretimi**
- Her model için otomatik QR kodu
- Yazdırılabilir, yüksek kalitede
- Makale, poster, sunu, ders notu vb. her yere yapıştırılabilir

**5. Ekran Görüntüsü Alma**
Kullanıcı beş açıdan modelin fotoğrafını çekebilir:
- Ön görünüş
- Sağ görünüş
- Sol görünüş
- Üst görünüş
- Perspektif (açılı görünüş)

Bu fotoğraflar makalede yayın öncesi gösterim için kullanılabilir.

**6. Gizlilik ve Anonimlik Koruması**
- Upload öncesinde zorunlu kontrol sorusu
- İP adres ve onay tarihi kaydedilir (ileride gerekirse kanıt olarak kullanılabilir)
- Hastaya ait bilgi içeren dosyaları yüklemek yasaklanmış

**7. Paket Seçenekleri**
- **Ücretsiz Geçici** (3 gün): Hızlı deneme, yakında sona erer
- **Akademik Paket** (3 yıl, 500 TL): Kalıcı bağlantı, birden çok model desteği

---

## Adım Adım Kullanım Senaryoları

### Senaryo 1: Bir Cerrah Tez Yazıyor
1. Tıp fakültesinde doktora yapan Dr. Ayşe yazısını hazırlıyor.
2. AcademicAR'a kayıt olur, "Tez" adında yeni bir yayın oluşturur.
3. 15 adet cerrahi model (3D segmentasyon) yükler. Sistem hepsini otomatik olarak QR kodlarla etiketler.
4. Tezini PDF'ye çevirir ve her modelin yanına ilgili QR kodunu yapıştırır.
5. Danışmanı tezin PDF'sini incelemek için QR kodları tarıyor, modelleri 3D olarak görüyor, cerrahi tekniği daha iyi anlıyor.
6. Tez başarıyla savunulur ve arşive girer — QR kodlar hala çalışıyor, okuyucular modelleri görebiliyor.

### Senaryo 2: Bir Araştırmacı Makalenin Sonucunu Paylaşıyor
1. Prof. Mehmet bir tarih makalesi yayınlıyor. Makale arkeolojik buluntular hakkında.
2. Ücretsiz geçici paket ile 3 adet 3D taramalı obje yükler.
3. Makalede QR kodlar bulunur. Okuyucu tarama yapınca modeli görebiliyor.
4. Makale 3 gün boyunca viral olur ve herkese açık. Sonra bağlantı sona eriyor.
5. Eğer makale dergide kabul edilirse, 500 TL ödeyip akademik pakete yükseltebilir — 3 yıl boyunca kalıcı hale gelir.

### Senaryo 3: Bir Öğretmen Sınıfında Modeli Gösteriyor
1. Biyoloji öğretmeni Fatih, kalp anatomisi hakkında interaktif ders anlatacak.
2. AcademicAR'a anatomik kalp modelini yükler.
3. Ders sırasında:
   - Projektörde QR kodu gösterir.
   - Öğrenciler hızlıca telefonlarından QR kodunu tarayıp modeli görüyor.
   - Her öğrenci kendi hızında modeli 3D olarak inceleyebiliyor.
   - Bazıları AR modunda kullanarak "sanal kalbi cebinde taşıyor".
4. Ders daha etkili, derli toplu ve görsel oluyor.

### Senaryo 4: Hastanede Konferans Sunumu
1. Doktor Zeynep tümör cerrahisinden bahseder bir konferans yapacak.
2. Hastanın 3D CT taramasını (anonimleştirilmiş) AcademicAR'a yükler.
3. Slaytlarda QR kodları var. İzleyiciler model sayesinde ameliyat planını anlıyor.
4. Soru-cevap sırasında, birisi "Modeli daha da yakınlaştırabilir misin?" diyince, projekte yapılabiliyor.
5. Sunu daha interaktif ve yararlı oluyor.

---

## Hedeflenebilecek Kullanıcı Alanları

| Alan | Kullanıcı Tipi | İhtiyaç |
|------|---|---|
| **Tıp & Anatomi** | Doktor, cerrah, öğretmen | Hasta modellerini anonimleştirilmiş olarak paylaşmak |
| **Mühendislik** | Mühendis, tasarımcı, araştırmacı | Tasarım, prototip, mekatronik modellerini göstermek |
| **Mimarlık** | Mimar, şehir plancısı | Bina, proje, restoration modellerini sunmak |
| **Arkeoloji** | Arkeolog, müzeoloji | Kazı buluntularını, eserleri 3D görselleştirmek |
| **Biyoloji** | Biyolog, genetikçi | Protein, DNA, hücre modellerini göstermek |
| **Sanattan İlişkili Bilimler** | Sanatçı, heykeltıraş | Sanat eserlerini, restorasyon sürecini paylaşmak |
| **Eğitim (K-12)** | Öğretmen, okul | Ders materyallerinde interaktif 3D modeller kullanmak |
| **Müze & Sergi** | Müze müdürü, kuratör | Sergi etiketlerine QR kodlar yapıştırıp ziyaretçilere detay göstermek |
| **Konferans & Seminer** | Sunucu, araştırmacı | Slaytlara 3D modeller entegre etmek |

---

## İlerleyen Aşamalarda Eklenebilecek Özellikler

### Kısa Vadede (1-2 Ay)
- **OBJ format desteği** — Daha çok modelleme yazılımı OBJ'yi ihraç eder
- **Model ön görseli/thumbnail** — Makale listesinde modelin küçük resmi görünmesi
- **Ödeme sistemi** — İyzico/PayTR entegrasyonu ile akademik paket satın alımı
- **Daha hızlı dönüştürme** — Draco mesh sıkıştırması, arka planda işlem
- **Upload progress göstergesi** — Yükleme yüzdesini kullanıcıya göstermek

### Orta Vadede (2-4 Ay)
- **Birden çok model tek sayfada** — Makalede 10 model varsa hepsi aynı sayfa altında
- **Modelı indirme seçeneği** — Bazı kullanıcılar kendi uygulamalarında kullanmak isteyebilir
- **Gözlemci linki** — Makale sahibi, belirli kişilere (hakemler) özel gizli bağlantı paylaşabilir
- **Kurumsal sayfalar** — "Tıp Fakültesi XYZ" diye bir kurum sayfası oluşturulabilir
- **BibTeX/Citation export** — Makale akademik formatında alıntılanabilir
- **Arama ve filtreleme** — Tüm paylaşılan modelleri alana/anahtar kelimeye göre bulabilme

### Uzun Vadede (4+ Ay)
- **Admin paneli** — Moderasyon, rapor edilen içeriği kontrol etme
- **Otomatik bir bağlantı süresi** — Üniversiteler AcademicAR'ın çatısı altında güvenli kalıcılık
- **ORCID entegrasyonu** — Araştırmacılar profil linkini otomatik bağlantılayabilir
- **Kurumsal API** — Üniversiteler kendi platformlarından AcademicAR'ı entegre edebilir
- **Veri analitikleri** — "Modelim kaç kez açıldı? Hangi ülkelerdeki insanlar baktı?"
- **Basılı katalog** — Semester başında öğrencilere QR kodlu broşür dağıtılabilir

---

## Ürün Tanıtımı İçin Fikirler

### Video İçerikleri
1. **30 saniyelik demo** — "QR kodunu tara, 3D modeli aç, AR'da incele"
2. **Hekim tanıtım videosu** — Bir doktor tez yazarken nasıl kullandığını anlatır
3. **Öğretmen öğretimi** — Sınıfta kullanmanın faydaları
4. **Teknik öğretici** — "5 dakikada STL'den yayında modele"
5. **Müze rehberi** — Müze ziyaretçisinin perspektifinden (AR'da tarihi eseri görme)

### Görseller & Tasarım
1. **Landing page görseli** — Makale açık, yanında holografik model, QR kodu
2. **İnfografik** — "10 dakikada 10 adım" (kayıt → makale → model → QR)
3. **Case studies** — Gerçek kullanıcılardan fotoğraf + sözler
4. **Sosyal medya kartları** — "#AcademicAR ile 3D makale yayını artık kolay"
5. **Broşür/Poster** — Üniversiteler asabilir, print-friendly

### Hedef Kullanıcı Grupları & Mesajlaşma

| Grup | Ana Mesaj | Kanal |
|------|-----------|-------|
| **Doktor/Cerrah** | "Hastaların daha iyi anlayacağı şekilde tedaviyi açıkla" | Tıp dergisi, dermatolog forumu |
| **Öğretmen** | "Ders daha etkili, öğrenci daha ilgili" | Eğitim platformları, öğretmen ağları |
| **Araştırmacı** | "Makaleni kelimelerin ötesine taşı" | ResearchGate, akademik seminerler |
| **Müze/Kültür** | "Ziyaretçileri makineden insana dönüştür" | Kültür kurumları, turizm sektörü |
| **Üniversite Yöneticisi** | "Kurumunun araştırmasını destekle" | Rektörlük, kütüphane, teknoloji ofisi |

### Tanıtım Etkinlikleri
1. **Tıp fakültelerinde sunum** — Doçent, araştırmacılarla direkt konuş
2. **Eğitim konferansları** — Öğretmen ağlarının bulunduğu yerlerde stand
3. **Müze & Sanat forumları** — Kültür kurumlarına özel demo
4. **Tech meetup'lar** — 3D/AR ilgilenen tasarımcı ve geliştiriciler
5. **Sosyal medya kampanyası** — Tik-Tok, Instagram'da "AR çekilişi"

### İçerik Stratejisi
1. **Blog yazıları** — "3D modellerim nasıl açık erişime geçti" başında kâhinler
2. **Webinar** — "Akademik yayında 3D modeller" başkanlıklı canlı oturum
3. **Podcast** — "Bilim iletişimi" tematlı podcast dizileri
4. **LinkedIn kampanyası** — Araştırmacılar hedef, "Kariyer+Impact" mesajı
5. **Üniversite haber bültenleri** — İletişim ofisleri aracılığı

### Başlangıç Hedefi
1. **İlk 100 kullanıcı** — 5-10 üniversitesinden erken benimseyen araştırmacılar
2. **İlk 1000 yüklü model** — 2-3 aylık ramp-up
3. **İlk pozitif referral** — Bir araştırmacı başka araştırmacıya tavsiye etmesi
4. **Press coverage** — Bir üniversite haber sitesinde feature

---

## Başarı Göstergeleri

Ürünün başarılı olduğunu nasıl bileyim?
- Günde en az 5-10 yeni model yüklenmesi
- Haftada en az 2 akademik paket satın alımı
- Kullanıcıların ürünü hiç yönlendirme olmadan başkasına tavsiye etmesi
- Müze, hastane veya üniversite kurumsal hesap açması
- Makalede AcademicAR'a yapılan atıflar artması

---

## Özet: Ürün Değer Önerisi

AcademicAR, **akademik içeriği statikten hareketli, interaktiften hale dönüştürür**. Bir makaleyi okumak yerine deneyimlemek mümkün hale gelir.

- İçeriği kâğıdın dışına çıkartır
- Okuru-izleyiciyi katılımcıya dönüştürür
- Bilimi daha ulaşılabilir kılır
- Çok az teknik bilgi gerektirir (sadece STL yükle)
- Hukuki sorumluluğu açık, net ve zorunlu kılar
