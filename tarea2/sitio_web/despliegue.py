"""Despliegue de flota ante un sismo: todos los cuarteles lanzan drones a la vez,
se reparten la zona afectada y cada dron va primero a las manzanas censales de
mayor prioridad. Ver README ("Despliegue de flota por sismo").

Supuestos (no vienen de ninguna fuente de datos, calibrar y justificar en el informe):
  - radio de la zona segun magnitud (RADIO_POR_MAGNITUD_KM)
  - autonomia por dron, alcance de camara y velocidad de crucero (defaults abajo)
"""

# (magnitud maxima exclusiva, radio km)
RADIO_POR_MAGNITUD_KM = [(4.0, 5.0), (5.0, 10.0), (6.0, 20.0), (float("inf"), 50.0)]
AUTONOMIA_KM_DEFAULT = 30.0
ALCANCE_CAMARA_M_DEFAULT = 150.0
VELOCIDAD_KMH_DEFAULT = 40.0
MAX_CANDIDATAS = 120  # tope de manzanas en la matriz de costos (rendimiento)
UMBRAL_ANTIGUA = 0.7


def radio_zona_km(magnitud):
    return next(r for m, r in RADIO_POR_MAGNITUD_KM if magnitud < m)


def prioridad(total_pers, indice_antiguedad, dist_epicentro_km, radio_km):
    return (total_pers or 0) * (1 + (indice_antiguedad or 0)) / (1 + dist_epicentro_km / radio_km)


def asignar_flota(drones, candidatas, costo, largo, ruta_fn, cubre_fn, autonomia_m):
    """Greedy multi-deposito. Funcion pura: la BD entra solo por ruta_fn/cubre_fn.

    drones:     [{"id", "nodo"}]  (nodo = nodo vial del cuartel de origen)
    candidatas: {manzana_id: {"nodo", "prioridad"}}
    costo/largo: {(nodo_a, nodo_b): valor} costo resiliente y metros reales
    ruta_fn(a, b) -> (edge_ids, metros)  ruta resiliente real entre dos nodos
    cubre_fn(edge_ids) -> set de manzana_id cubiertas por ese tramo
    Devuelve (estado por dron, {manzana_id: (dron_id, metros_acumulados_al_cubrirla)}).
    """
    cargas = {d["nodo"] for d in drones}
    est = {d["id"]: {"pos": d["nodo"], "usado": 0.0, "legs": [], "visitas": [], "activo": True}
           for d in drones}
    cubiertas = {}
    pendientes = set(candidatas)
    vetadas = {d["id"]: set() for d in drones}  # manzanas que a ese dron no le alcanzan

    def volver(nodo):  # metros al punto de carga mas cercano
        return min((largo.get((nodo, c), float("inf")) for c in cargas), default=float("inf"))

    def mejor(d_id):
        e = est[d_id]
        opciones = []
        for m in pendientes - vetadas[d_id]:
            c = candidatas[m]
            ida = largo.get((e["pos"], c["nodo"]))
            if ida is None or e["usado"] + ida + volver(c["nodo"]) > autonomia_m:
                continue
            opciones.append((c["prioridad"] / max(costo.get((e["pos"], c["nodo"]), 1.0), 1.0), m))
        return max(opciones, default=None)

    while pendientes:
        # ronda: cada dron activo da a lo mas un tramo; gana primero el mejor par dron-manzana
        por_mover = {d for d, e in est.items() if e["activo"]}
        movio = False
        while por_mover and pendientes:
            elegidas = []
            for d in list(por_mover):
                o = mejor(d)
                if o:
                    elegidas.append((o, d))
                else:
                    por_mover.discard(d)
            if not elegidas:
                break
            (_, m), d_id = max(elegidas, key=lambda x: x[0][0])
            e = est[d_id]
            por_mover.discard(d_id)
            edges, metros = ruta_fn(e["pos"], candidatas[m]["nodo"])
            if e["usado"] + metros + volver(candidatas[m]["nodo"]) > autonomia_m:
                vetadas[d_id].add(m)  # la ruta resiliente real es mas larga que la estimada
                por_mover.add(d_id)
                continue
            e["usado"] += metros
            e["pos"] = candidatas[m]["nodo"]
            e["legs"].append((edges, metros, m))
            e["visitas"].append(m)
            for mc in cubre_fn(edges) | {m}:
                if mc in pendientes:
                    pendientes.discard(mc)
                    cubiertas[mc] = (d_id, e["usado"])
            movio = True
        for d_id, e in est.items():
            if e["activo"] and not mejor(d_id):
                e["activo"] = False
        if not movio:
            break
    return est, cubiertas


