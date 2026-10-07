# DiTuS Kablo Analizör — Detaylı Kod Denetimi

| Alan | Değer |
|---|---|
| Denetlenen sürüm | `0.16.9.4.38` (`main` @ `fe4d80b`) |
| Denetim tarihi | 2026-10-07 |
| Kapsam | `src/` (≈50,7 bin satır: 29,1 bin hesap motoru, 19,2 bin UI), `tests/` (≈8,9 bin satır, 539 test), `tools/`, `examples/`, CI ve paketleme |
| Yöntem | Test paketinin Python 3.11 ve 3.12'de çalıştırılması, GitHub Actions kayıtlarının incelenmesi, statik analiz (ruff/pyflakes), çekirdek denklemlerin IEC 60287 / 60287-2-1 ile satır satır karşılaştırılması, hatalı girdi ve sınır durumları için ayrı doğrulama betikleri |

Bu belge bir yazılım denetimidir; sonuçların mühendislik onayı veya standart uygunluk beyanı yerine geçmez.

---

## 1. Yönetici özeti

**Genel değerlendirme:** Hesap çekirdeği iyi kurulmuş. IEC 60287-1-1 akım denklemi, ks/kp → xs/xp → ys/yp zinciri, dielektrik kayıp, λ1″ eddy-current terimi (Milliken F dahil) ve iki-bölge kuruma denklemi standartla birebir uyumlu. Üretim bonding ağı da IEC kapalı formuyla %1,3 içinde tutarlı. Provenance ve fail-closed yaklaşımı tutarlı uygulanmış. Python 3.12'de **539 testin tamamı geçiyor**. Satır kapsamı hesap motorunda %88, UI'da %28.

Buna karşın sürüm altyapısında ve bazı yan yollarda ciddi açıklar var:

1. **CI, `main` dalındaki 4 çalıştırmanın 4'ünde de kırmızı.** Bunun iki bağımsız nedeni var: `openpyxl` bağımlılığı eksik ve 3.11'de sözdizimi hatası var.
2. **Uygulama Python 3.11'de hiç açılmıyor.** `pyproject.toml` ve `setup_venv.bat` 3.11'i desteklenen sürüm olarak ilan ediyor.
3. **Legacy SOLID_BOTH_END λ1 hesabı ~4 kat düşük çıkıyor** (konservatif değil). Yol "tanısal" etiketli, ancak UI'daki "λ1 > proje kriteri" uyarısı bu değerle tetikleniyor.
4. **İki ayrı veri kaybı riski var.** Kullanıcı kablo veri tabanı bozulursa sessizce yok sayılıyor ve ilk değişiklikte üzerine yazılıyor. Proje dosyası da atomik olmayan bir yazımla kaydediliyor.
5. **Wheel paketi eksik.** Bağımlılık listesi yok, JSON kaynak dosyaları pakete girmiyor ve paket sürümü eski.

### Önem dağılımı

| Önem | Adet | Bulgular |
|---|---|---|
| Yüksek | 2 | Y-1, Y-2 |
| Orta | 7 | O-1 … O-7 |
| Düşük | 7 | D-1 … D-7 |
| Bilgi / kod kalitesi | 6 | K-1 … K-6 |

---

## 2. Bulgular

### Y-1 — CI tüm `main` çalıştırmalarında başarısız (Yüksek)

**Kanıt:** GitHub Actions "CI" iş akışı, 4/4 çalıştırmada `failure` verdi (run 1–4, 2026-09-04).

| İş | Hata |
|---|---|
| ubuntu/windows · 3.12 | `tests/test_phase5_procurement_integrity.py:8` → `ModuleNotFoundError: No module named 'openpyxl'` |
| ubuntu/windows · 3.11 | 29 test modülünde toplama (collection) hatası (bkz. Y-2) |

- `openpyxl`, testlerde (`tests/test_phase5_procurement_integrity.py:8`) ve `tools/run_release_acceptance.py:602` içinde kullanılıyor, ancak `requirements.txt` içinde yok.
- Toplama hatası pytest'i `Interrupted` durumunda durduruyor. Bu yüzden CI'da **hiçbir test fiilen koşmuyor**.
- `PACKAGED_TEST_RESULTS_*.txt` ve `BASELINE_LOCK_*.md` dosyaları yerel "PASS" sonuçları bildiriyor; bu sonuçlar CI'da yeniden üretilemiyor.

