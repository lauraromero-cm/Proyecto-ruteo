# Tarea 2 — Ruteo Resiliente de Drones BVLOS (Gran Valparaíso)

Pipeline completo de extracción de datos reales, carga a una base de datos geoespacial y visualización web para la planificación de rutas de drones BVLOS que evalúan daños tras una emergencia (sismo) en el Gran Valparaíso, siguiendo la red vial real.

Este documento está organizado según los puntos de la rúbrica de la Tarea 2, para que cada criterio sea fácil de ubicar y verificar.

## Índice

- [0. Diagrama y esquema de la base de datos](#0-diagrama-y-esquema-de-la-base-de-datos)
- [1. Infraestructura (grafo vial)](#1-infraestructura-grafo-vial)
- [2. Metadata](#2-metadata)
- [3. Amenazas](#3-amenazas)
- [4. Documentación de cada JSON](#4-documentación-de-cada-json)
- [5. Modelamiento de amenazas y asignación de pesos](#5-modelamiento-de-amenazas-y-asignación-de-pesos)
- [6. Restricciones del usuario](#6-restricciones-del-usuario)
- [6-cargadores. Carga de cada JSON a la BD](#carga-de-cada-json-a-la-bd)
- [7. Sitio web (Leaflet)](#7-sitio-web-leaflet)
- [7.1 / 7.2. Algoritmo de ruteo resiliente](#71--72-algoritmo-de-ruteo-resiliente)
- [8. Ruta de ejemplo (peor caso, pgr_dijkstra puro)](#8-ruta-de-ejemplo-peor-caso-pgr_dijkstra-puro)
- [9. k=2 rutas Dijkstra y k=2 rutas resilientes](#9-k2-rutas-dijkstra-y-k2-rutas-resilientes)
- [10. Métrica de resiliencia](#10-métrica-de-resiliencia)
- [11. Plataforma: dupla origen/destino y diferencial de resiliencia](#11-plataforma-dupla-origendestino-y-diferencial-de-resiliencia)
- [Cómo compilar y ejecutar](#cómo-compilar-y-ejecutar)
- [Estructura de carpetas](#estructura-de-carpetas)

---

## 0. Diagrama y esquema de la base de datos

- **Diagrama**: [`db/diagrama_bd.png`](db/diagrama_bd.png), generado con [`db/generar_diagrama_bd.py`](db/generar_diagrama_bd.py) (`python3 db/generar_diagrama_bd.py`).
- **Esquema SQL**: [`db/schema.sql`](db/schema.sql) — crea las 12 tablas desde cero (se ejecuta automáticamente al iniciar el contenedor `db`, vía `docker-entrypoint-initdb.d`).

**Modelo**: PostgreSQL 17 + PostGIS 3.5 + pgRouting 3.7, usando la imagen oficial `pgrouting/pgrouting:latest` (ver justificación de esta elección de imagen en `codigo/RESULTADOS.md` — el enfoque original de instalar `postgresql-16-pgrouting` vía `apt` falló porque el repositorio pgdg para Debian bullseye está archivado).

**Grupos de tablas**:

| Grupo | Tablas | Rol |
|---|---|---|
| **Infraestructura** | `infraestructura_nodos`, `infraestructura_aristas` | El grafo vial real (OSM/Geofabrik) sobre el que corre `pgr_dijkstra`. `length_m` es el costo base (Fase 2). |
| **Nodos especiales** | `nodo_carabineros` | Origen del dron (comisarías) y puntos de carga, vinculados al nodo vial más cercano por KNN (`<->`). |
| **Metadata** | `metadata_viento`, `metadata_tendido_electrico`, `metadata_relieve`, `metadata_poblacion`, `metadata_permisos_edificacion` | Capas que condicionan el costo de ruteo o informan restricciones del usuario (no son peligros en sí mismas). |
| **Amenazas** | `amenaza_firms`, `amenaza_conaf_ignicion`, `amenaza_conaf_areas_protegidas`, `amenaza_sismos` | Peligros reales: incendios activos, riesgo de ignición, áreas protegidas y sismicidad (esta última también define el **destino** de la misión). |

Además de las columnas fijas del esquema, `sitio_web/generar_rutas_comparativas.py` agrega en tiempo de ejecución 3 columnas derivadas a `infraestructura_aristas` (no están en `schema.sql` porque dependen de joins espaciales con las demás tablas, que deben estar ya cargadas): `cost_resiliente`, `factor_antiguedad_zona` y `viento_racha_max_kmh`. Se explican en las secciones 5 y 6.

`schema.sql` también define 3 funciones SQL reutilizables que determinan qué sismos son destinos válidos de ruteo, combinando magnitud del sismo y viento real de la zona: `radio_relevancia_sismo_km`, `radio_operacional_viento_km` y `distancia_max_valida_sismo_km` (ver sección "Qué sismos son destinos válidos" más abajo).

---

## 1. Infraestructura (grafo vial)

Carpeta [`infraestructura/`](infraestructura/):

- [`extraer_infraestructura.py`](infraestructura/extraer_infraestructura.py): transforma el grafo vial de OSM/Geofabrik (ya extraído y verificado en Tarea 1, copia local en `infraestructura/datos_tarea1/`) a `nodos.json` (52.856 nodos) y `aristas.json` (126.480 aristas).
- [`extraer_carabineros.py`](infraestructura/extraer_carabineros.py): obtiene los cuarteles de Carabineros de las 5 comunas (ArcGIS FeatureServer de Carabineros de Chile) → `carabineros.json` (29 cuarteles, 9 de ellos comisarías que sirven de origen/carga del dron).

Documentación de estructura: [`nodos.schema.md`](infraestructura/nodos.schema.md), [`aristas.schema.md`](infraestructura/aristas.schema.md), [`carabineros.schema.md`](infraestructura/carabineros.schema.md).

---

## 2. Metadata

Carpeta [`metadata/`](metadata/) — 5 fuentes, cada una con su extractor independiente:

| Script | JSON | Fuente | Qué aporta |
|---|---|---|---|
| `extraer_viento.py` | `viento.json` | Open-Meteo (6 puntos: Plan/costa y Cerros/Placilla de Valparaíso, Viña del Mar, Concón, Quilpué, Villa Alemana) | Viento y rachas — base de la **restricción** de usuario (punto 6) |
| `extraer_tendido_electrico.py` | `tendido_electrico.json` | OSM/Overpass (`power=line`/`minor_line`) | Riesgo de colisión físico y directo para vuelo bajo |
| `extraer_relieve.py` | `relieve.json` | Open Topo Data (SRTM 90m) | Pendiente del terreno por arista |
| `extraer_poblacion.py` | `poblacion.json` | INE Censo 2017 (Observatorio de Ciudades UC) | Densidad poblacional por manzana |
| `extraer_permisos_edificacion.py` | `permisos_edificacion.json` | Observatorio de Ciudades UC (permisos 2010-2018) | Proxy de antigüedad de la edificación por zona |

Todas las fuentes fueron verificadas primero en Tarea 1 (ver `codigo/RESULTADOS.md`).

---

## 3. Amenazas

Carpeta [`amenazas/`](amenazas/) — 3 fuentes, 4 JSON (CONAF expone dos capas):

| Script | JSON | Fuente | Qué aporta |
|---|---|---|---|
| `extraer_firms.py` | `firms.json` | NASA FIRMS | Focos de incendio activo (peligro físico inmediato) |
| `extraer_conaf.py` | `conaf_ignicion.json`, `conaf_areas_protegidas.json` | CONAF (ArcGIS FeatureServer) | Probabilidad de ignición a 5 días y áreas SNASPE (exclusión) |
| `extraer_sismos.py` | `sismos.json` | USGS FDSN Event API | Sismicidad — define el **destino** de la misión (evaluar daños) |

---

## 4. Documentación de cada JSON

Cada archivo `*.json` generado tiene un `*.schema.md` junto a él (mismo directorio) que explica su origen, cada campo, un ejemplo real y, cuando corresponde, cómo se usa en el ruteo:

`infraestructura/nodos.schema.md`, `infraestructura/aristas.schema.md`, `infraestructura/carabineros.schema.md`, `metadata/viento.schema.md`, `metadata/tendido_electrico.schema.md`, `metadata/relieve.schema.md`, `metadata/poblacion.schema.md`, `metadata/permisos_edificacion.schema.md`, `amenazas/firms.schema.md`, `amenazas/conaf.schema.md`, `amenazas/sismos.schema.md`.

---

## 5. Modelamiento de amenazas y asignación de pesos

### 5.1 Cómo se modela cada amenaza/metadata

Todo el modelamiento vive en [`sitio_web/generar_rutas_comparativas.py`](sitio_web/generar_rutas_comparativas.py) (catálogo precalculado) y se replica en [`sitio_web/servidor.py`](sitio_web/servidor.py) (backend dinámico), sobre la columna `cost_resiliente` de `infraestructura_aristas` (inicializada en `cost_resiliente = length_m`):

| Fuente | Operador espacial | Mecanismo |
|---|---|---|
| Tendido eléctrico | `ST_DWithin(arista, linea, 0.0002°)` (~22m) | **Penalización** ×1.5 — riesgo de colisión, no excluye porque el dron puede pasar cerca con precaución |
| CONAF ignición | `ST_Intersects(arista, polígono)` | **Penalización** ×1.3 — riesgo de incendio activo en 5 días, gradual según el polígono que cruce |
| CONAF áreas protegidas | `ST_Intersects(arista, polígono)` | **Exclusión efectiva** ×1000 — vuelo prohibido/no deseable en SNASPE |
| FIRMS (incendio activo) | `ST_DWithin(arista, foco, 0.005°)` (~500m) | **Exclusión efectiva** ×1000 — peligro físico inmediato, se evita por completo |
| Permisos de edificación (antigüedad) | `ST_Intersects(arista, manzana)` | **Penalización condicional** ×(1 + 0.3·índice), **solo si el destino es un sismo** (ver más abajo) |
| Sismos (USGS) | nodo vial más cercano al epicentro, con umbral combinado magnitud + viento (ver abajo) | Define el **destino** de la ruta, no penaliza aristas directamente |

Esta distinción (excluir vs. penalizar vs. priorizar) sigue la taxonomía de `codigo/RESULTADOS.md`: un incendio activo o una zona protegida son peligros de los que hay que alejarse (exclusión), mientras que el tendido eléctrico o una ignición pronosticada son riesgos que se pueden asumir con precaución (penalización leve). La sismicidad es distinta de ambas: define el **objetivo** de la misión (ir a evaluar daños), así que nunca se "evita" — en cambio, condiciona un riesgo secundario (ver abajo).

**Caso especial — por qué los sismos no penalizan, sino que condicionan**: la edificación antigua (proxy via permisos de edificación 2010-2018, `metadata_poblacion.indice_antiguedad`) representa riesgo de escombros/obstrucción de calle, que solo es relevante si la misión es justamente evaluar daños post-sismo. Por eso el costo `cost_resiliente * (1 + 0.3 * factor_antiguedad_zona)` solo se activa cuando `destino.tipo == "sismo"` — para un destino de tipo incendio, no se aplica en absoluto. El peso `0.3` se eligió deliberadamente bajo para que nunca domine sobre tendido eléctrico (×1.5) o CONAF (×1000), que son riesgos físicos directos e inmediatos, mientras que la edificación antigua es un riesgo indirecto y secundario.

**Qué sismos son destinos válidos**: se excluyen de antemano los sismos cuyo epicentro está demasiado lejos del nodo vial más cercano — un dron BVLOS evalúa daños siguiendo calles, no sobrevolando el océano. En vez de un número fijo, el umbral combina dos criterios reales (funciones SQL en `db/schema.sql`):

1. **`radio_relevancia_sismo_km(magnitud)`** — Wells & Coppersmith (1994), *"New Empirical Relationships among Magnitude, Rupture Length, Rupture Width, Rupture Area, and Surface Displacement"*, BSSA 84(4):974-1002. Longitud de ruptura subsuperficial `RLD(M) = 10^(-2.44 + 0.59·M)` km; se usa `3×RLD` como radio de la zona de impacto esperada (multiplicador propio), con piso de 2km para sismos pequeños.
2. **`radio_operacional_viento_km(racha_zona, umbral_usuario)`** — restricción física real, no arbitraria: el dron de referencia del proyecto es el **DJI Matrice 300 RTK** (plataforma enterprise/BVLOS usada en inspección y respuesta a desastres; ficha técnica en `enterprise.dji.com/matrice-300`). El radio base (15km) es su alcance máximo de transmisión OcuSync Enterprise (FCC/NCC); el umbral de viento por defecto (43.2 km/h = 12 m/s) es su resistencia al viento para despegue/aterrizaje, y el límite absoluto que acota cualquier valor que el operador ingrese (54 km/h = 15 m/s) es su resistencia al viento en vuelo. El radio operacional se reduce proporcionalmente cuando la racha de viento real de la zona del epicentro supera el umbral — mecanismo de derateo conceptualmente respaldado por Thibbotuwawa et al. 2020, *"UAV Mission Planning Resistant to Weather Uncertainty"*, Sensors 20(3):598.

**`distancia_max_valida_sismo_km`** = el **mínimo** de ambos: un sismo grande en una zona muy ventosa sigue sin ser alcanzable — el viento no desaparece porque el sismo sea importante. Ejemplo real: el M6.0 "33 km NW of Valparaíso" queda excluido (está a 32.8km de la red y el viento de esa zona limita el radio operacional a 8.3km, aunque su magnitud por sí sola permitiría hasta 37.8km), mientras que el M5.9 "13 km NNW de Viña del Mar" (a solo 5.5km de la red) sí es válido. De los 106 sismos reales, 26 cumplen el umbral combinado.

### 5.2 Asignación de pesos

Todos los pesos son multiplicadores sobre `length_m` (o sobre el costo ya penalizado por un paso anterior — los multiplicadores se componen):

```
cost_resiliente = length_m
                × 1.5   si cruza/corre paralelo a tendido eléctrico
                × 1.3   si está dentro de un polígono CONAF de ignición
                × 1000  si está dentro de un área protegida CONAF
                × 1000  si está a <500m de un foco FIRMS activo
                × (1 + 0.3 × factor_antiguedad_zona)   SOLO si el destino es un sismo
```

Los multiplicadores ×1000 son, en la práctica, una **exclusión**: `pgr_dijkstra` evitará esas aristas salvo que sean la única forma físicamente posible de llegar al destino. Los multiplicadores ×1.3/×1.5 son **penalizaciones graduales**: el algoritmo puede optar por cruzarlas si la alternativa es mucho más larga.

---

## 6. Restricciones del usuario

### 6.1 Metadata → restricción del usuario

A diferencia de los pesos fijos de la sección 5 (que modelan el riesgo físico de una amenaza), el **viento** se convirtió en una restricción **configurable por el operador**, simulando una preferencia de seguridad real: "no quiero que el dron cruce zonas donde la racha pronosticada supere X km/h".

Mecanismo: cada arista tiene precalculada la racha máxima de la estación Open-Meteo más cercana (`viento_racha_max_kmh`, de las 6 estaciones). En el momento de calcular la ruta resiliente, se agrega:

```sql
CASE WHEN viento_racha_max_kmh > {umbral_usuario} THEN (costo) * 1000.0 ELSE (costo) END
```

El umbral se recibe como parámetro `viento_max` en `GET /api/calcular_ruta` (ver `sitio_web/servidor.py`, función `cost_resiliente_sql`), y en el sitio se controla con el slider "Viento máx. permitido" — al soltarlo, se recalcula la ruta en vivo. Si todas las zonas del área exceden el umbral (puede pasar con pronósticos de viento fuerte), el multiplicador se aplica por igual a toda arista y la ruta no cambia respecto a no tener restricción — comportamiento correcto: una restricción uniforme no puede preferir una zona sobre otra.

**Importante**: esto nunca excluye literalmente una arista (el costo sigue siendo finito), por lo que siempre existe una ruta aunque toda la región esté ventosa — el sistema no se "rompe" ante condiciones extremas, sino que refleja honestamente que, bajo esas condiciones, ninguna zona es preferible a otra.

### 6.2 Restricción por defecto

Si el usuario no especifica un umbral (parámetro `viento_max` ausente), se usa **43.2 km/h (12 m/s)** — la resistencia al viento para despegue/aterrizaje del **DJI Matrice 300 RTK** según su ficha técnica oficial (`enterprise.dji.com/matrice-300`), el dron de referencia del proyecto. Se eligió ese límite (y no el de 15 m/s / 54 km/h en vuelo) como default porque es el más restrictivo: si el dron no puede despegar/aterrizar con seguridad, la misión no es viable aunque pudiera volar. Ese mismo límite en vuelo (54 km/h) acota el máximo valor que el operador puede pedir — no tiene sentido una restricción menos estricta que lo que el dron físicamente resiste. Constante `VIENTO_MAX_DEFAULT_KMH` en `sitio_web/servidor.py` y `sitio_web/generar_rutas_comparativas.py` (el catálogo precalculado siempre usa este valor).

---

## Carga de cada JSON a la BD

Cada extractor tiene un script `cargar_*.py` hermano en la misma carpeta:

| Extractor | Cargador | Tabla(s) destino |
|---|---|---|
| `extraer_infraestructura.py` | `cargar_infraestructura.py` | `infraestructura_nodos`, `infraestructura_aristas` |
| `extraer_carabineros.py` | `cargar_carabineros.py` | `nodo_carabineros` (vincula `nodo_vial_id` por KNN) |
| `extraer_viento.py` | `cargar_viento.py` | `metadata_viento` |
| `extraer_tendido_electrico.py` | `cargar_tendido_electrico.py` | `metadata_tendido_electrico` |
| `extraer_relieve.py` | `cargar_relieve.py` | `metadata_relieve` |
| `extraer_poblacion.py` | `cargar_poblacion.py` | `metadata_poblacion` |
| `extraer_permisos_edificacion.py` | `cargar_permisos_edificacion.py` | `metadata_permisos_edificacion` + calcula `indice_antiguedad` en `metadata_poblacion` (requiere que `cargar_poblacion.py` ya haya corrido) |
| `extraer_firms.py` | `cargar_firms.py` | `amenaza_firms` |
| `extraer_conaf.py` | `cargar_conaf.py` | `amenaza_conaf_ignicion`, `amenaza_conaf_areas_protegidas` |
| `extraer_sismos.py` | `cargar_sismos.py` | `amenaza_sismos` |

---

## 7. Sitio web (Leaflet)

Carpeta [`sitio_web/`](sitio_web/):

- [`index.html`](sitio_web/index.html) + [`app.js`](sitio_web/app.js): visor Leaflet con capas toggleables de infraestructura (cuarteles), metadata (viento, tendido eléctrico) y amenazas (sismos, CONAF ignición, áreas protegidas, FIRMS), selectores de origen/destino, slider de restricción de viento y tabla comparativa de métricas.
- [`servidor.py`](sitio_web/servidor.py): backend Python (`ThreadingHTTPServer` + pool de conexiones PostgreSQL) que sirve el sitio estático **y** una API dinámica (`/api/origenes`, `/api/sismos`, `/api/calcular_ruta`) que ejecuta `pgr_dijkstra` en vivo para cualquier par origen/destino y cualquier umbral de viento.
- [`exportar_capas_web.py`](sitio_web/exportar_capas_web.py): exporta cada tabla de la BD a `sitio_web/data/*.geojson` para las capas estáticas del mapa (punto 7).
- [`generar_rutas_comparativas.py`](sitio_web/generar_rutas_comparativas.py): calcula y cachea un catálogo de 144 pares (12 orígenes × 12 destinos) como respaldo si la API dinámica no estuviera disponible.
- [`generar_ruta_ejemplo.py`](sitio_web/generar_ruta_ejemplo.py): genera la ruta de ejemplo del punto 8.

---

## 7.1 / 7.2 Algoritmo de ruteo resiliente

**Algoritmo base**: `pgr_dijkstra` de pgRouting (implementación de Dijkstra, E.W. (1959). *"A note on two problems in connexion with graphs"*. Numerische Mathematik. 1: 269–271).

**Generación de k=2 rutas (para los puntos 8 y 9)**: en vez de usar `pgr_ksp` (algoritmo de Yen) de pgRouting, se implementó una heurística de **penalización iterativa de aristas**, inspirada en el objetivo de Suurballe (J.W. Suurballe (1974). *"Disjoint paths in a network"*. Networks. 4(2): 125–145) de encontrar un par de rutas de bajo costo y máxima disyunción, pero con una implementación mucho más simple sobre `pgr_dijkstra`:

1. Se calcula la ruta 1 con `pgr_dijkstra` sobre el costo base (`length_m` para Dijkstra puro, `cost_resiliente` — con las restricciones de la sección 6 — para la ruta resiliente).
2. Se penalizan ×1000 todas las aristas usadas por la ruta 1.
3. Se vuelve a correr `pgr_dijkstra` con ese costo modificado → ruta 2, que el algoritmo construye evitando casi por completo las aristas de la ruta 1 (solo las reutiliza si es la única forma físicamente posible de llegar al destino).

Esto **no** es Suurballe exacto (que usa una transformación de costos reducidos en dos pasadas y garantiza optimalidad formal del par), sino una aproximación heurística que captura el mismo objetivo — dos rutas razonablemente cortas y lo más disjuntas posible — con una sola función de pgRouting (`pgr_dijkstra`) invocada dos veces. Se eligió esta heurística sobre `pgr_ksp` porque el objetivo del proyecto no es solo "las k rutas más cortas" (que con Yen suelen compartir la mayoría de sus aristas en una red con pocas alternativas, como ocurre en varios tramos de los cerros de Valparaíso), sino rutas **diversificadas** que sirvan como respaldo ante el corte de una de ellas — que es precisamente lo que mide la métrica de resiliencia (sección 10).

La ruta "resiliente" (punto 9) usa exactamente el mismo mecanismo de dos pasadas, pero sobre `cost_resiliente` en vez de `length_m` — por eso ambas rutas (Dijkstra y resiliente) son directamente comparables en la sección 10/11.

---

## 8. Ruta de ejemplo (peor caso, pgr_dijkstra puro)

[`sitio_web/generar_ruta_ejemplo.py`](sitio_web/generar_ruta_ejemplo.py) calcula, con `pgr_dijkstra` y **únicamente `length_m` como costo** (sin metadata ni amenazas — eso es Fase 3), una ruta desde la 8va Comisaría de Valparaíso (Florida) hasta el nodo vial más cercano al epicentro de un sismo real (M4.6, 5km N de Viña del Mar). Resultado: ~97 tramos, ~13.8 km. Exporta `ruta_ejemplo.geojson` con metadata explicando el origen, destino y que es el "peor caso" de la Fase 2.

---

## 9. k=2 rutas Dijkstra y k=2 rutas resilientes

Implementado en `sitio_web/generar_rutas_comparativas.py` (catálogo) y `sitio_web/servidor.py` (API dinámica), usando el mecanismo de penalización iterativa descrito en la sección 7.2:

- **Dijkstra k=2** (punto 8 de la rúbrica de puntaje): `dijkstra_1` y `dijkstra_2`, ambas usando `length_m AS cost` en `pgr_dijkstra` — basadas únicamente en distancia, sin metadata ni restricciones.
- **Resiliente k=2** (punto 9): `resiliente_1` y `resiliente_2`, usando el costo descrito en las secciones 5 y 6 (amenazas + restricción de viento + antigüedad condicional si el destino es un sismo).

---

## 10. Métrica de resiliencia

Función `calcular_metricas()` en `sitio_web/servidor.py` (y su equivalente en `generar_rutas_comparativas.py`), aplicada por separado al par Dijkstra y al par Resiliente:

```
disyuncion   = 1 - |aristas(ruta1) ∩ aristas(ruta2)| / |aristas(ruta1) ∪ aristas(ruta2)|
riesgo       = aristas en (ruta1 ∪ ruta2) donde cost_resiliente > length_m  (es decir, penalizadas por alguna amenaza/metadata)
seguridad    = max(0.2, 1 - riesgo / |ruta1 ∪ ruta2|)
resiliencia  = disyuncion × seguridad × 100
```

**Justificación**: una dupla de rutas es más resiliente cuanto (a) más independientes son entre sí (si una se corta por una amenaza imprevista, la otra sigue siendo viable — componente de *disyunción*), y (b) menos aristas de ambas pasan por zonas penalizadas por amenazas conocidas (componente de *seguridad*). El piso de 0.2 en el factor de seguridad evita que la métrica colapse a cero cuando, por geografía, es imposible evitar completamente una zona de riesgo. Se calcula tanto para el par Dijkstra como para el par Resiliente, lo que permite compararlos directamente (sección 11.2).

---

## 11. Plataforma: dupla origen/destino y diferencial de resiliencia

### 11.1

El sitio (`sitio_web/index.html` + `app.js` + `servidor.py`) permite elegir cualquier cuartel de Carabineros como origen y cualquier sismo válido (según el umbral combinado magnitud+viento, sección 5) como destino — por selector desplegable o haciendo clic directo en el mapa — además de ajustar la restricción de viento (sección 6), acotada entre 15 y 54 km/h (el límite físico en vuelo del DJI Matrice 300 RTK). Si no se ajusta, se usa el default de 43.2 km/h. El resultado se calcula en vivo contra PostgreSQL/pgRouting (`GET /api/calcular_ruta`) y se dibuja sobre el mapa: 2 rutas Dijkstra (rojo/naranjo) y 2 rutas resilientes (azul/verde).

### 11.2

La tabla de métricas del panel lateral muestra, lado a lado, Dijkstra vs. Resiliente para: distancia promedio, disyunción de caminos, aristas en riesgo y resiliencia global — y un badge destacado con el **diferencial de resiliencia** (`resiliencia_resiliente − resiliencia_dijkstra`), que cuantifica cuánto mejora (o no) la estrategia resiliente frente al Dijkstra puro para ese par específico.

---

## 12. Despliegue de flota ante un sismo

Cuando el objetivo es un sismo, además de la ruta punto a punto, el botón *Desplegar todos los drones* (`/api/despliegue_sismo`, independiente de la ruta origen/destino y sin punto de partida; sismos en `/api/despliegue_sismos`; lógica en `sitio_web/despliegue.py`) lanza todos los drones de todos los cuarteles a la vez para cubrir la mayor parte posible de la zona afectada, priorizando según los datos de la plataforma.

- **Zona:** manzanas censales pobladas (`metadata_poblacion`) a ≤ radio del epicentro. Radio según magnitud: M<4 → 5 km, <5 → 10 km, <6 → 20 km, ≥6 → 50 km (supuesto).
- **Prioridad por manzana:** `total_pers × (1 + indice_antiguedad) / (1 + dist_epicentro / radio)`.
- **Flota:** los cuarteles que ofrece `/api/origenes` × `drones_por_cuartel`. Cada dron tiene una autonomía (default 30 km, con 10 % de reserva) y puede terminar en cualquier punto de carga.
- **Asignación (greedy):** en cada ronda, cada dron elige la manzana pendiente con mayor `prioridad / costo resiliente` que aún le permita volver a cargar. Las rutas usan el mismo `cost_resiliente_sql` (viento, tendido, SNASPE, antigüedad). Toda manzana a ≤ `alcance_camara_m` (150 m) de un tramo recorrido se considera cubierta, lo que reparte a los drones. Para acotar la matriz de costos solo las 120 manzanas de mayor prioridad son candidatas; la cobertura se mide sobre toda la zona.
- **Métricas:** % de población y de prioridad cubiertas, % de edificación antigua cubierta, km totales y minutos hasta inspeccionar la manzana más prioritaria (a `velocidad_kmh` = 40, supuesto).
- Los supuestos (radio, autonomía, cámara, velocidad) no provienen de ninguna fuente de datos. `python3 sitio_web/test_despliegue.py` prueba la lógica de asignación sin BD.

## Cómo compilar y ejecutar

**Requisitos**: Docker + Docker Compose. No se requiere Python, PostgreSQL ni ninguna otra dependencia instalada en el host — todo corre en contenedores.

### Opción 1 — automatizado (punto 9 de la rúbrica)

Desde la raíz del repositorio:

```bash
python3 main.py
```

Esto ejecuta [`main.py`](../main.py) (raíz del repo), que delega en [`tarea2/main.py`](main.py), el cual:

1. Levanta `docker compose up -d --build db sitio_web` (construye las imágenes de PostgreSQL+PostGIS+pgRouting y del sitio web, espera a que la BD esté *healthy*).
2. Ejecuta `docker compose run --rm etl python3 main.py --sin-docker`, que dentro del contenedor `etl`:
   - Corre los 10 extractores (`infraestructura/`, `metadata/`, `amenazas/`) → `*.json`.
   - Corre los 10 cargadores → tablas de PostgreSQL.
   - Corre los 3 scripts de `sitio_web/` que calculan rutas y exportan capas.
3. Al terminar, el sitio queda disponible en **http://localhost:8080**.

Una corrida completa desde cero (sin caché) toma entre 5 y 10 minutos, dependiendo de la latencia de las APIs externas (Overpass y Open Topo Data son las más variables).

### Opción 2 — manual / depuración

```bash
cd tarea2
docker compose up -d --build db sitio_web   # levanta BD + sitio
docker compose run --rm etl python3 main.py --sin-docker   # corre el pipeline ETL dentro del contenedor
```

Para reiniciar todo desde cero (borra los datos de la BD):

```bash
docker compose down -v
```

Para correr un solo script dentro del contenedor (por ejemplo, para depurar un extractor):

```bash
docker compose run --rm -w /app/metadata etl python3 extraer_viento.py
```

### Variables de entorno relevantes

- `FIRMS_MAP_KEY`: API key de NASA FIRMS (ya provista en `docker-compose.yml` para que el pipeline funcione out-of-the-box; si no está disponible, `extraer_firms.py` cae a un CSV cacheado de Tarea 1 y luego a un punto de ejemplo).
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`: configuración de conexión a PostgreSQL (ya seteadas para el entorno Docker).

---

## Estructura de carpetas

```
tarea2/
├── main.py                        # Punto 9: orquestador del pipeline completo
├── docker-compose.yml             # Servicios: db, etl, sitio_web
├── etl.Dockerfile                 # Imagen compartida por etl y sitio_web
├── db/
│   ├── Dockerfile                 # FROM pgrouting/pgrouting:latest
│   ├── schema.sql                 # Punto 0: esquema completo
│   ├── init_extensions.sql
│   ├── generar_diagrama_bd.py     # Genera diagrama_bd.png
│   └── diagrama_bd.png            # Punto 0: diagrama visual
├── infraestructura/                # Punto 1
│   ├── extraer_infraestructura.py / cargar_infraestructura.py
│   ├── extraer_carabineros.py / cargar_carabineros.py
│   └── *.schema.md, *.json
├── metadata/                       # Punto 2
│   ├── extraer_{viento,tendido_electrico,relieve,poblacion,permisos_edificacion}.py
│   ├── cargar_{...}.py
│   └── *.schema.md, *.json
├── amenazas/                       # Punto 3
│   ├── extraer_{firms,conaf,sismos}.py
│   ├── cargar_{firms,conaf,sismos}.py
│   └── *.schema.md, *.json
└── sitio_web/                      # Puntos 7, 8, 9, 10, 11
    ├── index.html / app.js / servidor.py
    ├── generar_rutas_comparativas.py
    ├── generar_ruta_ejemplo.py
    ├── exportar_capas_web.py
    └── data/ (GeoJSON exportados)
```

Ver también [`../codigo/RESULTADOS.md`](../codigo/RESULTADOS.md) para el detalle de verificación de cada fuente de datos (Tarea 1) y las decisiones de diseño que anteceden a esta Tarea 2.
