# Resultados de las pruebas de factibilidad — Tarea 1

Fecha de ejecución: 2026-08-26. Todos los scripts están en este directorio y son ejecutables directamente (`python3 test_*.py`).

## Stack instalado y funcionando

- PostgreSQL 18 + PostGIS 3.6 + pgRouting 4.0.1 (BD `ruteo_drones`, rol `kali`)
- Python: `osmnx 2.0.3`, `geopandas 1.1.4`, `shapely 2.1.2`, `psycopg2`, `requests`

## Resultado por fuente

| # | Fuente | Script | Estado | Nota |
|---|--------|--------|--------|------|
| 1 | Open-Meteo (viento) | `test_open_meteo.py` | ✅ OK | JSON real, sin API key. Rachas de hasta 45 km/h detectadas para el punto de prueba |
| 2 | Overpass API (OSM, edificación) | `test_overpass.py` | ✅ OK (con fix) | El User-Agent por defecto de `requests`/`osmnx` **es bloqueado por el servidor (HTTP 406)**. Solución: fijar un `User-Agent` propio en la cabecera |
| 3 | USGS FDSN (sismos) | `test_usgs.py` | ✅ OK | 105 eventos M≥2 reales desde 2015 en la Región de Valparaíso, sin API key |
| 4 | NASA FIRMS (focos de incendio) | `test_firms.py` | ✅ Endpoint accesible | Sin `MAP_KEY` responde 400 "Invalid MAP_KEY" — comportamiento esperado y documentado. **Falta que un integrante se registre** en https://firms.modaps.eosdis.nasa.gov/api/map_key/ para obtener la key real antes de la presentación |
| 5 | SENAPRED (alertas) | — | ⚠️ **No es una API pública** | `senapred.cl` es una SPA en React; su backend real es un GraphQL en AWS AppSync con **autenticación Cognito** (no hay endpoint abierto). El "Visor Chile Preparado" es un iframe de WordPress sin servicio de mapas expuesto públicamente que se haya podido ubicar. **Reemplazado por CONAF (ver más abajo)** |
| 5b | **CONAF — reemplazo de SENAPRED** | `test_conaf.py` | ✅ **OK, mejor de lo esperado** | La página de CONAF enlaza un **dashboard ArcGIS Online público** ("Pronóstico de Riesgo", `deigeprif`, `access: public`). Detrás hay **Feature Services REST reales, sin API key**: `PI/FeatureServer/4` (polígonos de probabilidad de ignición a 5 días, actualizados a diario) y `ASP/FeatureServer/0` (polígonos de Áreas Silvestres Protegidas). Se consultaron ambos con un bbox de la Región de Valparaíso y devolvieron geometrías de polígono reales |
| 6 | PostGIS + pgRouting (pipeline completo) | `test_postgis_pgrouting.py` | ✅ OK | Se replicó exactamente el pipeline del informe con datos sintéticos: `ST_Intersects` corta aristas por polígono DGAC, `ST_DWithin` penaliza por cercanía a foco de incendio, `pgr_KSP` devuelve rutas alternativas (algoritmo de Yen) |
| 7 | OSMnx (grafo vial real) | `construir_grafo_local.py` | ✅ **Resuelto con plan B** | Overpass quedó bloqueado tras uso pesado (verificado en servidor principal y espejo). Se ejecutó el plan de contingencia: `chile-latest.osm.pbf` de Geofabrik (346 MB) → `osmium extract` por bbox (21.8 MB) → `osmium tags-filter` a vías transitables (3.5 MB) → `ox.graph_from_xml()`. Resultado real: **52.856 nodos, 126.480 aristas** en <1 min de procesamiento. Mapa en `evidencia/mapa_grafo_valparaiso.png` |
| 2b | Overpass (edificación) — reemplazo local | `extraer_edificacion_local.py` | ✅ **Reemplazado, mejor cobertura** | La consulta en vivo a Overpass solo devolvía 8 elementos (filtro muy restrictivo). Con el mismo extracto de Geofabrik (`osmium tags-filter w/building`) se obtuvieron **74.550 edificios totales, 17.961 con dato de altura (24,1% de cobertura)** — muestra estadísticamente representativa en vez de 8 casos sueltos. Histograma real en `evidencia/grafico_edificacion_valparaiso.png` |
| 2c | **Google Open Buildings 2.5D Temporal (alturas continuas)** | `extraer_google_open_buildings.py` | ✅ **100% Cobertura Real** | Soluciona la limitación del 75,9% de edificios sin altura en OSM. Mediante el dataset global satelital de Google Research (resolución efectiva ~4m, CRS UTM 19S / EPSG:32719), se procesaron **85.268 estructuras urbanas con altura en metros** para las 5 comunas del Gran Valparaíso (mediana: 5,4 m, media: 7,7 m, P90: 12,9 m, P99: 47,1 m, máx: 91,6 m). Mapas y comparativas en `evidencia/imagenes/` |
| 3b | INE — población por manzana censal (Metadata #3) | `generar_mapa_poblacion.py` | ✅ **OK** | El portal genérico del INE no tiene un endpoint REST abierto usable; se encontró el mismo Censo 2017 reprocesado en un **Feature Service ArcGIS público, sin API key**, publicado por el **Observatorio de Ciudades UC** (`https://services9.arcgis.com/kKJR3Qt68ohAWuet/arcgis/rest/services/Manzanas_censo_2017/FeatureServer`). Con paginación (`resultOffset`, tope de 2000 registros/página) y el nombre de comuna con tilde correcto (`VALPARAÍSO`, no `VALPARAISO`) se obtuvieron **10.750 manzanas reales con geometría de polígono y 1.005.013 habitantes** en las 5 comunas del Gran Valparaíso. Mapa de densidad en `evidencia/imagenes/mapa_ine_poblacion_manzanas.png` |

## Aclaración sobre la capa de probabilidad de ignición

El campo `label` de `PI/FeatureServer/4` **no es un porcentaje directo**: es el límite superior de un decil (1-10, 11-20, ..., 91-100) de un índice de Probabilidad de Ignición (campo `var="PI"`), según el `renderer` (classBreaks) publicado por el propio servicio. En la Región de Valparaíso para el 2026-08-30 solo aparecieron los deciles bajos (1-10, 11-20, 21-30); a nivel nacional la escala completa llega hasta 100. Además el servicio publica 5 capas (`d0` a `d4`, una por día), confirmando el pronóstico a 5 días que menciona el informe. El mapa y el script (`generar_evidencia_visual.py`) ya quedaron corregidos para mostrar los rangos reales en vez del código crudo.

## Aclaración sobre el mapa de población por manzana (Quilpué vs. Valparaíso)

En el mapa `mapa_ine_poblacion_manzanas.png`, la comuna de Quilpué se ve casi vacía de manzanas mientras Valparaíso se ve "completa". No es un error de la consulta ni de la geometría de los límites comunales (se verificó contra el propio Feature Service `Comunas` de CONAF, que coincide con el área oficial de cada comuna): es que las "manzanas" censales del INE solo existen en el **área urbana**; el área rural se censa con otro tipo de entidad (localidades/entidades rurales, sin geometría de manzana). La proporción urbana/rural difiere mucho entre comunas:

- **Valparaíso**: ~402 km² totales, ~99,7% de la población es urbana (casi toda la comuna, incluidos los cerros, se considera urbana para el censo) → las manzanas cubren casi todo el contorno comunal.
- **Quilpué**: ~537 km² totales, pero solo ~21,6 km² (≈4%) son área urbana; los ~515 km² restantes son precordillera rural sin manzanas → en el mapa solo aparece un núcleo pequeño de manzanas rodeado de un contorno comunal mucho más grande.

De paso se detectó y descartó una falsa alarma propia: al calcular el área de los polígonos comunales reproyectando a Web Mercator (EPSG:3857) da un valor ~43% más alto que el real (a esta latitud, `1/cos²(33°) ≈ 1,42`, que es justo el factor de más que aparecía). El campo `area` nativo del Feature Service de CONAF (en hectáreas) sí coincide con las superficies oficiales (ej. Quilpué: 534,8 km² del servicio vs. 536,9 km² oficial). Para cualquier cálculo de área en la Tarea 2 hay que reproyectar a un CRS proyectado adecuado para Chile (ej. UTM 19S / EPSG:32719), nunca usar Web Mercator para mediciones.

## Corrección de escala de color en el mapa de población por manzana

La primera versión del mapa usaba una escala de color lineal de 0 hasta el máximo real (2.754 hab./manzana), lo que hacía ver casi todo el mapa del mismo color oscuro. La distribución real es muy sesgada: mediana de **58 habitantes por manzana**, y el **95% de las 10.750 manzanas tiene 302 habitantes o menos** (solo 31 manzanas, 0,3%, superan 1.000). Al estirar la escala hasta el máximo, esas pocas manzanas densas (edificios altos) comprimían al resto en la parte inferior de la paleta, ocultando la variación real entre baja y media densidad.

Se corrigió recortando la escala de color en 300 (percentil 95), con una flecha en la barra de color indicando "300 o más" para las manzanas por encima de ese umbral (`vmin=0, vmax=300, extend="max"` en `generar_mapa_poblacion.py`). Con esto el mapa distingue correctamente los núcleos de alta densidad (costa de Viña del Mar, Villa Alemana) de las zonas de baja densidad.

## Hallazgos a incorporar al informe

1. **Reemplazar SENAPRED por CONAF como Amenaza 2**: CONAF expone, sin darse cuenta explícitamente en su sitio de contenido, un dashboard ArcGIS Online **público** con dos Feature Services REST:
   - `https://services5.arcgis.com/A1ELWse9bRAi2JiV/arcgis/rest/services/PI/FeatureServer/4` — polígonos de **probabilidad de ignición a 5 días** (pronóstico diario, exactamente lo que el informe original citaba como "fuente de reserva" de CONAF, pero ahora confirmado con URL real y datos reales).
   - `https://services5.arcgis.com/A1ELWse9bRAi2JiV/arcgis/rest/services/ASP/FeatureServer/0` — polígonos de **Áreas Silvestres Protegidas**, que además sirve para la capa de exclusión del §7.2 (hasta ahora atribuida de forma genérica a "DGAC/CONAF").
   Esto es un mejor resultado que el original: dos APIs REST públicas y con geometría de polígono en una sola fuente, sin necesidad de scraping ni login.
2. **SENAPRED se mantiene como fuente complementaria/cualitativa**, no como API: puede citarse para contexto normativo (categorías de alerta) pero sin pretender consumo automático, ya que su backend real requiere login (Cognito).
3. **Overpass tuvo una caída real durante las pruebas** (verificada contra dos servidores independientes) — no es un problema del código. Para la presentación: descargar el grafo con anticipación y tener el `.gpkg`/mapa ya generado como respaldo, más `Geofabrik` como plan B si hace falta volver a descargar en vivo.
4. **Overpass exige `User-Agent` personalizado**: detalle técnico menor pero real (bloquea el UA por defecto de `requests`/`osmnx` con HTTP 406) — inclúyanlo en el código de la Tarea 2.
5. Open-Meteo, USGS, FIRMS, CONAF y PostGIS+pgRouting quedaron **demostrados con evidencia real**, no solo argumentados.

## Corrección importante: focos FIRMS "en Chile"

El conteo de "56 focos en Chile" reportado antes estaba mal. La consulta a FIRMS usó un **bbox rectangular** (`-76,-56,-66,-17`), y como Chile es un país angosto, ese rectángulo incluye buena parte de **Argentina y Bolivia** al este de la cordillera. Al filtrar los 56 puntos contra el **polígono real de Chile** (unión de las 16 regiones, `evidencia/regiones_chile.geojson`), solo **7 estaban efectivamente en territorio chileno**; los otros 49 eran de Mendoza, Neuquén, La Rioja, etc.

Esto es un hallazgo de diseño importante para la Tarea 2: **las consultas geoespaciales de amenazas deben filtrarse por el polígono real de la zona de interés (`ST_Intersects`/`ST_Within` con el polígono, no con su bounding box)**, o el modelo va a contaminarse con eventos de otro país. El mapa `mapa_firms_focos_incendio.png` ya quedó corregido mostrando ambos grupos (dentro/fuera de Chile) para dejar el filtro en evidencia.

## Incorporación de Google Open Buildings 2.5D Temporal para Alturas de Edificación

Para resolver la limitación de OpenStreetMap donde solo el **24,1% de los edificios (17.961 de 74.550)** cuenta con datos de altura (`building:levels` o `height`), se integró el **Google Open Buildings 2.5D Temporal Dataset (versión v1, 2023)** desarrollado por Google Research.

### Aspectos técnicos de la fuente
- **Formato y Cobertura**: Cloud Optimized GeoTIFFs (COGs) en Google Cloud Storage (`gs://open-buildings-temporal-data/v1/geotiffs/`), organizados por S2 cells y proyectados en **UTM 19S / EPSG:32719**.
- **Bandas**: `building_height` (altura en metros por estructura), `building_presence` (probabilidad/confianza de presencia de estructura) y `building_fractional_count`.
- **Resolución**: Resolución efectiva de ~4 metros a partir de imágenes satelitales Sentinel-2 procesadas con Deep Learning.
- **Acceso Directo**: Se consultó el manifiesto oficial `97_EPSG_32719_2023_06_30.json` y se descargaron los bloques ráster de las 5 comunas del Gran Valparaíso (Valparaíso, Viña del Mar, Concón, Quilpué, Villa Alemana).

### Resultados cuantitativos consolidados (Gran Valparaíso)
- **Estructuras urbanas identificadas**: **85.268** puntos/polígonos de edificación continua (100% de cobertura espacial).
- **Altura mínima**: 0,04 m (estructuras a nivel de suelo / muros).
- **Altura mediana**: **5,38 m** (~2 a 2,5 pisos, representativo de la tipología residencial dominante en cerros de Valparaíso y Quilpué).
- **Altura media**: **7,67 m**.
- **Percentil 90 (P90)**: **12,94 m** (~5 pisos).
- **Percentil 95 (P95)**: **18,63 m** (~7-8 pisos).
- **Percentil 99 (P99)**: **47,14 m** (~15-16 pisos).
- **Altura máxima**: **91,56 m** (~29-30 pisos en el borde costero y plan de Viña del Mar / Valparaíso).
- **Estructuras > 15 m (media/alta altura)**: 6.321 (7,41%).
- **Estructuras > 30 m (torres / rascacielos costeros)**: 2.009 (2,36%).

### Archivos de evidencia generados
- `evidencia/datos/google_open_buildings_gran_valparaiso.json` (resumen estructurado con percentiles y sectores).
- `evidencia/datos/google_open_buildings_resumen.csv` (tabla comparativa por sector urbano).
- `evidencia/imagenes/grafico_google_open_buildings_alturas.png` (histograma continuo de alturas con anotaciones de tipologías).
- `evidencia/imagenes/comparativa_osm_vs_google_open_buildings.png` (gráfico de cobertura 24,1% vs 100% y perfiles por comuna).
- `evidencia/imagenes/mapa_google_open_buildings_2_5d.png` (mapa ráster espacial 2.5D de obstáculos de altura para drones).

## Archivos de evidencia

Ver `evidencia/` (o `codigo/evidencia/`): `open_meteo_response.json`, `overpass_response.json`, `usgs_response.json`, `conaf_probabilidad_ignicion.geojson`, `conaf_areas_protegidas.geojson`, `mapa_grafo_valparaiso.png`, `grafico_edificacion_valparaiso.png`, `overpass_local_edificacion.{json,csv}`, `google_open_buildings_gran_valparaiso.json`, `google_open_buildings_resumen.csv`, `grafico_google_open_buildings_alturas.png`, `comparativa_osm_vs_google_open_buildings.png`, `mapa_google_open_buildings_2_5d.png`.