**Öneri:** `requirements.txt` dosyasına `openpyxl>=3.1,<4` ekleyin (yalnız testte kullanılıyorsa ayrı bir `requirements-dev.txt` de olur). Y-2'yi düzeltin. Ardından CI yeşil olmadan sürüm etiketlemeyin.

### Y-2 — Python 3.11'de uygulama ve testler açılmıyor (Yüksek)

**Konum:** `src/ucd/calculations/reporting.py:535`

```python
f"{getattr(item, 'circuit_id', '?')}={'OFF' if not getattr(item, 'energized', False) else f'{getattr(item, 'phase_current_a', 0.0):.3f} A'}"
```

İç f-string'de dış tırnak türünün yeniden kullanılması PEP 701 sözdizimidir ve yalnız Python ≥ 3.12'de geçerlidir. `ucd/calculations/__init__.py:355` `reporting` modülünü doğrudan (eager) içe aktarıyor. Sonuç olarak 3.11'de `import ucd.calculations` ve dolayısıyla `ucd.main` `SyntaxError` ile çöküyor.

- `pyproject.toml` → `requires-python = ">=3.11,<3.13"`
- `setup_venv.bat`, 3.12 bulamazsa 3.11'e geçiyor; bu durumda kullanıcı çalışmayan bir kurulum elde ediyor.
- `tests/test_phase7_4_ci_contract.py`, 3.11'in CI matrisinde olmasını zorunlu kılıyor.

**Doğrulama:** Bu satır 3.11 uyumlu biçime çevrildiğinde (örn. `format(getattr(item, 'phase_current_a', 0.0), '.3f') + ' A'`) tüm `src/` 3.11'de derleniyor. Testlerin 3.11 sonucu Ek A'da.

**Öneri:** Satırı düzeltin. CI'a `ruff check --target-version py311` (veya `python -m compileall`) adımı ekleyerek bu sınıf hatayı derleme aşamasında yakalayın.

**Not:** `reporting.py` motor kilidi kapsamında. Bu dosyaya (ve O-1, O-6, O-7 gibi diğer motor dosyası düzeltmelerine) yapılan her değişiklik `test_engine_directories_match_locked_v0169417_baseline` testini kırar; bu beklenen bir davranıştır. Düzeltmeyle birlikte yeni bir `ENGINE_BASELINE_<sürüm>.sha256` üretilmelidir.

### O-1 — Legacy SOLID_BOTH_END λ1, terminasyon topraklama direncini faz döngüsüne ekliyor (Orta, konservatif değil)

**Konum:** `src/ucd/calculations/bonding.py:946-947`

```python
earth_r = sum(max(0.0, n.earth_resistance_ohm) for n in terminal_nodes[:2])
impedance = complex(r_metal + earth_r, x_metal)
```

İki uçtan topraklı (solid-bonded) dengeli bir sistemde kılıf dolaşım akımları diğer fazların kılıfları üzerinden kapanır; topraklama elektrodundan akmaz. Toprak direncini her faz döngüsüne eklemek akımı ve λ1'i sistematik olarak küçültür.

**Sayısal doğrulama** (varsayılan `ProjectData`: Trefoil, s = 0,15 m, I = 800 A, 95 mm² Cu ekran, 1670 m):

| Terminasyon toprak direnci | Legacy λ1 | IEC kapalı form λ1′ = (Rs/R)/(1+(Rs/X)²) | Oran |
|---|---|---|---|
| 0,0 Ω | 1,30329 | 1,30329 | 1,000 |
| **0,2 Ω (varsayılan)** | **0,32329** | 1,30329 | **0,248** |
| 1,0 Ω | 0,03456 | 1,30329 | 0,027 |
| 5,0 Ω | 0,00180 | 1,30329 | 0,001 |

Üretim yolu (`primitive_cim.solve_primitive_network`) aynı durumda λ1 = 1,28669 veriyor (oran 0,987) ve toprak direncinden bağımsız. Yani **üretim zinciri doğru, hata yalnız legacy yolda**.

