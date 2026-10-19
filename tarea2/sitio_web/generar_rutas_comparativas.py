"""Puntos 8, 9, 10, 11 de la rubrica:
Genera catalogo completo de rutas comparativas (Dijkstra k=2 vs Resiliente k=2)
para todas las combinaciones clave de Cuarteles de Carabineros (origen)
y Emergencias (Sismos / Incendios FIRMS) en el Gran Valparaiso.
Exporta rutas_catalogo.json y rutas_comparativas.geojson para el visor web.
"""
import json
import os
import psycopg2

AQUI = os.path.dirname(os.path.abspath(__file__))
CONN = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "ruteo_drones"),
    user=os.environ.get("DB_USER", "ruteo"),
    password=os.environ.get("DB_PASSWORD", "ruteo"),
)

conn = psycopg2.connect(**CONN)
cur = conn.cursor()

# 1. Asegurar columna cost_resiliente e indices espaciales
print("Actualizando ponderaciones de amenazas y metadata en infraestructura vial...")
cur.execute("""
ALTER TABLE infraestructura_aristas ADD COLUMN IF NOT EXISTS cost_resiliente double precision;
UPDATE infraestructura_aristas SET cost_resiliente = length_m;

CREATE INDEX IF NOT EXISTS idx_tendido_geom ON metadata_tendido_electrico USING gist(geom);
CREATE INDEX IF NOT EXISTS idx_ignicion_geom ON amenaza_conaf_ignicion USING gist(geom);
CREATE INDEX IF NOT EXISTS idx_asp_geom ON amenaza_conaf_areas_protegidas USING gist(geom);
CREATE INDEX IF NOT EXISTS idx_firms_geom ON amenaza_firms USING gist(geom);

UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * 1.5
WHERE id IN (
    SELECT a.id FROM infraestructura_aristas a
    JOIN metadata_tendido_electrico t ON a.geom && ST_Expand(t.geom, 0.0002) AND ST_DWithin(a.geom, t.geom, 0.0002)
);

UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * 1.3
WHERE id IN (
    SELECT a.id FROM infraestructura_aristas a
    JOIN amenaza_conaf_ignicion i ON a.geom && i.geom AND ST_Intersects(a.geom, i.geom)
);

UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * 1000.0
WHERE id IN (
    SELECT a.id FROM infraestructura_aristas a
    JOIN amenaza_conaf_areas_protegidas asp ON a.geom && asp.geom AND ST_Intersects(a.geom, asp.geom)
);

UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * 1000.0
WHERE id IN (
    SELECT a.id FROM infraestructura_aristas a
    JOIN amenaza_firms f ON f.dentro_chile AND a.geom && ST_Expand(f.geom, 0.005) AND ST_DWithin(a.geom, f.geom, 0.005)
);

-- factor_antiguedad_zona: proxy de antiguedad de la edificacion por zona
-- (metadata_poblacion.indice_antiguedad, calculado a partir de permisos de
-- edificacion 2010-2018). NO se aplica de forma fija a cost_resiliente como
-- tendido/CONAF/FIRMS: es un riesgo condicional, solo relevante cuando la
-- mision es evaluar danos tras UN SISMO (riesgo de escombros/obstruccion de
-- calles por edificacion antigua), no para incendios ni en general. Se
-- aplica mas abajo, en el calculo de rutas, solo si dest["tipo"] == "sismo".
ALTER TABLE infraestructura_aristas ADD COLUMN IF NOT EXISTS factor_antiguedad_zona double precision DEFAULT 0;
UPDATE infraestructura_aristas SET factor_antiguedad_zona = 0;

CREATE INDEX IF NOT EXISTS idx_poblacion_geom ON metadata_poblacion USING gist(geom);

UPDATE infraestructura_aristas a
SET factor_antiguedad_zona = sub.max_indice
FROM (
    SELECT a2.id, max(m.indice_antiguedad) AS max_indice
    FROM infraestructura_aristas a2
    JOIN metadata_poblacion m ON a2.geom && m.geom AND ST_Intersects(a2.geom, m.geom)
    WHERE m.indice_antiguedad IS NOT NULL
    GROUP BY a2.id
) sub
WHERE a.id = sub.id;

-- viento_racha_kmh: racha PROMEDIO pronosticada (racha_media_kmh) de la
-- estacion Open-Meteo mas cercana (de las 6 del area). Es la base de la
-- RESTRICCION de usuario (punto 6 de la rubrica, distinta de una
-- ponderacion fija): el operador define un umbral maximo de viento
-- operacional (simulando su preferencia de seguridad); cualquier arista
-- cuya zona supere ese umbral se penaliza fuertemente (x1000, igual
-- convencion que CONAF/areas protegidas) en la ruta resiliente.
-- Se usa racha_media_kmh (promedio del periodo pronosticado) y NO
-- racha_max_kmh (el peor instante puntual de ~7 dias de pronostico):
-- en el Gran Valparaiso real, racha_max_kmh supera SIEMPRE el techo
-- fisico del DJI M300 RTK (54 km/h) en las 6 estaciones -> si se usara
-- ese valor, el 100% de las aristas quedarian penalizadas por igual y la
-- restriccion de viento dejaria de influir en la eleccion de ruta (un
-- no-op). racha_media_kmh (17.7-35.8 km/h en los datos reales) si varia
-- de forma significativa entre zonas y mayormente queda bajo el umbral
-- default, por lo que discrimina de verdad entre rutas. Se aplica en
-- tiempo de consulta, no aqui, porque el umbral es un parametro variable
-- (ver costo_resiliente_sql).
ALTER TABLE infraestructura_aristas ADD COLUMN IF NOT EXISTS viento_racha_kmh double precision;
ALTER TABLE infraestructura_aristas DROP COLUMN IF EXISTS viento_racha_max_kmh;
CREATE INDEX IF NOT EXISTS idx_viento_geom ON metadata_viento USING gist(geom);

-- Racha interpolada (IDW entre las 6 estaciones, ver
-- viento_racha_interpolada_kmh en db/schema.sql) evaluada en el punto medio
-- de cada arista, en vez de la de la estacion mas cercana: asi el viento
-- varia de forma gradual por el mapa y el slider de viento cambia las
-- rutas de forma progresiva, no solo al cruzar el valor de una estacion.
UPDATE infraestructura_aristas a
SET viento_racha_kmh = viento_racha_interpolada_kmh(ST_LineInterpolatePoint(a.geom, 0.5));
""")
conn.commit()

