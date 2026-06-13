#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AcademicAR — detaylı Türkçe pazarlama denetim raporu (PDF).

DejaVuSans TTF kullanır (tam Türkçe karakter desteği). Reportlab Platypus ile
çok sayfalı, kategori bazında detaylı bir rapor üretir.
"""
import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, HRFlowable)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DV", f"{FONT_DIR}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DV-B", f"{FONT_DIR}/DejaVuSans-Bold.ttf"))

# AcademicAR / Ventriloc paleti
INK = HexColor("#202020")
GRAPHITE = HexColor("#4d4d4d")
MUTED = HexColor("#828282")
LINE = HexColor("#e2e2e2")
SOFT = HexColor("#f5f5f5")
ORANGE = HexColor("#E8602C")
GREEN = HexColor("#2f8f4e")
AMBER = HexColor("#c98a1e")
RED = HexColor("#c0392b")

styles = getSampleStyleSheet()


def S(name, **kw):
    base = dict(fontName="DV", textColor=INK, fontSize=10, leading=15)
    base.update(kw)
    return ParagraphStyle(name, **base)


H1 = S("H1", fontName="DV-B", fontSize=24, leading=28, textColor=INK, spaceAfter=4)
SUB = S("SUB", fontSize=11, textColor=MUTED, leading=15)
EY = S("EY", fontName="DV-B", fontSize=9, textColor=ORANGE, spaceAfter=2)
H2 = S("H2", fontName="DV-B", fontSize=15, leading=19, textColor=INK, spaceBefore=14, spaceAfter=6)
H3 = S("H3", fontName="DV-B", fontSize=11.5, leading=15, textColor=INK, spaceBefore=8, spaceAfter=3)
BODY = S("BODY", fontSize=10, leading=15.5, textColor=GRAPHITE, alignment=TA_JUSTIFY, spaceAfter=6)
BODYL = S("BODYL", fontSize=10, leading=15.5, textColor=GRAPHITE, alignment=TA_LEFT, spaceAfter=4)
SMALL = S("SMALL", fontSize=8.5, leading=12, textColor=MUTED)
LI = S("LI", fontSize=9.7, leading=14.5, textColor=GRAPHITE, leftIndent=10, spaceAfter=3)
CELL = S("CELL", fontSize=8.8, leading=12, textColor=GRAPHITE)
CELLB = S("CELLB", fontName="DV-B", fontSize=8.8, leading=12, textColor=INK)
CELLW = S("CELLW", fontName="DV-B", fontSize=9, leading=12, textColor=white)


def grade(score):
    if score >= 85: return "A", GREEN
    if score >= 70: return "B", GREEN
    if score >= 55: return "C", AMBER
    if score >= 40: return "D", AMBER
    return "F", RED


def sev_color(s):
    return {"Kritik": RED, "Yüksek": ORANGE, "Orta": AMBER, "Düşük": MUTED}.get(s, GRAPHITE)


def bar(score, width=70*mm):
    g, col = grade(score)
    fill = width * score / 100.0
    t = Table([[ "", "" ]], colWidths=[fill, width-fill], rowHeights=[6])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), col),
        ("BACKGROUND", (1,0), (1,0), SOFT),
        ("LINEAFTER", (0,0), (0,0), 0, white),
        ("BOX", (0,0), (-1,-1), 0.25, LINE),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
    ]))
    return t


def rule(c=LINE, w=1.0, sb=4, sa=8):
    return HRFlowable(width="100%", thickness=w, color=c, spaceBefore=sb, spaceAfter=sa)


# ----------------------------------------------------------------------------
# İÇERİK
# ----------------------------------------------------------------------------
DATE = "13 Haziran 2026"
OVERALL = 69

CATS = [
    ("İçerik & Mesajlaşma", 78, "25%", "Net, fayda odaklı, iyi kurgulu — ama iddialar kanıtsız, sayısız."),
    ("Dönüşüm Optimizasyonu", 62, "20%", "Akış temiz ve ücretsiz katman var; tek CTA hedefi ve CTA yanında güven sinyali yok."),
    ("SEO & Bulunabilirlik", 80, "20%", "Sitemap, robots, schema, blog, disiplin sayfaları — güçlü; ana sayfa varsayılan meta kullanıyor."),
    ("Rekabetçi Konumlandırma", 58, "15%", "Kategori net ama hiç rakip farkındalığı / karşılaştırma içeriği yok."),
    ("Marka & Güven", 60, "10%", "İyi misyon ve açık standart hikâyesi; ekip, referans, logo, gerçek sayı yok."),
    ("Büyüme & Strateji", 65, "10%", "İçerik motoru + kurumsal yol + model-bazlı fiyat akıllıca; yakalama/referans döngüsü yok."),
]

CAT_DETAIL = {
    "İçerik & Mesajlaşma": (
        "Güçlü yönler: Hero başlığı ('Publish Interactive 3D Models with Your Research') "
        "spesifik ve 5 saniye testini geçiyor. Fayda çerçevelemesi tutarlı (\"No coding required\", "
        "\"Auto format conversion\", \"Mobile AR ready\"). Sayfanın en güçlü varlığı Geleneksel↔AcademicAR "
        "karşılaştırma bölümü: okuyucunun gerçek acısını adlandırıyor (statik PDF figürleri, kopan "
        "ek-materyal linkleri, özel yazılım gereksinimi) ve her birini somut bir faydaya bağlıyor. "
        "'Nasıl çalışır' 3 adımı temiz; ton akademik ve ölçülü.",
        "Zayıf yönler: Her iddia öne sürülüyor ama hiç kanıtlanmıyor — sayı, isim verilmiş kurum veya "
        "alıntı yok. CTA bandındaki 'Join researchers, universities and journals already using AcademicAR' "
        "ifadesi desteklenmemiş bir iddia; akademik okuyucu bunu kanıtsız görüp zihninde siler. "
        "'Dakikalar içinde' deniyor ama gerçek bir önce/sonra metriği yok. Metin fayda açısından zengin, "
        "kanıt açısından fakir."
    ),
    "Dönüşüm Optimizasyonu": (
        "Güçlü yönler: Net görsel hiyerarşi, hero'da canlı etkileşimli <model-viewer> maketi (ürün kendini "
        "satıyor), gerçek bir ücretsiz katman ve mantıklı çoklu-CTA düzeni (Start Publishing / View Example). "
        "Fiyatlandırma sayfasında karşılaştırma tablosu ve net paketleme var.",
        "Zayıf yönler: Tek dönüşüm hedefi — her şey register'a gidiyor; hazır olmayan çoğunluk için e-posta "
        "yakalama yok. Eylem noktasında risk azaltıcı yok ('kredi kartı yok', garanti, 'ücretsiz'). Hiçbir "
        "CTA'nın yanında sosyal kanıt yok. En güçlü yumuşak-dönüşüm varlığı (örnek yayın) bir kez kullanılıp "
        "tekrar edilmiyor."
    ),
    "SEO & Bulunabilirlik": (
        "Güçlü yönler: Öne çıkan kategori bu. app.py '/robots.txt' ve dinamik '/sitemap.xml' sunuyor. "
        "base.html canonical URL'ler, tam Open Graph + Twitter Card etiketleri ve Organization + WebSite "
        "JSON-LD içeriyor. Fiyatlandırma SoftwareApplication + Offers yapısal verisi, FAQ ise FAQPage "
        "schema'sı ekliyor. Başlıklar sayfa bazında açıklayıcı ve benzersiz. 6 yazılık blog ve 12 disiplin "
        "sayfası indekslenebilir uzun-kuyruk yüzeyi yaratıyor.",
        "Zayıf yönler: Ana sayfa özel meta description ayarlamıyor (genel varsayılanı miras alıyor). "
        "Yüksek niyetli sorguları yakalayacak karşılaştırma/alternatif içeriği yok. Canlı Core Web Vitals, "
        "görsel ağırlıkları ve render-blocking kaynaktan doğrulanamaz — model-viewer + Tailwind CDN gerçek "
        "bir PageSpeed kontrolünü hak ediyor."
    ),
    "Rekabetçi Konumlandırma": (
        "Güçlü yönler: Kategori net tanımlı — 'akademik yayıncılık için etkileşimli 3D + AR + kalıcı QR' — "
        "ve dayanıklı-link açısı (model değiştirilse bile QR/viewer URL'i korunur) çoğu rakibin "
        "vurgulamadığı gerçek, spesifik bir farklılaştırıcı.",
        "Zayıf yönler: Sıfır rakip farkındalığı. 'vs' sayfaları yok, alternatif içeriği yok, bir araştırmacının "
        "bunu bugün nasıl çözdüğünün (Sketchfab gömme, figshare/Zenodo yükleme, video figür) hiç kabulü yok. "
        "Bu, karşılaştırma arama trafiğini rakiplere bırakıyor ve karşılaştırmayı potansiyel müşterinin kendi "
        "çerçevelemesine terk ediyor."
    ),
    "Marka & Güven": (
        "Güçlü yönler: Hakkında sayfası inandırıcı bir misyon ve gerçekten güven veren bir 'açık standartlar "
        "üzerine kurulu (glTF/GLB, USDZ, WebXR)' mesajı sunuyor — taşınabilirliğe önem veren akademik kitle "
        "için tam isabet. Uyumluluk/anonimleştirme çerçevesi ciddiyet sinyali veriyor.",
        "Zayıf yönler: Ekip/kurucu yok, kurumsal bağlılık yok, referans yok, logo yok, kullanım sayısı yok. "
        "Akademisyenler için akran/kurum doğrulaması 1 numaralı güven tetikleyicisidir ve burada hiç yok."
    ),
    "Büyüme & Strateji": (
        "Güçlü yönler: Akıllı, farklılaşmış fiyatlandırma (model başına, tek seferlik, erişim penceresine göre "
        "— abonelik yorgunluğu yok). Net kurumsal üst-satış yolu. Araştırmacıların araç keşfetme biçimine uyan "
        "bir içerik motoru (blog + disiplinler). free→Academic→Extended merdiveni gerçek yayın yaşam "
        "döngülerine eşleniyor.",
        "Zayıf yönler: Büyüme döngüsü yok (referans/galeri/paylaşım), içerik trafiğini zamanla dönüştürecek "
        "e-posta beslemesi yok, elde tutma mekaniği yok. Huni 'gel → kayıt ol ya da git' şeklinde."
    ),
}

FINDINGS = [
    ("Kritik", "Sayfanın hiçbir yerinde sosyal kanıt yok — CTA kurumların 'zaten AcademicAR kullandığını' "
     "iddia ediyor ama tek bir logo, referans, isim verilmiş üniversite, atıf veya kullanım sayısı göstermiyor. "
     "Akademik kitle için en yüksek kaldıraçlı eksik."),
    ("Kritik", "Tek dönüşüm yolu — her CTA register'a gidiyor. İçerik kaynaklı huni-başı trafiği (blog + 12 "
     "disiplin sayfası) için e-posta yakalama veya lead magnet yok; hazır olmayanlar kayıpsız akıp gidiyor."),
    ("Yüksek", "Ana sayfa (landing.html) meta description bloğunu ezmiyor; ana-sayfaya özel bir açıklama yerine "
     "genel base varsayılanını miras alıyor."),
    ("Yüksek", "Rakip farkındalığı veya karşılaştırma içeriği yok (Sketchfab, figshare, Zenodo'ya karşı). "
     "Yüksek niyetli karşılaştırma arama trafiği rakiplere bırakılıyor."),
    ("Yüksek", "Hero CTA'sı 'Start Publishing' — gerçek bir ücretsiz katman olmasına rağmen eylem noktasında "
     "risk azaltıcı ('ücretsiz / kredi kartı yok') sinyali yok."),
    ("Orta", "Fiyatlandırma sayfası itirazları iyi karşılıyor ama satır-içi fiyat SSS'si yok (süre dolunca ne "
     "olur, yenileme, $9.90 gerçekten tek seferlik mi, iade)."),
    ("Orta", "'featured / öne çıkan' plan stili birincil dönüşüm hedefi Academic yerine Extended Archive'da — "
     "dikkat yanlış katmana sabitlenmiş."),
    ("Orta", "Hakkında sayfasının güçlü bir misyonu var ama ekip, kurucu veya kurumsal bağlılık yok — tam da "
     "akademik alıcının aradığı kredibilite sinyalleri."),
    ("Düşük", "OG/Twitter paylaşım görseli clinical-spatial-hero.png iken hero ürün görseli etkileşimli 3D "
     "viewer; paylaşılan link önizlemesi gerçek ürün deneyimini göstermeyebilir."),
    ("Düşük", "En güçlü kanıt varlığı — 'View Example Publication' — hero'da bir kez kullanılıp içerik "
     "sayfalarında pekiştirilmiyor."),
]

QUICK = [
    ("Ana sayfaya özel meta description ekle", "landing.html ~satır 2. Başlıkla eşleşen: 'Makale, tez ve "
     "posterlerinize etkileşimli 3D modeller, AR ve kalıcı QR ekleyin. Kod yok — GLB/STL/OBJ/FBX yükleyin ve "
     "paylaşın.' Etki yüksek, süre 5 dakika."),
    ("CTA bandının üstüne bir gerçek sosyal kanıt koy", "İsim verilmiş bir pilot üniversite, bir kullanım "
     "sayısı ya da elinizde zaten olan gerçek örnek yayın (DOI + ekran görüntüsü). Tek bir satır bile her "
     "kopya değişikliğinden daha çok iş görür. landing.html ~satır 327."),
    ("Hero CTA'sını risk azaltıcı yap", "'Start Publishing' jenerik. 'Ücretsiz başla — kredi kartı yok' olarak "
     "etiketle veya butonun altına mikro-satır ekle. Gerçek ücretsiz katman zaten var; eylem noktasında söyle. "
     "landing.html ~satır 20."),
    ("'Zaten kullanıyor' iddiasını yumuşat ya da kanıtla", "Kanıtla (kazanım #2) ya da faydaya çevir "
     "('Araştırmacılarla, araştırmacılar için geliştirildi'). landing.html ~satır 332."),
    ("Fiyatlandırma sayfasına satır-içi SSS ekle", "Sayfa itirazları iyi karşılıyor ama bariz olanları "
     "yanıtlamıyor: Süre dolunca ne olur? Yenilenir mi? $9.90 gerçekten tek seferlik mi? İade? Ayrı /faq var; "
     "4–5 fiyat-özel S&C'yi planların yanına getir."),
    ("Academic planını 'En popüler' olarak etiketle", "Orta plan birincil dönüşüm hedefin; 'featured' stili şu "
     "an Extended Archive'da. Dikkati en çok seçilmesini istediğin plana sabitle. pricing.html ~satır 95–135."),
    ("OG görselini ürünle eşleştir", "base.html og:image/twitter:image clinical-spatial-hero.png kullanıyor; "
     "paylaşımda tıklanan görsel gerçek 3D-viewer/AR deneyimini göstermeli."),
]

MEDIUM = [
    "Hazır olmayan ziyaretçiler için e-posta yakalama / yumuşak-CTA yolu ekle (lead magnet: 'Bir sonraki "
    "makalenize 3D/AR eklemenin 1 sayfalık rehberi') — içerik trafiği yakalanmadan sekmesin.",
    "Gerçek, atıf verilebilir bir örnek yayın yayınla (DOI + gömülü viewer + QR + ekran görüntüsü) ve onu "
    "hero'dan, AR bölümünden, blogdan ve disiplin sayfalarından öne çıkar. Tek varlıkta kanıt + demo.",
    "1–2 karşılaştırma sayfası kur: 'AcademicAR vs Sketchfab (araştırma için)' ve 'vs figshare/Zenodo (3D "
    "ek-materyal için)' — konumlandırma + yüksek niyetli SEO.",
    "Marka & Güven'i kredibilite işaretleriyle güçlendir: kısa bir kurucu/ekip notu, varsa lab/üniversite "
    "bağlılığı ve mevcut oldukça dergi/kurum logoları.",
    "Model-başına fiyat farklılaştırıcısını açık hale getir ('Model başına tek ödeme. Abonelik yok.') ve "
    "yenileme/sona erme yolunu netleştirerek ölü-link korkusunu gider — dayanıklılık zaten ana faydan.",
]

STRATEGIC = [
    "Kurumsal / bölüm bazlı satış hareketi kur: bir 'üniversiteler için' sayfası, bir sayfalık PDF ve isim "
    "verilmiş bir pilot. Akademik satış yukarıdan-aşağıdır; bir kütüphane/bölüm anlaşması yüzlerce bireysel "
    "modelden değerlidir.",
    "Disipline göre SEO içerik-boşluğu kampanyası: araştırmacıların gerçekten yazdığı sorgular ('teze nasıl "
    "3D model eklenir', 'dergi makalesi için etkileşimli figürler', 'poster için 3D model QR kodu') — disiplin "
    "ve karşılaştırma sayfalarıyla iç-bağla. Mevcut teknik-SEO avantajını katlar.",
    "Büyüme döngüsü olarak yayınlanmış-modeller galerisi: her ücretli model QR'lı bir herkese açık viewer "
    "sayfası. Sürekli sosyal kanıt + SEO yüzeyi + yazar-tahrikli referans mekaniği.",
]

COMPET = [
    ["Olası alternatif", "Araştırmacı nasıl kullanır", "AcademicAR'ın vurgulayacağı üstünlük"],
    ["Sketchfab", "3D viewer gömme", "Akademiye özgü, kalıcı QR + AR, atıf/DOI çerçevesi, dayanıklı link"],
    ["figshare / Zenodo", "3D dosya yükleme/deposit", "Tarayıcı-içi etkileşimli viewer + AR, sadece indirme değil"],
    ["Gömülü video / GIF", "3D figürü 'gösterme'", "Gerçek etkileşim + gerçek ölçekli AR yerleştirme"],
    ["Kendi model-viewer host'u", "DIY gömme", "Kod yok, dönüşüm hattı, QR çözücü, hosting dayanıklılığı"],
]

PRICING = [
    "Konumlandırma sağlam: 'workspace ücretsiz, erişim model başına' ilkesi net ve QR/viewer URL'inin plan "
    "değişse de sabit kalması gerçek bir güven mesajı.",
    "Üç katman gerçek yayın yaşam döngülerine eşleniyor: 3 gün önizleme (Free), 3 yıl yayın (Academic $9.90), "
    "10 yıl arşiv (Extended $24.90).",
    "Eksikler: (1) 'featured' vurgusu yanlış katmanda; (2) tek-seferlik / abonelik-yok avantajı yeterince "
    "bağırılmıyor; (3) süre dolunca davranış (yenileme, link akıbeti) sayfada net değil — bu, dayanıklılık "
    "vaadiyle çelişiyor ve satın alma sürtünmesi yaratıyor.",
]


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20*mm, 12*mm, "AcademicAR — Pazarlama Denetim Raporu")
    canvas.drawRightString(190*mm, 12*mm, f"Sayfa {doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(20*mm, 15*mm, 190*mm, 15*mm)
    canvas.restoreState()


def build(path):
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm,
                            title="AcademicAR Pazarlama Denetim Raporu", author="AI Marketing Suite")
    F = []
    g, gcol = grade(OVERALL)

    # --- KAPAK ---
    F.append(Paragraph("PAZARLAMA DENETİM RAPORU", EY))
    F.append(Paragraph("AcademicAR", H1))
    F.append(Paragraph("Akademik yayıncılık için etkileşimli 3D + AR + kalıcı QR platformu", SUB))
    F.append(Spacer(1, 6))
    F.append(rule(INK, 1.4, 2, 10))

    meta = Table([
        [Paragraph("URL", CELLB), Paragraph("academicar.com (repo-kaynak modu)", CELL)],
        [Paragraph("Tarih", CELLB), Paragraph(DATE, CELL)],
        [Paragraph("İş türü", CELLB), Paragraph("SaaS / Yazılım (niş: akademik yayıncılık araçları)", CELL)],
    ], colWidths=[30*mm, 140*mm])
    meta.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    F.append(meta)
    F.append(Spacer(1, 8))

    # Genel skor kutusu
    score_tbl = Table([[
        Paragraph(f"<font size=34><b>{OVERALL}</b></font><font size=14>/100</font>", S("x", fontName="DV-B", textColor=white, alignment=TA_CENTER)),
        Paragraph(f"<b>Genel Pazarlama Skoru</b><br/>Not: <b>{g}+ </b> — güçlü temel, tek yapısal eksik skoru aşağı çekiyor", S("y", textColor=white, fontSize=10, leading=15)),
    ]], colWidths=[45*mm, 125*mm])
    score_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0), gcol),
        ("BACKGROUND",(1,0),(1,0), INK),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
    ]))
    F.append(score_tbl)
    F.append(Spacer(1, 10))

    # Kapsam notu
    F.append(Paragraph("⚠ Kapsam notu", H3))
    F.append(Paragraph(
        "Canlı site (academicar.com) bu ortamın ağ politikası tarafından engellendi ('host_not_allowed') ve "
        "'www.' alt alanının DNS kaydı yok. Bu nedenle denetim, sayfa-içi içerik ve SEO etiketleri için kaynak "
        "olan <b>repodaki gerçek render edilen pazarlama metni</b> (landing.html, pricing.html, about.html, "
        "base.html + app.py SEO altyapısı) üzerinden yapıldı. Canlı sinyaller dışarıda kaldı: Core Web Vitals / "
        "sayfa hızı, üçüncü-taraf itibar (G2, backlink), gerçek trafik/dönüşüm sayıları. Bunlar için kısıtsız "
        "bir ortamda 'academicar.com' adresine canlı denetim tekrarlanmalı.", BODY))

    F.append(PageBreak())

    # --- YÖNETİCİ ÖZETİ ---
    F.append(Paragraph("Yönetici Özeti", H2))
    F.append(rule())
    F.append(Paragraph(
        "AcademicAR, erken aşamadaki bir ürün için <b>net ve özgüvenli bir ürün hikâyesine ve alışılmadık "
        "derecede güçlü teknik SEO'ya</b> sahip. Ana sayfa 5 saniye testini geçiyor ('Publish Interactive 3D "
        "Models with Your Research'), Geleneksel↔AcademicAR karşılaştırması gerçekten ikna edici ve kod tabanı "
        "sitemap.xml, robots.txt, canonical URL'ler, Open Graph/Twitter kartları ve üç tür JSON-LD "
        "(Organization, WebSite, SoftwareApplication, FAQPage) sunuyor. 6 yazılık blog ve 12 disiplin "
        "sayfası gerçek bir içerik-pazarlama motoru veriyor. Niş bir akademik araç için mesaj disiplini "
        "ortalamanın belirgin biçimde üstünde.", BODY))
    F.append(Paragraph(
        "Skoru aşağı çeken en büyük tek unsur <b>sosyal kanıt yokluğu</b>. CTA tam anlamıyla 'Join "
        "researchers, universities and journals already using AcademicAR' diyor — ama bu iddiayı destekleyecek "
        "sayfada tek bir logo, referans, isim verilmiş kurum, atıf ya da kullanım sayısı yok. Güven ve akran "
        "doğrulamasının benimsemeyi neredeyse her pazardan daha çok yönlendirdiği akademik kitlede bu, en "
        "yüksek kaldıraçlı eksiktir; aynı anda Dönüşüm, Marka & Güven ve Rekabetçi Konumlandırma'yı zayıflatır.", BODY))
    F.append(Paragraph(
        "İkinci yapısal eksik <b>tek dönüşüm yolu</b>: her CTA register'a işaret ediyor. SEO/içerik motorunuz "
        "bir blog yazısı ya da disiplin sayfası okuyan huni-başı araştırmacıları çekmek üzere kurulu — ama "
        "onlara sunduğunuz tek şey 'hesap oluştur'. İlk ziyarette kaydolmaya hazır olmayan %95 için e-posta "
        "yakalama, lead magnet ya da 'gerçek bir yayınlanmış örnek gör' kancası yok. (Hero'da 'View Example "
        "Publication' var — bu varlık az kullanılıyor; içerik sayfalarında ve her yerde yumuşak dönüşüm olarak "
        "görünmeli.)", BODY))
    F.append(Paragraph(
        "<b>İğneyi en çok oynatacak 3 hamle:</b> (1) gerçek sosyal kanıt ekle — bir isim verilmiş pilot "
        "üniversite + DOI'li gerçek bir yayınlanmış makale örneği bile yeterli; (2) ana sayfaya özel meta "
        "description ve hazır-olmayan ziyaretçiler için e-posta yakalama ekle; (3) bariz alternatiflere karşı "
        "kategorini tanımlamak ve yüksek-niyetli aramayı yakalamak için 1–2 karşılaştırma sayfası kur "
        "('AcademicAR vs Sketchfab', 'vs figshare/Zenodo').", BODY))

    # --- SKOR KIRILIMI ---
    F.append(Paragraph("Skor Kırılımı", H2))
    F.append(rule())
    rows = [[Paragraph("Kategori", CELLW), Paragraph("Skor", CELLW), Paragraph("Ağırlık", CELLW),
             Paragraph("Görsel", CELLW), Paragraph("Temel bulgu", CELLW)]]
    for name, sc, w, finding in CATS:
        g2, c2 = grade(sc)
        rows.append([
            Paragraph(name, CELLB),
            Paragraph(f"{sc}/100", CELL),
            Paragraph(w, CELL),
            bar(sc, 32*mm),
            Paragraph(finding, CELL),
        ])
    rows.append([Paragraph("TOPLAM", CELLB), Paragraph(f"{OVERALL}/100", CELLB),
                 Paragraph("100%", CELL), bar(OVERALL, 32*mm), Paragraph(f"Not {g}+", CELLB)])
    t = Table(rows, colWidths=[34*mm, 16*mm, 15*mm, 34*mm, 71*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), INK),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,-1),0.25, LINE),
        ("ROWBACKGROUNDS",(0,1),(-1,-2),[white, SOFT]),
        ("BACKGROUND",(0,-1),(-1,-1), HexColor("#efe7e2")),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
    ]))
    F.append(t)

    F.append(PageBreak())

    # --- KATEGORİ DETAYLARI ---
    F.append(Paragraph("Kategori Bazında Detaylı Analiz", H2))
    F.append(rule())
    for name, sc, w, _ in CATS:
        g2, c2 = grade(sc)
        head = Table([[Paragraph(name, S("h", fontName="DV-B", fontSize=12, textColor=INK)),
                       Paragraph(f"<b>{sc}/100</b>  ·  {g2}", S("s", fontName="DV-B", fontSize=11, textColor=c2, alignment=2))]],
                     colWidths=[120*mm, 50*mm])
        head.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
        F.append(head)
        F.append(bar(sc, 170*mm))
        F.append(Spacer(1, 5))
        pos, neg = CAT_DETAIL[name]
        F.append(Paragraph(pos, BODY))
        F.append(Paragraph(neg, S("neg", fontSize=10, leading=15.5, textColor=GRAPHITE, alignment=TA_JUSTIFY, spaceAfter=10, leftIndent=0)))

    F.append(PageBreak())

    # --- BULGULAR ---
    F.append(Paragraph("Önem Derecesine Göre Bulgular", H2))
    F.append(rule())
    frows = [[Paragraph("Önem", CELLW), Paragraph("Bulgu", CELLW)]]
    for sev, txt in FINDINGS:
        frows.append([Paragraph(f'<b>{sev}</b>', S("sv", fontName="DV-B", fontSize=8.8, textColor=sev_color(sev))),
                      Paragraph(txt, CELL)])
    ft = Table(frows, colWidths=[22*mm, 148*mm])
    ft.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), INK),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LINEBELOW",(0,0),(-1,-1),0.25, LINE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[white, SOFT]),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
    ]))
    F.append(ft)

    # --- HIZLI KAZANIMLAR ---
    F.append(Paragraph("Hızlı Kazanımlar (Bu Hafta)", H2))
    F.append(rule())
    for i, (title, detail) in enumerate(QUICK, 1):
        F.append(Paragraph(f"{i}. {title}", H3))
        F.append(Paragraph(detail, LI))

    F.append(PageBreak())

    # --- ORTA VADE ---
    F.append(Paragraph("Stratejik Öneriler (Bu Ay)", H2))
    F.append(rule())
    for i, item in enumerate(MEDIUM, 1):
        F.append(Paragraph(f"<b>{i}.</b> {item}", LI))
    F.append(Spacer(1, 6))

    # --- UZUN VADE ---
    F.append(Paragraph("Uzun Vadeli Girişimler (Bu Çeyrek)", H2))
    F.append(rule())
    for i, item in enumerate(STRATEGIC, 1):
        F.append(Paragraph(f"<b>{i}.</b> {item}", LI))
    F.append(Spacer(1, 6))

    # --- FİYATLANDIRMA ANALİZİ ---
    F.append(Paragraph("Fiyatlandırma Analizi", H2))
    F.append(rule())
    for item in PRICING:
        F.append(Paragraph(f"• {item}", LI))

    F.append(PageBreak())

    # --- RAKİP TABLOSU ---
    F.append(Paragraph("Rakip Karşılaştırması", H2))
    F.append(rule())
    F.append(Paragraph(
        "Canlı çekim engellendiği ve sitede rakip referansı bulunmadığı için rakip verisi toplanamadı. "
        "Araştırılması ve karşı-konumlandırılması gereken olası set:", BODY))
    crows = [[Paragraph(c, CELLW) for c in COMPET[0]]]
    for row in COMPET[1:]:
        crows.append([Paragraph(row[0], CELLB), Paragraph(row[1], CELL), Paragraph(row[2], CELL)])
    ct = Table(crows, colWidths=[36*mm, 52*mm, 82*mm])
    ct.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), INK),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LINEBELOW",(0,0),(-1,-1),0.25, LINE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[white, SOFT]),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
    ]))
    F.append(ct)
    F.append(Paragraph("Aksiyon: canlı çekim mümkün olunca '/market competitors' ile bu tablo gerçek veriyle doldurulmalı.", SMALL))

    # --- GELİR ETKİSİ ---
    F.append(Paragraph("Gelir Etkisi Özeti", H2))
    F.append(rule())
    F.append(Paragraph(
        "Canlı trafik, dönüşüm-oranı veya gelir verisi olmadığından net dolar rakamları uydurma olurdu; etki "
        "bunun yerine kaldıraca göre sıralanmıştır. (Fiyatlandırmadan referans ARPU: model başına ~$9.90–$24.90 "
        "tek seferlik; kurumsal anlaşmalar belirgin biçimde daha yüksek.)", BODY))
    irows = [[Paragraph("Öneri", CELLW), Paragraph("Kaldıraç", CELLW), Paragraph("Güven", CELLW), Paragraph("Süre", CELLW)]]
    impact = [
        ("Gerçek sosyal kanıt (logo/alıntı/örnek)", "Yüksek — Dönüşüm+Güven+Konumlandırma açılır", "Yüksek", "Bu hafta"),
        ("İçerik trafiği için e-posta/soft-CTA", "Yüksek — mevcut SEO hunisini dönüştürür", "Yüksek", "2–3 hafta"),
        ("Karşılaştırma sayfaları (vs Sketchfab/figshare)", "Yüksek — yüksek niyetli SEO + konumlandırma", "Orta", "Bu ay"),
        ("Özel ana-sayfa meta + risk azaltıcı CTA", "Orta — CTR + ilk-temas dönüşümü", "Yüksek", "Bu hafta"),
        ("Fiyat SSS + 'En popüler' sabitleme", "Orta — satın alma sürtünmesini azaltır", "Orta", "Bu hafta"),
        ("Kurumsal tek-sayfa + isimli pilot", "Yüksek — büyük anlaşma boyutu", "Orta", "Bu çeyrek"),
    ]
    for r in impact:
        irows.append([Paragraph(r[0], CELLB), Paragraph(r[1], CELL), Paragraph(r[2], CELL), Paragraph(r[3], CELL)])
    it = Table(irows, colWidths=[55*mm, 66*mm, 22*mm, 27*mm])
    it.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), INK),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LINEBELOW",(0,0),(-1,-1),0.25, LINE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[white, SOFT]),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
    ]))
    F.append(it)

    # --- SONRAKİ ADIMLAR ---
    F.append(Paragraph("Sonraki Adımlar", H2))
    F.append(rule())
    steps = [
        "Bu hafta ana-sayfa meta description'ı + bir gerçek kanıt unsuru yayınla — en düşük efor, en yüksek anlık kaldıraç.",
        "İçerik motorunun huni-başı trafiği sızdırmayı bırakması için örnek-yayın vitrinini ve bir e-posta yakalama yolunu ayağa kaldır.",
        "Kısıtsız bir ortamda 'academicar.com' canlı denetimini tekrarla (sayfa hızı, Core Web Vitals, itibar); ardından '/market competitors' ile karşılaştırma tablosunu doldur.",
    ]
    for i, s in enumerate(steps, 1):
        F.append(Paragraph(f"<b>{i}.</b> {s}", LI))
    F.append(Spacer(1, 10))
    F.append(rule(LINE, 0.6, 2, 4))
    F.append(Paragraph("AI Marketing Suite — /market audit (repo-kaynak modu; canlı çekim ağ politikasıyla engellendi) tarafından üretildi.", SMALL))

    doc.build(F, onFirstPage=header_footer, onLaterPages=header_footer)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "MARKETING-REPORT-TR.pdf")
    out = os.path.abspath(out)
    build(out)
    print("Yazıldı:", out)
