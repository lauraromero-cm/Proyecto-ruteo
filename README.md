# Ruteo resiliente de drones BVLOS sobre corredores viales

Evaluación rápida de daños post-emergencia en el Gran Valparaíso.

## Temática

El proyecto aborda el ruteo resiliente de drones **BVLOS** (*Beyond Visual Line
of Sight*, más allá del alcance visual del operador) para la evaluación rápida
de daños en el Gran Valparaíso tras una emergencia (incendios forestales,
sismos u otros eventos de falla). Los drones siguen el trazado de la red vial
—modelada como un grafo geolocalizado de nodos e intersecciones— en vez de
volar en línea recta, lo que reduce el riesgo sobre zonas edificadas, permite
un aterrizaje de emergencia seguro sobre la calzada y mantiene el enlace de
comando y control.

Sobre esa infraestructura vial se incorporan capas de metadata (viento, altura
de edificación, población por manzana censal) y de amenazas geolocalizadas
(focos de incendio activo, probabilidad de ignición, sismicidad) que permiten
penalizar o eliminar dinámicamente aristas del grafo según el riesgo real del
entorno, recalculando rutas alternativas (k-shortest-paths sobre
PostGIS/pgRouting) hacia la zona afectada.

## Fuentes de datos verificadas

| | Metadata #1 | Metadata #2 | Metadata #3 | Amenaza #1 | Amenaza #2 | Amenaza #3 |
|---|---|---|---|---|---|---|
| **Fuente** | Open-Meteo | Google Open Buildings 2.5D Temporal (Google Research) | INE Censo 2017 (vía Observatorio de Ciudades UC) | NASA FIRMS | CONAF | USGS / CSN |
| **Qué mide** | Viento y rachas | Altura de edificación/estructuras | Población por manzana censal | Focos de incendio activo | Probabilidad de ignición (5 días) | Sismicidad |
| **Resultado verificado** | Rachas hasta 45 km/h | 85.268 estructuras con altura real, 100% de cobertura (vs. 24,1% con OSM/Overpass) | 10.750 manzanas, 1.005.013 habitantes | 7 focos reales en Chile | Polígonos de riesgo + áreas protegidas | 105 sismos M≥2 desde 2015 |

Detalle completo de cada prueba en [`codigo/RESULTADOS.md`](codigo/RESULTADOS.md).

## Estructura del repositorio

- **`codigo/`** — scripts de prueba de cada fuente (`test_*.py`), scripts de
  construcción del grafo vial y de generación de mapas/gráficos.
- **`codigo/evidencia/datos/`** — datos crudos descargados (JSON, GeoJSON, CSV).
- **`codigo/evidencia/imagenes/`** — mapas y gráficos generados a partir de los datos.
- **`codigo/RESULTADOS.md`** — bitácora de resultados de factibilidad de cada fuente.

## Stack

Python (`geopandas`, `osmnx`, `shapely`, `requests`) + PostgreSQL/PostGIS/pgRouting.
