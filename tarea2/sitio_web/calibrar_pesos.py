"""Barrido de pesos para calibrar cost_resiliente.

Prueba combinaciones de:
  - peso_tendido (multiplicador por cruce con tendido eléctrico)
  - peso_ignicion (multiplicador por zona CONAF de ignición)
  - peso_antiguedad (factor condicional para sismos)

Los pesos fijos (áreas protegidas x1000, FIRMS x1000) no se tocan.
Métrica objetivo: resiliencia_score promedio sobre todos los pares origen-destino.
"""
import itertools
import json
import os
import sys
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

# --- Rangos a probar ---
PESOS_TENDIDO = [1.2, 1.5, 2.0, 3.0, 5.0]
PESOS_IGNICION = [1.1, 1.3, 1.5, 2.0, 3.0]
PESOS_ANTIGUEDAD = [0.1, 0.3, 0.5, 0.8, 1.0]

conn = psycopg2.connect(**CONN)
conn.autocommit = False
cur = conn.cursor()

# --- Precalcular sets de aristas afectadas (no cambian entre iteraciones) ---
print("Identificando aristas afectadas por cada amenaza...")

cur.execute("""
    SELECT ARRAY_AGG(DISTINCT a.id)
    FROM infraestructura_aristas a
    JOIN metadata_tendido_electrico t ON a.geom && ST_Expand(t.geom, 0.0002) AND ST_DWithin(a.geom, t.geom, 0.0002)
""")
ids_tendido = cur.fetchone()[0] or []

cur.execute("""
    SELECT ARRAY_AGG(DISTINCT a.id)
    FROM infraestructura_aristas a
    JOIN amenaza_conaf_ignicion i ON a.geom && i.geom AND ST_Intersects(a.geom, i.geom)
""")
ids_ignicion = cur.fetchone()[0] or []

cur.execute("""
    SELECT ARRAY_AGG(DISTINCT a.id)
    FROM infraestructura_aristas a
    JOIN amenaza_conaf_areas_protegidas asp ON a.geom && asp.geom AND ST_Intersects(a.geom, asp.geom)
""")
ids_asp = cur.fetchone()[0] or []

cur.execute("""
    SELECT ARRAY_AGG(DISTINCT a.id)
    FROM infraestructura_aristas a
    JOIN amenaza_firms f ON f.dentro_chile AND a.geom && ST_Expand(f.geom, 0.005) AND ST_DWithin(a.geom, f.geom, 0.005)
""")
ids_firms = cur.fetchone()[0] or []

print(f"  Tendido eléctrico: {len(ids_tendido)} aristas")
print(f"  CONAF ignición:    {len(ids_ignicion)} aristas")
print(f"  Áreas protegidas:  {len(ids_asp)} aristas")
print(f"  FIRMS activos:     {len(ids_firms)} aristas")

# --- Obtener orígenes y destinos ---
cur.execute("""
    SELECT id, nombre, comuna, es_comisaria, ST_X(geom) lon, ST_Y(geom) lat, nodo_vial_id
    FROM nodo_carabineros
    WHERE es_comisaria OR nombre IN ('SUBCOM. CONCON', 'RETEN PLACILLA PENUELAS', 'RETEN LAGUNA VERDE')
    ORDER BY comuna, nombre;
""")
ORIGENES = []
for r in cur.fetchall():
    ORIGENES.append({"id": r[0], "nombre": r[1], "nodo_vial": r[6]})

cur.execute("""
    SELECT s.id, s.magnitud, s.lugar, ST_X(s.geom) lon, ST_Y(s.geom) lat
    FROM amenaza_sismos s
    CROSS JOIN LATERAL (
        SELECT ST_Distance(s.geom::geography, n.geom::geography) AS d
        FROM infraestructura_nodos n ORDER BY n.geom <-> s.geom LIMIT 1
    ) dist_red
    CROSS JOIN LATERAL (
        SELECT viento_racha_interpolada_kmh(s.geom) AS racha_media_kmh
    ) v
    WHERE (dist_red.d / 1000.0) <= distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)
    ORDER BY s.magnitud DESC LIMIT 10;
""")
DESTINOS_SISMO = []
for r in cur.fetchall():
    cur.execute("SELECT id FROM infraestructura_nodos ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1", (r[3], r[4]))
    nodo = cur.fetchone()[0]
    DESTINOS_SISMO.append({"id": r[0], "tipo": "sismo", "magnitud": float(r[1]), "nodo_vial": nodo})

