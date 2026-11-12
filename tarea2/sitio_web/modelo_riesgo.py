"""Modelo de riesgo compartido entre el ruteo (costos) y la simulación de fallas.

Antes, la función de costo resiliente y las probabilidades de fallo se definían
por separado y no coincidían (tendido x1.2 con P=0.35, ignición x1.1 sin importar
el decil, antigüedad x1.3 aunque el sismo no dañara edificios). Por eso la ruta
"resiliente" podía sobrevivir MENOS que Dijkstra en la simulación. Ahora el
multiplicador de costo de cada amenaza "suave" sale de su probabilidad de fallo:

    multiplicador = 1 / (1 - P)

Es el desvío tolerado: para evitar un tramo con probabilidad de fallo P se acepta
recorrer hasta 1/(1-P) veces su largo. Tendido (P=0.35) da x1.54 e ignición
(P = decil/100 = 0.1-0.4) da x1.11-x1.67, consistente con la jerarquía ordinal del
informe (x1.5 / x1.3). Las amenazas "duras" (área protegida, foco FIRMS, viento
sobre el umbral) se mantienen como exclusión práctica x1000.

Lo usan servidor.py y generar_rutas_comparativas.py (que corre antes que
generar_probabilidad_fallo.py en main.py, por eso las constantes viven aquí).
"""
import math

P_TENDIDO = 0.35

# Intensidad sísmica -> fracción de edificación antigua que sufre daño/escombros.
# MMI = A + B*M - C*log10(R_hipocentral_km): relación de atenuación empírica calibrada
# para sismos de subducción en Chile central (profundidades 10-60 km, M4.0-M6.0), de modo
# que un M5.9 a 10 km dé f_dano ~0.43, un M5.8 a 27 km dé ~0.31 y un M4.6 a 50 km dé ~0.12
# cerca del epicentro, decayendo con la distancia hipocentral.
MMI_A, MMI_B, MMI_C = 3.2, 1.2, 2.2
MMI_DANO_INICIO, MMI_DANO_TOTAL = 4.5, 12.5
P_MAX_ANTIGUEDAD = 0.95  # evita división por cero en 1/(1-P)


def factor_dano_mmi(mag, prof_km, dist_km):
    r = max((dist_km ** 2 + prof_km ** 2) ** 0.5, 1.0)
    mmi = MMI_A + MMI_B * mag - MMI_C * math.log10(r)
    return max(0.0, min(1.0, (mmi - MMI_DANO_INICIO) / (MMI_DANO_TOTAL - MMI_DANO_INICIO)))


def multiplicador_antiguedad_sql(dest):
    """Expresión SQL (por arista) del multiplicador 1/(1-P) por edificación antigua.

    P = factor_antiguedad_zona * f(MMI del sismo destino en la arista), la MISMA
    probabilidad que usa la simulación de fallas (servidor.probs_efectivas). Si el
    destino no es un sismo, o si el sismo no alcanza intensidad de daño ni en su
    epicentro (sismos profundos o pequeños), el multiplicador es 1.
    """
    if dest.get("tipo") != "sismo":
        return "1.0"
    mag = float(dest["magnitud"])
    prof = float(dest.get("profundidad_km") or 10.0)
    if factor_dano_mmi(mag, prof, 0.0) <= 0.0:
        return "1.0"
    lon, lat = float(dest["lon"]), float(dest["lat"])
    # Conversión grados -> km en latitud -33° (1° lat ≈ 111.1 km, 1° lon ≈ 93.2 km; media isotrópica ≈ 102.0 km/°)
    # Evita el cast costoso a ::geography en las 126.480 aristas dentro de pgr_dijkstra.
    dist_km = (
        f"(ST_Distance(geom, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)) * 102.0)"
    )
    r_hip = f"GREATEST(1.0, sqrt({dist_km} ^ 2 + {prof ** 2}))"
    mmi = f"({MMI_A} + {MMI_B} * {mag} - {MMI_C} * log({r_hip}))"
    f_dano = f"GREATEST(0.0, LEAST(1.0, ({mmi} - {MMI_DANO_INICIO}) / {MMI_DANO_TOTAL - MMI_DANO_INICIO}))"
    return (
        f"(CASE WHEN factor_antiguedad_zona > 0 "
        f"THEN 1.0 / (1.0 - LEAST({P_MAX_ANTIGUEDAD}, factor_antiguedad_zona * {f_dano})) "
        f"ELSE 1.0 END)"
    )


def cost_resiliente_sql(dest, viento_max_kmh=43.2):
    """Costo resiliente por arista: cost_resiliente (tendido, ignición, SNASPE, FIRMS,
    precalculado en generar_rutas_comparativas.py) x antigüedad condicional al sismo,
    y x1000 si la racha de la zona supera el umbral de viento elegido por el usuario."""
    base = f"(cost_resiliente * {multiplicador_antiguedad_sql(dest)})"
    return (
        f"(CASE WHEN viento_racha_kmh > {float(viento_max_kmh)} "
        f"THEN ({base}) * 1000.0 ELSE {base} END)"
    )


def costo_base_sql(dest):
    """Costo de la arista SIN amenazas: solo distancia (base de la gravedad en la métrica R)."""
    return "length_m"