**Etki:** Legacy sonuç UI'da gösteriliyor:

- `main_window.py:3684` — "Hesaplanan λ1" satırı
- `main_window.py:5530` — λ1 > `maximum_lambda1` uyarısı
- iz/rapor metinleri

Solid-bonded bir tasarımda bu uyarı 4 kat düşük değer yüzünden **bastırılabilir**.

**Öneri:** Döngü empedansından `earth_r`'ı çıkarın. Ayrıca solid-bonded λ1 için IEC kapalı formuna karşı mutlak bir oracle testi ekleyin; mevcut oracle testleri yalnız cross/solid **oranını** kontrol ediyor ve bu hatayı yakalayamıyor. Legacy değerin uyarı mantığında kullanılmasını da üretim değerine taşıyın.

### O-2 — Bozuk kullanıcı kablo veri tabanı: açılışta çökme veya sessiz veri kaybı (Orta)

**Konum:** `src/ucd/calculations/application_database.py:33-42`, `src/ucd/calculations/cable_library.py:580`, `src/ucd/ui/main_window.py:229`, `:5025`

Sorun 1 — **Çökme:** Yalnız `OSError`, `JSONDecodeError` ve `CableLibraryInputError` yakalanıyor. Kökü sözlük olmayan geçerli bir JSON (`[]`, `"x"`) `raw.get(...)` satırında `AttributeError` fırlatıyor. Bu çağrı `MainWindow.__init__` içinde olduğu için **uygulama açılmıyor**. Doğrulama:

| Dosya içeriği | Sonuç |
|---|---|
| `[]` | `AttributeError: 'list' object has no attribute 'get'` |
| `"x"` | `AttributeError: 'str' object has no attribute 'get'` |
| Kesik JSON | Sessizce yok sayılıyor; yalnız 7 jenerik şablon yükleniyor |

Aynı açılış çökmesi sınıfı `ditus-standard-defaults.json` için de geçerli. `load_standard_defaults` (`standard_defaults_dialog.py:204`) yalnız `OSError`/`ValueError` yakalıyor; dosya içeriği `"x"` olduğunda `StandardDefaults.from_dict` `AttributeError` fırlatıyor. Bu çağrı da `MainWindow.__init__` içinde (`main_window.py:221`).

Sorun 2 — **Veri kaybı:** Bozuk dosya sessizce atlanınca bellekte yalnız yerleşik şablonlar kalıyor. Kullanıcı veri tabanında herhangi bir değişiklik yaptığında `_on_database_changed` bu kütüphaneyi asıl dosyanın **üzerine yazıyor**. Kurtarılabilir durumdaki (örn. tek satırı bozuk) kullanıcı kataloğu kalıcı olarak kayboluyor. Kullanıcıya hiçbir uyarı gösterilmiyor ve yedek alınmıyor.

**Öneri:** Herhangi bir yükleme hatasında bozuk dosyayı `*.corrupt-<zaman>` adıyla taşıyın, kullanıcıyı bilgilendirin ve kaydetmeyi bu karar alınana kadar engelleyin. `catalog_package_from_dict` girişine `isinstance(raw, dict)` kontrolü ekleyin.

### O-3 — Proje dosyası atomik olmayan yazımla kaydediliyor (Orta)

**Konum:** `src/ucd/ui/main_window.py:6396`

```python
self.current_file.write_text(json.dumps(...), encoding="utf-8")
```

`write_text` önce hedef dosyayı sıfırlıyor, sonra içeriği yazıyor. Yazım sırasında güç kesintisi, disk dolması veya ağ sürücüsü kopması olursa **tek proje kopyası bozuluyor** ve yedek yok. Aynı depoda `save_application_cable_database` zaten doğru deseni kullanıyor (`.tmp` + `replace`).

**Öneri:** Aynı geçici dosya + `os.replace` desenini kullanın (gerekirse `fsync` ekleyin). İsteğe bağlı olarak önceki sürümü `*.bak` olarak saklayın. Aynı düzeltme katalog dışa aktarma (`cable_library_widget.py:1168`) ve standart ön tanım dosyası (`standard_defaults_dialog.py:214`) için de geçerli. Katalog dışa aktarma ve ön tanım diyaloğunun `_accept` yolu (`standard_defaults_dialog.py:420`) ayrıca `OSError` yakalamıyor.