cur.execute("SELECT id, ST_X(geom) lon, ST_Y(geom) lat FROM amenaza_firms WHERE dentro_chile;")
DESTINOS_FIRMS = []
for r in cur.fetchall():
    cur.execute("SELECT id FROM infraestructura_nodos ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) LIMIT 1", (r[1], r[2]))
    nodo = cur.fetchone()[0]
    DESTINOS_FIRMS.append({"id": r[0], "tipo": "incendio", "nodo_vial": nodo})

DESTINOS = DESTINOS_SISMO + DESTINOS_FIRMS
print(f"\n{len(ORIGENES)} orígenes x {len(DESTINOS)} destinos = {len(ORIGENES) * len(DESTINOS)} pares")


def recalcular_cost_resiliente(peso_tendido, peso_ignicion):
    cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = length_m;")
    if ids_tendido:
        cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * %s WHERE id = ANY(%s);",
                     (peso_tendido, ids_tendido))
    if ids_ignicion:
        cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * %s WHERE id = ANY(%s);",
                     (peso_ignicion, ids_ignicion))
    if ids_asp:
        cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * 1000.0 WHERE id = ANY(%s);",
                     (ids_asp,))
    if ids_firms:
        cur.execute("UPDATE infraestructura_aristas SET cost_resiliente = cost_resiliente * 1000.0 WHERE id = ANY(%s);",
                     (ids_firms,))


def cost_sql(dest, peso_antiguedad):
    base = "cost_resiliente"
    if dest.get("tipo") == "sismo":
        base = f"({base} * (1 + {peso_antiguedad} * factor_antiguedad_zona))"
    return (
        f"(CASE WHEN viento_racha_kmh > {VIENTO_MAX_DEFAULT_KMH} "
        f"THEN ({base}) * 1000.0 ELSE {base} END)"
    )


def costo_base_sql(dest, peso_antiguedad):
    if dest.get("tipo") == "sismo":
        return f"(length_m * (1 + {peso_antiguedad} * factor_antiguedad_zona))"
    return "length_m"


