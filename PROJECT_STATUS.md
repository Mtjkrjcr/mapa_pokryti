# Project Status: mapapokryti

Aktualizováno: 2026-02-22

## 1) Cíl projektu
Lokální výpočet LOS/viewshed pokrytí z DEM pro velké množství nodů (cílově 350+), agregace do jedné coverage mapy a zobrazení ve web mapě.

## 2) Aktuální stav
- Pipeline je plně implementovaná a spustitelná.
- Výpočet běží lokálně přes GDAL (`gdal_viewshed`) + Python orchestraci.
- Web (Leaflet) běží lokálně a umí zobrazit OSM + coverage + body nodů.
- Vstupy nodů z `/Users/matt/codex/node-coordinates-*` jsou napojené.

## 3) Architektura projektu
- `src/main.py` CLI (`prepare-dem`, `compute`, `export`, `all`)
- `src/dem.py` příprava DEM (local/SRTM, reprojekce do UTM, AOI+buffer)
- `src/viewshed.py` výpočet viewshed per node + validace nodů + výšková logika
- `src/merge.py` merge binárních rastrů do `coverage_count`
- `src/export.py` export PNG + XYZ tiles (`gdal2tiles`)
- `web/` Leaflet aplikace
- `config/` více profilů konfigurace

## 4) Vstupy
### Nody
- Primární: `data/nodes/nodes.csv`
- Podporované formáty:
  - CSV s hlavičkou (`id,lat,lon[,height_m]`)
  - CSV bez hlavičky (`id,name,lat,lon,...`)
  - JSON s polem `nodes`

### DEM
- SRTM přes `elevation/eio` nebo lokální DEM.
- Připravený DEM výstup: `data/dem/dem_prepared.tif`.

## 5) Výstupy
- `out/coverage_count.tif`
- `out/coverage_count.png`
- `out/coverage_binary.png` (agregovaná binární červená maska: pokrytí alespoň 1 nodem)
- `out/coverage_meta.json`
- `out/nodes.geojson` (včetně `id` a `name`)
- `out/nodes_clean.csv`
- `out/nodes_rejected.csv`
- `out/nodes_stats.json`
- `out/tiles/{z}/{x}/{y}.png`
- `out/node_overlays/{node_id}.png` + `out/node_overlays/index.json` (single-node coverage překryvy)

## 6) Produkční profil (aktuálně výchozí logika)
Soubor: `config/config_production.yaml`

Klíčové parametry:
- `viewshed.max_distance_m: 80000`
- `viewshed.observer_height_default_m: 0`
- `viewshed.target_height_m: 0`
- `radio.frequency_mhz: 868`
- `input.height_strategy.mode: input_only`
- `input.height_strategy.min_height_m: 0`
- `input.height_strategy.use_fresnel: false`

Interpretace:
- Pokud node nemá vlastní výšku, počítá se s **0 m nad terénem v místě**.
- Terénní výška je vždy z DEM (tj. hora 1200 m n. m. je respektována přirozeně DEM daty).

## 7) Validace/čištění nodů
Aktivní v `input.validation`:
- drop invalid coords
- drop `(0,0)`
- dedupe podle `id`

Aktuální poslední běh (`out/nodes_stats.json`):
- `rows_input: 240`
- `rows_valid: 205`
- `rows_dropped_zero_coords: 35`
- `height_mode: input_only`
- `height_used_min_m: 0.0`
- `height_used_max_m: 0.0`
- `radio_frequency_mhz: 868.0`

## 8) Srovnávací profily (Heywhatsthat tuning)
- `config/config_compare_868_los.yaml`
  - 80 km, LOS, bez Fresnel
  - výstupy do `out/compare_los/`
- `config/config_compare_868_fresnel.yaml`
  - 80 km, Fresnel adaptace výšky
  - výstupy do `out/compare_fresnel/`

Poznámka:
- Pro podobnost s Heywhatsthat je vhodnější LOS profil.

## 9) Web
- URL: `http://localhost:8080/web/`
- Nutné spouštět server z kořene projektu, aby byly dostupné `out/*` cesty.
- Levý panel obsahuje seznam nodů (`name + id`) s filtrem.
- Agregovaná mapa má přepínač režimu:
  - `Barevna (count)` = počet viditelností
  - `Cervena (binarni)` = binární pokrytí (aspoň 1 node)
- Klik na marker i klik v levém seznamu přepíná pokrytí nodu.
- Lze vybrat více nodů současně (spojené zobrazení více overlayů).
- Tlačítko `Zobrazit vše` vrátí agregované pokrytí všech nodů.
- Sidebar je responzivní, plovoucí overlay nad mapou (desktop), s průhledným pozadím a kulatými rohy.

## 10) Spuštění
### Instalace
```bash
cd /Users/matt/mapapokryti
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### Produkční přepočet
```bash
cd /Users/matt/mapapokryti
source .venv/bin/activate
python -m src.main all --config config/config_production.yaml
```

### Web
```bash
cd /Users/matt/mapapokryti
python3 -m http.server 8080
# otevřít: http://localhost:8080/web/
```

## 11) Důležité implementační poznámky
- Merge viewshed rastrů je reprojektován do společné šablony DEM gridu (správná prostorová agregace).
- `gdal2tiles` vyžaduje 8-bit; export proto automaticky škáluje coverage do byte rastru pro tiles.
- Pro velké běhy je hlavní výkonový faktor kombinace:
  - `viewshed.max_distance_m`
  - `raster.resolution_m`
  - počet workerů `compute.workers`

## 12) Co dál (doporučeno)
- Pokud budeš mít per-node reálné výšky antén z Raspberry Pi, stačí je přidat do `height_m` v `nodes.csv`; pipeline je použije přímo.
- Pro přesnější RF model lze doplnit clutter (les/zástavba), aktuálně je model terrain-only LOS.