PESO_ANTIGUEDAD = 0.3  # penalizacion leve y condicional (ver justificacion en RESULTADOS.md);
# nunca domina sobre tendido electrico (x1.5) ni CONAF (x1000), que son riesgos fisicos directos.

VIENTO_MAX_DEFAULT_KMH = 43.2  # restriccion por defecto (punto 6.2): 12 m/s, el
# limite de resistencia al viento para despegue/aterrizaje del DJI Matrice 300
# RTK (dron de referencia del proyecto; enterprise.dji.com/matrice-300). Se usa
# cuando el operador no ingresa un valor propio. El catalogo precalculado
# siempre usa este valor; el sitio dinamico (servidor.py) permite cambiarlo,
# acotado al limite fisico en vuelo del mismo dron (15 m/s = 54 km/h).


def cost_resiliente_sql(dest, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH):
    """Expresion SQL de costo para la ruta 'resiliente':
    - agrega el riesgo de escombros por edificacion antigua SOLO si el
      destino es un sismo (ponderacion condicional, punto 5).
    - aplica la restriccion de viento operacional del usuario (o el
      default de 43.2 km/h): si la racha PROMEDIO de la zona
      (viento_racha_kmh, ver seccion anterior) supera el umbral, la
      arista se penaliza x1000 (punto 6). Nunca excluye literalmente la
      arista (costo finito), por lo que siempre existe una ruta aunque
      toda la zona este ventosa.
    """
    base = "cost_resiliente"
    if dest.get("tipo") == "sismo":
        base = f"({base} * (1 + {PESO_ANTIGUEDAD} * factor_antiguedad_zona))"
    return (
        f"(CASE WHEN viento_racha_kmh > {float(viento_max_kmh)} "
        f"THEN ({base}) * 1000.0 ELSE {base} END)"
    )

