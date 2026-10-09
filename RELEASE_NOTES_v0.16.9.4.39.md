# DiTuS Kablo Analizör v0.16.9.4.39 — Denetim Düzeltmeleri ve Yayın Sağlamlaştırma

Bu sürüm, v0.16.9.4.38 üzerinde yapılan ayrıntılı kod denetiminin yayın öncesi düzeltmelerini içerir. Hesap güvenliği, veri bütünlüğü, paketleme, Qt çalışma zamanı ve CI kapıları güçlendirilmiştir.

## Öne çıkan düzeltmeler

- Python 3.11 ve 3.12 desteği aynı CI matrisi içinde doğrulandı.
- Proje ve kullanıcı veri tabanı yazımları atomik hale getirildi; bozuk kullanıcı verisi karantinaya alınıyor.
- Wheel bağımlılıkları, paket kaynakları ve dinamik sürüm meta verisi düzeltildi.
- Solid-bonded kılıf kaybı, transient iç ısıl zinciri ve nodal yakınsama kararları düzeltildi.
- Karışık-zemin analitik modeli, dolgudaki ısıl özdirenç doğal zeminden yüksek olduğunda güvenli biçimde nodal çözüme yönlendiriliyor.
- 0 °C yüzey/derin-zemin sınır değerleri artık sentinel olarak yorumlanmıyor; “ortam sıcaklığını kullan” seçimi açıkça saklanıyor.
- “Beton hendek” gibi kurulumlar doğrudan gömülü analitik model olarak yanlış sınıflandırılmıyor.
- CSV/XLSX formül enjeksiyonu önlendi; rapor/dışa aktarma uç durumları sertleştirildi.
- Ağır GUI hesapları arka plan görevi altyapısına taşındı; Kablo-Kanal tuvali çökme korumaları eklendi.
- Ruff Python 3.11 kritik statik kontrolü, byte-compile ve Qt offscreen testleri zorunlu CI kapıları oldu.
- Hesap modüllerindeki 21 yanlış yerleştirilmiş modül docstring'i düzeltildi.
- Motor SHA-256 kilidi yeniden üretilebilir bir araçla 54 dosya için güncellendi; LF/CRLF farklarından bağımsız olarak Linux ve Windows'ta aynı sonucu veriyor.

## Uyumluluk ve kapsam

- Desteklenen Python: 3.11 ve 3.12.
- Proje şeması: 0.16.4; mevcut proje dosyalarıyla geriye uyumluluk korunur.
- Bu yazılım denetimi ve test paketi mühendislik onayı ya da standart uygunluk belgesi değildir.

## Yayın kapıları

- Ruff kritik kuralları: PASS
- Python byte-compile: PASS
- Pytest: PASS
- Hesap/model motor kilidi: PASS
- Paket bütünlüğü ve manifest: yayın kabul aracıyla üretilir.
