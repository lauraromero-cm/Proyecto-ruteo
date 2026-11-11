"""Calibración de pesos de cost_resiliente usando la SUPERVIVENCIA como objetivo.

La métrica R usa la misma función de costo que el ruteo, así que premia lo que el router ya
optimiza. La supervivencia es independiente: P(ninguna amenaza ocurre en la ruta) =
Π (1 - P_evento) sobre los eventos distintos que cruza la ruta (las amenazas espaciales se
cuentan una sola vez por zona, igual que en la simulación). Es el valor exacto al que converge
el Monte Carlo del sitio, sin ruido de muestreo.

Se barren peso_tendido y peso_ignicion (los pesos de áreas protegidas y FIRMS quedan en x1000;
peso_antiguedad fijo). Criterio: maximizar la supervivencia promedio de la ruta resiliente
sujeto a que la distancia extra promedio frente a Dijkstra sea <= MAX_EXTRA_PCT.
"""
import itertools
import json
import math
import os
import time

import psycopg2

AQUI = os.path.dirname(os.path.abspath(__file__))
CONN = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "ruteo_drones"),
    user=os.environ.get("DB_USER", "ruteo"),
    password=os.environ.get("DB_PASSWORD", "ruteo"),
)

VIENTO_MAX_DEFAULT_KMH = 43.2
PESO_ANTIGUEDAD = 0.3
MAX_EXTRA_PCT = 15.0
N_ORIGENES = 4

PESOS_TENDIDO = [1.2, 2.0, 5.0, 20.0, 100.0]
PESOS_IGNICION = [1.1, 1.5, 2.0, 3.0, 5.0]

MMI_A, MMI_B, MMI_C = 0.9, 1.5, 3.5
MMI_DANO_INICIO, MMI_DANO_TOTAL = 5.0, 9.0

with open(os.path.join(AQUI, "data", "probabilidad_fallo.json"), encoding="utf-8") as f:
    _j = json.load(f)
PROB = {int(k): v for k, v in _j["aristas"].items()}
ZONAS = {int(k): v for k, v in _j["zonas"].items()}

conn = psycopg2.connect(**CONN)
cur = conn.cursor()


def ids_de(sql):
    cur.execute(sql)
    return cur.fetchone()[0] or []


print("Identificando aristas afectadas por cada amenaza...")
ids_tendido = ids_de("""SELECT ARRAY_AGG(DISTINCT a.id) FROM infraestructura_aristas a
    JOIN metadata_tendido_electrico t ON a.geom && ST_Expand(t.geom, 0.0002) AND ST_DWithin(a.geom, t.geom, 0.0002)""")
ids_ignicion = ids_de("""SELECT ARRAY_AGG(DISTINCT a.id) FROM infraestructura_aristas a
    JOIN amenaza_conaf_ignicion i ON a.geom && i.geom AND ST_Intersects(a.geom, i.geom)""")
ids_asp = ids_de("""SELECT ARRAY_AGG(DISTINCT a.id) FROM infraestructura_aristas a
    JOIN amenaza_conaf_areas_protegidas asp ON a.geom && asp.geom AND ST_Intersects(a.geom, asp.geom)""")
ids_firms = ids_de("""SELECT ARRAY_AGG(DISTINCT a.id) FROM infraestructura_aristas a
    JOIN amenaza_firms f ON f.dentro_chile AND a.geom && ST_Expand(f.geom, 0.005) AND ST_DWithin(a.geom, f.geom, 0.005)""")

cur.execute("""SELECT nodo_vial_id FROM nodo_carabineros
    WHERE es_comisaria OR nombre IN ('SUBCOM. CONCON', 'RETEN PLACILLA PENUELAS', 'RETEN LAGUNA VERDE')
    ORDER BY comuna, nombre""")
