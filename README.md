# TradeCRM

Binance spot piyasasinda calisan, Python/ccxt tabanli algoritmik trading botu.

## Kurulum

```
docker compose up -d
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

`docker compose up -d`, `tradecrm` ve `tradecrm_test` veritabanlarina sahip bir PostgreSQL 16 container'i baslatir (host port 5434 -- 5432 diger projelerle cakisabildigi icin). `.env` dosyasini kendi API anahtarlarinizla doldurun. Binance API anahtari **sadece trading** yetkili olmali, **withdrawal kapali** birakilmalidir.

## Kullanim

```
tradecrm --help
```

## Strateji dogrulama

Calisan her sey bir **profil**dir (`src/tradecrm/strategy/profiles.py`): strateji + giris filtreleri + cikis katmani (ATR stop/trailing) + pozisyon boyutu + devre kesiciler. Backtest ve canli runner ayni profili ve ayni saf fonksiyonlari kullanir.

```
tradecrm backtest compare --timeframe 4h --profiles baseline,guarded,donchian   # yan yana OOS karsilastirma
tradecrm backtest walk-forward --profile donchian --timeframe 4h                # tek profil: OOS + Monte Carlo + hassasiyet + kapi
tradecrm backtest run --profile donchian --days 365                             # hizli tek donem kontrolu (kanit degil)
tradecrm live run --profile donchian                                            # paper mod
```

- Veri `data_cache/` altinda onbelleklenir. Son %30 **holdout** olarak ayrilir, walk-forward'da kullanilmaz.
- Onay kapisi (`backtest/gate.py`): en az 100 OOS islem, PF > 1.3, Sharpe > 1, Monte Carlo p95 drawdown < %15, sembollerin en az 2/3'u pozitif, +-%20 parametrede cokme yok. Kapiyi gecmeyen profil canliya aday degildir.

### Giris filtresi katki testi (Oneri 6)

```
tradecrm backtest filter-test --base donchian_fng --filters volume,vol_cap,confirm --timeframe 1d --days 2500 --train-days 365 --test-days 120 --symbols ...
```

Her filtre tabana **tek tek** eklenir. Taban OOS islemlerinden filtrenin eleyecekleri bulunur ve elenenlerin getirisinin rastgele elemeden kotu olup olmadigi permutasyon testiyle olculur (Bonferroni duzeltmeli). Anlamli olmayan filtre profile eklenmez. Ilk olcum (donchian_fng, 1d, 10 coin): hacim teyidi (p=0.94; elenen 16 islem ortalamada en iyileriydi), ATR yuzdelik tavani (p=0.89) ve bir bar teyit (`confirm`, p=0.59) anlamsiz -- hicbiri eklenmedi. Oneri 11e BTC trendi (`btc_trend`, EMA50, tek onceden belirlenmis ayar): yalnizca 4/82 islem eleniyor (elenenler ort. -%5,2, kalanlar +%19,7) ama p=0.19 -- anlamsiz, eklenmedi. Tabandaki EMA200 trend filtresi BTC dususlerinin cogunu zaten disarida biraktigi icin elenecek islem az.

### Ek veri kaynaklari

Filtreler ek kolonlari okur; `backtest` komutlari profilin ihtiyac duydugu kaynaklari otomatik yukler, `live run` her tick'te son degerleri ceker (kaynak erisilemezse filtre girisi engellemez, audit'e `source_unavailable` yazar).

| Kaynak | Filtre | Gecmis |
|---|---|---|
| Binance perpetual funding | `funding` | 2019'dan beri |
| Fear & Greed (alternative.me) | `fng` | 2018'den beri |
| Makro takvim (`src/tradecrm/data/macro_events.csv`, simdilik FOMC) | `macro` | statik; CPI/NFP satirlari dogrulanarak eklenebilir |
| BTC/USDT kapanisi (ayni zaman dilimi) | `btc_trend` (BTC < EMA50 iken BUY yok) | Binance spot; canlida her tick'te cekilir |
| Open interest + long/short orani | (henuz filtre yok) | Binance ~30 gun sakliyor -> `tradecrm collect run` surekli calismali |

```
tradecrm collect start               # arka planda baslat (panelle ayni surec; calisiyorsa dokunmaz)
tradecrm collect stop
tradecrm collect status              # saglik, son calismalar, sembol bazinda veri tazeligi
tradecrm autostart enable collector   # Windows oturumu acilinca otomatik baslat (Gorev Zamanlayici, yonetici gerekmez)
tradecrm autostart status            # collector | live | admin; panelde Surecler sayfasindan da acilip kapanir
tradecrm collect run                 # (alternatif) terminalde calistir, Ctrl+C ile durur
```

- Semboller `.env`'deki `COLLECT_SYMBOLS` (varsayilan 10 coin). Ilk calismada Binance'in tuttugu ~29 gunluk gecmis de cekilir (geri doldurma); toplayici uzun sure kapali kalirsa bosluk, borsada hala duruyorsa kendiliginden doldurulur.
- Saklama: her calismadan sonra `COLLECT_RETENTION_DAYS` (varsayilan 45) gunden eski satirlar silinir; tabloda her an en fazla son 45 gun bulunur.

- Izleme: her calisma `collectorrun` tablosuna yazilir (durum OK / PARTIAL / FAILED / INTERRUPTED, sure, eklenen/silinen satir, hata veren semboller; 45 gun saklanir). `tradecrm collect status` ve paneldeki Toplayicilar sayfasi buradan saglik uretir: surec calismiyorsa "durdu", ust uste 3 basarisiz calisma ya da butun semboller bayatsa "basarisiz" (Genel Bakis'ta kritik uyari), bir sembol bayat/eksikse "sorunlu".

Toplayici baslarken veritabanina ulasamazsa (oturum acilisinda Docker gec gelebilir) cikmaz, 30 sn arayla bekler.

Toplayici admin panelinden de baslatilir (asagida); ayri bir surectir, panel kapansa da calisir (`state/collector.pid`, `logs/collector.log`).

## Admin paneli

React + TypeScript arayuz (`frontend/`), FastAPI backend (`src/tradecrm/api/`). Birden cok kullanici, hepsi ayni yetkide.

```
cd frontend && npm ci && npm run build && cd ..   # arayuzu derle (src/tradecrm/api/static/)
tradecrm admin create-user ayse                     # ilk kullanici (parola gizli sorulur, en az 12 karakter)
tradecrm admin serve                                # http://127.0.0.1:8000  (eski ad: tradecrm dashboard run)
```

Arka planda: `tradecrm admin start` / `tradecrm admin stop` (logs/admin.log). Oturum acilisinda otomatik baslatma (Windows, Gorev Zamanlayici; panelden de acilip kapanir):

```
tradecrm autostart enable admin       # paneli
tradecrm autostart enable collector   # veri toplayiciyi
tradecrm autostart enable live        # paper runner'i SON baslatildigi parametrelerle (tradecrm live resume)
tradecrm autostart enable live_kucoin # KuCoin (gercek hesap) runner'i son parametrelerle; emir salterlerini ACMAZ
tradecrm autostart status
```

`live resume` son parametreleri panelle ayni dogrulamadan gecirir (profil/sembol/TF beyaz listesi, yalnizca paper, kayitli sermaye); gecersizse baslatmaz. Alim izni ayrica DB'dedir -- otomatik baslatma onu acmaz.

Gelistirme: `tradecrm admin serve` + `cd frontend && npm run dev` (http://localhost:5173, `/api` 8000'e yonlenir).

| Sayfa | Icerik |
|---|---|
| Genel Bakis | portfoy degeri, nakit, pozisyondaki deger, gerceklesmemis K/Z, equity grafigi, uyarilar; **Kar/Zarar panosu**: donem (7/30/90 gun, tumu) ve hesap secimiyle gerceklesen + anlik gerceklesmemis K/Z, kazanma orani, kar faktoru, kumulatif K/Z grafigi, sembol ve cikis nedeni dagilimi, acik islemlerin anlik K/Z'si (`GET /api/pnl`) |
| Hesaplar | hesap basina muhasebe, izinler, drift, risk islemleri |
| Pozisyonlar / Islemler / Emirler | anlik fiyatli pozisyonlar, mum grafigi (giris/stop/kar al), CSV, belirsiz emirler |
| Risk | alim izni ac/kapat, kill-switch (hesap adini yazarak onay), olay gecmisi |
| Raporlar | gunluk / haftalik / aylik / ozel donem raporu (hesap secimli): ozet, equity ve maks. dusus, gunluk K/Z, sembol ve cikis nedeni dagilimi, islemler, risk olaylari, toplayici ve panel islemleri; yazdir/PDF, Markdown ve islem CSV indirme, Telegram'a gonderme. Rapor olusturuldugu anin donmus goruntusudur |
| Surecler | paper runner (profil, semboller, TF, sermaye; son parametreleri forma yukle) ve toplayici baslat/durdur, canli log; her surec ve panelin kendisi icin "oturum acilisinda baslat" anahtari |
| Toplayicilar | saglik (saglikli/sorunlu/basarisiz/durdu), sembol x metrik veri tazeligi ve kayip saatler, calisma gecmisi (sure, eklenen/silinen, hatali semboller), "simdi calistir", surec logu |
| Backtest | walk-forward + MC + kapi, siradaki isi iptal, sonuc sayfasi, "drift baseline'i olarak kaydet" |
| Veri kaynaklari / Denetim kaydi / Kullanicilar | kaynak sagligi, kim-ne-zaman kaydi, kullanici yonetimi |

Guvenlik:
- Giris zorunlu; oturum cerezi HttpOnly + SameSite=Strict, DB'de yalnizca token hash'i. Parola scrypt ile saklanir. Ayni kullanici + IP icin 5 hatali denemede 5 dakika kilit.
- Durum degistiren her istek oturuma bagli CSRF token'i ister ve `adminaction` tablosuna kullanici adiyla yazilir.
- **Canli hesapta panel yalnizca guvenli yonde calisir**: alim iznini kapatabilir, kill-switch tetikleyebilir. Izni acmak, kill-switch'i sifirlamak ve ilk emir onayi yalnizca CLI'dan yapilir.
- Holdout panelden calistirilamaz. Paper hesap baslangic sermayesi `account` tablosunda saklanir; ayni hesap farkli sermayeyle baslatilamaz.
- Varsayilan olarak yalnizca 127.0.0.1'de dinler. Disari acilacaksa HTTPS reverse proxy arkasina koyun ve `ADMIN_COOKIE_SECURE=true` yapin.

### Coklu sembol

```
tradecrm live run --profile donchian_fng --timeframe 1d --symbols BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,LINK/USDT,AVAX/USDT,LTC/USDT
```

Sembol basina bir runner, hepsi **ayni hesapta**: nakit, %35 toplam pozisyon tavani (profilde `max_total_fraction`; 2026-11-18'de %75'ten dusuruldu), risk bayraklari, gunluk zarar ve devre kesiciler (art arda kayip, gunluk islem sayisi) hesap genelidir. Backtest her sembolu kendi sermayesiyle ayri olcer; canlida semboller sermaye icin yarisir ve tavan dolunca yeni sinyaller `exposure_cap_reached` ile atlanir -- forward test sonuclari bu farkla yorumlanmali.

### Performans bozulma alarmi ve rapor (Oneri 13)

```
tradecrm backtest walk-forward --profile donchian_fng --timeframe 1d --days 2500 --train-days 365 --test-days 120 --symbols ... --save-baseline
tradecrm report weekly --account paper --profile donchian_fng --timeframe 1d [--send]
tradecrm report generate --kind week|day|month|custom [--start YYYY-MM-DD --end YYYY-MM-DD] [--account paper] [--send] [--markdown rapor.md]
```

- `--save-baseline` OOS islemlerin fiyat getirilerini `state/baselines/<profil>__<tf>.json`'a yazar (git'e girmez; ayni komutla yeniden uretilir). `live run` baseline'i bulamazsa drift izlemesinin kapali oldugunu soyler.
- Her tick'te hesabin son 30 kapanan isleminin (en az 10) ortalama fiyat getirisi, backtest dagilimindan bootstrap ile turetilen %5 alt sinirla karsilastirilir. Altindaysa hesapta **yeni alim kapatilir** + Telegram uyarisi; cikislar calismaya devam eder.
- Pencere, hesabin alim izninin **son acildigi andan** baslar: inceleme sonrasi `risk enable-trading` temiz sayfa demektir.
- `live run` her pazartesi 08:00 UTC'de hesap basina rapor gonderir (kapanan islemler, P&L, cikis nedenleri, hesap durumu, izinler, drift).
- Alarm istatistiksel: strateji saglamken de kontrol basina ~%5 yanlis alarm olasiligi vardir; kontroller tekrarlandikca birikir.

### Portfoy

`a+b` (esit agirlik) ya da `a:0.6+b:0.4` yazimi, her profili sermayenin kendi payiyla ayri bir paper alt hesapta (`paper:<profil>`) calistirir; backtest'te equity'ler toplanir:

```
tradecrm backtest compare --profiles donchian_fng,donchian_fng+mean_reversion
tradecrm live run --profile donchian_fng:0.7+mean_reversion:0.3
```

## Canliya gecis (Faz 10: KuCoin spot; Binance da destekli)

Adapter: `src/tradecrm/broker/ccxt_spot_adapter.py` -- borsa-bagimsiz; farklar `ExchangeSpec`'te (KuCoin, Binance).
MARKET alim tutar ile, satis miktar ile; her emirde borsanin istemci kimligi (KuCoin `clientOid`) = sistemin deterministik
kimligi. KuCoin emir yanitinda dolum dondurmez: dolum ayni kimlikle sorgulanarak teyit edilir; "kimlik zaten var" (126044)
gelirse emir tekrar GONDERILMEZ, sorgulanir. Bot hesaptaki butun USDT'yi degil `--initial-capital` butcesini (+ gerceklesen K/Z)
yonetir ve borsadaki serbest USDT'yi asamaz; diger varliklara dokunmaz. Sinyaller Binance'in herkese acik piyasa verisinden uretilir.
Canli runner panelden baslatilamaz ve otomatik baslatilmaz (bilincli).

KuCoin'de **test ortami yok**; bu yuzden once kuru calisma, sonra cok kucuk butce:

1. API anahtari (KuCoin > API Management): yalnizca **General + Spot (Trade)**, **Withdrawal (para cekme) KAPALI**,
   IP kisitlamali; passphrase'i not edin.
2. Butceyi KuCoin'de **Funding -> Trading** hesabina transfer edin (bot Trading hesabini kullanir).
3. `.env`: `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE`.
4. On kontrol (emir gondermez): `tradecrm live check --exchange kucoin --initial-capital 100`
   -- para cekme izni aciksa HATA, para Funding'de kaldiysa UYARI verir.
5. Kuru calisma: `.env` `TRADING_ENABLED=false` / `DRY_RUN=true` iken
   `tradecrm live run --mode kucoin --profile donchian_fng --timeframe 1d --symbols BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT --initial-capital 100`
   -> bakiye okunur, sinyaller degerlendirilir, emir GONDERILMEZ (`order_blocked: env_live_trading_disabled`).
6. Emirleri ac: `.env` `TRADING_ENABLED=true`, `DRY_RUN=false`; sonra
   `tradecrm risk enable-trading --account kucoin` ve `tradecrm risk confirm-first-order --account kucoin`.

Surekli calisma (arka planda, panelin Surecler sayfasinda da gorunur):
`tradecrm live start --mode kucoin --profile donchian_fng --timeframe 1d --symbols ... --initial-capital 75`,
`tradecrm live stop --mode kucoin`, oturum acilisinda: `tradecrm autostart enable live_kucoin`.
Panel bu runner'i yalnizca durdurur ve SON parametrelerle yeniden baslatir; parametreler ve emir salterleri panelden degismez.
Baslaticilar veritabanini en fazla 2 dk bekler; runner'in kendisi DB hazir olana kadar bekler (oturum acilisinda Docker gec gelebilir).

Binance icin ayni adimlar `--exchange binance` / `--mode binance` ile (testnet: `BINANCE_TESTNET=true`, hesap `binance:testnet`).

Notlar: sinyaller Binance verisinden, dolumlar KuCoin fiyatindan olur (buyuk coinlerde fark kucuktur). Stoplar gun icinde
izlenmez (gunluk mum); runner'in calistigi bilgisayar kapaliysa cikislar da gecikir.

## Guvenlik

### Alim izni: hangisi belirleyici?

Hesaplar: `paper`, `paper:<profil>` (portfoy alt hesabi), `binance` (canli). Bakiyeler, islemler, risk bayraklari ve gunluk zarar hesap bazinda tamamen ayridir.

| | Paper hesap | Canli hesap |
|---|---|---|
| `.env` `TRADING_ENABLED` / `DRY_RUN` | etkisiz | **ana salter**: `TRADING_ENABLED=true` VE `DRY_RUN=false` olmadan hicbir canli emir verilmez |
| DB bayragi (`tradecrm risk enable-trading --account ...`) | gerekli | gerekli (operasyonel anahtar) |
| Ilk emir onayi (`tradecrm risk confirm-first-order --account binance`) | - | gerekli |
| Kill-switch | hesap ya da kok hesap | hesap ya da kok hesap |

Yani canli hesapta `.env` VE DB birlikte acik olmali; biri kapaliysa alim yok. Kok hesabi kapatmak (`--account paper`) butun paper alt hesaplarini durdurur. Mevcut durumu ve etkin izni `tradecrm risk status --account <hesap>` gosterir.

### Emir guvenligi

- Her emir `(hesap, sembol, yon, mum)` ile tekildir ve borsaya gonderilmeden once `trade_order` tablosuna yazilir: ayni mum icin ikinci emir -- tekrar gelen sinyal, yeniden baslatma ya da eszamanli iki runner -- veritabani kisitiyla engellenir. DB'ye yazilamayan emir gonderilmez.
- `client_order_id` deterministiktir. Zaman asiminda emir kor tekrar gonderilmez: once ayni kimlikle borsaya sorulur.
- Her tick basinda (ve yeniden baslatmada) kesinlesmemis emirler borsaya sorulup uzlastirilir.

### Yeni alimin durdugu durumlar (satis her zaman acik)

Gunluk kar hedefi (+%5, profilin `BreakerConfig.daily_profit_target_pct`): gunun K/Z'si (gerceklesen + acik pozisyonlar) hedefe ulasinca o UTC gunu yeni alim yapilmaz, ertesi gun kendiliginden acilir. Pozisyon hedefe gore BUYUTULMEZ; kill-switch tetiklenmez. Backtest simulatoru ayni kurali uygular.

Kesinlesmemis emir ya da hesap uyusmazligi, hesap durumu/fiyat alinamamasi, bayat sinyal (son kapanis yarim mumdan eski), eksik mum, gecersiz OHLC, anlik fiyatin son kapanistan %10'dan fazla sapmasi, kill-switch, devre kesiciler, toplam pozisyon tavani (%35 -- tek emre degil butun acik pozisyonlara uygulanir). Gunluk zarar limiti gerceklesen + butun acik pozisyonlarin gerceklesmemis zararini kapsar.

### Testler

```
docker compose up -d          # test PostgreSQL'i (port 5434)
pytest -q
pip install -r requirements.lock && pip install -e . --no-deps   # kilitli, test edilmis surumler
```

CI (`.github/workflows/tests.yml`) kilitli surumlerle PostgreSQL servisi uzerinde lint + testleri, ayrica frontend icin lint + tip kontrolu + test + build calistirir. Frontend testleri: `cd frontend && npm test`.

- Sirlar (`.env`) asla commit edilmez.
