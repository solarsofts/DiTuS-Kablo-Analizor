# Paket Kabul ve Yayın Veri Bütünlüğü — v0.16.9.4.39

## Genel sonuç: **PASS**

| Kapı | Sonuç | Kanıt |
|---|---:|---|
| Pytest | PASS | 724/725 PASS, 1 skipped |
| Yayın veri bütünlüğü | PASS | 0 blocker, 5 konuma bağlı izin |
| Hesap/model motor kilidi | PASS | 54/54 dosya byte doğrulandı |
| Manifest | PASS | 484/484 |
| Makara planı sayısal denetimi | PASS | 126 makara, aşım 0.000 m, atanmamış 0 |
| Bonding aksesuar planı | VALID | Cross LB 28, grounding LB 12, SVL set/pol 28/84 |
| Kabul belgeleri öz-denetimi | PASS | JSON/TXT/MD yeniden tarandı |

## PDF kapsam kanıtı

- PDF dosyası: 2/2
- PDF sayfası: 19/19
- Çıkarılan PDF metni: 43796 karakter
- PDF metadata alanı: 18
- PDF annotation/form/ek: 0/0/0
- Taranamayan nesne: 0

## Denetim sözleşmesi

Genel desenler ve kimlik metadata kontrolleri ana kapıdır. Geçmiş sızıntılar SHA-256 regresyon parmak izleriyle tamamlayıcı olarak izlenir. İzinler yalnız belirli kural ve belirli dosya yolunda geçerlidir. PDF açılamaz, şifreliyse veya metin çıkarımı boş kalırsa denetim fail-closed davranır.

Yeni kabul belgeleri tek yapılandırılmış JSON sonucundan otomatik türetilmiştir. Manifest, kendisi ile sonradan üretilen kabul attestasyonlarını çevrimsel hash bağı nedeniyle kapsam dışında bırakır; bu üç attestasyon ayrıca yayın tarayıcısıyla öz-denetlenir.

## Makara ve aksesuar kapsam kanıtı

- Sipariş miktarı: 124175.000 m
- Tahsis edilen / edilmeyen: 124175.000 / 0.000 m
- Güzergâh kesimi: 126
- Fiziksel makara: 126
- Toplam / en büyük aşım: 0.000 / 0.000 m
- JSON/CSV/XLSX tutarlılığı: PASS

## Tarihsel düzeltme

`PACKAGED_TEST_RESULTS_v0.16.9.4.14.txt` ve `PUBLISH_CLEANUP_AUDIT_v0.16.9.4.14.md` içindeki PDF ve azami makara boyu PASS beyanları eksiksiz makine-okunur kanıta dayanmıyordu. Bu paket bu beyanları kanıt kabul etmez ve otomatik sayısal denetimle geçersiz kılar.

_Bu belge `tools/run_release_acceptance.py` tarafından otomatik üretilmiştir._
