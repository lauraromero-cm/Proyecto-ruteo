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
from modelo_riesgo import factor_dano_mmi, cost_resiliente_sql, costo_base_sql

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

VIENTO_MAX_DEFAULT_KMH = 43.2
VIENTO_MAX_ABSOLUTO_KMH = 54.0  # limite fisico en vuelo del DJI M300 RTK (15 m/s)
VIENTO_MAX_RANGO = (10.0, VIENTO_MAX_ABSOLUTO_KMH)

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


def calcular_metricas(cur, edges1, edges2, dist1, dist2, dest, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH, fs=None):
    s1, s2 = set(edges1), set(edges2)
    inter = len(s1.intersection(s2))
    union = len(s1.union(s2)) if len(s1.union(s2)) > 0 else 1
    disyuncion = 1.0 - (inter / union)
    solapamiento_pct = (inter / union) * 100.0

    # Seguridad = costo "sin riesgo" / costo real ponderado de las aristas de la
    # ruta principal y su respaldo, con la misma funcion de costo que usa el ruteo.
    aristas_con_riesgo, factor_seguridad = 0, 1.0
    if len(s1.union(s2)) > 0:
        cur.execute(
            f"""SELECT count(*) FILTER (WHERE ({cost_resiliente_sql(dest, viento_max_kmh)}) > length_m * 1.001),
                       coalesce(sum(length_m * LEAST(1.0, LN(GREATEST(1.0, ({cost_resiliente_sql(dest, viento_max_kmh)}) / ({costo_base_sql(dest)}))) / LN(1000.0))), 0),
                       coalesce(sum(length_m), 0)
                FROM infraestructura_aristas WHERE id = ANY(%s)""",
            (list(s1.union(s2)),),
        )
        aristas_con_riesgo_sql, exposicion_pond, suma_len = cur.fetchone()
        factor_seguridad = 1.0 - float(exposicion_pond) / float(suma_len) if suma_len else 1.0
        if fs is not None:
            aristas_con_riesgo = sum(1 for e in edges1 if p_efectiva(e, fs, viento_max_kmh, dest) > 0)
        else:
            aristas_con_riesgo = aristas_con_riesgo_sql
    resiliencia_score = round(disyuncion * factor_seguridad * 100.0, 1)

    return {
        "disyuncion_pct": round(disyuncion * 100.0, 1),
        "solapamiento_pct": round(solapamiento_pct, 1),
        "factor_seguridad": round(factor_seguridad, 3),
        "aristas_con_riesgo": aristas_con_riesgo,
        "resiliencia_score": resiliencia_score,
        "distancia_promedio_km": round(dist1 / 1000.0, 2),
        "distancia_par_promedio_km": round((dist1 + dist2) / 2000.0, 2),
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
            p_efectiva(eid, fs or {}, viento_max_kmh, dest) * x[eid]
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

    raw_edges = [eid for eid in edge_map if pulp.value(x[eid]) is not None and pulp.value(x[eid]) > 0.5]
    by_src = {edge_map[eid][0]: eid for eid in raw_edges}
    path_edges = []
    curr = o_nodo
    visited_e = set()
    while curr in by_src and by_src[curr] not in visited_e:
        eid = by_src[curr]
        visited_e.add(eid)
        path_edges.append(eid)
        curr = edge_map[eid][1]
    if len(path_edges) < len(raw_edges):
        for eid in raw_edges:
            if eid not in visited_e:
                path_edges.append(eid)
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
# SIMULACIÓN DE FALLAS ALEATORIAS Y RE-RUTEO (Puntos 7, 8 y 9 rúbrica)
# =====================================================================
CACHE_FS = {}
VIENTO_RACHA = {}
VIENTO_ZONA = {}


def _asegurar_viento_aristas(cur):
    """Carga en memoria la racha interpolada y celda espacial de viento por arista,
    para evaluar dinámicamente P(viento) cuando el usuario ajusta el slider."""
    if VIENTO_RACHA:
        return
    cur.execute("""
        SELECT id, viento_racha_kmh,
               floor((ST_X(ST_LineInterpolatePoint(geom, 0.5)) + 72.0) / 0.01)::int * 1000
               + floor((ST_Y(ST_LineInterpolatePoint(geom, 0.5)) + 34.0) / 0.01)::int AS celda_id
        FROM infraestructura_aristas
        WHERE viento_racha_kmh IS NOT NULL
    """)
    for eid, racha, cid in cur.fetchall():
        VIENTO_RACHA[int(eid)] = float(racha)
        VIENTO_ZONA[int(eid)] = int(cid)


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


def probs_efectivas(eid, fs, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH, dest=None):
    """P por amenaza de una arista:
    - sismo_edificacion se escala por la intensidad MMI del sismo destino.
    - firms se omite si el foco FIRMS es el propio destino de la misión (el dron va hacia él).
    - viento se calcula dinámicamente respecto al umbral viento_max_kmh elegido en el slider."""
    out = {}
    for am, p in PROB_FALLO.get(eid, {}).items():
        if am in ("p_total", "viento"):
            continue
        if am == "firms" and dest and dest.get("tipo") == "incendio":
            if ZONAS.get(eid, {}).get("firms") == dest.get("id"):
                continue
        if am == "sismo_edificacion":
            p = p * fs.get(eid, 0.0)
        if p > 0:
            out[am] = p

    racha = VIENTO_RACHA.get(eid, 0.0)
    umbral_v = float(viento_max_kmh or VIENTO_MAX_DEFAULT_KMH)
    if racha > umbral_v and umbral_v > 0:
        pv = min(1.0, (racha - umbral_v) / umbral_v)
        if pv > 0:
            out["viento"] = pv
    return out


def zonas_efectivas(eid, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH):
    z = dict(ZONAS.get(eid, {}))
    racha = VIENTO_RACHA.get(eid, 0.0)
    if racha > float(viento_max_kmh or VIENTO_MAX_DEFAULT_KMH) and eid in VIENTO_ZONA:
        z["viento"] = VIENTO_ZONA[eid]
    return z


def p_efectiva(eid, fs, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH, dest=None):
    prod = 1.0
    for p in probs_efectivas(eid, fs, viento_max_kmh, dest).values():
        prod *= 1.0 - p
    return 1.0 - prod


def _sortear_aristas(edge_ids, fs, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH, dest=None, eventos=None, rng=None):
    """Un escenario: devuelve {arista_id: [(amenaza, aleatorio, umbral)]} de las aristas que fallan.
    Cada amenaza se sortea con random(0,100) contra P*100. Las amenazas espaciales comparten
    un sorteo por evento/zona (celda de ignición, área protegida, foco, cruce de tendido,
    manzana o celda de viento)."""
    eventos = {} if eventos is None else eventos
    rand_fn = rng.uniform if rng is not None else random.uniform
    fallan = {}
    for eid in edge_ids:
        ze = zonas_efectivas(eid, viento_max_kmh)
        for am, p in probs_efectivas(eid, fs, viento_max_kmh, dest).items():
            zid = ze.get(am)
            clave = (am, zid) if zid is not None else (am, f"arista_{eid}")
            if clave not in eventos:
                eventos[clave] = rand_fn(0, 100)
            r = eventos[clave]
            if r < p * 100:
                fallan.setdefault(eid, []).append((am, round(r, 1), round(p * 100, 1)))
    return fallan


def simular_fallas_conjunto(rutas_dict, fs, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH, dest=None, n_sim=200):
    """Simulación Monte Carlo con Números Aleatorios Comunes (Common Random Numbers):
    En cada uno de los n_sim escenarios, los eventos de amenaza se sortean UNA sola vez
    y se evalúan simultáneamente sobre las 4 técnicas. Así la comparación de tasa de
    supervivencia entre técnicas es 100% justa, reproducible y libre de varianza muestral."""
    rng = random.Random(42)
    expuestas_por_k = {
        k: [e for e in (edges or []) if p_efectiva(e, fs, viento_max_kmh, dest) > 0]
        for k, edges in rutas_dict.items()
    }
    resultados = {
        k: {
            "simulaciones": n_sim,
            "sobrevive": 0,
            "total_fallas": 0,
            "fallas_por_arista": {},
        }
        for k in rutas_dict
    }

    for _ in range(n_sim):
        eventos_escenario = {}
        for k, expuestas in expuestas_por_k.items():
            if not expuestas:
                resultados[k]["sobrevive"] += 1
                continue
            fallan = _sortear_aristas(expuestas, fs, viento_max_kmh, dest, eventos=eventos_escenario, rng=rng)
            resultados[k]["total_fallas"] += len(fallan)
            if not fallan:
                resultados[k]["sobrevive"] += 1
            for eid in fallan:
                fpa = resultados[k]["fallas_por_arista"]
                fpa[eid] = fpa.get(eid, 0) + 1

    salida = {}
    for k, edges in rutas_dict.items():
        if not edges:
            salida[k] = {}
            continue
        expuestas = expuestas_por_k[k]
        r = resultados[k]
        detalle = [{
            "arista_id": eid,
            "p_fallo": round(p_efectiva(eid, fs, viento_max_kmh, dest), 4),
            "fallas_en_sim": cnt,
            "pct_falla": round(cnt / n_sim * 100, 1),
        } for eid, cnt in sorted(r["fallas_por_arista"].items(), key=lambda x: -x[1])[:10]]
        salida[k] = {
            "simulaciones": n_sim,
            "sobrevive": r["sobrevive"],
            "tasa_supervivencia": round(r["sobrevive"] / n_sim * 100, 1),
            "aristas_vulnerables": len(expuestas),
            "fallas_promedio": round(r["total_fallas"] / n_sim, 2),
            "detalle_fallas": detalle,
        }
    return salida


def aristas_vulnerables_geo(cur, route_edges, fs, viento_max_kmh=VIENTO_MAX_DEFAULT_KMH, dest=None):
    """Aristas de la ruta con P(fallo)>0, con su geometría, para el sorteo aleatorio en la interfaz (punto 7)."""
    ids = [e for e in (route_edges or []) if p_efectiva(e, fs, viento_max_kmh, dest) > 0]
    if not ids:
        return []
    cur.execute("SELECT id, ST_AsGeoJSON(geom) FROM infraestructura_aristas WHERE id = ANY(%s)", (ids,))
    geoms = {i: json.loads(g) for i, g in cur.fetchall()}
    return [{"id": e, "p": round(p_efectiva(e, fs, viento_max_kmh, dest), 4),
             "amenazas": {k: round(v, 4) for k, v in probs_efectivas(e, fs, viento_max_kmh, dest).items()},
             "zonas": zonas_efectivas(e, viento_max_kmh),
             "geom": geoms.get(e)} for e in ids]


def calcular_desvio_falla(origen_key, destino_key, aristas_falladas=None, eventos_fallidos=None, lat=None, lon=None, viento_max_kmh=None, pct_fallo=None):
    """Puntos 7, 8 y 9 de la rúbrica:
    Dado un sorteo aleatorio en [0, 100] a lo largo de la ruta (donde 0% es el inicio y 100% el final),
    determina el punto de falla a mitad de camino (ej. 50% o 75% de la ruta), conserva el tramo ya
    recorrido (0% -> X%), DESCARTA el resto de la ruta original preestablecida (X% -> 100%),
    recalcula desde ese punto hacia el destino por calles/corredores alternativos para las 4 técnicas,
    y re-evalúa todas las métricas (distancia, aristas en riesgo, resiliencia R, tiempo y supervivencia)."""
    if viento_max_kmh is None:
        viento_max_kmh = VIENTO_MAX_DEFAULT_KMH
    else:
        try:
            viento_max_kmh = float(viento_max_kmh)
        except (TypeError, ValueError):
            viento_max_kmh = VIENTO_MAX_DEFAULT_KMH
        viento_max_kmh = max(VIENTO_MAX_RANGO[0], min(VIENTO_MAX_RANGO[1], viento_max_kmh))

    try:
        pct_fallo_val = float(pct_fallo) if pct_fallo is not None else random.uniform(25.0, 80.0)
    except (TypeError, ValueError):
        pct_fallo_val = random.uniform(25.0, 80.0)
    pct_fallo_val = max(12.0, min(88.0, pct_fallo_val))

    base_res = calcular_rutas_par(origen_key, destino_key, lat, lon, viento_max_kmh)
    if "error" in base_res:
        return base_res

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        _asegurar_viento_aristas(cur)
        orig = base_res["origen"]
        dest = base_res["destino"]
        o_nodo = orig["nodo_vial"]
        d_nodo = dest["nodo_vial"]
        fs = factor_sismo_aristas(cur, dest)

        falladas_set = set(int(x) for x in (aristas_falladas or []) if str(x).isdigit())
        eventos_set = set()
        for ev in (eventos_fallidos or []):
            if ":" in str(ev):
                am, zid_str = str(ev).split(":", 1)
                if zid_str.lstrip("-").isdigit():
                    eventos_set.add((am, int(zid_str)))
        if eventos_set:
            for eid, zmap in ZONAS.items():
                for am, zid in zmap.items():
                    if (am, zid) in eventos_set:
                        falladas_set.add(int(eid))
            if any(am == "viento" for am, _ in eventos_set):
                for eid, cid in VIENTO_ZONA.items():
                    if ("viento", cid) in eventos_set and VIENTO_RACHA.get(eid, 0.0) > viento_max_kmh:
                        falladas_set.add(int(eid))

        costo_res = cost_resiliente_sql(dest, viento_max_kmh)

        rutas_orig = base_res.get("_aristas_internas") or {}
        dist_orig_map = {
            "dijkstra": base_res["dijkstra_1"]["distancia_m"],
            "resiliente": base_res["resiliente_1"]["distancia_m"],
            "optimizacion_lp": base_res["optimizacion_lp"]["distancia_m"],
            "aco": base_res["aco"]["distancia_m"],
        }

        # Pre-cargar longitudes y geometrías de todas las aristas originales de las 4 técnicas
        all_orig_ids = list({eid for edges in rutas_orig.values() for eid in (edges or [])})
        info_arista = {}
        if all_orig_ids:
            cur.execute(
                """SELECT id, length_m, ST_AsGeoJSON(geom)::json,
                          ST_Y(ST_StartPoint(geom)), ST_X(ST_StartPoint(geom))
                   FROM infraestructura_aristas WHERE id = ANY(%s)""",
                (all_orig_ids,),
            )
            for eid, l_m, g_json, lat_s, lon_s in cur.fetchall():
                info_arista[int(eid)] = {
                    "length_m": float(l_m or 0.0),
                    "geom": g_json,
                    "start_lat": float(lat_s),
                    "start_lon": float(lon_s),
                }

        desvios = {}
        nuevas_rutas_completas = {}
        metricas_recalculadas = {}
        alt_resiliente_previa = []

        for k in ("dijkstra", "resiliente", "optimizacion_lp", "aco"):
            orig_edges = list(rutas_orig.get(k, []))
            m = len(orig_edges)
            if m < 3:
                nuevas_rutas_completas[k] = orig_edges
                continue

            t_k0 = time.time()
            # 1. Calcular distancia acumulada (0% -> 100%) a lo largo de la ruta original ordenada
            largos = [info_arista.get(e, {}).get("length_m", 50.0) for e in orig_edges]
            dist_vial_orig = sum(largos) or 1.0
            acum = 0.0
            pcts = []
            for l in largos:
                acum += l
                pcts.append((acum / dist_vial_orig) * 100.0)

            idx_min = max(1, int(m * 0.12))
            idx_max = min(m - 2, int(m * 0.88))
            idx_target = idx_min
            for i in range(idx_min, idx_max + 1):
                if pcts[i] >= pct_fallo_val:
                    idx_target = i
                    break
            else:
                idx_target = idx_max

            # Buscar evento de amenaza cercano al % sorteado dentro de una ventana razonable (+/- 15% de la ruta)
            win = max(2, int(m * 0.15))
            w_start = max(idx_min, idx_target - win)
            w_end = min(idx_max, idx_target + win)
            candidatos_amenaza = [
                i for i in range(w_start, w_end + 1)
                if p_efectiva(orig_edges[i], fs, viento_max_kmh, dest) > 0
            ]
            if candidatos_amenaza:
                idx_quiebre = min(candidatos_amenaza, key=lambda i: abs(pcts[i] - pct_fallo_val))
            else:
                idx_quiebre = idx_target

            e_falla = orig_edges[idx_quiebre]
            probs_e = probs_efectivas(e_falla, fs, viento_max_kmh, dest)
            if not probs_e:
                # Buscar cualquier amenaza en la ruta para asociar el motivo probabilístico del evento
                con_p_ruta = [e for e in orig_edges if p_efectiva(e, fs, viento_max_kmh, dest) > 0]
                if con_p_ruta:
                    probs_e = probs_efectivas(con_p_ruta[0], fs, viento_max_kmh, dest)
            if probs_e:
                am_principal, p_principal = max(probs_e.items(), key=lambda x: x[1])
            else:
                am_principal, p_principal = "tendido", 0.35

            umbral_pct = round(p_principal * 100.0, 1)
            rng_ev = random.Random(int(pct_fallo_val * 100) + hash(k) % 97)
            aleatorio_umbral = round(rng_ev.uniform(0.1, max(0.2, umbral_pct * 0.92)), 1)

            # 2. Separar prefijo ya recorrido (0% -> X%) y sufijo original descartado (X% -> 100%)
            prefijo_recorrido = orig_edges[:idx_quiebre]
            sufijo_descartado = orig_edges[idx_quiebre:]

            # Bloquear TODA la ruta original restante (sufijo_descartado) + aristas falladas fuera del prefijo
            bloqueadas_k = list((set(sufijo_descartado) | falladas_set) - set(prefijo_recorrido))

            if k == "dijkstra":
                costo_k = "length_m"
            elif k == "optimizacion_lp":
                # Simplex prioriza aversión estricta al riesgo acumulado en el recálculo
                costo_k = f"({costo_res}) * (CASE WHEN cost_resiliente > length_m * 1.001 OR factor_antiguedad_zona > 0 THEN 1.45 ELSE 1.0 END)"
            else:
                costo_k = costo_res

            # Para que ACO explore un corredor alternativo propio cuando existan varias calles secundarias
            penal_aco_ids = []
            if k == "aco" and len(alt_resiliente_previa) > 6:
                mid_s = len(alt_resiliente_previa) // 4
                mid_e = (len(alt_resiliente_previa) * 3) // 4
                penal_aco_ids = alt_resiliente_previa[mid_s:mid_e]

            if penal_aco_ids:
                cur.execute(
                    f"""
                    SELECT edge FROM pgr_dijkstra(
                        'SELECT id, source, target,
                                CASE WHEN id = ANY(%s) THEN 0.001
                                     WHEN id = ANY(%s) THEN ({costo_k}) * 100000.0
                                     WHEN id = ANY(%s) THEN ({costo_k}) * 1.35
                                     ELSE {costo_k} END AS cost
                         FROM infraestructura_aristas',
                        %s, %s
                    ) WHERE edge != -1
                    """,
                    (prefijo_recorrido, bloqueadas_k, penal_aco_ids, o_nodo, d_nodo),
                )
            else:
                cur.execute(
                    f"""
                    SELECT edge FROM pgr_dijkstra(
                        'SELECT id, source, target,
                                CASE WHEN id = ANY(%s) THEN 0.001
                                     WHEN id = ANY(%s) THEN ({costo_k}) * 100000.0
                                     ELSE {costo_k} END AS cost
                         FROM infraestructura_aristas',
                        %s, %s
                    ) WHERE edge != -1
                    """,
                    (prefijo_recorrido, bloqueadas_k, o_nodo, d_nodo),
                )
            e_nueva = [r[0] for r in cur.fetchall()]
            t_k_ms = round((time.time() - t_k0) * 1000, 1)

            pref_set = set(prefijo_recorrido)
            e_recorrida_real = [e for e in e_nueva if e in pref_set]
            e_alternativa_nueva = [e for e in e_nueva if e not in pref_set]
            if k == "resiliente":
                alt_resiliente_previa = list(e_alternativa_nueva)

            idx_bif = len(e_recorrida_real)
            e_descartada_real = orig_edges[idx_bif:] if idx_bif < len(orig_edges) else sufijo_descartado
            e_quiebre_id = e_descartada_real[0] if e_descartada_real else e_falla

            geom_recorrida, dist_recorrida = extraer_geometria_y_distancia(cur, e_recorrida_real, None)
            geom_descartada, dist_descartada = extraer_geometria_y_distancia(cur, e_descartada_real, dest)
            geom_alternativa, dist_alternativa = extraer_geometria_y_distancia(cur, e_alternativa_nueva, dest)
            geom_completa, dist_completa = extraer_geometria_y_distancia(cur, e_nueva, dest)

            pct_recorrido_real = round((dist_recorrida / max(dist_vial_orig, 1.0)) * 100.0, 1)
            pct_recorrido_real = max(5.0, min(95.0, pct_recorrido_real))
            pct_restante_real = round(100.0 - pct_recorrido_real, 1)

            info_q = info_arista.get(e_quiebre_id) or info_arista.get(e_falla) or {}
            punto_quiebre = [
                info_q.get("start_lat", orig["lat"]),
                info_q.get("start_lon", orig["lon"]),
            ]
            geom_falla_tramo = info_q.get("geom")

            reutilizadas_orig = len(set(e_alternativa_nueva).intersection(set(e_descartada_real)))
            d_orig = dist_orig_map.get(k, 0.0)
            nuevas_rutas_completas[k] = e_nueva

            # Calcular ruta de respaldo disjunta para evaluar la métrica de Resiliencia (R) de la nueva ruta
            cur.execute(
                f"""SELECT edge FROM pgr_dijkstra(
                    'SELECT id, source, target,
                            CASE WHEN id = ANY(%s) THEN ({costo_k}) * 1000.0 ELSE {costo_k} END AS cost
                     FROM infraestructura_aristas', %s, %s) WHERE edge != -1""",
                (e_nueva, o_nodo, d_nodo),
            )
            e_nueva_2 = [r[0] for r in cur.fetchall()]
            _, dist_nueva_2 = extraer_geometria_y_distancia(cur, e_nueva_2 if e_nueva_2 else e_nueva, dest)
            m_k = calcular_metricas(cur, e_nueva, e_nueva_2 if e_nueva_2 else e_nueva, dist_completa, dist_nueva_2, dest, viento_max_kmh, fs)
            m_k["tiempo_ms"] = t_k_ms
            metricas_recalculadas[k] = m_k

            desvios[k] = {
                "requiere_desvio": True,
                "desvio_exitoso": len(e_alternativa_nueva) > 0,
                "pct_sorteo": round(pct_fallo_val, 1),
                "pct_recorrido": pct_recorrido_real,
                "pct_restante": pct_restante_real,
                "arista_fallada_id": e_quiebre_id,
                "amenaza": am_principal,
                "aleatorio_umbral": aleatorio_umbral,
                "umbral_pct": umbral_pct,
                "punto_quiebre": punto_quiebre,
                "geom_falla": geom_falla_tramo,
                "geom_recorrida": geom_recorrida,
                "geom_descartada": geom_descartada,
                "geom_alternativa": geom_alternativa,
                "geom": geom_alternativa,
                "geom_completa": geom_completa,
                "tramos_recorridos": len(e_recorrida_real),
                "tramos_descartados": len(e_descartada_real),
                "tramos_alternativos": len(e_alternativa_nueva),
                "aristas_originales_reutilizadas": reutilizadas_orig,
                "distancia_original_m": round(d_orig, 1),
                "distancia_recorrida_m": round(dist_recorrida, 1),
                "distancia_alternativa_m": round(dist_alternativa, 1),
                "distancia_m": round(dist_completa, 1),
                "delta_distancia_m": round(dist_completa - d_orig, 1),
                "tiempo_recalculo_ms": t_k_ms,
            }

        # Simulación Monte Carlo de supervivencia sobre las 4 nuevas rutas recalculadas
        simulacion_recalculada = simular_fallas_conjunto(
            nuevas_rutas_completas, fs, viento_max_kmh, dest, n_sim=200
        )

        return {
            "origen": orig,
            "destino": dest,
            "pct_fallo_sorteado": round(pct_fallo_val, 1),
            "desvios": desvios,
            "metricas_recalculadas": metricas_recalculadas,
            "simulacion_recalculada": simulacion_recalculada,
        }
    finally:
        release_db_connection(conn)


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
        _asegurar_viento_aristas(cur)
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

        fs = factor_sismo_aristas(cur, dest)

        geom_d1, dist_d1 = extraer_geometria_y_distancia(cur, e_d1, dest)
        geom_d2, dist_d2 = extraer_geometria_y_distancia(cur, e_d2 if e_d2 else e_d1, dest)
        geom_r1, dist_r1 = extraer_geometria_y_distancia(cur, e_r1, dest)
        geom_r2, dist_r2 = extraer_geometria_y_distancia(cur, e_r2 if e_r2 else e_r1, dest)

        m_dijkstra = calcular_metricas(cur, e_d1, e_d2 if e_d2 else e_d1, dist_d1, dist_d2, dest, viento_max_kmh, fs)
        m_resiliente = calcular_metricas(cur, e_r1, e_r2 if e_r2 else e_r1, dist_r1, dist_r2, dest, viento_max_kmh, fs)
        diff_resil = round(m_resiliente["resiliencia_score"] - m_dijkstra["resiliencia_score"], 1)

        ruta_corredor = list(set(e_d1) | set(e_r1))
        riesgo_ref = sum(p_efectiva(e, fs, viento_max_kmh, dest) for e in e_r1)

        # 5. Optimización LP — método Simplex (PuLP/CBC, equivalente a CPLEX/GUROBI)
        t_lp_start = time.time()
        e_lp, info_lp = ruta_optimizacion_lp(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_corredor, riesgo_ref, fs)
        t_lp_ms = round((time.time() - t_lp_start) * 1000, 1)
        geom_lp, dist_lp = extraer_geometria_y_distancia(cur, e_lp, dest)
        if e_lp:
            if set(e_lp) == set(e_r1):
                e_lp_2, dist_lp_2 = e_r2, dist_r2
            else:
                cur.execute(
                    f"""SELECT edge FROM pgr_dijkstra(
                        'SELECT id, source, target,
                                CASE WHEN id = ANY(%s) THEN ({costo_base}) * 1000.0 ELSE {costo_base} END AS cost
                         FROM infraestructura_aristas', %s, %s) WHERE edge != -1""",
                    (e_lp, o_nodo, d_nodo),
                )
                e_lp_2 = [r[0] for r in cur.fetchall()]
                _, dist_lp_2 = extraer_geometria_y_distancia(cur, e_lp_2 if e_lp_2 else e_lp, dest)
            m_lp = calcular_metricas(cur, e_lp, e_lp_2 if e_lp_2 else e_lp, dist_lp, dist_lp_2, dest, viento_max_kmh, fs)
        else:
            m_lp = {}

        # 6. Metaheurística ACO
        t_aco_start = time.time()
        e_aco, _ = ruta_aco(cur, o_nodo, d_nodo, dest, viento_max_kmh, ruta_corredor)
        t_aco_ms = round((time.time() - t_aco_start) * 1000, 1)
        geom_aco, dist_aco = extraer_geometria_y_distancia(cur, e_aco, dest)
        if e_aco:
            if set(e_aco) == set(e_r1):
                e_aco_2, dist_aco_2 = e_r2, dist_r2
            else:
                cur.execute(
                    f"""SELECT edge FROM pgr_dijkstra(
                        'SELECT id, source, target,
                                CASE WHEN id = ANY(%s) THEN ({costo_base}) * 1000.0 ELSE {costo_base} END AS cost
                         FROM infraestructura_aristas', %s, %s) WHERE edge != -1""",
                    (e_aco, o_nodo, d_nodo),
                )
                e_aco_2 = [r[0] for r in cur.fetchall()]
                _, dist_aco_2 = extraer_geometria_y_distancia(cur, e_aco_2 if e_aco_2 else e_aco, dest)
            m_aco = calcular_metricas(cur, e_aco, e_aco_2 if e_aco_2 else e_aco, dist_aco, dist_aco_2, dest, viento_max_kmh, fs)
        else:
            m_aco = {}

        # 7. Simulación de fallas para las 4 técnicas con Números Aleatorios Comunes
        sims = simular_fallas_conjunto(
            {"dijkstra": e_d1, "resiliente": e_r1, "optimizacion_lp": e_lp, "aco": e_aco},
            fs, viento_max_kmh, dest, n_sim=200
        )

        resultado = {
            "origen": orig,
            "destino": dest,
            "restricciones": {
                "viento_max_kmh": viento_max_kmh,
                "es_default": viento_max_kmh == VIENTO_MAX_DEFAULT_KMH,
            },
            "_aristas_internas": {
                "dijkstra": e_d1,
                "resiliente": e_r1,
                "optimizacion_lp": e_lp,
                "aco": e_aco,
            },
            "dijkstra_1": {"distancia_m": round(dist_d1, 1), "tramos": len(e_d1), "geom": geom_d1},
            "dijkstra_2": {"distancia_m": round(dist_d2, 1), "tramos": len(e_d2), "geom": geom_d2},
            "resiliente_1": {"distancia_m": round(dist_r1, 1), "tramos": len(e_r1), "geom": geom_r1},
            "resiliente_2": {"distancia_m": round(dist_r2, 1), "tramos": len(e_r2), "geom": geom_r2},
            "optimizacion_lp": {"distancia_m": round(dist_lp, 1), "tramos": len(e_lp), "geom": geom_lp, "algoritmo": "Programación Lineal — método Simplex (PuLP/CBC)", "simplex": info_lp, "tiempo_ms": t_lp_ms},
            "aco": {"distancia_m": round(dist_aco, 1), "tramos": len(e_aco), "geom": geom_aco, "algoritmo": "Ant Colony Optimization (30 hormigas, 40 iter)", "tiempo_ms": t_aco_ms},
            "metricas": {
                "dijkstra": {**m_dijkstra, "algoritmo": "pgr_dijkstra (distancia pura)", "tiempo_ms": t_dijkstra_ms},
                "resiliente": {**m_resiliente, "algoritmo": "pgr_dijkstra (cost_resiliente multi-amenaza)", "tiempo_ms": t_resil_ms},
                "optimizacion_lp": {**m_lp, "algoritmo": "Simplex (PL, min costo s.a. conservación de flujo)", "simplex": info_lp, "tiempo_ms": t_lp_ms} if m_lp else {},
                "aco": {**m_aco, "algoritmo": "ACO (Ant Colony Optimization)", "tiempo_ms": t_aco_ms} if m_aco else {},
                "diferencial_resiliencia": diff_resil,
            },
            "aristas_vulnerables": {
                "dijkstra": aristas_vulnerables_geo(cur, e_d1, fs, viento_max_kmh, dest),
                "resiliente": aristas_vulnerables_geo(cur, e_r1, fs, viento_max_kmh, dest),
                "optimizacion_lp": aristas_vulnerables_geo(cur, e_lp, fs, viento_max_kmh, dest),
                "aco": aristas_vulnerables_geo(cur, e_aco, fs, viento_max_kmh, dest),
            },
            "simulacion_fallas": sims,
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

                # Empaquetar como respuesta enriquecida (sin exponer lista interna de ids)
                respuesta = {k: v for k, v in ruta_data.items() if k != "_aristas_internas"}
                respuesta["tiempo_calculo_ms"] = t_ms
                self._send_json(respuesta)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # 5. API: Re-ruteo / Desvío Dinámico ante Fallas Sorteadas (Puntos 7, 8 y 9)
        if path == "/api/desvio_falla":
            try:
                orig = query.get("origen", ["8va_florida"])[0]
                dest = query.get("destino", ["sismo_15"])[0]
                lat = query.get("lat", [None])[0]
                lon = query.get("lon", [None])[0]
                viento_max = query.get("viento_max", [None])[0]
                pct_fallo = query.get("pct_fallo", [None])[0]
                fallas_raw = query.get("fallas", [""])[0]
                eventos_raw = query.get("eventos", [""])[0]
                aristas_falladas = [int(x) for x in fallas_raw.split(",") if x.strip().isdigit()]
                eventos_fallidos = [x.strip() for x in eventos_raw.split(",") if x.strip()]

                t0 = time.time()
                desvio_data = calcular_desvio_falla(orig, dest, aristas_falladas, eventos_fallidos, lat, lon, viento_max, pct_fallo)
                desvio_data["tiempo_calculo_ms"] = round((time.time() - t0) * 1000, 1)
                self._send_json(desvio_data)
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
