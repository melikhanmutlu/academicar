#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AcademicAR — detaylı Türkçe rekabet istihbaratı raporu (PDF).

DejaVuSans TTF (tam Türkçe karakter desteği) + Reportlab Platypus.
Somut fırsatlar genişletilmiş ayrı bir bölüm olarak işlenir.
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, HRFlowable)

FD = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DV", f"{FD}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DV-B", f"{FD}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DV-I", "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"))

INK = HexColor("#202020"); GRAPHITE = HexColor("#4d4d4d"); MUTED = HexColor("#828282")
LINE = HexColor("#e2e2e2"); SOFT = HexColor("#f5f5f5"); ORANGE = HexColor("#E8602C")
GREEN = HexColor("#2f8f4e"); AMBER = HexColor("#c98a1e"); RED = HexColor("#c0392b")
ORSOFT = HexColor("#fbeee7")


def st(name, **kw):
    base = dict(fontName="DV", textColor=INK, fontSize=10, leading=15)
    base.update(kw); return ParagraphStyle(name, **base)


H1 = st("H1", fontName="DV-B", fontSize=23, leading=27, spaceAfter=4)
SUB = st("SUB", fontSize=11, textColor=MUTED, leading=15)
EY = st("EY", fontName="DV-B", fontSize=9, textColor=ORANGE, spaceAfter=2)
H2 = st("H2", fontName="DV-B", fontSize=15, leading=19, spaceBefore=12, spaceAfter=5)
H3 = st("H3", fontName="DV-B", fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=3)
BODY = st("BODY", fontSize=10, leading=15.3, textColor=GRAPHITE, alignment=TA_JUSTIFY, spaceAfter=6)
LI = st("LI", fontSize=9.7, leading=14.4, textColor=GRAPHITE, leftIndent=10, spaceAfter=3)
SMALL = st("SMALL", fontSize=8.3, leading=11.5, textColor=MUTED)
CELL = st("CELL", fontSize=8.5, leading=11.5, textColor=GRAPHITE)
CELLB = st("CELLB", fontName="DV-B", fontSize=8.5, leading=11.5, textColor=INK)
CELLW = st("CELLW", fontName="DV-B", fontSize=8.6, leading=11.5, textColor=white)
QUOTE = st("QUOTE", fontName="DV-I", fontSize=10, leading=15, textColor=INK, leftIndent=10, rightIndent=8, alignment=TA_JUSTIFY, spaceAfter=6)


def rule(c=LINE, w=1.0, sb=3, sa=8):
    return HRFlowable(width="100%", thickness=w, color=c, spaceBefore=sb, spaceAfter=sa)


def tbl(rows, widths, header=True, zebra=True, first_bold=True):
    t = Table(rows, colWidths=widths)
    s = [("VALIGN", (0,0), (-1,-1), "TOP"),
         ("LINEBELOW", (0,0), (-1,-1), 0.25, LINE),
         ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
         ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5)]
    if header:
        s.append(("BACKGROUND", (0,0), (-1,0), INK))
    if zebra:
        s.append(("ROWBACKGROUNDS", (0,1 if header else 0), (-1,-1), [white, SOFT]))
    t.setStyle(TableStyle(s)); return t


def P(txt, style=CELL):
    return Paragraph(txt, style)


# ---------------------------------------------------------------- İÇERİK
DATE = "13 Haziran 2026"


def hf(c, d):
    c.saveState(); c.setFont("DV", 8); c.setFillColor(MUTED)
    c.drawString(20*mm, 12*mm, "AcademicAR — Rekabet İstihbaratı Raporu")
    c.drawRightString(190*mm, 12*mm, f"Sayfa {d.page}")
    c.setStrokeColor(LINE); c.line(20*mm, 15*mm, 190*mm, 15*mm); c.restoreState()


