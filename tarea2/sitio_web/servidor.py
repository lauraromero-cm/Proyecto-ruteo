#!/usr/bin/env python3
"""
Servidor Web y API Dinámica de Ruteo Resiliente BVLOS
Calcula rutas Dijkstra y Resilientes en tiempo real usando PostGIS y pgRouting
para CUALQUIERA de los 106 sismos, focos de incendio o puntos personalizados.
"""

import os
import sys
import json
import time
import random
import heapq
import math
import re
import tempfile
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import psycopg2
from psycopg2 import pool

try:
    import pulp
except ImportError:
    pulp = None

AQUI = os.path.dirname(os.path.abspath(__file__))

DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "ruteo_drones")
DB_USER = os.getenv("DB_USER", "ruteo")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ruteo")
PORT = int(os.getenv("PORT", 8080))

# Penalizacion leve y condicional por edificacion antigua (riesgo de escombros
# post-sismo). Solo se aplica cuando el destino es un sismo, nunca para
# incendios ni en general - ver generar_rutas_comparativas.py y RESULTADOS.md.
PESO_ANTIGUEDAD = 0.3

# Restriccion de usuario (punto 6 de la rubrica): umbral de racha de viento
# operacional. Valores anclados a la ficha tecnica oficial del DJI Matrice
# 300 RTK (enterprise.dji.com/matrice-300), el dron de referencia del
# proyecto (plataforma BVLOS usada en inspeccion/respuesta a desastres):
#   - 12 m/s (43.2 km/h): resistencia al viento para despegue/aterrizaje
#     segun DJI. Es el default: mas restrictivo que el limite en vuelo
#     porque si no puede despegar/aterrizar seguro, la mision no es viable.
#   - 15 m/s (54.0 km/h): resistencia al viento EN VUELO segun DJI. Es el
#     techo absoluto del rango valido: no tiene sentido que el operador
#     pida una restriccion mas permisiva que lo que el dron fisicamente
#     resiste.
# El umbral se compara contra la racha PROMEDIO del pronostico Open-Meteo
# (metadata_viento.racha_media_kmh), no contra la racha maxima: en las 6
# estaciones del area, la racha maxima de ~7 dias de pronostico supera
# SIEMPRE el techo fisico del dron, lo que volveria la restriccion un
# no-op (penalizaria el 100% de las aristas por igual, sin afectar la
# eleccion de ruta). La racha promedio si varia de forma real entre zonas.
VIENTO_MAX_DEFAULT_KMH = 43.2
VIENTO_MAX_ABSOLUTO_KMH = 54.0  # limite fisico en vuelo del DJI M300 RTK (15 m/s)
VIENTO_MAX_RANGO = (10.0, VIENTO_MAX_ABSOLUTO_KMH)


def cost_resiliente_sql(dest, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH):
    """Ver docstring equivalente en generar_rutas_comparativas.py."""
    base = "cost_resiliente"
    if dest.get("tipo") == "sismo":
        base = f"({base} * (1 + {PESO_ANTIGUEDAD} * factor_antiguedad_zona))"
    return (
        f"(CASE WHEN viento_racha_kmh > {float(viento_max_kmh)} "
        f"THEN ({base}) * 1000.0 ELSE {base} END)"
    )

# Pool de conexiones a PostgreSQL
db_pool = None
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=15,
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    print(f"OK: Pool PostgreSQL conectado a {DB_HOST}:{DB_PORT}/{DB_NAME}")
except Exception as e:
    print(f"AVISO: No se pudo conectar inmediatamente al pool DB ({e}). Se reintentará al recibir peticiones.")

# Caché en memoria para respuestas de rutas instantáneas (<5ms)
CACHE_RUTAS = {}

# Mapeo de alias a IDs de cuarteles
ALIAS_CUARTEL = {
    "8va_florida": 17,
    "1ra_sur": 25,
    "2da_central": 23,
    "3ra_norte": 21,
    "4ta_puerto": 20,
    "1ra_vina": 24,
    "5ta_vina": 19,
    "subcom_concon": 15,
    "2da_quilpue": 22,
    "6ta_villa_alemana": 18,
    "reten_placilla": 2,
    "reten_laguna_verde": 28,
}
ID_A_ALIAS = {v: k for k, v in ALIAS_CUARTEL.items()}


def get_db_connection():
    global db_pool
    if db_pool is None:
        db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=15,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
    return db_pool.getconn()


def release_db_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)


