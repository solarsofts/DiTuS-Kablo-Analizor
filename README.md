
# DiTuS Kablo Analizör

Güncel sürüm numarasının tek kaynağı `VERSION.txt` dosyasıdır; `ucd.__version__` ve paket meta verisi aynı değeri taşır.

DiTuS Kablo Analizör; yeraltı güç kablosu güzergâhı, kablo-kanal geometrisi, IEC 60287 termal rating, bonding/kılıf, 2D nodal termal alan, transient, arıza/EPR, SVL ve tedarik metrajı iş akışlarını tek proje içinde yürütür.

## Kurulum

Windows üzerinde:

1. `setup_venv.bat`
2. `run_windows.bat`

Testler için `run_tests.bat` kullanılabilir.

## Kablo-Kanal Düzeni

Kurulum ekranı fiziksel kesitin yetkili giriş alanıdır. Devrelerin gerçek `x-y` koordinatları, TREFOIL/FLAT/VERTICAL/DUCT yerleşimleri, hendek katmanları, backfill, duct/grout, malzeme bölgeleri ve harici ısı kaynakları buradan yönetilir.

- TREFOIL kabloları gerçek dış çapa göre temas eden üçgen demet olarak otomatik yerleşir; faz aralığı kullanıcı girdisi değildir.
- FLAT ve VERTICAL yerleşimlerde faz merkez aralığı düzenlenebilir.
- Spinbox okları ayrı ve geniş tıklama alanına sahiptir.
- Mouse wheel, parametre değerlerini yanlışlıkla değiştirmez; panel kaydırmasına bırakılır.
- Kesit kanvasındaki zoom kontrollü ve daha yumuşaktır.
- Geometri kaydedildiğinde geometriye bağlı sonuçlar bayatlatılır ve kullanıcıdan yeniden hesap onayı istenir.


## Kablo kütüphanesi ve açık kaynak dağıtım

Paket üretici katalog satırı veya katalog PDF'i içermez. Kütüphane yedi üretici-bağımsız, koşullu parametrik şablonla gelir. Kullanıcı kendi üretici verisini uygulamada kaydedebilir, katalog paketi olarak dışa aktarabilir ve başka bir DiTuS kurulumuna içe aktarabilir.

- Katman zinciri kablo geometrisinin tek otoritesidir.
- Dış çap parametrik üretim sonucudur; katalog dış çapı yalnız doğrulama kapısıdır.
- Bilinen malzeme ısıl özdirençleri merkezi profilden çözülür.
- Üretici katalog erişim sayfaları `SOURCES.md` ve uygulamadaki **Katalog Bağlantıları** diyaloğunda listelenir.
- Liste yalnız kolaylık içindir; onay, temsilcilik veya işbirliği anlamına gelmez.

## Sentetik 20 km örnek hat

Paket herhangi bir gerçek tesis, müşteri, saha veya geçmiş proje verisi içermez. Başlangıç ekranındaki **Sentetik 20 km Örnek Hattı Aç** komutu şu tamamen üretilmiş örneği açar:

- 34,5 kV, 20 MVA
- 20.000 m toplam güzergâh
- iki devre, N-1 senaryosu
- dört termal/güzergâh bölgesi
- 21 minör kablo kesimi ve 7 cross-bonding ana grubu
- gerçekçi 1 km sınıfı makara planına uygun kesimler
- standart hendek, yüksek ısıl özdirenç bölgesi, duct bank ve HDD örnekleri

Ana dosyalar:

- `examples/synthetic_20km_line.ucd.json`
- `examples/synthetic_20km_audit_case.ucd.json`
- `examples/synthetic_20km_applied.ucd.json`
- `examples/synthetic_20km_regression_suite.json`

Örnekleri yeniden üretmek için:

```text
python examples/generate_synthetic_20km_examples.py
```

Sentetik regresyon için:

```text
run_synthetic_20km_regression.bat
```

## Demo akışları

- `run_catalog_selection_demo.bat`
- `run_catalog_comparison_demo.bat`
- `run_project_application_demo.bat`
- `run_project_report_demo.bat`
- `run_procurement_demo.bat`
- `run_engine_precheck_demo.bat`
- `run_workflow_demo.bat`
- `run_multiconductor_em_demo.bat`
- `run_multiconductor_thermal_demo.bat`
- `run_multiconductor_bonding_network_demo.bat`
- `run_multiconductor_global_network_demo.bat`
- `run_electrothermal_coupled_demo.bat`

Bütün demo sonuçları mühendislik ön tasarım çıktısıdır; nihai uygunluk, üretici onayı veya saha/as-built kabulü değildir.

## Yayın gizliliği

Bu sürümde gerçek proje tabanlı örnekler, raporlar, hash kayıtları, test fixture'ları ve tarihsel devir belgeleri yayın paketinden çıkarılmıştır. Paket içinde yalnız üretici-bağımsız jenerik şablonlar, sentetik örnekler ve yazılımın kendi teknik belgeleri bulunur.


## v0.16.9.4.34 — Kılıf kaybı bütünlük kapısı

Global primitive ağın `network_sheath_loss_ratio` çıktısı yalnız boyuna metalik kılıf I²R kaybıdır ve toplam IEC λ1 olarak sunulmaz. IEC rating zinciri uygun tekli-devre Trefoil/Flat geometrilerinde λ1″ eddy-current bileşenini ayrı hesaplar; Note 3 ekran konstrüksiyonu ve solid-bonded non-Milliken kapsam kararları provenance ile taşınır. CUSTOM veya paralel/çok-devre kesitlerde doğrulanmış dış λ1″ yoksa ampacity üretim otoritesi fail-closed durur.