def calcular_despliegue_sismo(cur, sismo_id, costo_sql, drones_por_cuartel=1,
                              autonomia_km=AUTONOMIA_KM_DEFAULT,
                              alcance_camara_m=ALCANCE_CAMARA_M_DEFAULT,
                              velocidad_kmh=VELOCIDAD_KMH_DEFAULT):
    """costo_sql: expresion de costo por arista (servidor.cost_resiliente_sql)."""
    cur.execute("SELECT id, magnitud, lugar, ST_X(geom), ST_Y(geom) FROM amenaza_sismos WHERE id = %s", (sismo_id,))
    s = cur.fetchone()
    if not s:
        return {"error": "Sismo no encontrado"}
    _, mag, lugar, s_lon, s_lat = s
    radio = radio_zona_km(float(mag))

    # Flota: mismos cuarteles operativos que ofrece /api/origenes
    cur.execute("""
        SELECT id, nombre, comuna, ST_X(geom), ST_Y(geom), nodo_vial_id FROM nodo_carabineros
        WHERE es_punto_carga AND nodo_vial_id IS NOT NULL AND (es_comisaria OR nombre IN
              ('SUBCOM. CONCON', 'RETEN PLACILLA PENUELAS', 'RETEN LAGUNA VERDE'))
        ORDER BY id""")
    cuarteles = cur.fetchall()
    drones = [{"id": f"dron_{c[0]}_{k + 1}", "nodo": c[5], "cuartel": c[1], "comuna": c[2]}
              for c in cuarteles for k in range(drones_por_cuartel)]

    # Zona afectada: manzanas con gente a <= radio del epicentro
    cur.execute("""
        SELECT p.id, coalesce(p.total_pers, 0), coalesce(p.indice_antiguedad, 0),
               ST_X(ST_PointOnSurface(p.geom)), ST_Y(ST_PointOnSurface(p.geom)),
               ST_Distance(p.geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) / 1000.0
        FROM metadata_poblacion p
        WHERE ST_DWithin(p.geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
          AND coalesce(p.total_pers, 0) > 0""", (s_lon, s_lat, s_lon, s_lat, radio * 1000))
    zona = {}
    for pid, pers, antig, lon, lat, dist in cur.fetchall():
        zona[pid] = {"id": pid, "poblacion": pers, "antiguedad": round(antig, 2),
                     "lon": round(lon, 5), "lat": round(lat, 5),
                     "prioridad": prioridad(pers, antig, dist, radio)}
    if not zona or not drones:
        return {"error": "Sin manzanas pobladas en la zona o sin cuarteles disponibles"}

    top = sorted(zona.values(), key=lambda z: -z["prioridad"])[:MAX_CANDIDATAS]
    cur.execute("""
        SELECT z.id, (SELECT n.id FROM infraestructura_nodos n ORDER BY n.geom <-> p.geom LIMIT 1)
        FROM unnest(%s::int[]) AS z(id) JOIN metadata_poblacion p ON p.id = z.id""",
                ([z["id"] for z in top],))
    candidatas = {mid: {"nodo": nodo, "prioridad": zona[mid]["prioridad"]} for mid, nodo in cur.fetchall()}

    nodos = sorted({d["nodo"] for d in drones} | {c["nodo"] for c in candidatas.values()})
    def matriz(cost_expr):
        cur.execute(
            f"SELECT start_vid, end_vid, agg_cost FROM pgr_dijkstraCostMatrix("
            f"'SELECT id, source, target, {cost_expr} AS cost FROM infraestructura_aristas', %s)", (nodos,))
        return {(a, b): c for a, b, c in cur.fetchall()}
    costo, largo = matriz(costo_sql), matriz("length_m")

    def ruta_fn(a, b):
        cur.execute(
            f"SELECT edge FROM pgr_dijkstra('SELECT id, source, target, {costo_sql} AS cost "
            f"FROM infraestructura_aristas', %s, %s) WHERE edge != -1", (a, b))
        edges = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT coalesce(sum(length_m), 0) FROM infraestructura_aristas WHERE id = ANY(%s)", (edges,))
        return edges, float(cur.fetchone()[0])

    def cubre_fn(edges):
        if not edges:
            return set()
        cur.execute("""
            SELECT p.id FROM metadata_poblacion p
            WHERE p.id = ANY(%s) AND ST_DWithin(p.geom::geography,
                  (SELECT ST_Collect(geom) FROM infraestructura_aristas WHERE id = ANY(%s))::geography, %s)""",
                    (list(zona), edges, alcance_camara_m))
        return {r[0] for r in cur.fetchall()}

    # reserva 10%: la ruta resiliente de retorno puede ser mas larga que la estimada por distancia
    est, cubiertas = asignar_flota(drones, candidatas, costo, largo, ruta_fn, cubre_fn, autonomia_km * 900)

    salida_drones = []
    for d in drones:
        e = est[d["id"]]
        if not e["legs"]:
            continue
        # retorno al punto de carga mas cercano (ya garantizado dentro de la autonomia)
        destino_carga = min({x["nodo"] for x in drones}, key=lambda c: largo.get((e["pos"], c), float("inf")))
        ret_edges, ret_m = ruta_fn(e["pos"], destino_carga) if destino_carga != e["pos"] else ([], 0.0)
        todas = [x for leg in e["legs"] for x in leg[0]] + ret_edges
        cur.execute("SELECT ST_AsGeoJSON(ST_LineMerge(ST_Collect(geom)))::json FROM infraestructura_aristas WHERE id = ANY(%s)", (todas,))
        salida_drones.append({
            "id": d["id"], "cuartel": d["cuartel"], "comuna": d["comuna"],
            "geom": cur.fetchone()[0],
            "distancia_km": round((e["usado"] + ret_m) / 1000.0, 2),
            "manzanas_visitadas": e["visitas"],
        })

    # cobertura de TODA la zona (no solo las candidatas) por la union de rutas
    todas_aristas = list({x for d in est.values() for leg in d["legs"] for x in leg[0]})
    cubiertas_zona = cubre_fn(todas_aristas) | set(cubiertas)
    dron_de = {m: v[0] for m, v in cubiertas.items()}

    pob = sum(z["poblacion"] for z in zona.values())
    pob_cub = sum(zona[m]["poblacion"] for m in cubiertas_zona)
    pr = sum(z["prioridad"] for z in zona.values())
    pr_cub = sum(zona[m]["prioridad"] for m in cubiertas_zona)
    antiguas = [m for m, z in zona.items() if z["antiguedad"] > UMBRAL_ANTIGUA]
    prim = max(zona.values(), key=lambda z: z["prioridad"])["id"]
    t_prim = (cubiertas[prim][1] / 1000.0 / velocidad_kmh * 60.0) if prim in cubiertas else None

    return {
        "sismo": {"id": sismo_id, "magnitud": float(mag), "lugar": lugar, "lon": s_lon, "lat": s_lat},
        "parametros": {"radio_km": radio, "autonomia_km": autonomia_km, "drones_por_cuartel": drones_por_cuartel,
                       "alcance_camara_m": alcance_camara_m, "velocidad_kmh": velocidad_kmh},
        "drones": salida_drones,
        "manzanas": [{"id": z["id"], "lon": z["lon"], "lat": z["lat"], "poblacion": z["poblacion"],
                      "antiguedad": z["antiguedad"], "prioridad": round(z["prioridad"], 1),
                      "cubierta": z["id"] in cubiertas_zona, "dron": dron_de.get(z["id"])}
                     for z in zona.values()],
        "metricas": {
            "drones_desplegados": len(salida_drones), "drones_totales": len(drones),
            "manzanas_zona": len(zona), "manzanas_cubiertas": len(cubiertas_zona),
            "poblacion_zona": pob, "pct_poblacion_cubierta": round(100.0 * pob_cub / pob, 1) if pob else 0.0,
            "pct_prioridad_cubierta": round(100.0 * pr_cub / pr, 1) if pr else 0.0,
            "pct_antiguas_cubiertas": round(100.0 * len([m for m in antiguas if m in cubiertas_zona]) / len(antiguas), 1) if antiguas else None,
            "km_totales": round(sum(d["distancia_km"] for d in salida_drones), 1),
            "min_primera_inspeccion_top": round(t_prim, 1) if t_prim is not None else None,
        },
    }