# 2. Obtener lista de Origenes Clave (Cuarteles / Comisarías de todas las comunas)
cur.execute("""
SELECT id, nombre, comuna, es_comisaria, ST_X(geom) lon, ST_Y(geom) lat, nodo_vial_id
FROM nodo_carabineros
WHERE es_comisaria OR nombre IN ('SUBCOM. CONCON', 'RETEN PLACILLA PENUELAS', 'RETEN LAGUNA VERDE')
ORDER BY comuna, nombre;
""")
filas_cuarteles = cur.fetchall()

ORIGENES = []
ALIAS_CUARTEL = {
    17: "8va_florida",
    25: "1ra_sur",
    23: "2da_central",
    21: "3ra_norte",
    20: "4ta_puerto",
    24: "1ra_vina",
    19: "5ta_vina",
    15: "subcom_concon",
    22: "2da_quilpue",
    18: "6ta_villa_alemana",
    2: "reten_placilla",
    28: "reten_laguna_verde",
}

for r in filas_cuarteles:
    c_id, nom, com, es_com, lon, lat, nodo_vial = r
    key = ALIAS_CUARTEL.get(c_id, f"cuartel_{c_id}")
    ORIGENES.append({
        "key": key,
        "id": c_id,
        "nombre": nom,
        "comuna": com,
        "es_comisaria": es_com,
        "lon": round(float(lon), 5),
        "lat": round(float(lat), 5),
        "nodo_vial": nodo_vial,
    })

# 3. Obtener lista de Destinos Clave (Sismos USGS + Focos NASA FIRMS)
# Umbral combinado (reemplaza el umbral fijo de 2000m): un sismo es destino
# valido si el nodo vial mas cercano esta dentro del MINIMO de (a) el radio
# de relevancia segun su magnitud (Wells & Coppersmith 1994) y (b) el radio
# operacional segun el viento real de la zona (restriccion fisica de alcance
# del dron, calculado con racha_media_kmh - ver justificacion en la seccion
# anterior). Funciones SQL en db/schema.sql.
cur.execute("""
SELECT s.id, s.usgs_id, s.magnitud, s.lugar, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat
FROM amenaza_sismos s
CROSS JOIN LATERAL (
    SELECT ST_Distance(s.geom::geography, n.geom::geography) AS d
    FROM infraestructura_nodos n ORDER BY n.geom <-> s.geom LIMIT 1
) dist_red
CROSS JOIN LATERAL (
    SELECT viento_racha_interpolada_kmh(s.geom) AS racha_media_kmh
) v
WHERE (dist_red.d / 1000.0) <= distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)
ORDER BY s.magnitud DESC, dist_red.d ASC
LIMIT 10;
""")
filas_sismos = cur.fetchall()

DESTINOS = []
for r in filas_sismos:
    s_id, usgs, mag, lug, lon, lat = r
    cur.execute("SELECT id FROM infraestructura_nodos ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1", (lon, lat))
    dest_nodo = cur.fetchone()[0]
    key = f"sismo_{s_id}"
    DESTINOS.append({
        "key": key,
        "id": s_id,
        "tipo": "sismo",
        "descripcion": f"Sismo M{mag} ({lug})",
        "magnitud": float(mag),
        "lugar": lug,
        "lon": round(float(lon), 5),
        "lat": round(float(lat), 5),
        "nodo_vial": dest_nodo,
    })

cur.execute("""
SELECT id, fecha, confianza, ST_X(geom) lon, ST_Y(geom) lat
FROM amenaza_firms
WHERE dentro_chile;
""")
filas_firms = cur.fetchall()
for r in filas_firms:
    f_id, fec, conf, lon, lat = r
    cur.execute("SELECT id FROM infraestructura_nodos ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1", (lon, lat))
    dest_nodo = cur.fetchone()[0]
    key = f"firms_{f_id}"
    sector_str = "Valparaíso Cerros / Placilla" if f_id == 1 else "Sector Forestal Norte"
    DESTINOS.append({
        "key": key,
        "id": f_id,
        "tipo": "incendio",
        "descripcion": f"Foco Incendio Activo FIRMS #{f_id} ({sector_str})",
        "fecha": str(fec),
        "lon": round(float(lon), 5),
        "lat": round(float(lat), 5),
        "nodo_vial": dest_nodo,
    })