### O-4 — Wheel paketi kullanılamaz durumda (Orta)

**Konum:** `pyproject.toml`

`git archive` + `uv build --wheel` ile doğrulandı:

| Sorun | Ayrıntı |
|---|---|
| Bağımlılık yok | `[project]` altında `dependencies` tanımlı değil; wheel metadata'sında `Requires-Dist` satırı yok. `pip install .` PySide6/numpy/scipy kurmuyor. |
| Kaynak dosyaları eksik | `ucd/resources/*.json` wheel'e girmiyor. `cable_template_generator.load_generic_profile_data()` kurulu wheel'den çağrıldığında `FileNotFoundError` veriyor. |
| Sürüm eski | `version = "0.16.9.4.34"`; `ucd.__version__` ve `VERSION.txt` ise `0.16.9.4.38`. |
| Varlıklar paket dışında | `assets/ditus_mascot.png` depo köküne göreli yolla okunuyor (`parents[3] / "assets"`); kurulu pakette yok. |

CI `pip install -e .` kullandığı için bu sorunlar şu an görünmüyor.

**Öneri:** `dependencies` listesini `requirements.txt` ile eşleyin. `[tool.setuptools.package-data] ucd = ["resources/*.json", "resources/catalogs/*"]` ekleyin. Sürümü `ucd.__version__`'dan dinamik okuyun (`[tool.setuptools.dynamic]`). Maskotu `ucd/resources` altına taşıyıp `importlib.resources` ile okuyun.

### O-5 — Ağır hesaplar GUI iş parçacığında çalışıyor (Orta, kullanılabilirlik)

`src/` içinde `QThread`, `QThreadPool`, `QRunnable`, `processEvents` veya bekleme imleci kullanımı yok. Testlerde tek bir nodal/elektro-termal çağrı 9–11 saniye sürüyor. 20 km'lik bir hatta tüm senaryolar çalıştırıldığında pencere uzun süre yanıt vermiyor. Windows bu durumda "Yanıt vermiyor" gösteriyor ve kullanıcı uygulamayı kapatabiliyor; bu da O-3 ile birleşince veri kaybı riskini artırıyor.

**Öneri:** Motor çağrılarını `QThreadPool` + sinyal ile arka plana alın. İptal ve ilerleme göstergesi ekleyin. Motorlar saf fonksiyon olduğu için bu geçiş kolay.

### O-6 — Transient model, iç ısıl zinciri IEC'den farklı topluyor (Orta, konservatif değil)

**Konum:** `src/ucd/calculations/transient_thermal.py:289`, `:335`

Transient modelde iletken tek bir ısıl kapasite düğümü ve tek bir direnç olarak temsil ediliyor (`r_internal = T1 + T2 + T3`). Kılıf ve zırh kayıpları ise doğrudan dış yüzeye (jacket) veriliyor. Kalıcı-durum nodal çözücüsü (`nodal_thermal.py`, `_solve_at_current`) ise doğru IEC zincirini kullanıyor: `T1 + n(1+λ1)T2 + n(1+λ1+λ2)T3` ve dielektrik için `0,5·T1 + n(T2+T3)`.

Fark şu terimlerden oluşuyor: `I²R·[λ1(T2+T3) + λ2·T3]` ve `0,5·Wd·(T2+T3)`. Ayrıca `n > 1` (üç damarlı kablo) için T2/T3 `n` ile çarpılmıyor.

Varsayılan kablo (n = 1, T1/T2/T3 = 0,366/0,050/0,050 K·m/W, Wc = 12,2 W/m) için transient modelin kalıcı-durum limitindeki iç sıcaklık artışı açığı:

| λ1 | IEC iç artış | Transient (toplanmış) | Fark |
|---|---|---|---|
| 0,05 | 5,90 K | 5,82 K | 0,09 K |
| 0,30 | 6,21 K | 5,82 K | 0,39 K |
| 1,30 | 7,43 K | 5,82 K | **1,61 K** |

Sonuç olarak çevrimsel ve acil durum akım değerleri, aynı koşuldaki kalıcı-durum sonucuyla tutarsız ve iyimser çıkıyor. Bu fark üç damarlı kablolarda belirgin şekilde büyür.

