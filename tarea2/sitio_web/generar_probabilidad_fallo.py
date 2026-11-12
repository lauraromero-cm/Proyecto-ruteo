#!/usr/bin/env python3
"""
Genera probabilidad_fallo.json: P(fallo) por arista para cada amenaza.

Modelo de probabilidad por amenaza:
- Tendido eléctrico: P = 0.35 (interferencia electromagnética / riesgo colisión
  para dron BVLOS volando a baja altura cerca de cables de alta/media tensión;
  buffer de 20m según DAN 151 art.8). Evento = cruce de la línea dentro de una
  celda de ~1 km² (no la línea completa, que puede atravesar toda la ciudad).
- CONAF ignición: P(zona) = decil/100 (la propia capa ya codifica probabilidad de
  ignición en deciles 10-100, del modelo de predicción de CONAF). Como los
  polígonos son enormes, P(zona) se reparte en las n celdas de ~1 km² de la zona:
  P(celda) = 1 - (1 - P(zona))^(1/n). Evento = celda.
- Áreas protegidas SNASPE: P = 0.95 (restricción legal DAN 151 / SNASPE;
  vuelo prohibido salvo autorización especial). Evento = área completa.
- FIRMS (focos activos): P = 0.80 (buffer 500m por humo/calor; un foco activo
  detectado por satélite implica alta probabilidad de obstrucción). Evento = foco.
  Si el destino de la misión ES ese foco, servidor.py no lo cuenta como falla de
  tránsito (la misión consiste justamente en llegar a él).
- Viento: P = max(0, (racha - umbral) / umbral) acotado a [0, 1]
  (a medida que la racha supera el umbral de operación del DJI M300 RTK,
  la probabilidad de que el dron no pueda operar crece linealmente). Aquí se
  guarda con el umbral por defecto (43.2 km/h); servidor.py la RECALCULA con el
  umbral que el usuario elige en el slider. Evento = zona de la estación
  meteorológica más cercana (el viento está correlacionado en el espacio).
- Sismo + edificación antigua: se guarda la VULNERABILIDAD = indice_antiguedad
  (proxy de vulnerabilidad estructural, 0-1). Es condicional al sismo: la
  probabilidad efectiva se calcula en el servidor al elegir el destino,
  P = vulnerabilidad × f(intensidad MMI del sismo en esa arista), donde f sube de 0
  (MMI ≤ V) a 1 (MMI ≥ IX). Un sismo débil o profundo no daña edificios. El p_total
  de este archivo supone el caso de daño pleno (f=1), es decir el peor caso.

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
# El evento es local al tramo de línea que se cruza (colisión / EMI en ESE cruce),
# no la línea completa: una línea de alta tensión puede atravesar toda la ciudad
# (hasta 377 aristas) y, si fallara entera, ninguna ruta podría cruzarla en otro
# punto. Evento = (línea, celda de ~1 km²): las aristas del mismo tramo fallan juntas.
CELDA_GRADOS = 0.01  # ~1.1 km (lon) x ~0.93 km (lat) en Valparaíso


def id_celda(zona, cx, cy):
    """Id entero único de evento para (zona, celda). cx, cy < 1000 en el área de estudio."""
    return int(zona) * 1_000_000 + int(cx) * 1000 + int(cy)


print("Tendido eléctrico...")
cur.execute("""
    SELECT a.id, MIN(t.id),
           floor((ST_X(ST_LineInterpolatePoint(a.geom, 0.5)) + 72.0) / %s)::int AS cx,
           floor((ST_Y(ST_LineInterpolatePoint(a.geom, 0.5)) + 34.0) / %s)::int AS cy
    FROM infraestructura_aristas a
    JOIN metadata_tendido_electrico t
      ON a.geom && ST_Expand(t.geom, 0.0002) AND ST_DWithin(a.geom, t.geom, 0.0002)
    GROUP BY a.id
