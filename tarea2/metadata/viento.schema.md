# Estructura de `viento.json`

Metadata operacional de meteorología para los puntos clave del Gran Valparaíso (viento, rachas y dirección).

**Rol operacional:** Encarece el costo de las aristas del grafo vial según la velocidad y dirección del viento, y limita qué sismos son alcanzables como destino (ver `amenazas/sismos.schema.md`). El umbral por defecto es **43.2 km/h (12 m/s)**, la resistencia al viento para despegue/aterrizaje del **DJI Matrice 300 RTK** (dron de referencia del proyecto, plataforma enterprise/BVLOS para inspección y respuesta a desastres — ficha técnica oficial en `enterprise.dji.com/matrice-300`); el límite físico absoluto en vuelo del mismo dron es 54 km/h (15 m/s), y el operador no puede fijar una restricción menos estricta que ese límite.

**Por qué se usa `racha_media_kmh` y no `racha_max_kmh` como variable operacional:** `racha_media_kmh` es el promedio de las ráfagas pronosticadas por Open-Meteo para todo el período horario consultado (~7 días); `racha_max_kmh` es el pico puntual más alto de esa misma ventana completa (un solo instante en toda la semana, no "el viento actual"). En los datos reales del Gran Valparaíso, `racha_max_kmh` supera los 54 km/h en las 6 estaciones sin excepción — por encima incluso del techo físico absoluto del dron — lo que convertiría la restricción de viento en una penalización uniforme sobre el 100% de las aristas del grafo (un no-op: no afectaría en nada la elección de ruta, porque multiplicar todos los costos por igual no cambia cuál es el camino más corto). `racha_media_kmh` (17.7–35.8 km/h en los datos reales) sí varía de forma significativa entre zonas y mayormente queda bajo el umbral por defecto, por lo que es la que se usa como "viento actual/operacional de la zona" en `cost_resiliente_sql()` (`sitio_web/servidor.py`, `sitio_web/generar_rutas_comparativas.py`) y en `distancia_max_valida_sismo_km()` (`db/schema.sql`). `racha_max_kmh` se conserva en el dato como referencia informativa de peor caso, pero no participa en ningún cálculo de costo o umbral.

**Interpolación espacial:** el viento de cada arista (y de cada epicentro de sismo) no es el de la estación más cercana, sino un promedio de las 6 estaciones ponderado por 1/distancia² (IDW, Shepard 1968), calculado por la función SQL `viento_racha_interpolada_kmh` (`db/schema.sql`). Es una estimación a partir de solo 6 puntos, no una medición en cada calle.

**Fuente:** Open-Meteo API (`api.open-meteo.com/v1/forecast`), REST/JSON público sin API key.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | entero | Identificador del punto de monitoreo |
| `comuna` | texto | Comuna (Valparaíso, Viña del Mar, Concón, Quilpué, Villa Alemana) |
| `sector` | texto | Detalle del sector geográfico (ej. `Plan / costa`, `Cerros / Placilla`) |
| `elevacion_m` | float | Altitud del punto sobre el nivel del mar (m) |
| `viento_medio_kmh` | float | Velocidad media del viento a 10m de altura (km/h) |
| `racha_media_kmh` | float | Velocidad media de ráfagas a 10m (km/h) |
| `racha_max_kmh` | float | Racha máxima registrada en el pronóstico (km/h) |
| `direccion_deg` | float | Dirección promedio del viento en grados (0° a 360°, de donde sopla) |
| `lon` | float | Longitud del punto (EPSG:4326) |
| `lat` | float | Latitud del punto (EPSG:4326) |

Ejemplo:
```json
{
  "id": 1,
  "comuna": "Valparaíso",
  "sector": "Plan / costa",
  "elevacion_m": 15.0,
  "viento_medio_kmh": 18.4,
  "racha_media_kmh": 28.2,
  "racha_max_kmh": 41.5,
  "direccion_deg": 210.5,
  "lon": -71.6188,
  "lat": -33.0488
}
```