def obtener_todos_los_sismos():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Umbral de validez combinado (reemplaza el umbral fijo de 2000m):
        # un sismo es destino valido si el nodo vial mas cercano esta dentro
        # del MINIMO de (a) el radio de relevancia segun su magnitud (Wells &
        # Coppersmith 1994) y (b) el radio operacional segun el viento de la
        # zona (restriccion fisica real de alcance del dron). Funciones SQL
        # en db/schema.sql: radio_relevancia_sismo_km, radio_operacional_
        # viento_km, distancia_max_valida_sismo_km.
        # "en_tierra" (geografico, ST_X >= -71.68) y "destino_valido" (umbral
        # dinamico de ruteo) son conceptos INDEPENDIENTES y no deben
        # confundirse: un sismo puede estar en el mar y aun asi ser destino
        # valido (si es grande y el viento de la zona es favorable), o estar
        # en tierra y no serlo (si esta lejos de cualquier calle). El primero
        # es un hecho geografico fijo; el segundo depende de magnitud+viento.
        cur.execute("""
            SELECT s.id, s.usgs_id, s.magnitud, s.lugar, s.profundidad_km,
                   ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
                   round(dist_red.d::numeric, 1) AS dist_red_m,
                   round(v.racha_media_kmh::numeric, 1) AS racha_zona_kmh,
                   round(distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)::numeric, 1) AS umbral_km,
                   ((dist_red.d / 1000.0) <= distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)) AS destino_valido,
                   (ST_X(s.geom) >= -71.68) AS en_tierra
            FROM amenaza_sismos s
            CROSS JOIN LATERAL (
                SELECT ST_Distance(s.geom::geography, n.geom::geography) AS d
                FROM infraestructura_nodos n ORDER BY n.geom <-> s.geom LIMIT 1
            ) dist_red
            CROSS JOIN LATERAL (
                SELECT viento_racha_interpolada_kmh(s.geom) AS racha_media_kmh
            ) v
            WHERE (dist_red.d / 1000.0) <= distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)
            ORDER BY s.magnitud DESC, dist_red_m ASC;
        """)
        filas = cur.fetchall()
        sismos = []
        for r in filas:
            s_id, usgs_id, mag, lugar, prof, lon, lat, dist_red, racha_zona, umbral_km, destino_valido, en_tierra = r
            sismos.append({
                "key": f"sismo_{s_id}",
                "id": s_id,
                "usgs_id": usgs_id,
                "magnitud": float(mag),
                "lugar": lugar,
                "profundidad_km": float(prof) if prof is not None else None,
                "lon": round(float(lon), 5),
                "lat": round(float(lat), 5),
                "dist_red_m": float(dist_red) if dist_red is not None else 0.0,
                "racha_zona_kmh": float(racha_zona) if racha_zona is not None else None,
                "umbral_valido_km": float(umbral_km) if umbral_km is not None else None,
                "destino_valido": bool(destino_valido),
                "en_tierra": bool(en_tierra),
                "descripcion": f"Sismo M{mag} ({lugar})",
            })
        return sismos
    finally:
        release_db_connection(conn)


