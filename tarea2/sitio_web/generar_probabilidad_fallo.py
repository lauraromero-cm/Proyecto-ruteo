#!/usr/bin/env python3
"""
Genera probabilidad_fallo.json: P(fallo) por arista para cada amenaza.

Modelo de probabilidad por amenaza:
- Tendido eléctrico: P = 0.35 (interferencia electromagnética / riesgo colisión
  para dron BVLOS volando a baja altura cerca de cables de alta/media tensión;
  buffer de 20m según DAN 151 art.8).
- CONAF ignición: P = decil/100 (la propia capa ya codifica probabilidad de
  ignición en deciles 10-100, del modelo de predicción de CONAF).
- Áreas protegidas SNASPE: P = 0.95 (restricción legal DAN 151 / SNASPE;
  vuelo prohibido salvo autorización especial).
- FIRMS (focos activos): P = 0.80 (buffer 500m por humo/calor; un foco activo
  detectado por satélite implica alta probabilidad de obstrucción).
- Viento: P = max(0, (racha - umbral) / umbral) acotado a [0, 1]
  (a medida que la racha supera el umbral de operación del DJI M300 RTK,
  la probabilidad de que el dron no pueda operar crece linealmente).
- Sismo + edificación antigua: P = indice_antiguedad × 0.5 (el índice de
  antigüedad es proxy de vulnerabilidad estructural; ×0.5 porque es un proxy,
  no medición directa de daño).

Salida: sitio_web/data/probabilidad_fallo.json
  {
    "amenazas": ["tendido", "ignicion", "areas_protegidas", "firms", "viento", "sismo_edificacion"],
    "aristas": {
      "<id>": {"tendido": 0.35, "ignicion": 0.4, ...},
      ...
    },
    "resumen": { ... }
  }
"nodos": {"<id>": P_total del nodo} (máx. de sus aristas incidentes).
Solo incluye aristas con P > 0 en al menos una amenaza.
"""
import json
import os
import sys
import psycopg2

AQUI = os.path.dirname(os.path.abspath(__file__))
CONN = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "ruteo_drones"),
    user=os.environ.get("DB_USER", "ruteo"),
    password=os.environ.get("DB_PASSWORD", "ruteo"),
)

VIENTO_UMBRAL_KMH = 43.2

conn = psycopg2.connect(**CONN)
cur = conn.cursor()

prob = {}  # {arista_id: {amenaza: prob}}

# Eventos de amenaza compartidos: las amenazas espaciales (zona de ignición, área
# protegida, foco FIRMS, línea de tendido) son UN solo evento que afecta a todas las
# aristas que toca, no un evento independiente por arista. zonas[arista][amenaza] = id
# del evento; al simular, todas las aristas del mismo evento fallan o no juntas.
zonas = {}

def agregar(arista_id, amenaza, p, zona=None):
    if zona is not None:
        zonas.setdefault(int(arista_id), {})[amenaza] = int(zona)
    aid = int(arista_id)
    if aid not in prob:
        prob[aid] = {}
    prob[aid][amenaza] = round(max(prob[aid].get(amenaza, 0), p), 4)


# 1. Tendido eléctrico — P = 0.35 para aristas dentro de buffer 20m
print("Tendido eléctrico...")
cur.execute("""
    SELECT a.id, MIN(t.id)
    FROM infraestructura_aristas a
    JOIN metadata_tendido_electrico t
      ON a.geom && ST_Expand(t.geom, 0.0002) AND ST_DWithin(a.geom, t.geom, 0.0002)
    GROUP BY a.id
""")
cnt = 0
for aid, zid in cur.fetchall():
    agregar(aid, "tendido", 0.35, zid)
    cnt += 1
print(f"  {cnt} aristas afectadas")


# 2. CONAF ignición — P = decil/100
print("CONAF ignición...")
cur.execute("""
    SELECT DISTINCT ON (a.id) a.id, i.label, i.id
    FROM infraestructura_aristas a
    JOIN amenaza_conaf_ignicion i ON a.geom && i.geom AND ST_Intersects(a.geom, i.geom)
    ORDER BY a.id, i.label DESC
""")
cnt = 0
for aid, label, zid in cur.fetchall():
    p = min(1.0, float(label) / 100.0)
    agregar(aid, "ignicion", p, zid)
    cnt += 1
print(f"  {cnt} aristas afectadas")


# 3. Áreas protegidas SNASPE — P = 0.95
print("Áreas protegidas SNASPE...")
cur.execute("""
    SELECT DISTINCT ON (a.id) a.id, asp.id
    FROM infraestructura_aristas a
    JOIN amenaza_conaf_areas_protegidas asp ON a.geom && asp.geom AND ST_Intersects(a.geom, asp.geom)
    ORDER BY a.id, asp.id
""")
cnt = 0
for aid, zid in cur.fetchall():
    agregar(aid, "areas_protegidas", 0.95, zid)
    cnt += 1
print(f"  {cnt} aristas afectadas")


# 4. FIRMS focos activos — P = 0.80 dentro de buffer 500m
print("FIRMS focos activos...")
cur.execute("""
    SELECT DISTINCT ON (a.id) a.id, f.id
    FROM infraestructura_aristas a
    JOIN amenaza_firms f ON f.dentro_chile
      AND a.geom && ST_Expand(f.geom, 0.005)
      AND ST_DWithin(a.geom, f.geom, 0.005)
    ORDER BY a.id, f.id
""")
cnt = 0
for aid, zid in cur.fetchall():
    agregar(aid, "firms", 0.80, zid)
    cnt += 1
