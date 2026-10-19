-- Esquema de la base de datos - Ruteo resiliente de drones BVLOS
-- Gran Valparaiso. Ver db/diagrama_bd.png para el diagrama visual.

-- ============================================================
-- INFRAESTRUCTURA (grafo vial real, OSM via Geofabrik)
-- ============================================================

CREATE TABLE infraestructura_nodos (
    id      BIGINT PRIMARY KEY,         -- id interno que usa pgRouting
    osmid   BIGINT,                     -- id original en OpenStreetMap
    lon     DOUBLE PRECISION NOT NULL,
    lat     DOUBLE PRECISION NOT NULL,
    geom    GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX idx_infraestructura_nodos_geom ON infraestructura_nodos USING GIST (geom);

CREATE TABLE infraestructura_aristas (
    id          BIGINT PRIMARY KEY,
    osmid       BIGINT,
    source      BIGINT NOT NULL REFERENCES infraestructura_nodos(id),
    target      BIGINT NOT NULL REFERENCES infraestructura_nodos(id),
    highway     TEXT,                   -- tipo de via (primary, residential, etc.)
    surface     TEXT,                   -- tipo de superficie (asphalt, unpaved, etc.)
    oneway      BOOLEAN DEFAULT FALSE,
    length_m    DOUBLE PRECISION NOT NULL,  -- costo base para pgr_dijkstra (fase 2)
    geom        GEOMETRY(LineString, 4326) NOT NULL
);
CREATE INDEX idx_infraestructura_aristas_geom ON infraestructura_aristas USING GIST (geom);
CREATE INDEX idx_infraestructura_aristas_source ON infraestructura_aristas (source);
CREATE INDEX idx_infraestructura_aristas_target ON infraestructura_aristas (target);

-- ============================================================
-- NODOS ESPECIALES (origen del dron / puntos de carga)
-- ============================================================

CREATE TABLE nodo_carabineros (
    id              SERIAL PRIMARY KEY,
    nombre          TEXT NOT NULL,
    tipo            TEXT,               -- Comisaria, Tenencia, Subcomisaria, Reten
    comuna          TEXT NOT NULL,
    es_comisaria    BOOLEAN NOT NULL DEFAULT FALSE,
    es_punto_carga  BOOLEAN NOT NULL DEFAULT TRUE,
    nodo_vial_id    BIGINT REFERENCES infraestructura_nodos(id), -- nodo mas cercano del grafo
    geom            GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX idx_nodo_carabineros_geom ON nodo_carabineros USING GIST (geom);

-- ============================================================
-- METADATA (una tabla por fuente, carga independiente)
-- ============================================================

CREATE TABLE metadata_viento (
    id                  SERIAL PRIMARY KEY,
    comuna              TEXT NOT NULL,
    sector              TEXT,
    elevacion_m         DOUBLE PRECISION,
    viento_medio_kmh    DOUBLE PRECISION,
    racha_media_kmh     DOUBLE PRECISION,  -- usada como restriccion operacional (umbral viento)
    racha_max_kmh       DOUBLE PRECISION,  -- peor instante del pronostico, solo informativo
    direccion_deg       DOUBLE PRECISION,  -- de donde sopla, en grados
    geom                GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX idx_metadata_viento_geom ON metadata_viento USING GIST (geom);

CREATE TABLE metadata_tendido_electrico (
    id      SERIAL PRIMARY KEY,
    tipo    TEXT NOT NULL,              -- line (alta/media tension) | minor_line (distribucion)
    geom    GEOMETRY(LineString, 4326) NOT NULL
);
CREATE INDEX idx_metadata_tendido_geom ON metadata_tendido_electrico USING GIST (geom);

CREATE TABLE metadata_relieve (
    id              SERIAL PRIMARY KEY,
    arista_id       BIGINT REFERENCES infraestructura_aristas(id),
    elev_u_m        DOUBLE PRECISION,
    elev_v_m        DOUBLE PRECISION,
    desnivel_m      DOUBLE PRECISION,
    pendiente_pct   DOUBLE PRECISION
);

CREATE TABLE metadata_poblacion (
    id                  SERIAL PRIMARY KEY,
    comuna              TEXT NOT NULL,
    manzent             TEXT,                   -- id de manzana censal (MANZENT_I)
    total_pers          INTEGER,
    total_vivi          INTEGER,
    permisos_count      INTEGER,            -- permisos de edificacion 2010-2018 dentro de la manzana
    indice_antiguedad   DOUBLE PRECISION,   -- 0 (edificacion renovada) a 1 (sin permisos recientes); proxy, no probabilidad de colapso
    geom                GEOMETRY(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX idx_metadata_poblacion_geom ON metadata_poblacion USING GIST (geom);

CREATE TABLE metadata_permisos_edificacion (
    id              SERIAL PRIMARY KEY,
    comuna          TEXT NOT NULL,
    anio            INTEGER,
    num_pisos       INTEGER,
    superficie_m2   DOUBLE PRECISION,
    geom            GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX idx_metadata_permisos_geom ON metadata_permisos_edificacion USING GIST (geom);

-- ============================================================
-- AMENAZAS (una tabla por fuente)
-- ============================================================

CREATE TABLE amenaza_firms (
    id              SERIAL PRIMARY KEY,
    fecha           DATE,
    confianza       TEXT,
    dentro_chile    BOOLEAN NOT NULL,
    geom            GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX idx_amenaza_firms_geom ON amenaza_firms USING GIST (geom);

CREATE TABLE amenaza_conaf_ignicion (
    id              SERIAL PRIMARY KEY,
    label           INTEGER,            -- limite superior del decil (10,20,...,100)
    rango_pi        TEXT,               -- ej. "21-30"
    dia_pronostico  INTEGER,            -- 0 a 4 (pronostico a 5 dias)
    geom            GEOMETRY(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX idx_amenaza_conaf_ignicion_geom ON amenaza_conaf_ignicion USING GIST (geom);

CREATE TABLE amenaza_conaf_areas_protegidas (
    id          SERIAL PRIMARY KEY,
    nombre      TEXT,
    categoria   TEXT,
    sup_ha      DOUBLE PRECISION,
    geom        GEOMETRY(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX idx_amenaza_conaf_asp_geom ON amenaza_conaf_areas_protegidas USING GIST (geom);

CREATE TABLE amenaza_sismos (
    id              SERIAL PRIMARY KEY,
    usgs_id         TEXT UNIQUE,
    magnitud        DOUBLE PRECISION,
    profundidad_km  DOUBLE PRECISION,
    fecha           TIMESTAMP,
    lugar           TEXT,
    geom            GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX idx_amenaza_sismos_geom ON amenaza_sismos USING GIST (geom);

-- ============================================================
-- UMBRAL DE VALIDEZ DE UN SISMO COMO DESTINO DE RUTEO
-- ============================================================
-- Reemplaza un umbral fijo de distancia (version anterior) por uno que
-- combina dos criterios reales, no arbitrarios:

-- Racha de viento estimada en un punto cualquiera, interpolando las 6
-- estaciones de metadata_viento por ponderacion inversa a la distancia al
-- cuadrado (IDW, Shepard 1968, "A two-dimensional interpolation function
-- for irregularly-spaced data", Proc. 23rd ACM National Conference). Evita
-- que el viento sea constante por zona (estacion mas cercana) y cambie de
-- golpe en el borde entre zonas. Es una ESTIMACION con solo 6 puntos, no
-- una medicion en cada calle. Piso de 100m a la distancia para no dividir
-- por ~0 si el punto coincide con una estacion. Usa racha_media_kmh (ver
-- justificacion en metadata/viento.schema.md).
CREATE OR REPLACE FUNCTION viento_racha_interpolada_kmh(p geometry)
RETURNS double precision AS $$
    SELECT SUM(t.racha / POWER(t.d, 2)) / SUM(1.0 / POWER(t.d, 2))
    FROM (
        SELECT v.racha_media_kmh AS racha,
               GREATEST(ST_Distance(v.geom::geography, p::geography), 100.0) AS d
        FROM metadata_viento v
    ) t;
$$ LANGUAGE SQL STABLE;

CREATE OR REPLACE FUNCTION radio_relevancia_sismo_km(magnitud double precision)
RETURNS double precision AS $$
    -- Wells & Coppersmith (1994), "New Empirical Relationships among
    -- Magnitude, Rupture Length, Rupture Width, Rupture Area, and Surface
    -- Displacement", Bulletin of the Seismological Society of America,
    -- 84(4):974-1002. Longitud de ruptura subsuperficial (todos los tipos
    -- de falla, regresion "All"): log10(RLD) = -2.44 + 0.59*M.
    -- Se usa 3x RLD como radio de relevancia del area de impacto esperada
    -- (el area afectada/sentida suele ser varias veces la ruptura misma;
    -- el multiplicador 3 es una eleccion propia, no del paper), con un
    -- piso de 2km para sismos pequenos (consistente con el umbral
    -- original de Tarea 2, documentado en RESULTADOS.md).
    SELECT GREATEST(2.0, 3.0 * POWER(10, -2.44 + 0.59 * magnitud));
$$ LANGUAGE SQL IMMUTABLE;

CREATE OR REPLACE FUNCTION radio_operacional_viento_km(
    -- racha_zona_kmh debe ser un valor REPRESENTATIVO del viento actual de
    -- la zona (ej. metadata_viento.racha_media_kmh, el promedio del
    -- pronostico), nunca un extremo puntual como la racha maxima de varios
    -- dias de pronostico: en el Gran Valparaiso real esa racha maxima
    -- supera siempre el techo fisico del dron en las 6 estaciones, lo que
    -- volveria esta funcion un umbral constante (0.15x del radio base) sin
    -- capacidad de discriminar entre zonas.
    racha_zona_kmh double precision,
    umbral_usuario_kmh double precision DEFAULT 43.2,
    radio_base_km double precision DEFAULT 15.0
) RETURNS double precision AS $$
    -- Restriccion fisica real del dron de referencia (DJI Matrice 300 RTK,
    -- plataforma enterprise/BVLOS usada en inspeccion y respuesta a
    -- desastres), no una regla inventada:
    --   - umbral_usuario_kmh DEFAULT 43.2 = 12 m/s, el limite de resistencia
    --     al viento para despegue/aterrizaje segun la ficha tecnica oficial
    --     DJI (el limite EN VUELO es 15 m/s = 54 km/h; se usa el mas
    --     restrictivo como default porque si no puede despegar/aterrizar
    --     con seguridad, la mision no es viable aunque pudiera volar).
    --   - radio_base_km DEFAULT 15.0 = alcance maximo de transmision
    --     OcuSync Enterprise del M300 RTK en condiciones FCC/NCC (ideal,
    --     sin obstrucciones/interferencia), segun la pagina oficial DJI
    --     Enterprise (enterprise.dji.com/matrice-300).
    -- A mayor viento real de la zona (racha_zona_kmh) por sobre el umbral,
    -- menor el radio operacional seguro (ver p.ej. Thibbotuwawa, A. et al.
    -- (2020), "UAV Mission Planning Resistant to Weather Uncertainty",
    -- Sensors, 20(3):598, sobre como el viento reduce el alcance/autonomia
    -- planificable de un UAV). El factor de derateo (razon umbral/racha,
    -- acotada entre 0.15x y 1.5x) es una heuristica propia; los 3 valores
    -- que escala (umbral, radio base, y el limite absoluto de 54km/h que
    -- acota la entrada de usuario en servidor.py) vienen de la ficha del
    -- fabricante, no son arbitrarios.
    SELECT radio_base_km * LEAST(1.5, GREATEST(0.15, umbral_usuario_kmh / GREATEST(racha_zona_kmh, 1.0)));
$$ LANGUAGE SQL IMMUTABLE;

CREATE OR REPLACE FUNCTION distancia_max_valida_sismo_km(
    magnitud double precision,
    racha_zona_kmh double precision,
    umbral_usuario_kmh double precision DEFAULT 43.2
) RETURNS double precision AS $$
    -- El umbral final es el MINIMO de ambos criterios: un sismo grande en
    -- una zona muy ventosa sigue sin ser alcanzable - el viento es una
    -- restriccion fisica que no desaparece solo porque el sismo sea
    -- importante.
    SELECT LEAST(
        radio_relevancia_sismo_km(magnitud),
        radio_operacional_viento_km(racha_zona_kmh, umbral_usuario_kmh)
    );
$$ LANGUAGE SQL IMMUTABLE;