todos = [r[0] for r in cur.fetchall() if r[0]]
paso = max(1, len(todos) // N_ORIGENES)
ORIGENES = todos[::paso][:N_ORIGENES]

cur.execute("""
    SELECT s.id, s.magnitud, s.profundidad_km, ST_X(s.geom), ST_Y(s.geom)
    FROM amenaza_sismos s
    CROSS JOIN LATERAL (SELECT ST_Distance(s.geom::geography, n.geom::geography) AS d
        FROM infraestructura_nodos n ORDER BY n.geom <-> s.geom LIMIT 1) dr
    WHERE (dr.d / 1000.0) <= distancia_max_valida_sismo_km(s.magnitud, viento_racha_interpolada_kmh(s.geom))
    ORDER BY s.magnitud DESC LIMIT 10""")
DESTINOS = []
ids_sismo = [e for e, d in PROB.items() if "sismo_edificacion" in d]
for sid, mag, prof, lon, lat in cur.fetchall():
    cur.execute("SELECT id FROM infraestructura_nodos ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1", (lon, lat))
    nodo = cur.fetchone()[0]
    cur.execute("""SELECT id, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) / 1000.0
                   FROM infraestructura_aristas WHERE id = ANY(%s)""", (lon, lat, ids_sismo))
    fs = {}
    for eid, d_km in cur.fetchall():
        r = max((d_km ** 2 + float(prof or 10.0) ** 2) ** 0.5, 1.0)
        mmi = MMI_A + MMI_B * float(mag) - MMI_C * math.log10(r)
        fs[eid] = max(0.0, min(1.0, (mmi - MMI_DANO_INICIO) / (MMI_DANO_TOTAL - MMI_DANO_INICIO)))
    DESTINOS.append({"tipo": "sismo", "nodo": nodo, "fs": fs})
cur.execute("SELECT ST_X(geom), ST_Y(geom) FROM amenaza_firms WHERE dentro_chile")
for lon, lat in cur.fetchall():
    cur.execute("SELECT id FROM infraestructura_nodos ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1", (lon, lat))
    DESTINOS.append({"tipo": "incendio", "nodo": cur.fetchone()[0], "fs": {}})

PARES = [(o, d) for o in ORIGENES for d in DESTINOS if o != d["nodo"]]
print(f"{len(ORIGENES)} orígenes x {len(DESTINOS)} destinos = {len(PARES)} pares")


def supervivencia(ruta, fs):
    eventos = {}
    for eid in ruta:
        for am, p in PROB.get(eid, {}).items():
            if am == "p_total":
                continue
            if am == "sismo_edificacion":
                p = p * fs.get(eid, 0.0)
            if p <= 0:
                continue
            zid = ZONAS.get(eid, {}).get(am)
            eventos[(am, zid if zid is not None else ("e", eid))] = p
    s = 1.0
    for p in eventos.values():
        s *= 1.0 - p
    return s


def ruta_dijkstra(costo_sql, o, d):
    cur.execute(
        f"SELECT edge FROM pgr_dijkstra('SELECT id, source, target, {costo_sql} AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
        (o, d))
    return [r[0] for r in cur.fetchall()]


def largo(ruta):
    cur.execute("SELECT coalesce(sum(length_m), 0) FROM infraestructura_aristas WHERE id = ANY(%s)", (ruta,))
    return float(cur.fetchone()[0])


def cost_sql(dest):
    base = "cost_resiliente"
    if dest["tipo"] == "sismo":
        base = f"({base} * (1 + {PESO_ANTIGUEDAD} * factor_antiguedad_zona))"
    return f"(CASE WHEN viento_racha_kmh > {VIENTO_MAX_DEFAULT_KMH} THEN ({base}) * 1000.0 ELSE {base} END)"


# Dijkstra por distancia no depende de los pesos: se calcula una sola vez.
print("Calculando línea base (Dijkstra por distancia)...")
BASE = {}
for o, d in PARES:
    e = ruta_dijkstra("length_m", o, d["nodo"])
    BASE[(o, d["nodo"])] = (e, supervivencia(e, d["fs"]), largo(e))
surv_base = sum(v[1] for v in BASE.values()) / len(BASE)
print(f"Supervivencia promedio Dijkstra: {surv_base * 100:.2f}%")


def evaluar(peso_tendido, peso_ignicion):
    cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = length_m")
    for ids, w in ((ids_tendido, peso_tendido), (ids_ignicion, peso_ignicion), (ids_asp, 1000.0), (ids_firms, 1000.0)):
        if ids:
            cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * %s WHERE id = ANY(%s)", (w, ids))
    survs, extras = [], []
    for o, d in PARES:
        e = ruta_dijkstra(cost_sql(d), o, d["nodo"])
        survs.append(supervivencia(e, d["fs"]))
        _, _, l_base = BASE[(o, d["nodo"])]
        if l_base > 0:
            extras.append((largo(e) - l_base) / l_base * 100.0)
    conn.rollback()
    return sum(survs) / len(survs) * 100.0, sum(extras) / len(extras)


combos = list(itertools.product(PESOS_TENDIDO, PESOS_IGNICION))
print(f"\nProbando {len(combos)} combinaciones (tendido x ignición)...\n")
print(f"{'#':>3} {'Tendido':>8} {'Ignición':>9} {'Superv. %':>10} {'Dist extra %':>13}")
res = []
t0 = time.time()
for i, (pt, pi) in enumerate(combos, 1):
    s, x = evaluar(pt, pi)
    res.append({"peso_tendido": pt, "peso_ignicion": pi, "supervivencia_pct": round(s, 2), "distancia_extra_pct": round(x, 2)})
    print(f"{i:3d} {pt:8.1f} {pi:9.1f} {s:10.2f} {x:13.2f}   ({time.time() - t0:.0f}s)", flush=True)

validas = [r for r in res if r["distancia_extra_pct"] <= MAX_EXTRA_PCT]
validas.sort(key=lambda r: -r["supervivencia_pct"])
print(f"\nSupervivencia Dijkstra (línea base): {surv_base * 100:.2f}%")
print(f"Top combinaciones con distancia extra <= {MAX_EXTRA_PCT}%:")
for r in validas[:8]:
    print(f"  tendido={r['peso_tendido']:6.1f} ignición={r['peso_ignicion']:4.1f}  supervivencia={r['supervivencia_pct']:6.2f}%  extra={r['distancia_extra_pct']:5.2f}%")

with open(os.path.join(AQUI, "calibracion_pesos_supervivencia.json"), "w", encoding="utf-8") as f:
    json.dump({"supervivencia_dijkstra_pct": round(surv_base * 100, 2), "max_extra_pct": MAX_EXTRA_PCT, "resultados": res}, f, ensure_ascii=False, indent=2)
print("Guardado en calibracion_pesos_supervivencia.json")