**Öneri:** İç zinciri en azından IEC 60853 iki-düğümlü (iletken + kılıf) eşdeğer modeline genişletin, ya da kalıcı-durum limitinde nodal sonuçla eşleşecek şekilde efektif bir `r_internal` türetin. "Transient'in kalıcı limiti = nodal kalıcı sonuç" regresyon testi ekleyin.

### O-7 — MIXED-zone T4 yaklaşımı, kötü dolguda konservatif değil (Orta)

**Konum:** `src/ucd/calculations/thermal_resistance.py:238-245`

Dolgu düzeltmesi yalnız matrisin köşegenine (öz-ısınma) uygulanıyor. IEC 60287-2-1'in dolgu / duct-bank düzeltmesi `N/(2π)·(ρe−ρc)·ln(u+√(u²−1))` ise karşılıklı ısınmayı da kapsıyor. Temas eden trefoil, h = 1,0 m, D = 100 mm, r_b = 0,30 m, ρ_doğal = 1,0 K·m/W için:

| ρ_dolgu | Kod T4 | IEC 2-1 dolgu formülü | Fark |
|---|---|---|---|
| 0,7 | 1,488 | 1,377 | +8,1 % (konservatif) |
| 1,5 | 1,717 | 1,903 | **−9,8 %** |
| 2,5 | 2,002 | 2,561 | **−21,8 %** |

Model kodda "ön-tarama" olarak belgelenmiş ve üretim otoritesi politika gereği nodal çözüme geçebiliyor. Yine de analitik yol seçildiğinde, doğal zeminden **kötü** bir dolguda T4 olduğundan düşük hesaplanıyor.

**Öneri:** IEC 2-1 düzeltmesini (N kablo, u = L_G/r_b) kullanın, ya da `ρ_dolgu > ρ_doğal` durumunda MIXED modunu fail-closed yapıp nodal çözüme yönlendirin. `max(0.0, …)` kırpmasını da hata olarak raporlayın.

### D-1 — Şema sürümü float olarak karşılaştırılıyor (Düşük)

**Konum:** `src/ucd/models/project.py:1856`

`float("0.10") = 0.1 < 0.3` olduğu için `0.10`–`0.16` ve gelecekteki `0.17` şemaları "pre-0.3 legacy" kabul ediliyor. `except ValueError` dalındaki izin listesine (içinde `"0.10"`…`"0.16"` var) hiç ulaşılmıyor. Doğrulama: `"0.10"`, `"0.16"`, `"0.17"` için eksik anahtarlar `MANUAL` T1–T4 moduna; `"0.16.4"` ve `"1.0"` için `AUTO_*` moduna düşüyor. Etki, yalnız ilgili anahtarı eksik dosyalarla sınırlı.

**Öneri:** Sürümü `tuple(int(p) for p in v.split("."))` ile karşılaştırın. Uygulamanın bilmediği daha yeni bir şemayı açarken kullanıcıyı uyarın; şu an `_load_project_path` şemayı koşulsuz `0.16.4` olarak damgalıyor.

### D-2 — Nodal çözücüde 0,0 °C, "ortam sıcaklığını kullan" anlamına geliyor (Düşük)

**Konum:** `src/ucd/calculations/nodal_thermal.py:855`, `:858`, `:1065`, `:1068`

Şablon varsayılanı `0.0` sentinel olarak kullanılıyor. Kullanıcı yüzey veya derin toprak sıcaklığını bilinçli olarak 0 °C girerse (kış koşulu) çözücü bunun yerine ortam sıcaklığını kullanıyor. Detay diyaloğu (`thermal_detail_dialog.py:288`) ise girilen 0,0 °C değerini gösteriyor; yani rapor ile çözüm birbirini tutmuyor. Sonuç çoğunlukla konservatif yönde, ancak bu sessiz bir girdi değişikliği.

**Öneri:** Alanı `float | None` yapın, ya da açık bir `use_ambient` bayrağı ekleyin.

### D-3 — XLSX/CSV formül enjeksiyonu (Düşük, güvenlik)