print(f"  {cnt} aristas afectadas")


# 5. Viento — P = max(0, (racha - umbral) / umbral), cap 1.0
print("Viento...")
cur.execute("""
    SELECT a.id, a.viento_racha_kmh
    FROM infraestructura_aristas a
    WHERE a.viento_racha_kmh > %s
""", (VIENTO_UMBRAL_KMH,))
cnt = 0
for aid, racha in cur.fetchall():
    p = min(1.0, (float(racha) - VIENTO_UMBRAL_KMH) / VIENTO_UMBRAL_KMH)
    if p > 0:
        agregar(aid, "viento", p)
        cnt += 1
print(f"  {cnt} aristas afectadas")


# 6. Sismo + edificación antigua — P = indice_antiguedad × 0.5
print("Sismo / edificación antigua...")
cur.execute("""
    SELECT DISTINCT ON (a.id) a.id, m.indice_antiguedad, m.id
    FROM infraestructura_aristas a
    JOIN metadata_poblacion m ON a.geom && m.geom AND ST_Intersects(a.geom, m.geom)
    WHERE m.indice_antiguedad > 0
    ORDER BY a.id, m.indice_antiguedad DESC
""")
cnt = 0
for aid, faz, zid in cur.fetchall():
    p = min(1.0, float(faz) * 0.5)
    if p > 0.01:
        agregar(aid, "sismo_edificacion", p, zid)
        cnt += 1
print(f"  {cnt} aristas afectadas")


# Consolidar: P_total = 1 - Π(1 - P_i) (probabilidad combinada independiente)
for aid in prob:
    ps = list(prob[aid].values())
    p_total = 1.0
    for p in ps:
        p_total *= (1.0 - p)
    prob[aid]["p_total"] = round(1.0 - p_total, 4)


# Nodos: P(nodo) = max de p_total de sus aristas incidentes. Un nodo (intersección)
# queda inutilizable si cualquiera de los tramos que concurren a él está bloqueado
# por la amenaza, por lo que hereda la peor probabilidad entre ellos.
cur.execute("SELECT id, source, target FROM infraestructura_aristas WHERE id = ANY(%s)", (list(prob.keys()),))
prob_nodos = {}
for aid, src, tgt in cur.fetchall():
    for n in (src, tgt):
        prob_nodos[n] = max(prob_nodos.get(n, 0.0), prob[aid]["p_total"])


# Resumen
amenazas = ["tendido", "ignicion", "areas_protegidas", "firms", "viento", "sismo_edificacion"]
resumen = {}
for am in amenazas:
    vals = [prob[a][am] for a in prob if am in prob[a]]
    resumen[am] = {
        "aristas_afectadas": len(vals),
        "prob_media": round(sum(vals) / len(vals), 4) if vals else 0,
        "prob_max": round(max(vals), 4) if vals else 0,
    }
resumen["total_aristas_con_riesgo"] = len(prob)

cur.execute("SELECT count(*) FROM infraestructura_aristas")
total_aristas = cur.fetchone()[0]
resumen["total_aristas_grafo"] = total_aristas
resumen["pct_aristas_con_riesgo"] = round(len(prob) / total_aristas * 100, 1)

# Convertir keys a string para JSON
salida = {
    "amenazas": amenazas,
    "modelo": {
        "tendido": "P=0.35 (buffer 20m, interferencia EM / riesgo colisión DAN 151)",
        "ignicion": "P=decil/100 (modelo predicción CONAF, deciles 10-100)",
        "areas_protegidas": "P=0.95 (restricción legal DAN 151 / SNASPE)",
        "firms": "P=0.80 (buffer 500m, foco activo NASA FIRMS)",
        "viento": "P=max(0, (racha-umbral)/umbral) (DJI M300 RTK, umbral 43.2 km/h)",
        "sismo_edificacion": "P=indice_antiguedad×0.5 (proxy vulnerabilidad estructural)",
        "p_total": "1 - Π(1-P_i) (combinación independiente)",
    },
    "resumen": resumen,
    "modelo_nodos": "P(nodo) = max P_total de aristas incidentes",
    "modelo_simulacion": "Amenazas espaciales (tendido, ignicion, areas_protegidas, firms, sismo_edificacion por manzana): un sorteo por evento/zona compartido por todas sus aristas. viento: sorteo independiente por arista.",
    "aristas": {str(k): v for k, v in prob.items()},
    "zonas": {str(k): v for k, v in zonas.items()},
    "nodos": {str(k): round(v, 4) for k, v in prob_nodos.items()},
}

outpath = os.path.join(AQUI, "data", "probabilidad_fallo.json")
os.makedirs(os.path.join(AQUI, "data"), exist_ok=True)
with open(outpath, "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2)

print(f"\nGenerado {outpath}")
print(f"  {len(prob_nodos)} nodos con P(fallo) > 0")
print(f"  {len(prob)} aristas con P(fallo) > 0 de {total_aristas} totales ({resumen['pct_aristas_con_riesgo']}%)")
for am in amenazas:
    r = resumen[am]
    print(f"  {am}: {r['aristas_afectadas']} aristas, P media={r['prob_media']}, P max={r['prob_max']}")

conn.close()