def obtener_origenes_cuarteles():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, comuna, es_comisaria, ST_X(geom) lon, ST_Y(geom) lat, nodo_vial_id
            FROM nodo_carabineros
            WHERE es_comisaria OR nombre IN ('SUBCOM. CONCON', 'RETEN PLACILLA PENUELAS', 'RETEN LAGUNA VERDE')
            ORDER BY comuna, nombre;
        """)
        filas = cur.fetchall()
        origenes = []
        for r in filas:
            c_id, nom, com, es_com, lon, lat, nodo_vial = r
            key = ID_A_ALIAS.get(c_id, f"cuartel_{c_id}")
            origenes.append({
                "key": key,
                "id": c_id,
                "nombre": nom,
                "comuna": com,
                "es_comisaria": bool(es_com),
                "lon": round(float(lon), 5),
                "lat": round(float(lat), 5),
                "nodo_vial": nodo_vial,
            })
        return origenes
    finally:
        release_db_connection(conn)


def resolver_origen_nodo(cur, origen_str):
    c_id = None
    if str(origen_str).isdigit():
        c_id = int(origen_str)
    elif origen_str in ALIAS_CUARTEL:
        c_id = ALIAS_CUARTEL[origen_str]
    elif origen_str.startswith("cuartel_"):
        c_id = int(origen_str.replace("cuartel_", ""))

    if c_id:
        cur.execute("SELECT id, nombre, comuna, es_comisaria, ST_X(geom), ST_Y(geom), nodo_vial_id FROM nodo_carabineros WHERE id = %s", (c_id,))
    else:
        cur.execute("SELECT id, nombre, comuna, es_comisaria, ST_X(geom), ST_Y(geom), nodo_vial_id FROM nodo_carabineros WHERE id = 17")

    row = cur.fetchone()
    if not row:
        return None
    key = ID_A_ALIAS.get(row[0], f"cuartel_{row[0]}")
    return {
        "key": key,
        "id": row[0],
        "nombre": row[1],
        "comuna": row[2],
        "es_comisaria": bool(row[3]),
        "lon": round(float(row[4]), 5),
        "lat": round(float(row[5]), 5),
        "nodo_vial": row[6],
    }


def resolver_destino_nodo(cur, destino_str, lat_param=None, lon_param=None):
    if destino_str.startswith("sismo_") or destino_str.isdigit():
        s_id = int(destino_str.replace("sismo_", ""))
        cur.execute("SELECT id, usgs_id, magnitud, lugar, ST_X(geom), ST_Y(geom), profundidad_km FROM amenaza_sismos WHERE id = %s", (s_id,))
        row = cur.fetchone()
        if row:
            s_id, usgs, mag, lug, s_lon, s_lat, prof_km = row
            cur.execute("""
                SELECT id, ST_X(geom), ST_Y(geom),
                       ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                FROM infraestructura_nodos
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT 1;
            """, (s_lon, s_lat, s_lon, s_lat))
            d_nodo, dn_lon, dn_lat, dist_aerea = cur.fetchone()
            cur.execute(
                "SELECT distancia_max_valida_sismo_km(%s, viento_racha_interpolada_kmh(ST_SetSRID(ST_MakePoint(%s, %s), 4326)))",
                (mag, s_lon, s_lat),
            )
            umbral_km = cur.fetchone()[0]
            if (dist_aerea / 1000.0) > umbral_km:
                # Sismo fuera del umbral combinado (radio por magnitud x radio
                # operacional por viento - ver distancia_max_valida_sismo_km en
                # db/schema.sql): el nodo vial mas cercano no representa un
                # area de inspeccion alcanzable para el dron.
                return None
            return {
                "key": f"sismo_{s_id}",
                "id": s_id,
                "tipo": "sismo",
                "descripcion": f"Sismo M{mag} ({lug})",
                "magnitud": float(mag),
                "profundidad_km": float(prof_km or 10.0),
                "lugar": lug,
                "lon": round(float(s_lon), 5),
                "lat": round(float(s_lat), 5),
                "nodo_vial": d_nodo,
                "nodo_lon": float(dn_lon),
                "nodo_lat": float(dn_lat),
                "dist_aerea_m": float(dist_aerea),
            }

    if destino_str.startswith("firms_"):
        f_id = int(destino_str.replace("firms_", ""))
        cur.execute("SELECT id, fecha, confianza, ST_X(geom), ST_Y(geom) FROM amenaza_firms WHERE id = %s", (f_id,))
        row = cur.fetchone()
        if row:
            f_id, fec, conf, f_lon, f_lat = row
            cur.execute("""
                SELECT id, ST_X(geom), ST_Y(geom),
                       ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                FROM infraestructura_nodos
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT 1;
            """, (f_lon, f_lat, f_lon, f_lat))
            d_nodo, dn_lon, dn_lat, dist_aerea = cur.fetchone()
            sec_str = "Valparaíso Cerros / Placilla" if f_id == 1 else "Sector Forestal Norte"
            return {
                "key": f"firms_{f_id}",
                "id": f_id,
                "tipo": "incendio",
                "descripcion": f"Foco Incendio Activo FIRMS #{f_id} ({sec_str})",
                "lon": round(float(f_lon), 5),
                "lat": round(float(f_lat), 5),
                "nodo_vial": d_nodo,
                "nodo_lon": float(dn_lon),
                "nodo_lat": float(dn_lat),
                "dist_aerea_m": float(dist_aerea),
            }

    # Destino por coordenadas personalizadas (clic en cualquier parte del mapa)
    if lat_param is not None and lon_param is not None:
        c_lat, c_lon = float(lat_param), float(lon_param)
        cur.execute("""
            SELECT id, ST_X(geom), ST_Y(geom),
                   ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
            FROM infraestructura_nodos
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            LIMIT 1;
        """, (c_lon, c_lat, c_lon, c_lat))
        d_nodo, dn_lon, dn_lat, dist_aerea = cur.fetchone()
        return {
            "key": f"coord_{round(c_lat,4)}_{round(c_lon,4)}",
            "tipo": "coordenada",
            "descripcion": f"Punto personalizado ({round(c_lat,4)}, {round(c_lon,4)})",
            "lon": round(c_lon, 5),
            "lat": round(c_lat, 5),
            "nodo_vial": d_nodo,
            "nodo_lon": float(dn_lon),
            "nodo_lat": float(dn_lat),
            "dist_aerea_m": float(dist_aerea),
        }

    # Fallback default: Sismo 15 (M4.6 Viña del Mar Norte)
    return resolver_destino_nodo(cur, "sismo_15")


def costo_base_sql(dest):
    """Ver docstring equivalente en generar_rutas_comparativas.py."""
    if dest.get("tipo") == "sismo":
        return f"(length_m * (1 + {PESO_ANTIGUEDAD} * factor_antiguedad_zona))"
    return "length_m"


def calcular_metricas(cur, edges1, edges2, dist1, dist2, dest, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH):
    s1, s2 = set(edges1), set(edges2)
    inter = len(s1.intersection(s2))
    union = len(s1.union(s2)) if len(s1.union(s2)) > 0 else 1
    disyuncion = 1.0 - (inter / union)
    solapamiento_pct = (inter / union) * 100.0

    # Seguridad = costo "sin riesgo" / costo real ponderado de las aristas del
    # par, con la misma funcion de costo que usa el ruteo (ver
    # generar_rutas_comparativas.py).
    aristas_con_riesgo, factor_seguridad = 0, 1.0
    if len(s1.union(s2)) > 0:
        cur.execute(
            f"""SELECT count(*) FILTER (WHERE cost_resiliente > length_m OR viento_racha_kmh > %s),
                       coalesce(sum(length_m * LEAST(1.0, LN(GREATEST(1.0, ({cost_resiliente_sql(dest, viento_max_kmh)}) / ({costo_base_sql(dest)}))) / LN(1000.0))), 0),
                       coalesce(sum(length_m), 0)
                FROM infraestructura_aristas WHERE id = ANY(%s)""",
            (float(viento_max_kmh), list(s1.union(s2))),
        )
        aristas_con_riesgo, exposicion_pond, suma_len = cur.fetchone()
        factor_seguridad = 1.0 - float(exposicion_pond) / float(suma_len) if suma_len else 1.0
    resiliencia_score = round(disyuncion * factor_seguridad * 100.0, 1)

    return {
        "disyuncion_pct": round(disyuncion * 100.0, 1),
        "solapamiento_pct": round(solapamiento_pct, 1),
        "factor_seguridad": round(factor_seguridad, 2),
        "aristas_con_riesgo": aristas_con_riesgo,
        "resiliencia_score": resiliencia_score,
        "distancia_promedio_km": round((dist1 + dist2) / 2000.0, 2),
    }


def extraer_geometria_y_distancia(cur, edge_ids, dest_info=None):
    if not edge_ids:
        return None, 0.0

    cur.execute(
        "SELECT ST_AsGeoJSON(ST_LineMerge(ST_Collect(geom)))::json, coalesce(sum(length_m), 0) FROM infraestructura_aristas WHERE id = ANY(%s)",
        (edge_ids,),
    )
    geom_json, dist_m = cur.fetchone()
    dist_total = float(dist_m or 0.0)

    # Si el objetivo se encuentra fuera de la red vial (por ejemplo en el mar o cerro a > 40 metros),
    # conectamos el corredor aéreo directo desde el último nodo vial hasta el epicentro exacto.
    if dest_info and dest_info.get("dist_aerea_m", 0) > 40.0:
        dist_total += dest_info["dist_aerea_m"]
        tramo_aereo = [[dest_info["nodo_lon"], dest_info["nodo_lat"]], [dest_info["lon"], dest_info["lat"]]]
        if geom_json:
            gtype = geom_json.get("type")
            if gtype == "LineString":
                coords = geom_json["coordinates"]
                coords.append([dest_info["lon"], dest_info["lat"]])
                geom_json["coordinates"] = coords
            elif gtype == "MultiLineString":
                coords = geom_json["coordinates"]
                coords.append(tramo_aereo)
                geom_json["coordinates"] = coords

    return geom_json, dist_total


# =====================================================================
# PROB. DE FALLO — carga en memoria al arrancar (Point 5 rúbrica)
# =====================================================================
PROB_FALLO = {}
ZONAS = {}
_prob_path = os.path.join(AQUI, "data", "probabilidad_fallo.json")
if os.path.exists(_prob_path):
    with open(_prob_path, "r", encoding="utf-8") as _f:
        _pdata = json.load(_f)
        PROB_FALLO = {int(k): v for k, v in _pdata.get("aristas", {}).items()}
        ZONAS = {int(k): v for k, v in _pdata.get("zonas", {}).items()}
    print(f"OK: Probabilidades de fallo cargadas — {len(PROB_FALLO)} aristas")
else:
    print(f"AVISO: {_prob_path} no encontrado. Ejecutar generar_probabilidad_fallo.py primero.")


# =====================================================================
# OPTIMIZACIÓN FORMAL: Programación Lineal con Simplex (Point 6.2 rúbrica — CPLEX/GUROBI)
# =====================================================================
def _subgrafo_acotado(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_ref=None, max_edges=30000):
    """Subgrafo acotado para LP y ACO: corredor alrededor de las rutas de referencia
    (Dijkstra y resiliente), que garantiza que origen y destino queden conectados
    aunque la ruta sea larga. Se reduce el ancho del corredor hasta tener <= max_edges
    aristas, porque el LP y el ACO son computacionalmente costosos sobre el grafo completo."""
    if not ruta_ref:
        return []
    costo = cost_resiliente_sql(dest, viento_max_kmh)
    ref_sql = "SELECT ST_Collect(geom) AS g FROM infraestructura_aristas WHERE id = ANY(%s)"
    for buf in (0.03, 0.02, 0.012, 0.006, 0.003):
        cur.execute(
            f"""WITH ref AS ({ref_sql})
                SELECT a.id, a.source, a.target, {costo} AS cost
                FROM infraestructura_aristas a, ref
                WHERE a.geom && ST_Expand(ref.g, %s) AND ST_DWithin(a.geom, ref.g, %s)""",
            (list(ruta_ref), buf, buf),
        )
        rows = cur.fetchall()
        if len(rows) <= max_edges:
            return rows
    return rows[:max_edges]


def ruta_optimizacion_lp(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_ref=None, riesgo_max=None, fs=None):
    """Camino más corto como Programación Lineal resuelta con Simplex (PuLP + CBC/Clp).
    Formulación: min Σ c_e·x_e  s.a. conservación de flujo en cada nodo.
    La matriz de incidencia nodo-arco es totalmente unimodular, por lo que el
    vértice óptimo que entrega el Simplex ya es entero (x_e ∈ {0,1}).
    Devuelve (aristas, info) con info = iteraciones Simplex, objetivo, tamaño del LP."""
    if pulp is None:
        return [], {}

    edges = _subgrafo_acotado(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_ref)
    if not edges:
        return [], {}

    edge_map = {}
    nodos_set = set()
    for eid, src, tgt, cost in edges:
        edge_map[eid] = (src, tgt, float(cost))
        nodos_set.add(src)
        nodos_set.add(tgt)

    if o_nodo not in nodos_set or d_nodo not in nodos_set:
        return [], {}

    prob_lp = pulp.LpProblem("shortest_path_resiliente", pulp.LpMinimize)

    x = {}
    for eid in edge_map:
        x[eid] = pulp.LpVariable(f"x_{eid}", lowBound=0, upBound=1, cat="Continuous")

    prob_lp += pulp.lpSum(edge_map[eid][2] * x[eid] for eid in edge_map)

    out_edges = {}
    in_edges = {}
    for eid, (src, tgt, _) in edge_map.items():
        out_edges.setdefault(src, []).append(eid)
        in_edges.setdefault(tgt, []).append(eid)

    for nodo in nodos_set:
        flow_out = pulp.lpSum(x[e] for e in out_edges.get(nodo, []))
        flow_in = pulp.lpSum(x[e] for e in in_edges.get(nodo, []))
        if nodo == o_nodo:
            prob_lp += flow_out - flow_in == 1
        elif nodo == d_nodo:
            prob_lp += flow_in - flow_out == 1
        else:
            prob_lp += flow_out - flow_in == 0

    # Restricción de riesgo: la probabilidad acumulada de fallo de la ruta no puede
    # superar la de la mejor ruta de referencia (modelo de confiabilidad). Un tope fijo
    # haría el LP infactible en rutas largas, donde la suma de P crece con la distancia.
    if PROB_FALLO and riesgo_max is not None:
        prob_lp += pulp.lpSum(
            p_efectiva(eid, fs or {}) * x[eid]
            for eid in edge_map
        ) <= riesgo_max + 1e-6, "max_riesgo_acumulado"

    log_path = tempfile.mktemp(suffix=".cbc.log")
    try:
        prob_lp.solve(pulp.PULP_CBC_CMD(msg=True, timeLimit=15, logPath=log_path))
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            log = f.read()
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)

    if prob_lp.status != 1:
        return [], {}

    iters = re.findall(r"Optimal objective\s+\S+\s+-\s+(\d+)\s+iterations", log)
    info = {
        "iteraciones_simplex": int(iters[-1]) if iters else None,
        "objetivo": round(pulp.value(prob_lp.objective), 2),
        "variables": len(edge_map),
        "restricciones": len(prob_lp.constraints),
    }

    path_edges = [eid for eid in edge_map if pulp.value(x[eid]) is not None and pulp.value(x[eid]) > 0.5]
    return path_edges, info


# =====================================================================
# METAHEURÍSTICA — Ant Colony Optimization (Point 6.4 rúbrica)
# =====================================================================
def ruta_aco(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_ref=None,
             n_ants=30, n_iter=40, alpha=1.0, beta=3.0, rho=0.3, q=100.0):
    """ACO para shortest path resiliente sobre subgrafo acotado."""
    rows = _subgrafo_acotado(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_ref)
    if not rows:
        return [], 0.0

    adj = {}
    edge_cost = {}
    for eid, src, tgt, cost in rows:
        c = max(float(cost), 0.01)
        adj.setdefault(src, []).append((tgt, eid, c))
        edge_cost[eid] = c

    if o_nodo not in adj:
        return [], 0.0

    # Heurística: distancia precomputada con Dijkstra (para guiar a las hormigas)
    dist_to_target = _dijkstra_dist(adj, d_nodo)
    if o_nodo not in dist_to_target:
        return [], 0.0

    # Inicializar feromonas con el camino de Dijkstra para arranque en caliente
    pheromone = {eid: 1.0 for eid in edge_cost}
    dij_path = _greedy_path(adj, o_nodo, d_nodo, dist_to_target)
    if dij_path:
        for eid in dij_path:
            pheromone[eid] = 5.0
    best_path = dij_path
    best_cost = sum(edge_cost.get(e, 0) for e in dij_path) if dij_path else float("inf")

    for _it in range(n_iter):
        paths = []
        for _ant in range(n_ants):
            path, cost = _ant_walk(adj, o_nodo, d_nodo, pheromone, edge_cost,
                                   dist_to_target, alpha, beta)
            if path:
                paths.append((path, cost))
                if cost < best_cost:
                    best_cost = cost
                    best_path = path

        # Evaporación
        for eid in pheromone:
            pheromone[eid] *= (1 - rho)

        # Depósito
        for path, cost in paths:
            deposit = q / cost if cost > 0 else q
            for eid in path:
                pheromone[eid] += deposit

    return best_path or [], best_cost


def _dijkstra_dist(adj, target):
    """BFS/Dijkstra inverso desde target para heurística ACO."""
    rev_adj = {}
    for src, neighbors in adj.items():
        for tgt, eid, cost in neighbors:
            rev_adj.setdefault(tgt, []).append((src, cost))

    dist = {target: 0.0}
    heap = [(0.0, target)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        for v, c in rev_adj.get(u, []):
            nd = d + c
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist


def _greedy_path(adj, origin, target, dist_to_target):
    """Camino greedy guiado por distancia al target (para seed del ACO)."""
    current = origin
    visited = {current}
    path = []
    for _ in range(2000):
        if current == target:
            return path
        neighbors = adj.get(current, [])
        best = None
        best_score = float("inf")
        for tgt, eid, cost in neighbors:
            if tgt in visited:
                continue
            d = dist_to_target.get(tgt, float("inf"))
            if d < best_score:
                best_score = d
                best = (tgt, eid, cost)
        if best is None:
            return None
        tgt, eid, cost = best
        path.append(eid)
        visited.add(tgt)
        current = tgt
    return None


def _ant_walk(adj, origin, target, pheromone, edge_cost,
              dist_to_target, alpha, beta, max_steps=1500):
    """Una hormiga construye un camino de origin a target."""
    current = origin
    visited = {current}
    path = []
    total_cost = 0.0

    for _ in range(max_steps):
        if current == target:
            return path, total_cost

        neighbors = adj.get(current, [])
        cur_dist = dist_to_target.get(current, 1.0)
        candidates = []
        for tgt, eid, cost in neighbors:
            if tgt in visited:
                continue
            if tgt not in dist_to_target:
                continue
            tau = pheromone.get(eid, 1.0) ** alpha
            eta = (1.0 / max(cost, 0.01)) ** beta
            # Bonus por acercarse al destino (normalizado respecto a distancia actual)
            progress = max(0.01, cur_dist - dist_to_target.get(tgt, cur_dist)) / max(cur_dist, 1.0) + 0.01
            candidates.append((tgt, eid, cost, tau * eta * (progress ** 2)))

        if not candidates:
            return None, 0.0

        total_prob = sum(c[3] for c in candidates)
        r = random.random() * total_prob
        cumul = 0.0
        chosen = candidates[-1]
        for cand in candidates:
            cumul += cand[3]
            if cumul >= r:
                chosen = cand
                break

        tgt, eid, cost, _ = chosen
        path.append(eid)
        total_cost += cost
        visited.add(tgt)
        current = tgt

    return None, 0.0


# =====================================================================
# SIMULACIÓN DE FALLAS ALEATORIAS (Point 7 rúbrica)
# =====================================================================
# Intensidad sísmica -> fracción de edificación antigua que sufre daño/escombros.
# MMI = A + B*M - C*log10(R_hipocentral_km): relación de atenuación simplificada de la forma
# empírica tipo Gutenberg-Richter. Coeficientes de orden de magnitud (NO de una GMPE publicada),
# elegidos para que M6.5 a 10 km dé MMI ~VII y un M4.4 a 48 km de profundidad dé MMI ~I-II.
MMI_A, MMI_B, MMI_C = 0.9, 1.5, 3.5
MMI_DANO_INICIO, MMI_DANO_TOTAL = 5.0, 9.0  # daño leve desde MMI VI aprox.; pleno hacia MMI IX
CACHE_FS = {}


def factor_dano_mmi(mag, prof_km, dist_km):
    r = max((dist_km ** 2 + prof_km ** 2) ** 0.5, 1.0)
    mmi = MMI_A + MMI_B * mag - MMI_C * math.log10(r)
    return max(0.0, min(1.0, (mmi - MMI_DANO_INICIO) / (MMI_DANO_TOTAL - MMI_DANO_INICIO)))


def factor_sismo_aristas(cur, dest):
    """{arista_id: fracción de daño} para las aristas con edificación vulnerable, según el sismo
    de destino. Para destinos que no son sismo la amenaza no aplica (factor 0)."""
    if dest.get("tipo") != "sismo":
        return {}
    if dest["id"] in CACHE_FS:
        return CACHE_FS[dest["id"]]
    ids = [e for e, d in PROB_FALLO.items() if "sismo_edificacion" in d]
    cur.execute(
        """SELECT id, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) / 1000.0
           FROM infraestructura_aristas WHERE id = ANY(%s)""",
        (dest["lon"], dest["lat"], ids),
    )
    fs = {eid: factor_dano_mmi(dest["magnitud"], dest.get("profundidad_km", 10.0), d_km) for eid, d_km in cur.fetchall()}
    CACHE_FS[dest["id"]] = fs
    return fs


def probs_efectivas(eid, fs):
    """P por amenaza de una arista; sismo_edificacion (vulnerabilidad) se escala por la intensidad del sismo."""
    out = {}
    for am, p in PROB_FALLO.get(eid, {}).items():
        if am == "p_total":
            continue
        if am == "sismo_edificacion":
            p = p * fs.get(eid, 0.0)
        if p > 0:
            out[am] = p
    return out


def p_efectiva(eid, fs):
    prod = 1.0
    for p in probs_efectivas(eid, fs).values():
        prod *= 1.0 - p
    return 1.0 - prod


def _sortear_aristas(edge_ids, fs, eventos=None):
    """Un escenario: devuelve {arista_id: [(amenaza, aleatorio, umbral)]} de las aristas que fallan.
    Cada amenaza se sortea con random(0,100) contra P*100. Las amenazas espaciales comparten
    un sorteo por evento/zona (todas las aristas de la misma zona de ignición, área protegida,
    foco o línea de tendido fallan juntas); viento y sismo se sortean por arista."""
    eventos = {} if eventos is None else eventos
    fallan = {}
    for eid in edge_ids:
        for am, p in probs_efectivas(eid, fs).items():
            zid = ZONAS.get(eid, {}).get(am)
            if zid is not None:
                if (am, zid) not in eventos:
                    eventos[(am, zid)] = random.uniform(0, 100)
                r = eventos[(am, zid)]
            else:
                r = random.uniform(0, 100)
            if r < p * 100:
                fallan.setdefault(eid, []).append((am, round(r, 1), round(p * 100, 1)))
    return fallan


def simular_fallas(cur, route_edges, fs, n_sim=100):
    """Monte Carlo: n_sim escenarios de sorteo aleatorio (ver _sortear_aristas).
    La ruta sobrevive a un escenario si ninguna de sus aristas falla."""
    expuestas = [e for e in (route_edges or []) if p_efectiva(e, fs) > 0]
    if not expuestas:
        return {"simulaciones": n_sim, "sobrevive": n_sim, "tasa_supervivencia": 100.0,
                "aristas_vulnerables": 0, "fallas_promedio": 0.0, "detalle_fallas": []}

    sobrevive = 0
    total_fallas = 0
    fallas_por_arista = {}
    for _ in range(n_sim):
        fallan = _sortear_aristas(expuestas, fs)
        total_fallas += len(fallan)
        if not fallan:
            sobrevive += 1
        for eid in fallan:
            fallas_por_arista[eid] = fallas_por_arista.get(eid, 0) + 1

    detalle = [{
        "arista_id": eid,
        "p_fallo": round(p_efectiva(eid, fs), 4),
        "fallas_en_sim": cnt,
        "pct_falla": round(cnt / n_sim * 100, 1),
    } for eid, cnt in sorted(fallas_por_arista.items(), key=lambda x: -x[1])[:10]]

    return {
        "simulaciones": n_sim,
        "sobrevive": sobrevive,
        "tasa_supervivencia": round(sobrevive / n_sim * 100, 1),
        "aristas_vulnerables": len(expuestas),
        "fallas_promedio": round(total_fallas / n_sim, 2),
        "detalle_fallas": detalle,
    }


def aristas_vulnerables_geo(cur, route_edges, fs):
    """Aristas de la ruta con P(fallo)>0, con su geometría, para el sorteo aleatorio en la interfaz (punto 7)."""
    ids = [e for e in route_edges if p_efectiva(e, fs) > 0]
    if not ids:
        return []
    cur.execute("SELECT id, ST_AsGeoJSON(geom) FROM infraestructura_aristas WHERE id = ANY(%s)", (ids,))
    geoms = {i: json.loads(g) for i, g in cur.fetchall()}
    return [{"id": e, "p": round(p_efectiva(e, fs), 4),
             "amenazas": {k: round(v, 4) for k, v in probs_efectivas(e, fs).items()},
             "zonas": ZONAS.get(e, {}),
             "geom": geoms.get(e)} for e in ids]


def calcular_rutas_par(origen_key, destino_key, lat=None, lon=None, viento_max_kmh=None):
    if viento_max_kmh is None:
        viento_max_kmh = VIENTO_MAX_DEFAULT_KMH
    else:
        try:
            viento_max_kmh = float(viento_max_kmh)
        except (TypeError, ValueError):
            viento_max_kmh = VIENTO_MAX_DEFAULT_KMH
        viento_max_kmh = max(VIENTO_MAX_RANGO[0], min(VIENTO_MAX_RANGO[1], viento_max_kmh))

    cache_key = f"{origen_key}_{destino_key}_{lat}_{lon}_{viento_max_kmh}"
    if cache_key in CACHE_RUTAS:
        return CACHE_RUTAS[cache_key]

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        orig = resolver_origen_nodo(cur, origen_key)
        dest = resolver_destino_nodo(cur, destino_key, lat, lon)

        if not orig or not dest:
            return {"error": "Origen o destino no encontrado"}

        o_nodo = orig["nodo_vial"]
        d_nodo = dest["nodo_vial"]

        t_start_dijkstra = time.time()

        # 1. Dijkstra 1 (Distancia Pura)
        cur.execute(
            "SELECT edge FROM pgr_dijkstra('SELECT id, source, target, length_m AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
            (o_nodo, d_nodo),
        )
        e_d1 = [r[0] for r in cur.fetchall()]

        # 2. Dijkstra 2 (Bypass con penalización)
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

        t_dijkstra_ms = round((time.time() - t_start_dijkstra) * 1000, 1)
        t_resil_start = time.time()

        # 3. Resiliente 1 (Ponderación multi-amenaza y seguridad; agrega riesgo
        #    de edificación antigua solo si el destino es un sismo, y aplica
        #    la restricción de viento operacional del usuario o su default)
        costo_base = cost_resiliente_sql(dest, viento_max_kmh)
        cur.execute(
            f"SELECT edge FROM pgr_dijkstra('SELECT id, source, target, {costo_base} AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
            (o_nodo, d_nodo),
        )
        e_r1 = [r[0] for r in cur.fetchall()]

        # 4. Resiliente 2 (Bypass resiliente disjunto)
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

        t_resil_ms = round((time.time() - t_resil_start) * 1000, 1)

        geom_d1, dist_d1 = extraer_geometria_y_distancia(cur, e_d1, dest)
        geom_d2, dist_d2 = extraer_geometria_y_distancia(cur, e_d2 if e_d2 else e_d1, dest)
        geom_r1, dist_r1 = extraer_geometria_y_distancia(cur, e_r1, dest)
        geom_r2, dist_r2 = extraer_geometria_y_distancia(cur, e_r2 if e_r2 else e_r1, dest)

        m_dijkstra = calcular_metricas(cur, e_d1, e_d2 if e_d2 else e_d1, dist_d1, dist_d2, dest, viento_max_kmh)
        m_resiliente = calcular_metricas(cur, e_r1, e_r2 if e_r2 else e_r1, dist_r1, dist_r2, dest, viento_max_kmh)
        diff_resil = round(m_resiliente["resiliencia_score"] - m_dijkstra["resiliencia_score"], 1)

        ruta_corredor = list(set(e_d1) | set(e_r1))
        fs = factor_sismo_aristas(cur, dest)
        riesgo_ref = sum(p_efectiva(e, fs) for e in e_r1)

        # 5. Optimización LP — método Simplex (PuLP/CBC, equivalente a CPLEX/GUROBI)
        t_lp_start = time.time()
        e_lp, info_lp = ruta_optimizacion_lp(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_corredor, riesgo_ref, fs)
        t_lp_ms = round((time.time() - t_lp_start) * 1000, 1)
        geom_lp, dist_lp = extraer_geometria_y_distancia(cur, e_lp, dest)
        m_lp = calcular_metricas(cur, e_lp, e_d2 if e_d2 else e_lp, dist_lp, dist_d2, dest, viento_max_kmh) if e_lp else {}

        # 6. Metaheurística ACO
        t_aco_start = time.time()
        e_aco, _ = ruta_aco(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_corredor)
        t_aco_ms = round((time.time() - t_aco_start) * 1000, 1)
        geom_aco, dist_aco = extraer_geometria_y_distancia(cur, e_aco, dest)
        m_aco = calcular_metricas(cur, e_aco, e_d2 if e_d2 else e_aco, dist_aco, dist_d2, dest, viento_max_kmh) if e_aco else {}

        # 7. Simulación de fallas para cada técnica
        sim_dijkstra = simular_fallas(cur, e_d1, fs)
        sim_resiliente = simular_fallas(cur, e_r1, fs)
        sim_lp = simular_fallas(cur, e_lp, fs) if e_lp else {}
        sim_aco = simular_fallas(cur, e_aco, fs) if e_aco else {}

        resultado = {
            "origen": orig,
            "destino": dest,
            "restricciones": {
                "viento_max_kmh": viento_max_kmh,
                "es_default": viento_max_kmh == VIENTO_MAX_DEFAULT_KMH,
            },
            "dijkstra_1": {"distancia_m": round(dist_d1, 1), "tramos": len(e_d1), "geom": geom_d1},
            "dijkstra_2": {"distancia_m": round(dist_d2, 1), "tramos": len(e_d2), "geom": geom_d2},
            "resiliente_1": {"distancia_m": round(dist_r1, 1), "tramos": len(e_r1), "geom": geom_r1},
            "resiliente_2": {"distancia_m": round(dist_r2, 1), "tramos": len(e_r2), "geom": geom_r2},
            "optimizacion_lp": {"distancia_m": round(dist_lp, 1), "tramos": len(e_lp), "geom": geom_lp, "algoritmo": "Programación Lineal — método Simplex (PuLP/CBC)", "simplex": info_lp, "tiempo_ms": t_lp_ms},
            "aco": {"distancia_m": round(dist_aco, 1), "tramos": len(e_aco), "geom": geom_aco, "algoritmo": "Ant Colony Optimization (20 hormigas, 30 iter)", "tiempo_ms": t_aco_ms},
            "metricas": {
                "dijkstra": {**m_dijkstra, "algoritmo": "pgr_dijkstra (distancia pura)", "tiempo_ms": t_dijkstra_ms},
                "resiliente": {**m_resiliente, "algoritmo": "pgr_dijkstra (cost_resiliente multi-amenaza)", "tiempo_ms": t_resil_ms},
                "optimizacion_lp": {**m_lp, "algoritmo": "Simplex (PL, min costo s.a. conservación de flujo)", "simplex": info_lp, "tiempo_ms": t_lp_ms} if m_lp else {},
                "aco": {**m_aco, "algoritmo": "ACO (Ant Colony Optimization)", "tiempo_ms": t_aco_ms} if m_aco else {},
                "diferencial_resiliencia": diff_resil,
            },
            "aristas_vulnerables": {
                "dijkstra": aristas_vulnerables_geo(cur, e_d1, fs),
                "resiliente": aristas_vulnerables_geo(cur, e_r1, fs),
                "optimizacion_lp": aristas_vulnerables_geo(cur, e_lp, fs),
                "aco": aristas_vulnerables_geo(cur, e_aco, fs),
            },
            "simulacion_fallas": {
                "dijkstra": sim_dijkstra,
                "resiliente": sim_resiliente,
                "optimizacion_lp": sim_lp,
                "aco": sim_aco,
            },
        }

        # Guardar en caché
        CACHE_RUTAS[cache_key] = resultado
        return resultado
    finally:
        release_db_connection(conn)


class RuteoRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=AQUI, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. API: Listado de todos los sismos (106)
        if path == "/api/sismos":
            try:
                sismos = obtener_todos_los_sismos()
                self._send_json(sismos)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # 2. API: Listado de orígenes (cuarteles)
        if path == "/api/origenes":
            try:
                origenes = obtener_origenes_cuarteles()
                self._send_json(origenes)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # 3. API: Probabilidades de fallo (Point 5)
        if path == "/api/probabilidad_fallo":
            try:
                prob_path = os.path.join(AQUI, "data", "probabilidad_fallo.json")
                if os.path.exists(prob_path):
                    with open(prob_path, "r", encoding="utf-8") as f:
                        self._send_json(json.load(f))
                else:
                    self._send_json({"error": "Archivo de probabilidades no generado"}, 404)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # 4. API: Cálculo Dinámico de Ruta en tiempo real
        if path == "/api/calcular_ruta":
            try:
                orig = query.get("origen", ["8va_florida"])[0]
                dest = query.get("destino", ["sismo_15"])[0]
                lat = query.get("lat", [None])[0]
                lon = query.get("lon", [None])[0]
                viento_max = query.get("viento_max", [None])[0]

                t0 = time.time()
                ruta_data = calcular_rutas_par(orig, dest, lat, lon, viento_max)
                t_ms = round((time.time() - t0) * 1000, 1)

                if "error" in ruta_data:
                    self._send_json(ruta_data, 400)
                    return

                # Empaquetar como respuesta enriquecida
                ruta_data["tiempo_calculo_ms"] = t_ms
                self._send_json(ruta_data)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # 4. Servir archivos estáticos normales (index.html, app.js, data/*.geojson)
        super().do_GET()

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Log conciso para peticiones API
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {args[0]} - {args[1]}\n")


def run():
    print(f"Iniciando Servidor Web Ruteo BVLOS en puerto {PORT}...")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RuteoRequestHandler)
    print(f"Servidor listo en http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Cerrando servidor...")
        server.server_close()


if __name__ == "__main__":
    run()