**Konum:** `src/ucd/calculations/procurement.py:1061`; CSV: `procurement.py:1026`, `installation_designer_dialog.py:3245`

`xlsxwriter.Workbook(path)` varsayılan olarak `strings_to_formulas=True` ile açılıyor. Proje adı `=HYPERLINK("…","Tıkla")` yapıldığında RFQ çalışma kitabındaki `Özet!B4` hücresi **formül** olarak yazılıyor (openpyxl ile `data_type='f'` doğrulandı). RFQ dosyaları tedarikçilere gönderildiği için, paylaşılan bir proje dosyası alıcı tarafında formül çalıştırabilir.

**Öneri:** `Workbook(path, {"strings_to_formulas": False})` kullanın. CSV'de `=`, `+`, `-`, `@`, sekme veya CR ile başlayan hücrelerin başına `'` ekleyin.

### D-4 — Serbest metin kurulum tipi eşleştirmesi (Düşük)

**Konum:** `src/ucd/calculations/thermal_resistance.py:271`

`"HENDEK" in installation_type` koşulu, "Beton hendek" gibi doğrudan gömülü **olmayan** bir kurulumu da `DIRECT_BURIED` sayıyor ve otomatik image yöntemi uygulanıyor. Diğer kurulum tipleri için var olan `ANALYTIC_MODEL_SCOPE_REQUIRES_NODAL` korumasını bu durum atlatıyor.

**Öneri:** Serbest metin yerine `RouteSection` üzerinde enum bir `installation_type` alanı kullanın.

### D-5 — `ucd.calculations.STATUS_CONDITIONAL` iki farklı değerle tanımlı (Düşük)

**Konum:** `src/ucd/calculations/__init__.py:436` ve `:494`

`procurement.STATUS_CONDITIONAL = "CONDITIONAL_PROJECT_DATA"` değeri `project_workflow.STATUS_CONDITIONAL = "CONDITIONAL"` tarafından gölgeleniyor. Şu an tek tüketici (`main_window.py`) iş akışı anlamında kullanıyor ve davranış tesadüfen doğru. Paket düzeyinden tedarik durumu bekleyen yeni bir kod ise yanlış değeri alır.

**Öneri:** Birini yeniden adlandırarak dışa aktarın (örn. `PROCUREMENT_STATUS_CONDITIONAL`).

### D-6 — Toplanan istisnalar uç durumları gizleyebilir (Düşük)

- `production_electrothermal.py:193` — `_project_requires_nodal_dryout` bölge çözümünde `except Exception: continue` kullanıyor. Hemen aşağıdaki yorum "analitik önizleme kuruma verisini sessizce yok sayamaz" diyor, ancak bölge çözülemezse kuruma tespiti atlanıyor.
- `dxf_reader.py:37`, `:49` — Okunamayan POLYLINE/TEXT varlıkları sessizce düşüyor; kullanıcı eksik geometriden haberdar olmuyor.
- `nodal_thermal.py` `_find_ampacity` — `_solve_at_current` sonucundaki `converged` bayrağı kontrol edilmiyor. Yakınsamamış (alt-gevşetilmiş) bir iterasyonun sıcaklığı ampacity kararında kullanılabiliyor.

### D-7 — CI eylemleri Node 20'ye bağlı (Düşük)

`actions/checkout@v4` ve `actions/setup-python@v5` için Actions kayıtlarında Node 20 kullanımdan kaldırma uyarısı var. Güncel ana sürümlere geçin.

### K — Kod kalitesi ve depo hijyeni (Bilgi)

| # | Gözlem | Öneri |
|---|---|---|
| K-1 | Depo kökünde **232 dosya** var; 121'i sürüm artefaktı (`BASELINE_LOCK_*` 14, `ENGINE_BASELINE_*` 21, `PACKAGED_TEST_RESULTS_*` 22, `PUBLISH_INTEGRITY_AUDIT_*` 20, `PUBLISH_CLEANUP_AUDIT_*` 21, `RELEASE_NOTES_*` 23). | `docs/releases/<sürüm>/` altına taşıyın; GitHub Releases kullanın. |
| K-2 | `.gitignore` yok; `pytest` ve `pip install -e .` sonrası `__pycache__/` ve `*.egg-info/` izlenmeyen dosya olarak birikiyor. | Standart Python `.gitignore` ekleyin. |
| K-3 | Sürüm dizgileri tutarsız: `README.md`/`README_TR.md` başlığı `v0.16.9.4.18`, `pyproject.toml` `0.16.9.4.34`, `ucd.__version__`/`VERSION.txt` `0.16.9.4.38`. | Tek kaynak (`ucd.__version__`) ve bunu doğrulayan bir test kullanın. |
| K-4 | `main_window.py` 6.452 satır ve 208 metot; `installation_designer_dialog.py` 4.598 satır. | Sayfa/denetleyici sınıflarına bölün; hesap çağrılarını O-5 ile birlikte bir servis katmanına alın. |
| K-5 | ruff (pyflakes): 97 kullanılmayan import, 6 kullanılmayan değişken (örn. `fault_epr.py:207 minor_map`, `primitive_cim.py:771 zero`), 3 yeniden tanımlama, 46 kör `except Exception` (39'u UI'da). `procurement.py` birçok satırda `;` ile birden fazla deyim içeriyor. | CI'a `ruff check` adımı ekleyin; `--fix` ile güvenli olanları temizleyin. |
| K-6 | `calculations/` altındaki 21 modülde (örn. `fault_epr.py`, `primitive_cim.py`, `soil_dryout.py`, `bonding_closed_form_validation.py`) modül "docstring"i `from __future__`/import satırlarından sonra geliyor (`__doc__ is None`). UI testleri `pytest.importorskip` ile sistem kütüphanesi (libEGL) yokken **sessizce atlanıyor**; pytest 9.1'de bu bir hataya dönüşecek. | Docstring'leri başa alın. CI'da UI testlerinin atlanmasını hata sayın (`exc_type` belirtin ve Linux runner'a `libegl1` kurun). |

---

## 3. Doğrulanan ve doğru bulunan alanlar

Aşağıdaki denklemler satır satır IEC metinleriyle karşılaştırıldı ve doğru bulundu:

| Alan | Konum | Sonuç |
|---|---|---|
| IEC 60287-1-1 akım denklemi; dielektrik terim `Wd[0,5T1 + n(T2+T3+T4)]` | `iec60287.py:320-329` | ✔ |
| Rdc20 = ρ·10⁹/A [Ω/km]; α20 sıcaklık düzeltmesi | `cable_physical_parameters.py` | ✔ |
| xs² = 8πf/R′·10⁻⁷·ks; ys'nin üç parçalı ifadesi; yp (0,312·(dc/s)² + 1,18/(F+0,27)) | `cable_physical_parameters.py:376-401`, `:447` | ✔ |
| Tablo 2 ks/kp çiftleri (Cu/Al masif, çok telli, Milliken) | `cable_physical_parameters.py:286-373` | ✔ yaygın kaynaklardaki tablo değerleriyle uyumlu; lisanslı metinle son teyit önerilir. Masif Cu için yalıtım sisteminden bağımsız (1, 1) seçimi konservatif yönde. |
| Wd = ωCU0²tanδ, birim dönüşümleri (µF/km → F/m) | `iec60287.py:226-231` | ✔ |
| T = ρ/(2π)·ln(Do/Di); image-method T4 (acosh(h/r) + Σ ln(d′/d)) | `thermal_resistance.py` | ✔ |
| λ1″: λ0, Δ1 (trefoil/orta), g_s, β1, (β1·ts)⁴/12·10¹², Milliken F | `sheath_loss_completeness.py:171-243` | ✔ Yassı dizilimde dış kablonun öncü/geri faz Δ1–Δ2 atamaları (`FLAT_OUTER_LEADING`/`LAGGING`) ve `_formation` içindeki faz rolü eşlemesi, lisanslı metinle ayrıca teyit edilmeli. |
| İki-bölge kuruma: v = ρd/ρw, (v−1)Δθx terimi | `soil_dryout.py:147-161` | ✔ |
| Kılıf EMF'si jω(µ0/2π)[Ik·ln(1/r) + Σ Ij·ln(1/dkj)] | `bonding.py:331-366` | ✔ |
| Üretim primitive ağı solid-bonded λ1, IEC kapalı formuna göre | `primitive_cim.py` | ✔ (%1,3 sapma) |
| Cross-bonding eşit olmayan minor kesit oranı (IEC 2.3.6.2 / IEEE 575) | `bonding_closed_form_validation.py` | ✔ |
| Nodal FV çözücü: harmonik iletkenlik, Dirichlet/konvektif sınırlar, enerji dengesi | `nodal_thermal.py:845-1090` | ✔ |
| Rapor HTML/PDF çıktılarında `html.escape` | `reporting.py`, `catalog_comparison.py` | ✔ |
| Çıktı dosya adlarının temizlenmesi (dizin aşımı yok) | `procurement.py:1322`, `reporting.py:1468` | ✔ |
| `subprocess` kullanımı `shlex.quote` ile; `eval`, `pickle`, `shell=True` yok | `tools/run_release_acceptance.py` | ✔ |

---

## 4. Önceliklendirilmiş eylem planı

| Öncelik | Eylem | Bulgu | Tahmini iş |
|---|---|---|---|
| 1 | `openpyxl` ekle; `reporting.py:535` f-string'i düzelt; CI'ı yeşile çek | Y-1, Y-2 | < 1 saat |
| 2 | Legacy solid-bonding döngüsünden `earth_r`'ı çıkar; mutlak λ1′ oracle testi ekle; uyarıyı üretim λ1'ine bağla | O-1 | 2–4 saat |
| 3 | Atomik proje kaydı + `.bak`; bozuk veri tabanı karantinası ve kullanıcı uyarısı | O-2, O-3 | 0,5 gün |
| 4 | `pyproject.toml`: bağımlılıklar, package-data, dinamik sürüm | O-4, K-3 | 2 saat |
| 5 | Transient iç zinciri ve MIXED T4 düzeltmesi (veya fail-closed) | O-6, O-7 | 1–2 gün |
| 6 | Hesapları arka plan iş parçacığına taşı | O-5 | 1–2 gün |
| 7 | Düşük öncelikli bulgular, `.gitignore`, ruff CI adımı, kök dizin düzeni | D-*, K-* | 1 gün |

---

## Ek A — Yeniden üretme

```text
# Bağımlılıklar (3.12)
python -m pip install -r requirements.txt openpyxl
python -m pip install --no-deps -e .

