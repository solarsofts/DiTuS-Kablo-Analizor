# v0.13.0 — IEC 60853 Geçici ve Çevrimsel Termal Tasarım Notları

## Amaç

Bu sürüm, v0.12.1'deki chainage bazlı 2D kararlı durum modelini yük-zaman alanına taşır. Kullanıcı günlük çevrim, acil yük veya özel işletme profili tanımlar; her seçili termal bölge ayrı geçici çözülür ve güzergâh sonucu en düşük bölgesel rating ile sınırlandırılır.

## Hesap zinciri

```text
Yük-zaman profili
→ 2D termal bölge ve mesh
→ malzeme hacimsel ısı kapasiteleri
→ sıcaklığa bağlı iletken kaybı
→ metalik kılıf/zırh/dielektrik kayıpları
→ implicit transient sonlu hacim adımı
→ çevrimsel ön koşullandırma
→ sıcaklık-zaman eğrisi
→ çevrimsel rating ve acil rating
```

## Isıl model

Kararlı durum sonlu hacim matrisi korunur. Her hücreye `ρc·A` biçiminde birim kablo uzunluğu başına ısı kapasitesi eklenir ve backward-Euler zaman adımı uygulanır:

```text
(C/Δt + K) T[n+1] = q[n+1] + b + (C/Δt) T[n]
```

Kablo çekirdeği, kablo dış alanından ayrı bir lumped iletken düğümü olarak tutulur. İletken I²R kaybı sıcaklığa göre güncellenir; iç termal direnç üzerinden jacket alanına ısı aktarılır. Sheath, armour ve dielektrik kayıpları 2D alanın kablo hücrelerine uygulanır.

İç zincir, iletken düğümü (ısı kapasiteli) → T1 → yarı-statik ekran düğümü → T2+T3 → jacket eşdeğeridir; kılıf/zırh kaybı ve Wd/2 ekran düğümüne girer. `R = T1+T2+T3` üzerinde sıcaklık ofseti ile yazılır:

```text
K = Wc·(T1 + n(1+λ1)T2 + n(1+λ1+λ2)T3 − R) + Wd·(0,5·T1 + n(T2+T3) − 0,5·R)
q_transfer = (θc − θjacket − K) / R
```

Kalıcı limitte `θc − θjacket = Wc·[T1 + n(1+λ1)T2 + n(1+λ1+λ2)T3] + Wd·[0,5·T1 + n(T2+T3)]` olur; bu, 2D nodal kararlı durum çözücüsüyle aynı IEC 60287 zinciridir. Toprağa verilen ısı kalıcı durumda toplam kablo kaybına eşittir; ısı iletkene geri aktığında `q_transfer` negatif olabilir ve enerji korunumu için kırpılmaz.

## Başlangıç koşulları

- `CYCLIC_STEADY`: yük çevrimi uç sıcaklık farkı toleransa girene veya maksimum çevrim sayısına ulaşana kadar tekrarlanır.
- `STEADY_AT_FIRST_POINT`: profil ilk yük noktasındaki kararlı durumdan başlar.
- `USER_TEMPERATURE`: kullanıcı iletken başlangıç sıcaklığını verir; toprak alanı ortam sıcaklığından başlar.


## Yük faktörü ve kayıp-yük faktörü semantiği — FAZ 6.5

Kararlı durum IEC 60287 hesabı **%100 yük faktörü** koşuludur; devre veya fiziksel kablo `load_factor` alanı RMS akımı çarpmak için kullanılmaz. Bu legacy alanlar yalnız eski proje dosyalarını kayıpsız açmak için korunur.

Aktif IEC 60853 yük profilinden iki ayrı boyutsuz büyüklük türetilir:

```text
LF = (1/T) ∫ I(t)/Ipeak dt
μ  = (1/T) ∫ [I(t)/Ipeak]^2 dt
```

- `LF`: ortalama akımın tepe akıma oranıdır.
- `μ`: IEC 60853 kayıp-yük faktörüdür; Joule kayıplarının tepe-akım kaybına göre zaman ortalamasını temsil eder.
- STEP profillerde zaman aralıkları sol uç değeriyle; LINEAR profillerde akım ve akım-karesi integralleri analitik olarak hesaplanır.
- Tam yük-zaman profili mevcutken transient sayısal çözüm profilin kendisini kullanır; `μ` sonucu bir özet/izlenebilirlik metriğidir ve profil yerine ikinci kez uygulanmaz.
- Yalnız `μ` bilinen fakat profil şekli bilinmeyen IEC 60853 kapalı-form yaklaşımı bu sürümde uygulanmaz; yazılım profil uydurmaz.

## Çevrimsel rating

Aktif yük profilinin şekli korunarak baz akım iteratif değiştirilir. Son çevrimdeki maksimum iletken sıcaklığı normal sıcaklık limitine geldiğinde bulunan baz akım, bölgesel çevrimsel rating olarak kaydedilir.

```text
Cyclic rating factor = I_cyclic / I_continuous,2D
```

Bu faktör yük profilinin duty cycle'ına bağlıdır; sabit bir kablo katalog çarpanı değildir.

## Acil rating

Çevrimsel ön koşullandırmanın son termal durumu başlangıç alınır. Kullanıcı tarafından verilen acil süre boyunca sabit akım uygulanır ve acil sıcaklık limitine ulaşan akım iteratif bulunur.

## Malzeme ısı kapasiteleri

Öncelik sırası:

1. Termal malzeme kaydındaki `volumetric_heat_capacity_mj_m3k`
2. Malzeme kategorisine göre açık ön tasarım varsayımı

Kategori varsayımı kullanılırsa hesap sonucu uyarı üretir. Gerçek tasarımda zemin, termal dolgu, grout ve yüzey malzemelerinin yoğunluk/nem/ısı kapasitesi verileri proje kaynağıyla girilmelidir.

## Arayüz

**Geçici Termal** çalışma alanı şunları içerir:

- Aktif yük profili seçimi
- Zaman–akım çarpanı tablosu
- Zaman adımı, başlangıç koşulu ve sıcaklık limitleri
- Çözülecek termal bölgeler
- Son çevrim akım ve sıcaklık grafiği
- Bölgesel çevrimsel ve acil rating sonuçları

## Bilinçli sınırlar

- Bu motor IEC 60853 iş akışını ve terminolojisini izleyen bağımsız 2D sayısal çözümdür; standardın telifli kapalı-form denklemlerinin bire bir yeniden üretimi değildir.
- IEC 60853-3 kısmi toprak kuruması ve nem göçü fiziksel modeli uygulanmamıştır.
- Boyuna eksenel ısı akışı, joint bay, HDD giriş/çıkışı ve diğer 3D geçişler çözülmez.
- Yayımlanmış IEC referans vakaları ve bağımsız yazılım karşılaştırması tamamlanmadan sonuçlar nihai tasarım kabulü için kullanılmamalıdır.
