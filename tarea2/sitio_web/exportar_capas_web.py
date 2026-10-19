"""Punto 7 de la rubrica - Exporta las capas de infraestructura, metadata y amenazas
desde PostgreSQL a GeoJSON para que el visor Leaflet las consuma en el sitio web.
"""
import json
import os
import psycopg2

AQUI = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(AQUI, "data")
os.makedirs(DATA_DIR, exist_ok=True)

CONN = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "ruteo_drones"),
    user=os.environ.get("DB_USER", "ruteo"),
    password=os.environ.get("DB_PASSWORD", "ruteo"),
)

conn = psycopg2.connect(**CONN)
cur = conn.cursor()


def exportar(nombre, sql):
    try:
        cur.execute(sql)
        row = cur.fetchone()
        geojson = row[0] if row else {"type": "FeatureCollection", "features": []}
        ruta_salida = os.path.join(DATA_DIR, f"{nombre}.geojson")
        with open(ruta_salida, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False)
        n = len(geojson.get("features", []))
        print(f"  {nombre}.geojson: {n} features")
    except Exception as e:
        print(f"  Aviso al exportar {nombre}: {e}")
        # Crear FeatureCollection vacía si la tabla aún no tiene datos
        ruta_salida = os.path.join(DATA_DIR, f"{nombre}.geojson")
        with open(ruta_salida, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)


print("Exportando capas a sitio_web/data/ ...")

# 1. Cuarteles de Carabineros (origen/carga)
exportar("carabineros", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geom)::json,
            'properties', json_build_object(
                'id', id, 'nombre', nombre, 'comuna', comuna,
                'es_comisaria', es_comisaria, 'es_punto_carga', es_punto_carga
            )
        )), '[]'::json)
    ) FROM nodo_carabineros;
""")

# 2. Sismos (amenaza/destino)
# Umbral combinado (mismo criterio que servidor.py y generar_rutas_
# comparativas.py): "destino_valido" exige que el nodo vial mas cercano este
# dentro del MINIMO de (a) el radio de relevancia segun la magnitud del
# sismo (Wells & Coppersmith 1994) y (b) el radio operacional segun la
# racha de viento PROMEDIO de la zona (metadata_viento.racha_media_kmh;
# restriccion fisica de alcance del dron). Funciones SQL en db/schema.sql:
# distancia_max_valida_sismo_km y sus componentes.
# "en_tierra" es un hecho geografico INDEPENDIENTE (no un sinonimo de
# destino_valido): un sismo puede estar en el mar y aun asi ser destino
# valido (si es grande y el viento de la zona es favorable), o estar en
# tierra y no serlo. No confundir ambos campos en el frontend.
# Se exportan igual TODOS los sismos para la capa informativa del mapa (punto 7).
exportar("sismos", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(s.geom)::json,
            'properties', json_build_object(
                'id', s.id, 'magnitud', s.magnitud, 'profundidad_km', s.profundidad_km,
                'fecha', s.fecha, 'lugar', s.lugar,
                'dist_red_m', round(dist_red.d::numeric, 1),
                'racha_zona_kmh', round(v.racha_media_kmh::numeric, 1),
                'umbral_valido_km', round(distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)::numeric, 1),
                'destino_valido', ((dist_red.d / 1000.0) <= distancia_max_valida_sismo_km(s.magnitud, v.racha_media_kmh)),
                'en_tierra', (ST_X(s.geom) >= -71.68)
            )
        )), '[]'::json)
    ) FROM amenaza_sismos s
    CROSS JOIN LATERAL (
        SELECT ST_Distance(s.geom::geography, n.geom::geography) AS d
        FROM infraestructura_nodos n ORDER BY n.geom <-> s.geom LIMIT 1
    ) dist_red
    CROSS JOIN LATERAL (
        SELECT viento_racha_interpolada_kmh(s.geom) AS racha_media_kmh
    ) v;
""")

# 3. Tendido Eléctrico (metadata)
exportar("tendido_electrico", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geom)::json,
            'properties', json_build_object('id', id, 'tipo', tipo)
        )), '[]'::json)
    ) FROM metadata_tendido_electrico;
""")

# 4. CONAF Probabilidad de Ignición (amenaza)
exportar("conaf_ignicion", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geom)::json,
            'properties', json_build_object('label', label, 'rango_pi', rango_pi, 'dia', dia_pronostico)
        )), '[]'::json)
    ) FROM amenaza_conaf_ignicion;
""")

# 5. CONAF Áreas Silvestres Protegidas (exclusión)
exportar("conaf_areas_protegidas", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geom)::json,
            'properties', json_build_object('nombre', nombre, 'categoria', categoria, 'sup_ha', sup_ha)
        )), '[]'::json)
    ) FROM amenaza_conaf_areas_protegidas;
""")

# 6. Viento (metadata)
exportar("viento", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geom)::json,
            'properties', json_build_object(
                'comuna', comuna, 'sector', sector,
                'viento_kmh', viento_medio_kmh,
                'racha_media_kmh', racha_media_kmh, 'racha_max_kmh', racha_max_kmh,
                'direccion_deg', direccion_deg
            )
        )), '[]'::json)
    ) FROM metadata_viento;
""")

# 7. NASA FIRMS (focos de calor)
exportar("firms", """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', COALESCE(json_agg(json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geom)::json,
            'properties', json_build_object('fecha', fecha, 'confianza', confianza)
        )), '[]'::json)
    ) FROM amenaza_firms WHERE dentro_chile;
""")

conn.commit()
cur.close()
conn.close()
print("OK: Todas las capas exportadas a sitio_web/data/")