print(f"Generando catálogo para {len(ORIGENES)} orígenes x {len(DESTINOS)} destinos = {len(ORIGENES) * len(DESTINOS)} pares...")


def calcular_metricas(edges1, edges2, dist1, dist2):
    s1, s2 = set(edges1), set(edges2)
    inter = len(s1.intersection(s2))
    union = len(s1.union(s2)) if len(s1.union(s2)) > 0 else 1
    disyuncion = (1.0 - (inter / union))
    solapamiento_pct = (inter / union) * 100.0

    cur.execute(
        "SELECT count(*) FROM infraestructura_aristas WHERE id = ANY(%s) AND cost_resiliente > length_m",
        (list(s1.union(s2)),),
    )
    aristas_con_riesgo = cur.fetchone()[0]
    factor_seguridad = max(0.2, 1.0 - (aristas_con_riesgo / max(len(s1.union(s2)), 1)))
    resiliencia_score = round(disyuncion * factor_seguridad * 100.0, 1)

    return {
        "disyuncion_pct": round(disyuncion * 100.0, 1),
        "solapamiento_pct": round(solapamiento_pct, 1),
        "factor_seguridad": round(factor_seguridad, 2),
        "aristas_con_riesgo": aristas_con_riesgo,
        "resiliencia_score": resiliencia_score,
        "distancia_promedio_km": round((dist1 + dist2) / 2000.0, 2),
    }


def obtener_geometria_y_distancia(edge_ids):
    if not edge_ids:
        return None, 0.0
    cur.execute(
        "SELECT ST_AsGeoJSON(ST_Collect(geom))::json, coalesce(sum(length_m), 0) FROM infraestructura_aristas WHERE id = ANY(%s)",
        (edge_ids,),
    )
    geom_json, dist_m = cur.fetchone()
    return geom_json, float(dist_m or 0.0)


CATALOGO = {
    "origenes": ORIGENES,
    "destinos": DESTINOS,
    "rutas": {},
}

pares_procesados = 0

for orig in ORIGENES:
    for dest in DESTINOS:
        o_nodo = orig["nodo_vial"]
        d_nodo = dest["nodo_vial"]
        if not o_nodo or not d_nodo or o_nodo == d_nodo:
            continue

        # 1. Dijkstra 1
        cur.execute(
            "SELECT edge FROM pgr_dijkstra('SELECT id, source, target, length_m AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
            (o_nodo, d_nodo),
        )
        e_d1 = [r[0] for r in cur.fetchall()]

        # 2. Dijkstra 2
        cur.execute(
            """
            SELECT edge FROM pgr_dijkstra(
                'SELECT id, source, target,
                        CASE WHEN id = ANY(%s) THEN length_m * 1000.0 ELSE length_m END AS cost
                 FROM infraestructura_aristas',
                %s, %s
            ) WHERE edge != -1
            """,
            (e_d1, o_nodo, d_nodo),
        )
        e_d2 = [r[0] for r in cur.fetchall()]

        # 3. Resiliente 1 (agrega riesgo de edificacion antigua si el destino es un sismo)
        costo_base = cost_resiliente_sql(dest)
        cur.execute(
            f"SELECT edge FROM pgr_dijkstra('SELECT id, source, target, {costo_base} AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
            (o_nodo, d_nodo),
        )
        e_r1 = [r[0] for r in cur.fetchall()]

        # 4. Resiliente 2 (Bypass disjunto)
        cur.execute(
            f"""
            SELECT edge FROM pgr_dijkstra(
                'SELECT id, source, target,
                        CASE WHEN id = ANY(%s) THEN ({costo_base}) * 1000.0 ELSE {costo_base} END AS cost
                 FROM infraestructura_aristas',
                %s, %s
            ) WHERE edge != -1
            """,
            (e_r1, o_nodo, d_nodo),
        )
        e_r2 = [r[0] for r in cur.fetchall()]

        geom_d1, dist_d1 = obtener_geometria_y_distancia(e_d1)
        geom_d2, dist_d2 = obtener_geometria_y_distancia(e_d2 if e_d2 else e_d1)
        geom_r1, dist_r1 = obtener_geometria_y_distancia(e_r1)
        geom_r2, dist_r2 = obtener_geometria_y_distancia(e_r2 if e_r2 else e_r1)

        m_dijkstra = calcular_metricas(e_d1, e_d2 if e_d2 else e_d1, dist_d1, dist_d2)
        m_resiliente = calcular_metricas(e_r1, e_r2 if e_r2 else e_r1, dist_r1, dist_r2)
        diff_resil = round(m_resiliente["resiliencia_score"] - m_dijkstra["resiliencia_score"], 1)

        par_key = f"{orig['key']}_{dest['key']}"
        CATALOGO["rutas"][par_key] = {
            "origen": orig,
            "destino": dest,
            "dijkstra_1": {"distancia_m": round(dist_d1, 1), "tramos": len(e_d1), "geom": geom_d1},
            "dijkstra_2": {"distancia_m": round(dist_d2, 1), "tramos": len(e_d2), "geom": geom_d2},
            "resiliente_1": {"distancia_m": round(dist_r1, 1), "tramos": len(e_r1), "geom": geom_r1},
            "resiliente_2": {"distancia_m": round(dist_r2, 1), "tramos": len(e_r2), "geom": geom_r2},
            "metricas": {
                "dijkstra": m_dijkstra,
                "resiliente": m_resiliente,
                "diferencial_resiliencia": diff_resil,
            },
        }
        pares_procesados += 1

