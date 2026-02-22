# mapapokryti

Lokální výpočet LOS/viewshed coverage nad DEM pro velké množství nodů (350+).

## Co to dělá
- Načte nodes soubor (`data/nodes/nodes.csv`) s `id,lat,lon[,height_m]`
- Podporované vstupy: CSV s hlavičkou, export CSV bez hlavičky (`id,name,lat,lon,...`) i JSON (`{"nodes":[...]}`)
- Pro každý node spustí `gdal_viewshed` proti DEM
- Sloučí binární viewshed rastry do `out/coverage_count.tif` (hodnota = počet nodů, které vidí pixel)
- Exportuje `out/coverage_count.png`
- Volitelně vyrenderuje XYZ tiles do `out/tiles/`
- Vytvoří `out/nodes.geojson`
- `web/` obsahuje Leaflet mapu (OSM + coverage + nodes + opacity slider)
- Nody v mapě i v levém seznamu zobrazují `name + id`.
- Klik na node (v mapě nebo vlevo v seznamu) přidá/odebere jeho pokrytí; lze vybrat více nodů současně (kombinace overlayů).
- Tlačítko `Zobrazit vše` vrátí agregované pokrytí všech nodů.
- Agregovaná vrstva jde přepnout: `Barevna (count)` / `Cervena (binarni)`.

## Požadavky
- Python 3.10+
- GDAL CLI nástroje (`gdal_viewshed`, `gdal_translate`, volitelně `gdal2tiles.py`)
- Na macOS doporučeno přes Homebrew:
  - `brew install gdal`

## Instalace
```bash
cd mapapokryti
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## Konfigurace
- Zkopíruj sample:
```bash
cp config/config_sample.yaml config/config.yaml
cp data/nodes/nodes_sample.csv data/nodes/nodes.csv
```
- Pokud máš vlastní DEM, vlož ho jako `data/dem/dem.tif` a nastav `dem.mode: local`.
- `prepare-dem` vytvoří výpočetní DEM v metrickém CRS (`data/dem/dem_prepared.tif`), které používá krok `compute`.
- AOI lze zadat přes `aoi.bbox` nebo `aoi.polygon_geojson` (Polygon/MultiPolygon v EPSG:4326).
- Výška nodů: `input.height_strategy.mode: adaptive_min` dopočítá minimální výšku antény podle lokálního reliéfu DEM (`local_radius_m`, `clearance_margin_m`).
- RF model: `radio.frequency_mhz: 868` + Fresnel clearance (`input.height_strategy.use_fresnel`) pro realističtější mesh 868 MHz dosah.

## Spuštění
```bash
python -m src.main prepare-dem --config config/config.yaml
python -m src.main compute --config config/config.yaml
python -m src.main export --config config/config.yaml
# nebo vše najednou
python -m src.main all --config config/config.yaml
```

Production profil (350+ nodů):
```bash
python -m src.main all --config config/config_production.yaml
```

## Web náhled
```bash
cd web
python -m http.server 8080
```
Pak otevři `http://localhost:8080`.

## Poznámky k DEM
`prepare-dem` podporuje režimy:
- `local`: použije existující `data/dem/dem.tif`
- `srtm`: pokusí se stáhnout SRTM přes balíček `elevation` pro AOI bbox
- V obou režimech se DEM reprojektuje do UTM, ořízne na AOI + buffer (`max_distance_m`) a přeresampluje na `raster.resolution_m`.

Pokud auto-download selže (síť, provider změny, atd.), fallback je vložit vlastní `dem.tif` a použít `local`.

## Výstupy
- `out/coverage_count.tif`
- `out/coverage_count.png`
- `out/coverage_binary.png` (agregovaná binární červená vrstva)
- `out/coverage_meta.json` (bbox pro web)
- `out/nodes.geojson`
- `out/nodes_clean.csv` (vyčištěné nody použité ve výpočtu)
- `out/nodes_rejected.csv` (odfiltrované nody)
- `out/nodes_stats.json` (statistika validace)
- `out/nodes_clean.csv` obsahuje i `ground_elev_m`, `height_input_m`, `height_min_required_m`, `height_used_m`
- `out/tiles/{z}/{x}/{y}.png` (pokud zapnuto)
- `out/node_overlays/{node_id}.png` + `out/node_overlays/index.json` (single-node překryvy pro web)

## Výkon
- Paralelizace viewshed přes `compute.workers`
- Vypnutí paralelizace: `compute.parallel: false`
- Dočasné files v `tmp/viewsheds/`
- Produkční profil je v `config/config_production.yaml`, rychlý test profil v `config/config.yaml`

## CLI
```bash
python -m src.main prepare-dem --config config/config.yaml
python -m src.main compute --config config/config.yaml
python -m src.main export --config config/config.yaml
python -m src.main all --config config/config.yaml
```