# Test paketi
QT_QPA_PLATFORM=offscreen python -m pytest -q
#  → 530 geçti, 9 atlandı (libEGL yokken UI testleri). libEGL kurulunca atlanan 9 test dahil
#    UI dosyaları: 19/19 geçti. Toplam 539/539.

# 3.11 sözdizimi kontrolü
python3.11 -m py_compile src/ucd/calculations/reporting.py
#  → SyntaxError: f-string: unmatched '('

# 3.11, reporting.py:535 düzeltilmiş kopyada
#  → 538 geçti, 1 hariç tutuldu (motor kilidi hash testi; Y-2 notuna bakın).
#    Tek satırlık düzeltme 3.11 desteğini tamamen geri getiriyor.

# Satır kapsamı (pytest-cov, 3.12, libEGL kurulu)
#  → 539 geçti; toplam %62 (27.823 deyim)
```

| Alan | Deyim | Kapsam |
|---|---|---|
| `ucd/calculations` | 14.313 | **%88** |
| `ucd/ui` | 12.089 | **%28** |
| diğer (`models`, `cad`, `resources`, `main`) | 1.421 | %94 |

En düşük kapsamlı motor modülü `sheath_loss_completeness.py` (%73); λ1″ fail-closed dallarının bir kısmı test edilmiyor. UI tarafında `installation_designer_dialog.py` (%7, 3.127 deyim) en büyük kör nokta. O-2 ve O-3'teki dosya yazma/okuma hataları da bu kapsanmayan UI kodunda yer alıyor.

O-1, O-2, O-6, O-7, D-1 ve D-3 için kullanılan doğrulama betikleri yalnız mevcut genel API'leri (`ProjectData`, `solve_bonding`, `solve_primitive_network`, `load_application_cable_database`, `mixed_zone_direct_buried_thermal_matrix_km_w`, `build_procurement_package`, `write_procurement_package`) varsayılan proje verisiyle çağırır. Depoya ek kod gerektirmez.