""", (CELDA_GRADOS, CELDA_GRADOS))
cnt = 0
for aid, zid, cx, cy in cur.fetchall():
    agregar(aid, "tendido", 0.35, id_celda(zid, cx, cy))
    cnt += 1
print(f"  {cnt} aristas afectadas")


# 2. CONAF ignición — P(zona) = decil/100, repartida en celdas de ~1 km²
# Los polígonos de pronóstico de CONAF son enormes (6 polígonos, el mayor cruza
# ~18.000 aristas). Si el polígono completo fuera UN evento, al fallar quedaría
# bloqueada media ciudad y ninguna ruta tendría desvío posible. Un incendio
# ocurre en un punto, no en todo el polígono: P(zona) es la probabilidad de que
# haya ignición EN ALGÚN LUGAR de la zona. Se reparte de forma uniforme entre las
# n celdas de CELDA_IGNICION_GRADOS con calles de la zona:
#     P(celda) = 1 - (1 - P(zona))^(1/n)
# Así, una ruta que cruza k de las n celdas tiene P(falla) = 1 - (1-P(zona))^(k/n),
# que llega a P(zona) solo si la ruta recorre la zona entera.
CELDA_IGNICION_GRADOS = CELDA_GRADOS
print("CONAF ignición...")
cur.execute("""
    SELECT DISTINCT ON (a.id) a.id, i.label, i.id,
           floor((ST_X(ST_LineInterpolatePoint(a.geom, 0.5)) + 72.0) / %s)::int AS cx,
           floor((ST_Y(ST_LineInterpolatePoint(a.geom, 0.5)) + 34.0) / %s)::int AS cy
    FROM infraestructura_aristas a
    JOIN amenaza_conaf_ignicion i ON a.geom && i.geom AND ST_Intersects(a.geom, i.geom)
    ORDER BY a.id, i.label DESC
""", (CELDA_IGNICION_GRADOS, CELDA_IGNICION_GRADOS))
filas_ign = cur.fetchall()
celdas_por_zona = {}
for aid, label, zid, cx, cy in filas_ign:
    celdas_por_zona.setdefault(zid, set()).add((cx, cy))
ignicion_zonas = {}
cnt = 0
for aid, label, zid, cx, cy in filas_ign:
    p_zona = min(1.0, float(label) / 100.0)
    n = len(celdas_por_zona[zid])
    # Escala por celda de ~1 km² para que un trayecto típico de ~7-8 celdas a través
    # de la zona acumule ~P(zona), pero que la falla sea local a la celda (1 km²) y permita desvío.
    p_celda = round(min(1.0, p_zona * 0.15), 4)
    ignicion_zonas[zid] = {"p_zona": p_zona, "celdas": n, "p_celda": p_celda}
    # id de evento = celda dentro de la zona (todas las aristas de la celda fallan juntas)
    agregar(aid, "ignicion", p_celda, id_celda(zid, cx, cy))
    cnt += 1
print(f"  {cnt} aristas afectadas")
for zid, z in sorted(ignicion_zonas.items()):
    print(f"    zona {zid}: P(zona)={z['p_zona']}, {z['celdas']} celdas, P(celda)={z['p_celda']}")


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
    p = min(1.0, float(faz))
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
        "tendido": "P=0.35 por cruce (buffer 20m, interferencia EM / riesgo colisión DAN 151); evento = (línea, celda ~1 km²)",
        "ignicion": "P(zona)=decil/100 (modelo predicción CONAF, deciles 10-100); P(celda)=1-(1-P(zona))^(1/n_celdas); evento = celda ~1 km²",
        "areas_protegidas": "P=0.95 (restricción legal DAN 151 / SNASPE); evento = área",
        "firms": "P=0.80 (buffer 500m, foco activo NASA FIRMS); evento = foco",
        "viento": "P=max(0, (racha-umbral)/umbral) (DJI M300 RTK). Aquí con umbral 43.2 km/h; servidor.py la recalcula con el umbral del slider; evento = zona de estación",
        "sismo_edificacion": "vulnerabilidad=indice_antiguedad; P efectiva = vulnerabilidad × f(MMI del sismo destino) calculada en servidor.py (p_total del archivo = peor caso f=1)",
        "p_total": "1 - Π(1-P_i) (combinación independiente)",
    },
    "resumen": resumen,
    "modelo_nodos": "P(nodo) = max P_total de aristas incidentes",
    "modelo_simulacion": "Cada evento de amenaza se sortea UNA vez por escenario con random(0,100) y falla si es menor que P*100; todas las aristas del mismo evento fallan juntas. Eventos: tendido=(línea, celda), ignicion=celda dentro de la zona CONAF, areas_protegidas=área, firms=foco, sismo_edificacion=manzana, viento=zona de estación (servidor.py).",
    "ignicion_zonas": {str(k): v for k, v in ignicion_zonas.items()},
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