def build(path):
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm,
                            title="AcademicAR Rekabet İstihbaratı Raporu", author="AI Marketing Suite")
    F = []

    # KAPAK
    F.append(Paragraph("REKABET İSTİHBARATI RAPORU", EY))
    F.append(Paragraph("AcademicAR", H1))
    F.append(Paragraph("Akademik yayıncılık için etkileşimli 3D + AR + kalıcı QR platformu", SUB))
    F.append(Spacer(1, 5)); F.append(rule(INK, 1.4, 2, 10))
    meta = tbl([
        [P("Tarih", CELLB), P(DATE, CELL)],
        [P("Analiz edilen rakip", CELLB), P("8 (3 doğrudan, 3 dolaylı, 2 aspirasyonel)", CELL)],
        [P("Rekabetçi konum", CELLB), P("Orta — gerçek ve savunulabilir bir boşluk var, ama mindshare/dağıtım rakiplerde", CELL)],
        [P("Yöntem", CELLB), P("Canlı çekim (WebFetch) tüm dış host'lara engelli; veri WebSearch ile, kaynaklı toplandı", CELL)],
    ], [40*mm, 130*mm], header=False, zebra=False)
    F.append(meta); F.append(Spacer(1, 10))

    # ÖNE ÇIKAN BULGU kutusu
    box = Table([[Paragraph(
        "<b>EN ÖNEMLİ BULGU.</b> Sektör lideri <b>Sketchfab, Epic Games'in Fab pazaryerine taşındı</b>: "
        "mağazası kapandı, ücretsiz lisanslama kaldırılıyor ve modellerin bir kısmı (CC-BY-SA, Editorial) "
        "taşınamıyor. Müze / kültürel miras / akademik topluluk — <b>yani tam da AcademicAR'ın hedef "
        "kitlesi</b> — bu durumdan endişeli; \"Sketchfab'i yaşatın\" imza kampanyası dönüyor. Bu, bu "
        "manzaradaki en zamanlı ve en sömürülebilir fırsattır.",
        st("box", fontSize=10, leading=15, textColor=INK))]], colWidths=[170*mm])
    box.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), ORSOFT),
                             ("BOX",(0,0),(-1,-1), 0.8, ORANGE),
                             ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
                             ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    F.append(box)

    F.append(PageBreak())

    # YÖNETİCİ ÖZETİ
    F.append(Paragraph("Yönetici Özeti", H2)); F.append(rule())
    for p in [
        "AcademicAR, <b>akademiye özgü tek bir baskın oyuncunun bulunmadığı</b> bir alanda yarışıyor — bu "
        "hem en büyük fırsatı hem de kategorisini anlatmasının neden zor olduğunun nedeni. Web'de 3D pazarına "
        "Sketchfab liderlik ediyor; ama Sketchfab, Epic Games'in <b>Fab</b> pazaryerine soğuruldu: mağazası "
        "kapandı, ücretsiz lisanslama kaldırılıyor ve müze/miras/akademik topluluk, \"Sketchfab'i yaşatın\" "
        "imza kampanyası çevirecek kadar tedirgin. Bu göç kargaşası, şu an bu manzarada sömürülebilecek en "
        "büyük tekil olay.",
        "Akademiye özgü mevcut oyuncular AcademicAR'dan daha dar. <b>MorphoSource</b> (Duke) güvenilir "
        "akademik 3D arşivi (~27.000 model, ücretsiz, DOI ile atıf verilebilir, tarayıcıda görüntüleme) ama "
        "<b>numune/arşiv</b> odaklı, kurumsal ve \"herhangi bir makaleye cilalı 3D+AR viewer ekle\" tarzı "
        "self-servis bir ürün değil; app-free AR ve baskı-QR'ı öne çıkarmıyor. <b>figshare</b> ve "
        "<b>Zenodo</b> DOI'li, yayıncı entegrasyonlu (Springer Nature, ACS) araştırma-veri depoları ama "
        "dosyaları genel biçimde gösteriyor; ithtisaslaşmış, AR-hazır bir 3D viewer'ları yok. <b>Smithsonian "
        "Voyager</b> gerçekten güçlü, açık kaynak bir 3D gezgini (WebXR AR, açıklama/tur) ama kendi-host'lu "
        "ve teknik; bir doktora öğrencisinin beş dakikada benimseyeceği bir SaaS değil.",
        "<b>AcademicAR'ın savunulabilir konumu, hiçbir rakibin tam olarak doldurmadığı kesişim:</b> "
        "akademiye-özgü (DOI/atıf çerçevesi, uyumluluk) + self-servis SaaS cilası + app-free mobil AR + "
        "<b>model değiştirilse bile kopmayan kalıcı QR/viewer URL'i</b> + model başına tek seferlik fiyat "
        "(abonelik yok). Sketchfab'de AR/QR/cila var ama artık oyun-ve-ticaret odaklı ve göç ortasında; "
        "MorphoSource'ta akademik güven var ama ürün yok; depolarda DOI var ama viewer yok; WebAR araçlarında "
        "(echo3D, Zappar, AR Code) AR+QR var ama sıfır akademik bağlam.",
        "<b>İlk 3 stratejik hamle:</b> (1) bir <b>\"araştırmacılar için Sketchfab alternatifi\"</b> sayfası "
        "kur ve Fab-göçü endişesini istikrar + açık standartlar + akademik-öncelikli mesajıyla yakala; "
        "(2) <b>kopmayan link / kırılmayan ek-materyal</b> vaadini depolara ve ölen embed'lere karşı ana "
        "farklılaştırıcı yap; (3) <b>yayıncı ve kurumsal entegrasyon</b> peşine düş — Taylor &amp; Francis "
        "zaten Sketchfab'i makalelerine gömüyor; bu hem tehdit hem ödül.",
    ]:
        F.append(Paragraph(p, BODY))

    # RAKİP MANZARASI
    F.append(Paragraph("Rakip Manzarası", H2)); F.append(rule())
    F.append(Paragraph("Doğrudan rakipler", H3))
    F.append(tbl([
        [P("Rakip", CELLW), P("Nedir", CELLW), P("Konumlandırma", CELLW), P("Fiyat (yön)", CELLW), P("AcademicAR'a karşı ayrım", CELLW)],
        [P("Sketchfab / Fab (Epic)", CELLB), P("Genel 3D viewer + (eski) pazaryeri", CELL), P("Varsayılan 3D embed; artık Fab altında oyun/ticaret", CELL), P("Ücretsiz; ~$15/ay+; AR Premium'da", CELL), P("Mindshare + app-free AR + oto-QR; ama göç ortasında, mağaza kapalı, ücretsiz lisans kalkıyor", CELL)],
        [P("MorphoSource (Duke)", CELLB), P("Akademik 3D numune arşivi", CELL), P("Biyo/miras için güvenilir bilimsel depo", CELL), P("Ücretsiz (fon destekli)", CELL), P("Akademik güven + DOI + 27k model; ama arşiv/numune odaklı, self-servis AR/QR ürünü değil", CELL)],
        [P("Smithsonian Voyager", CELLB), P("Açık kaynak 3D gezgini", CELL), P("Bilimsel 3D anlatı + WebXR AR", CELL), P("Ücretsiz / OSS (self-host)", CELL), P("Güçlü viewer, açıklama, AR; ama teknik, self-host, baskı-QR akışı yok", CELL)],
    ], [26*mm, 30*mm, 33*mm, 24*mm, 57*mm]))
    F.append(Spacer(1, 6))
    F.append(Paragraph("Dolaylı rakipler", H3))
    F.append(tbl([
        [P("Rakip", CELLW), P("Nedir", CELLW), P("Neden rakip", CELLW), P("AcademicAR'ın kullandığı boşluk", CELLW)],
        [P("figshare", CELLB), P("Araştırma-veri deposu", CELL), P("DOI, ek-materyal, yayıncı entegrasyonu", CELL), P("İthtisas 3D+AR viewer yok; genel dosya gösterimi", CELL)],
        [P("Zenodo (CERN)", CELLB), P("Açık araştırma deposu", CELL), P("Ücretsiz DOI, arşiv kalıcılığı", CELL), P("Deposit/indirme; tarayıcı-içi etkileşimli 3D+AR değil", CELL)],
        [P("Yayıncı-içi (Elsevier, T&F+Sketchfab, 3D-PDF)", CELLB), P("Makale-içi 3D viewer", CELL), P("Okuyucu makalede 3D'yi keşfeder", CELL), P("Tek yayıncı/formata kilitli; yazarın kontrol ettiği taşınabilir QR+viewer yok", CELL)],
    ], [30*mm, 34*mm, 44*mm, 62*mm]))
    F.append(Spacer(1, 6))
    F.append(Paragraph("Aspirasyonel rakipler", H3))
    F.append(Paragraph("• <b>Zirvedeki Sketchfab / Smithsonian 3D Open Access</b> — AcademicAR'ın akademide istediği "
                       "güven + erişim standardı (açık, atıf verilebilir, tarayıcı-yerel, kurum destekli).", LI))
    F.append(Paragraph("• <b>WebAR platformları (8th Wall, Zappar)</b> — sınıfının en iyisi app-free AR mühendisliği ve "
                       "marka cilası (ama akademik konumlandırma yok).", LI))

    F.append(PageBreak())

    # DETAYLI PROFİLLER
    F.append(Paragraph("Detaylı Rakip Profilleri", H2)); F.append(rule())
    profiles = [
        ("Sketchfab (artık Fab / Epic Games)",
         "Güç: 3D yayınlama/paylaşma/gömmenin \"o\" yeri; iOS/Android'de app-free AR; masaüstünde modeli "
         "telefonda AR olarak açan <b>oto-üretilen QR kod</b> (AcademicAR'ın amiral özelliğiyle birebir "
         "örtüşüyor). G2'de ~4,4/5; kullanım kolaylığı (~9,5) ve görselleştirme kalitesiyle övülüyor; "
         "embed viewer + API'ler çalışmaya devam ediyor.",
         "Zayıflık: <b>Mağaza kapandı</b>; ücretsiz lisanslama kaldırılacak; içerik Fab'a taşınmalı ve "
         "<b>CC-BY-SA / Editorial modeller taşınamıyor</b> — arşivcileri ve müzeleri tedirgin ediyor. "
         "Marka, bilimden uzaklaşıp oyun/ticarete kayıyor. Kütüphane kalitesi tutarsız.",
         "AcademicAR fırsatı: Erişimini kaybetmekten korkan, istikrarlı ve akademik-öncelikli bir yuva "
         "isteyen müze/miras/akademik kullanıcıları çek. \"Sketchfab'den geçiş\" anlatısı zamanlı ve gerçek. "
         "Tehdit: devasa kurulu taban, ücretsiz katman, mevcut yayıncı entegrasyonu (T&F) ve hâlâ çalışan AR/QR yığını."),
        ("MorphoSource (Duke Üniversitesi)",
         "Güç: Derin akademik güven, ~27.000 numune (~13.000 açık erişim), DOI ile atıf verilebilir, "
         "yazılımsız tarayıcı görüntüleme, müze/kurum katkıcıları, nüanslı yeniden-kullanım lisanslaması.",
         "Zayıflık: Numune/morfoloji-merkezli (biyo-antropoloji, doğa tarihi); cilalı self-servis SaaS yerine "
         "arşiv/kurumsal; her disiplin için mobil AR veya baskıya-hazır QR etrafında kurulu değil; UX depo "
         "seviyesinde, ürün seviyesinde değil.",
         "Fırsat: Aynı akademik ciddiyetle <b>self-servis, her-disiplin, AR-öncelikli</b> seçenek ol — ve "
         "kafa kafaya savaşmak yerine birlikte çalış (MorphoSource'a aktar / yanında atıf ver)."),
        ("Smithsonian Voyager",
         "Güç: Açık kaynak; OBJ/PLY/glTF/GLB (Draco) destekler; <b>entegre WebXR AR</b>, ölçüm, kesit, "
         "açıklama, makale, tur; üst düzey bilimsel 3D platformu olarak anılıyor.",
         "Zayıflık: Self-host/geliştirici odaklı; hosting yok, hesap yok, baskı-QR akışı yok, teknik olmayan "
         "araştırmacı için fiyat/onboarding yok.",
         "Fırsat: AcademicAR = \"mühendislik olmadan Voyager kalitesinde görüntüleme\" (hosted, QR, hesap, kalıcı link)."),
        ("figshare / Zenodo (depolar)",
         "Güç: DOI, kalıcılık, FAIR uyumu, yayıncı entegrasyonları, kurumsal benimseme.",
         "Zayıflık: Birinci-sınıf etkileşimli 3D+AR viewer yok; deneyim yükle-ve-indir, yerinde-keşfet değil; "
         "baskı-QR'dan-AR'a köprü yok.",
         "Fırsat: Depo dünyasının <b>üstündeki viewer/AR katmanı</b> ol — \"kalıcılık için deposit, deneyim "
         "için AcademicAR\" — ve eşleşemeyecekleri kalıcı link + AR'ı vurgula."),
    ]
    for title, g, z, f in profiles:
        F.append(Paragraph(title, H3))
        F.append(Paragraph(g, BODY))
        F.append(Paragraph(z, BODY))
        F.append(Paragraph(f, st("op", fontSize=9.6, leading=14.4, textColor=GRAPHITE, alignment=TA_JUSTIFY, spaceAfter=10, backColor=SOFT, borderPadding=6)))

    F.append(PageBreak())

    # KARŞILAŞTIRMA TABLOLARI
    F.append(Paragraph("Özellik Karşılaştırması", H2)); F.append(rule())
    F.append(Paragraph("Tam / Kısmi / Yok", SMALL))
    feat = [["Özellik", "AcademicAR", "Sketchfab/Fab", "MorphoSource", "Voyager", "figshare/Zenodo"]]
    rows = [
        ("Tarayıcı-içi 3D viewer", "Tam", "Tam", "Tam", "Tam", "Kısmi"),
        ("App-free mobil AR (WebXR)", "Tam", "Tam (Premium)", "Yok", "Tam", "Yok"),
        ("Oto QR → AR (baskıya hazır)", "Tam", "Kısmi (masaüstü)", "Yok", "Yok", "Yok"),
        ("Akademik/DOI/atıf çerçevesi", "Tam", "Yok", "Tam", "Kısmi", "Tam"),
        ("Self-servis SaaS onboarding", "Tam", "Tam", "Kısmi", "Yok", "Tam"),
        ("Model değişince KOPMAYAN link", "Tam", "Yok", "Kısmi", "Yok", "Kısmi"),
        ("Uyumluluk/anonimleştirme akışı", "Tam", "Yok", "Kısmi", "Yok", "Kısmi"),
        ("Format dönüşümü (STL/OBJ/FBX→GLB)", "Tam", "Kısmi", "Kısmi", "Yok", "Yok"),
        ("Hosting kalıcılığı / arşiv", "Kısmi", "Kısmi (akışkan)", "Tam", "Yok", "Tam"),
        ("Mindshare / kurulu taban", "Düşük", "Yüksek", "Orta", "Orta", "Yüksek"),
    ]
    for r in rows:
        feat.append([P(r[0], CELLB)] + [P(x, CELL) for x in r[1:]])
    # vurgula: kopmayan link satırı
    ft = Table(feat, colWidths=[44*mm, 24*mm, 27*mm, 26*mm, 21*mm, 28*mm])
    ft.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), INK), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,-1),0.25, LINE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[white, SOFT]),
        ("BACKGROUND",(0,6),(-1,6), ORSOFT),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
    ]))
    F.append(ft)
    F.append(Spacer(1, 6))

    F.append(Paragraph("Fiyatlandırma Karşılaştırması (yönsel, aramadan)", H2)); F.append(rule())
    F.append(tbl([
        [P("", CELLW), P("AcademicAR", CELLW), P("Sketchfab/Fab", CELLW), P("MorphoSource", CELLW), P("Voyager", CELLW), P("figshare/Zenodo", CELLW), P("WebAR araçları", CELLW)],
        [P("Model", CELLB), P("Model başına tek seferlik", CELL), P("Abonelik", CELL), P("Ücretsiz (fonlu)", CELL), P("Ücretsiz/OSS", CELL), P("Ücretsiz (+figshare+)", CELL), P("Abonelik", CELL)],
        [P("Giriş", CELLB), P("$0 (3 gün)", CELL), P("Ücretsiz katman", CELL), P("Ücretsiz", CELL), P("Ücretsiz", CELL), P("Ücretsiz", CELL), P("$9–15/ay", CELL)],
        [P("Ücretli", CELLB), P("$9.90 (3y) / $24.90 (10y)", CELL), P("~$15/ay+", CELL), P("—", CELL), P("—", CELL), P("figshare+ ücretli", CELL), P("$99–$315/ay (Zappar/8th Wall)", CELL)],
        [P("Abonelik kaygısı", CELLB), P("Yok", CELLB), P("Var", CELL), P("Yok", CELL), P("Yok", CELL), P("Yok", CELL), P("Var", CELL)],
    ], [24*mm, 27*mm, 22*mm, 24*mm, 18*mm, 27*mm, 28*mm]))
    F.append(Spacer(1, 6))

    F.append(Paragraph("İnceleme Sinyali (aramadan)", H2)); F.append(rule())
    F.append(tbl([
        [P("Rakip", CELLW), P("Puan", CELLW), P("En çok övülen", CELLW), P("En çok şikayet", CELLW)],
        [P("Sketchfab", CELLB), P("~4,4/5 (G2, küçük n)", CELL), P("Kullanım kolaylığı, görselleştirme kalitesi", CELL), P("Düşük kaliteli kütüphane; (artık) göç/lisans kargaşası", CELL)],
        [P("MorphoSource", CELLB), P("y/d (akademik)", CELL), P("Açık erişim, bilimsel güven", CELL), P("Niş/arşiv, eski UX", CELL)],
        [P("Voyager", CELLB), P("y/d (OSS)", CELL), P("Güç, açık standartlar", CELL), P("Kurulumu teknik", CELL)],
    ], [28*mm, 30*mm, 56*mm, 56*mm]))

    F.append(PageBreak())

    # KONUMLANDIRMA HARİTASI
    F.append(Paragraph("Konumlandırma Haritası", H2)); F.append(rule())
    map_txt = ("AKADEMİK / BİLİMSEL\n"
               "                                |\n"
               "  MorphoSource   Voyager        |   figshare / Zenodo\n"
               "  (arşiv)        (OSS)          |   (depo + DOI)\n"
               "                                |        ★ AcademicAR\n"
               "ARŞİV/STATİK ───────────────────┼─────────────── ETKİLEŞİMLİ / AR\n"
               "                                |\n"
               "                                |   Sketchfab/Fab\n"
               "                                |   (artık oyun/ticaret)\n"
               "                  echo3D / Zappar / 8th Wall\n"
               "                                |\n"
               "GENEL / TİCARİ")
    mt = Table([[Paragraph(map_txt.replace("\n", "<br/>"), st("map", fontName="DV", fontSize=8.5, leading=12.5, textColor=INK))]], colWidths=[170*mm])
    mt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), SOFT), ("BOX",(0,0),(-1,-1),0.5, LINE),
                            ("LEFTPADDING",(0,0),(-1,-1),12),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    F.append(mt)
    F.append(Paragraph("<b>Okuma:</b> AcademicAR az-doldurulmuş sağ-üst köşeyi hedefliyor — <i>hem bilimsel HEM "
                       "etkileşimli/AR/self-servis</i>. MorphoSource/Voyager bilimsel ama arşiv/teknik; Sketchfab ve "
                       "WebAR araçları etkileşimli ama ticari. O köşe boş alan.", BODY))

    # SWOT
    F.append(Paragraph("SWOT — AcademicAR (toplu)", H2)); F.append(rule())
    swot = tbl([
        [P("GÜÇLÜ YÖNLER", CELLW), P("ZAYIF YÖNLER", CELLW)],
        [P("Akademik çerçeve + self-servis + app-free AR + baskı-QR + <b>model değişince kopmayan link</b> + "
           "abonelik-yok model-başı fiyatı birleştiren tek oyuncu; açık standartlar (glTF/USDZ/WebXR); uyumluluk akışı.", CELL),
         P("Düşük mindshare/kurulu taban; henüz sosyal kanıt ve isim verilmiş kurum yok; tek dönüşüm yolu; "
           "pazaryeri/keşif yok; hosting-kalıcılığı hikâyesi fonlu arşivlere karşı kanıtlanmamış.", CELL)],
        [P("FIRSATLAR", CELLW), P("TEHDİTLER", CELLW)],
        [P("Sketchfab→Fab göçünde akademik/miras kullanıcıların kayması; yayıncılar gömülü 3D istiyor (T&F zaten "
           "yapıyor); depolarda iyi viewer yok; \"3D'niz için istikrarlı akademik yuva\" boşta.", CELL),
         P("Sketchfab'in ücretsiz katmanı, erişimi, çalışan embed/AR/QR'ı ve mevcut yayıncı anlaşmaları; "
           "fiyatta fonlu ücretsiz arşivler (MorphoSource, Zenodo); WebAR satıcılarının üstün AR mühendisliği.", CELL)],
    ], [85*mm, 85*mm], zebra=False)
    swot.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0), GREEN), ("BACKGROUND",(1,0),(1,0), RED),
        ("BACKGROUND",(0,2),(0,2), ORANGE), ("BACKGROUND",(1,2),(1,2), MUTED),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BACKGROUND",(0,1),(-1,1), SOFT), ("BACKGROUND",(0,3),(-1,3), SOFT),
        ("BOX",(0,0),(-1,-1),0.4, LINE), ("INNERGRID",(0,0),(-1,-1),0.4, LINE),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
    ]))
    F.append(swot)

    F.append(PageBreak())

    # SOMUT FIRSATLAR — GENİŞLETİLMİŞ
    F.append(Paragraph("Somut Fırsatlar (Genişletilmiş Eylem Planı)", H2)); F.append(rule())

    # Fırsat 1
    F.append(Paragraph("Fırsat 1 — \"Sketchfab Göçünü Yakala\" (en zamanlı, en yüksek niyet)", H3))
    F.append(Paragraph(
        "<b>Neden şimdi:</b> Sketchfab'in Fab'a geçişi, mağaza kapanışı ve ücretsiz lisansın kaldırılması, "
        "tam da AcademicAR'ın hedeflediği müze/miras/akademik kullanıcıları yerinden ediyor. Bu kullanıcılar "
        "<i>aktif olarak</i> istikrarlı bir yuva arıyor. Niyet zirvede, pencere dar.", BODY))
    F.append(Paragraph("<b>Ne yapmalı:</b>", LI))
    for x in [
        "<b>/vs/sketchfab</b> karşılaştırma sayfası kur: karşılaştırma tablosu (özellik/fiyat/istikrar), "
        "AcademicAR'ın kazandığı yerler (akademik çerçeve, kopmayan link, abonelik yok), Sketchfab'in kazandığı "
        "yerler (pazaryeri/erişim — dürüstçe), kim için ideal, taşıma yardımı, SSS, CTA.",
        "<b>Geçiş teklifi:</b> \"Sketchfab'den geçenlere ücretsiz taşıma yardımı + uzatılmış ücretsiz pencere.\"",
        "<b>Geçiş anlatısı</b> (aşağıdaki alıntı) ile blog yazısı + LinkedIn gönderisi.",
        "<b>SEO:</b> 'Sketchfab alternatifi', 'Fab migration museum', 'Sketchfab for researchers' anahtar "
        "kelimelerini hedefle — dipteki, yüksek-niyetli aramalar.",
    ]:
        F.append(Paragraph("• " + x, LI))
    F.append(Paragraph(
        "\"Birçok lab ve müze gibi siz de 3D modellerinizi Sketchfab'e koydunuz çünkü işe yarıyordu. Fab'a geçişle "
        "— mağaza kapandı, lisanslama değişiyor — istikrarlı, bilimsel bir yuvaya ihtiyacınız var. AcademicAR "
        "viewer linkinizi ve QR'ınızı kalıcı tutar, her modeli atıf için çerçeveler ve açık standartlarda app-free "
        "AR çalıştırır. GLB'nizi getirin; linkleriniz kalsın.\"", QUOTE))
    F.append(Paragraph("Tahmini efor: <b>Düşük–Orta</b> · Beklenen etki: <b>Yüksek</b> · Süre: <b>Bu hafta–2 hafta</b>", SMALL))
    F.append(Spacer(1, 6))

    # Fırsat 2
    F.append(Paragraph("Fırsat 2 — Farklılaşmayı \"Kopmayan + Atıf Verilebilir + App-free AR\"a Sabitle", H3))
    F.append(Paragraph(
        "<b>Neden:</b> AcademicAR'ın gerçek savunulabilir hendeği, model değiştirilse bile <b>kopmayan kalıcı "
        "link/QR</b>. Depolar (figshare/Zenodo) kalıcılık verir ama etkileşimli viewer/AR vermez; ölen embed'ler "
        "(eski Sketchfab gömüleri) zamanla kırılır. Bu, akademik kitlenin en korktuğu şeyi — ölü ek-materyal "
        "linki — doğrudan çözer.", BODY))
    F.append(Paragraph("<b>Ne yapmalı:</b>", LI))
    for x in [
        "Ana sayfa başlık testi: <i>\"3D araştırmanız, sonsuza dek etkileşimli — app-free AR ve hiç kırılmayan bir QR.\"</i>",
        "Kanıt noktaları: linkler model değişiminden sağ çıkar; açık standartlar; model başına tek ödeme; uyumluluk dahili.",
        "Depo konumlandırması: <b>\"Kalıcılık için deposit (Zenodo/figshare), deneyim için AcademicAR.\"</b> — rakip değil, tamamlayıcı.",
        "Her model sayfasını <b>DOI/atıf verilebilir</b> yap (\"Nasıl atıf verilir\" bloğu) — MorphoSource'a karşı kredibilite.",
    ]:
        F.append(Paragraph("• " + x, LI))
    F.append(Paragraph("Tahmini efor: <b>Düşük</b> (kopya) – <b>Orta</b> (DOI) · Beklenen etki: <b>Yüksek</b> · Süre: <b>Bu hafta–bu ay</b>", SMALL))
    F.append(Spacer(1, 6))

    # Fırsat 3
    F.append(Paragraph("Fırsat 3 — Yayıncı & Kurumsal Entegrasyon (dağıtım > destinasyon)", H3))
    F.append(Paragraph(
        "<b>Neden:</b> Taylor &amp; Francis makalelerine Sketchfab viewer'ını <i>yerel olarak gömüyor</i> — yani "
        "yayıncılar gömülü 3D istiyor; bu hem en büyük tehdit hem en büyük ödül. Bir dergi/yayıncı pilotu, "
        "AcademicAR'ı bir destinasyon olmaktan çıkarıp makalenin içine taşır = dağıtım. Akademik satış yukarıdan-"
        "aşağıdır; bir kütüphane/bölüm anlaşması yüzlerce bireysel modelden değerlidir.", BODY))
    F.append(Paragraph("<b>Ne yapmalı:</b>", LI))
    for x in [
        "Bir 'üniversiteler/yayıncılar için' sayfası + tek-sayfalık PDF (mevcut market-proposal / report-pdf skilleri ile üretilebilir).",
        "İsim verilmiş bir pilot (bir bölüm, lab veya dergi) ve gömülebilir viewer demosu.",
        "Kurumsal paket: paylaşılan workspace, toplu kredi, fatura/satınalma akışı, veri saklama — sayfada zaten taslağı var, hunileştir.",
        "Hedef segment: doğa tarihi/antropoloji (MorphoSource kitlesi), anatomi/tıp eğitimi, arkeoloji/miras (Sketchfab göçünden etkilenen).",
    ]:
        F.append(Paragraph("• " + x, LI))
    F.append(Paragraph("Tahmini efor: <b>Yüksek</b> · Beklenen etki: <b>Yüksek</b> (büyük anlaşma boyutu) · Süre: <b>Bu çeyrek</b>", SMALL))

    F.append(PageBreak())

    # İÇERİK & SEO BOŞLUKLARI
    F.append(Paragraph("İçerik & SEO Boşluk Analizi", H2)); F.append(rule())
    F.append(Paragraph("Rakiplerin/ekosistemin sıralandığı, AcademicAR'ın muhtemelen henüz girmediği konular:", BODY))
    for x in [
        "\"Sketchfab alternatifi\" / \"araştırmacılar/müzeler için Sketchfab\" — <b>yüksek niyet, Fab göçüyle zamanlı</b>",
        "\"dergi makalesine etkileşimli 3D figür nasıl eklenir\" (Elsevier, T&F, PLOS, Wiley bu konuda yayın yapıyor)",
        "\"3D PDF vs web 3D viewer\" / \"U3D alternatifi\"",
        "\"poster/konferans için QR kod AR\" (WebAR satıcıları bu terimi ticari olarak sahipleniyor)",
        "\"MorphoSource ile / yanında\" — biyo/antropoloji kitlesi için",
        "\"FAIR 3D veri\" / \"atıf verilebilir 3D model DOI\" (depo bölgesi)",
    ]:
        F.append(Paragraph("• " + x, LI))
    F.append(Paragraph("<b>Oluşturulacak karşılaştırma sayfaları (dipte, yüksek niyet):</b>", H3))
    F.append(Paragraph("• <b>/vs/sketchfab</b> — öncelik, göçü yakala &nbsp; • <b>/vs/figshare-zenodo</b> — "
                       "\"kalıcılık için deposit, deneyim için AcademicAR\" &nbsp; • <b>/alternatives/3d-pdf</b> — "
                       "modern web/AR vs eski U3D 3D-PDF", LI))

    # ÇALINMAYA DEĞER TAKTİKLER
    F.append(Paragraph("Çalınmaya Değer Taktikler", H2)); F.append(rule())
    for i, (src, tac) in enumerate([
        ("Sketchfab", "masaüstü oto-QR→AR: tarayan QR modeli telefonda AR açar; AcademicAR'ın amiral özelliği — sürtünmesizliği eşleştir, 10 saniyelik demo ile göster. (Düşük efor, Yüksek etki)"),
        ("Sketchfab/WebAR", "\"app-free AR\"ı açık, tekrarlanan bir ifade olarak kullan — her AR bahsinin yanında. (Düşük/Orta)"),
        ("T&F × Sketchfab", "yayıncı-içi gömme: bir yayıncı/dergi pilotuyla AcademicAR viewer'ı makaleye gömülsün — dağıtım. (Yüksek/Yüksek)"),
        ("MorphoSource", "DOI ile atıf verilebilir model sayfaları + \"Nasıl atıf verilir\". (Orta/Yüksek)"),
        ("Voyager", "açıklama/turlar: hafif açıklamalar bile viewer'ı eğitim kullanımı için ayrıştırır. (Orta/Orta)"),
    ], 1):
        F.append(Paragraph(f"<b>{i}. {src}</b> — {tac}", LI))

    # İZLEME PLANI
    F.append(Paragraph("Rekabet İzleme Planı", H2)); F.append(rule())
    for x in [
        "<b>Fab göç takvimini</b> izle (ücretsiz-lisans kaldırma tarihleri) — lansman-mesajı pencereniz.",
        "Yayıncı 3D girişimlerini izle (Elsevier, T&F, Wiley, PLOS) — ortaklık açıklıkları için.",
        "MorphoSource / Zenodo / figshare özellik sürümlerini izle (AR'a herhangi bir hamle = doğrudan tehdit).",
        "WebAR satıcılarını (8th Wall, Zappar, echo3D) akademik/atıf konumlandırması için izle.",
        "Uyarılar: \"Sketchfab alternatifi\", \"Fab migration museum\", \"3D model journal article\".",
    ]:
        F.append(Paragraph("• " + x, LI))

    # SONRAKİ ADIMLAR
    F.append(Paragraph("Sonraki Adımlar", H2)); F.append(rule())
    for i, s in enumerate([
        "<b>/vs/sketchfab + geçiş teklifini şimdi yayınla</b> — bu manzaradaki en zamanlı, en yüksek-niyetli fırsat.",
        "<b>Farklılaşmayı \"kalıcı + atıf verilebilir + app-free AR, akademik-öncelikli\"ye kilitle</b> — ana sayfa "
        "+ gerçek DOI'li örnek yayın (audit'teki sosyal-kanıt açığını da kapatır).",
        "<b>Bir yayıncı/kurum pilot görüşmesi aç</b> — dağıtım, tek başına bir destinasyondan değerli.",
    ], 1):
        F.append(Paragraph(f"{i}. {s}", LI))

    # KAYNAKLAR
    F.append(Spacer(1, 8)); F.append(rule(LINE, 0.6, 2, 4))
    F.append(Paragraph("Kaynaklar (WebSearch)", H3))
    src = [
        "Epic Games Sketchfab'i 2025'te kaldırıyor, Fab'ı başlatıyor — Fabbaloo",
        "Tarihçiler Sketchfab→Fab göçünden endişeli — 80.lv",
        "İmza: Keep Sketchfab Alive — Change.org",
        "Sketchfab güncellemesi / embed durumu — Sketchfab Community",
        "Sketchfab App-free AR — sketchfab.com/augmented-reality",
        "Sketchfab incelemeleri/fiyat — Capterra · G2",
        "MorphoSource — ms1.morphosource.org · genel bakış — AABA (bioanth.org)",
        "figshare paylaş/atıf/göm · figshare × Springer Nature — Enago",
        "Smithsonian Voyager — genel bakış · GitHub",
        "Taylor & Francis'te 3D model yayınlama (Sketchfab) — Author Services",
        "Makalelerde etkileşimli 3D — Elsevier/Applied Geography",
        "En iyi AR QR üreticileri (echo3D/Zappar/8th Wall fiyatı) — ME-QR",
    ]
    for s in src:
        F.append(Paragraph("• " + s, SMALL))
    F.append(Spacer(1, 6))
    F.append(Paragraph("AI Marketing Suite — /market competitors (WebSearch istihbarat modu; canlı çekim ağ "
                       "politikasıyla engellendi) tarafından üretildi.", SMALL))

    doc.build(F, onFirstPage=hf, onLaterPages=hf)


if __name__ == "__main__":
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "COMPETITOR-REPORT-TR.pdf"))
    build(out)
    print("Yazıldı:", out)