def evaluar_combinacion(peso_tendido, peso_ignicion, peso_antiguedad):
    recalcular_cost_resiliente(peso_tendido, peso_ignicion)

    scores = []
    distancias_extra_pct = []

    for orig in ORIGENES:
        for dest in DESTINOS:
            o_nodo, d_nodo = orig["nodo_vial"], dest["nodo_vial"]
            if not o_nodo or not d_nodo or o_nodo == d_nodo:
                continue

            # Dijkstra puro (distancia)
            cur.execute(
                "SELECT edge FROM pgr_dijkstra('SELECT id, source, target, length_m AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
                (o_nodo, d_nodo))
            e_d1 = [r[0] for r in cur.fetchall()]

            cur.execute(
                "SELECT edge FROM pgr_dijkstra('SELECT id, source, target, CASE WHEN id = ANY(%s) THEN length_m * 1000.0 ELSE length_m END AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
                (e_d1, o_nodo, d_nodo))
            e_d2 = [r[0] for r in cur.fetchall()]

            # Resiliente
            costo = cost_sql(dest, peso_antiguedad)
            cur.execute(
                f"SELECT edge FROM pgr_dijkstra('SELECT id, source, target, {costo} AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
                (o_nodo, d_nodo))
            e_r1 = [r[0] for r in cur.fetchall()]

            cur.execute(
                f"SELECT edge FROM pgr_dijkstra('SELECT id, source, target, CASE WHEN id = ANY(%s) THEN ({costo}) * 1000.0 ELSE {costo} END AS cost FROM infraestructura_aristas', %s, %s) WHERE edge != -1",
                (e_r1, o_nodo, d_nodo))
            e_r2 = [r[0] for r in cur.fetchall()]

            # Métricas
            s1, s2 = set(e_r1), set(e_r2 if e_r2 else e_r1)
            union = s1.union(s2)
            inter = s1.intersection(s2)
            disyuncion = 1.0 - (len(inter) / len(union)) if union else 0.0

            cur.execute(
                f"""SELECT coalesce(sum(length_m * LEAST(1.0, LN(GREATEST(1.0, ({cost_sql(dest, peso_antiguedad)}) / ({costo_base_sql(dest, peso_antiguedad)}))) / LN(1000.0))), 0),
                           coalesce(sum(length_m), 0)
                    FROM infraestructura_aristas WHERE id = ANY(%s)""",
                (list(union),))
            exposicion, suma_len = cur.fetchone()
            factor_seg = 1.0 - float(exposicion) / float(suma_len) if suma_len else 1.0
            score = disyuncion * factor_seg * 100.0
            scores.append(score)

            # Distancia extra: cuánto más recorre la ruta resiliente vs Dijkstra
            cur.execute("SELECT coalesce(sum(length_m), 0) FROM infraestructura_aristas WHERE id = ANY(%s)", (e_d1,))
            dist_dij = cur.fetchone()[0]
            cur.execute("SELECT coalesce(sum(length_m), 0) FROM infraestructura_aristas WHERE id = ANY(%s)", (e_r1,))
            dist_res = cur.fetchone()[0]
            if dist_dij > 0:
                distancias_extra_pct.append((float(dist_res) - float(dist_dij)) / float(dist_dij) * 100.0)

    conn.rollback()  # deshacer cambios a cost_resiliente

    avg_score = sum(scores) / len(scores) if scores else 0.0
    avg_extra = sum(distancias_extra_pct) / len(distancias_extra_pct) if distancias_extra_pct else 0.0
    return round(avg_score, 2), round(avg_extra, 2)


# --- Barrido ---
combos = list(itertools.product(PESOS_TENDIDO, PESOS_IGNICION, PESOS_ANTIGUEDAD))
print(f"\nProbando {len(combos)} combinaciones de pesos...\n")
print(f"{'#':>4}  {'Tendido':>8}  {'Ignición':>9}  {'Antigüedad':>11}  {'Resiliencia':>12}  {'Dist Extra %':>12}")
print("-" * 68)

resultados = []
t0 = time.time()

for i, (pt, pi, pa) in enumerate(combos, 1):
    avg_score, avg_extra = evaluar_combinacion(pt, pi, pa)
    resultados.append({
        "peso_tendido": pt,
        "peso_ignicion": pi,
        "peso_antiguedad": pa,
        "resiliencia_promedio": avg_score,
        "distancia_extra_pct": avg_extra,
    })
    print(f"{i:4d}  {pt:8.1f}  {pi:9.1f}  {pa:11.1f}  {avg_score:12.2f}  {avg_extra:12.2f}")

    if i % 25 == 0:
        elapsed = time.time() - t0
        eta = elapsed / i * (len(combos) - i)
        print(f"  ... {i}/{len(combos)} ({elapsed:.0f}s, ETA {eta:.0f}s)")

# --- Resultados ---
resultados.sort(key=lambda r: r["resiliencia_promedio"], reverse=True)

print(f"\n{'=' * 68}")
print("TOP 10 COMBINACIONES (mayor resiliencia promedio):\n")
print(f"{'#':>4}  {'Tendido':>8}  {'Ignición':>9}  {'Antigüedad':>11}  {'Resiliencia':>12}  {'Dist Extra %':>12}")
print("-" * 68)
for i, r in enumerate(resultados[:10], 1):
    print(f"{i:4d}  {r['peso_tendido']:8.1f}  {r['peso_ignicion']:9.1f}  {r['peso_antiguedad']:11.1f}  {r['resiliencia_promedio']:12.2f}  {r['distancia_extra_pct']:12.2f}")

print(f"\nTiempo total: {time.time() - t0:.1f}s")

path_out = os.path.join(AQUI, "calibracion_pesos.json")
with open(path_out, "w", encoding="utf-8") as f:
    json.dump(resultados, f, ensure_ascii=False, indent=2)
print(f"Resultados guardados en {path_out}")