print(f"OK: {pares_procesados} pares de rutas comparativas calculados.")

# Guardar catálogo completo
path_catalogo = os.path.join(AQUI, "rutas_catalogo.json")
with open(path_catalogo, "w", encoding="utf-8") as f:
    json.dump(CATALOGO, f, ensure_ascii=False)
print(f"Catálogo guardado en {path_catalogo} ({round(os.path.getsize(path_catalogo)/1024, 1)} KB)")

# Guardar rutas_comparativas.geojson con el par por defecto (8va Comisaría -> Sismo M4.6 Viña del Mar Norte)
par_default_key = "8va_florida_sismo_15"
if par_default_key not in CATALOGO["rutas"]:
    par_default_key = list(CATALOGO["rutas"].keys())[0]

par_def = CATALOGO["rutas"][par_default_key]
features_geojson = [
    {"type": "Feature", "geometry": par_def["dijkstra_1"]["geom"], "properties": {"ruta_tipo": "dijkstra_1", "etiqueta": "Dijkstra Ruta 1 (Distancia Pura)", "distancia_m": par_def["dijkstra_1"]["distancia_m"]}},
    {"type": "Feature", "geometry": par_def["dijkstra_2"]["geom"], "properties": {"ruta_tipo": "dijkstra_2", "etiqueta": "Dijkstra Ruta 2 (Bypass Distancia)", "distancia_m": par_def["dijkstra_2"]["distancia_m"]}},
    {"type": "Feature", "geometry": par_def["resiliente_1"]["geom"], "properties": {"ruta_tipo": "resiliente_1", "etiqueta": "Resiliente Ruta 1 (Corredor Seguro)", "distancia_m": par_def["resiliente_1"]["distancia_m"]}},
    {"type": "Feature", "geometry": par_def["resiliente_2"]["geom"], "properties": {"ruta_tipo": "resiliente_2", "etiqueta": "Resiliente Ruta 2 (Bypass Seguro)", "distancia_m": par_def["resiliente_2"]["distancia_m"]}},
]

geojson_default = {
    "type": "FeatureCollection",
    "metadata": {
        "origen": par_def["origen"],
        "destino": par_def["destino"],
        "metricas": par_def["metricas"],
    },
    "features": features_geojson,
}

path_comparativo = os.path.join(AQUI, "rutas_comparativas.geojson")
with open(path_comparativo, "w", encoding="utf-8") as f:
    json.dump(geojson_default, f, ensure_ascii=False, indent=2)
print(f"OK: GeoJSON por defecto guardado en {path_comparativo}")

conn.close()
