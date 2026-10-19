#!/usr/bin/env python3
"""Genera el diagrama visual de la base de datos (Punto 0 de la rubrica).

Genera dos imagenes:
  - diagrama_bd.png         : version completa, con notas de arquitectura
                               e interacciones de ruteo.
  - diagrama_bd_simple.png  : solo el modelo de tablas/relaciones (respaldo
                               mas compacto, sin los bloques de texto).
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches

AQUI = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- paleta ---
COLOR_BG = "#eef2f7"
COLOR_BOX_BG = "#ffffff"
COLOR_BORDER = "#d2d9e3"
COLOR_SHADOW = "#c7cfdb"
COLOR_ROW_ALT = "#f6f8fb"
COLOR_TEXT = "#1e293b"
COLOR_TYPE = "#8592a6"

HEADER_INFRA = "#1d3f6e"
HEADER_META = "#0f6e5e"
HEADER_AMEN = "#9c2a2a"

PK_COLOR = "#c9922a"
FK_COLOR = "#2563a8"

ROW_H = 0.32
HEADER_H = 0.44
GAP = 0.55

COL1_X, COL1_W = 0.55, 3.75
COL2_X, COL2_W = 5.05, 4.05
COL3_X, COL3_W = 9.85, 3.85
TOP = 11.1


def generar(salida, incluir_notas=True):
    fig, ax = plt.subplots(figsize=(19.5 if incluir_notas else 14.2, 13.2), dpi=220)
    ax.axis("off")
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)

    registry = {}  # nombre_tabla -> dict(x, width, top, bottom, rows: {col_name: y_center})

    def table_height(columns):
        return HEADER_H + len(columns) * ROW_H

    def draw_table(nombre, x, top, width, title, columns, header_color):
        h = table_height(columns)
        bottom = top - h

        shadow = patches.FancyBboxPatch(
            (x + 0.06, bottom - 0.06), width, h,
            boxstyle="round,pad=0.035,rounding_size=0.09",
            facecolor=COLOR_SHADOW, edgecolor="none", zorder=1, alpha=0.55,
        )
        ax.add_patch(shadow)

        box = patches.FancyBboxPatch(
            (x, bottom), width, h,
            boxstyle="round,pad=0.035,rounding_size=0.09",
            facecolor=COLOR_BOX_BG, edgecolor=COLOR_BORDER, linewidth=1.1, zorder=2,
        )
        ax.add_patch(box)

        cursor = top - HEADER_H
        for i, _ in enumerate(columns):
            if i % 2 == 1:
                ax.add_patch(patches.Rectangle(
                    (x + 0.02, cursor - ROW_H), width - 0.04, ROW_H,
                    facecolor=COLOR_ROW_ALT, edgecolor="none", zorder=2.5,
                ))
            cursor -= ROW_H

        header = patches.FancyBboxPatch(
            (x, top - HEADER_H), width, HEADER_H,
            boxstyle="round,pad=0.0,rounding_size=0.09",
            facecolor=header_color, edgecolor="none", zorder=3,
        )
        ax.add_patch(header)
        ax.add_patch(patches.Rectangle(
            (x, top - HEADER_H), width, HEADER_H / 2,
            facecolor=header_color, edgecolor="none", zorder=3,
        ))
        ax.text(x + width / 2, top - HEADER_H / 2, title,
                color="white", weight="bold", fontsize=9.3, ha="center", va="center", zorder=4)

        rows_y = {}
        cursor = top - HEADER_H - ROW_H / 2
        for name, dtype, note in columns:
            is_pk = "PK" in note
            is_fk = "FK" in note
            if is_pk:
                badge_color, badge_text = PK_COLOR, "PK"
            elif is_fk:
                badge_color, badge_text = FK_COLOR, "FK"
            else:
                badge_color, badge_text = None, None

            if badge_color:
                badge_w, badge_h = 0.34, 0.165
                badge = patches.FancyBboxPatch(
                    (x + 0.1, cursor - badge_h / 2), badge_w, badge_h,
                    boxstyle="round,pad=0.015,rounding_size=0.045",
                    facecolor=badge_color, edgecolor="none", zorder=4,
                )
                ax.add_patch(badge)
                ax.text(x + 0.1 + badge_w / 2, cursor, badge_text, color="white",
                        weight="bold", fontsize=6.3, ha="center", va="center", zorder=5)
                name_x = x + 0.55
            else:
                name_x = x + 0.14

            ax.text(name_x, cursor, name, color=COLOR_TEXT, fontsize=7.6,
                    weight="semibold" if is_pk else "normal", va="center", zorder=4)
            ax.text(x + width - 0.13, cursor, dtype, color=COLOR_TYPE, fontsize=6.9,
                    ha="right", va="center", style="italic", zorder=4)
            rows_y[name] = cursor
            cursor -= ROW_H

        registry[nombre] = {"x": x, "width": width, "top": top, "bottom": bottom, "rows": rows_y}
        return bottom

    def stack_column(x, width, top, specs, header_color):
        cursor = top
        for nombre, titulo, cols in specs:
            bottom = draw_table(nombre, x, cursor, width, titulo, cols, header_color)
            cursor = bottom - GAP
        return cursor + GAP

    col1_bottom = stack_column(COL1_X, COL1_W, TOP, [
        ("nodos", "infraestructura_nodos", [
            ("id", "BIGINT", "PK"),
            ("osmid", "BIGINT", ""),
            ("lon", "DOUBLE", ""),
            ("lat", "DOUBLE", ""),
            ("geom", "Point(4326)", "GIST"),
        ]),
        ("aristas", "infraestructura_aristas", [
            ("id", "BIGINT", "PK"),
            ("osmid", "BIGINT", ""),
            ("source", "BIGINT", "FK"),
            ("target", "BIGINT", "FK"),
            ("highway", "TEXT", ""),
            ("surface", "TEXT", ""),
            ("oneway", "BOOLEAN", ""),
            ("length_m", "DOUBLE", "Costo base"),
            ("cost_resiliente", "DOUBLE", "Costo dinámico"),
            ("geom", "LineString", "GIST"),
        ]),
        ("carabineros", "nodo_carabineros", [
            ("id", "SERIAL", "PK"),
            ("nombre", "TEXT", ""),
            ("tipo", "TEXT", "Comisaría/Tenencia"),
            ("comuna", "TEXT", ""),
            ("es_comisaria", "BOOLEAN", ""),
            ("es_punto_carga", "BOOLEAN", "Default TRUE"),
            ("nodo_vial_id", "BIGINT", "FK"),
            ("geom", "Point(4326)", "GIST"),
        ]),
    ], HEADER_INFRA)

    col2_bottom = stack_column(COL2_X, COL2_W, TOP, [
        ("viento", "metadata_viento (Open-Meteo)", [
            ("id", "SERIAL", "PK"),
            ("comuna", "TEXT", ""),
            ("sector", "TEXT", ""),
            ("elevacion_m", "DOUBLE", ""),
            ("viento_medio_kmh", "DOUBLE", ""),
            ("racha_media_kmh", "DOUBLE", "Umbral DJI M300"),
            ("racha_max_kmh", "DOUBLE", "Peor caso, informativo"),
            ("direccion_deg", "DOUBLE", "Dirección"),
            ("geom", "Point(4326)", "GIST"),
        ]),
        ("tendido", "metadata_tendido_electrico", [
            ("id", "SERIAL", "PK"),
            ("tipo", "TEXT", "line / minor_line"),
            ("geom", "LineString", "GIST (OSM)"),
        ]),
        ("relieve", "metadata_relieve (Open Topo Data)", [
            ("id", "SERIAL", "PK"),
            ("arista_id", "BIGINT", "FK"),
            ("elev_u_m", "DOUBLE", ""),
            ("elev_v_m", "DOUBLE", ""),
            ("desnivel_m", "DOUBLE", ""),
            ("pendiente_pct", "DOUBLE", "Penalización"),
        ]),
        ("poblacion", "metadata_poblacion (INE Censo)", [
            ("id", "SERIAL", "PK"),
            ("comuna", "TEXT", ""),
            ("manzent", "TEXT", "ID Manzana"),
            ("total_pers", "INTEGER", "Población"),
            ("total_vivi", "INTEGER", "Viviendas"),
            ("permisos_count", "INTEGER", "Permisos 2010-18"),
            ("indice_antiguedad", "DOUBLE", "0-1, proxy sismo"),
            ("geom", "MultiPolygon", "GIST (Buffer 50m)"),
        ]),
        ("permisos", "metadata_permisos_edificacion", [
            ("id", "SERIAL", "PK"),
            ("comuna", "TEXT", ""),
            ("anio", "INTEGER", "2010-2018"),
            ("num_pisos", "INTEGER", ""),
            ("superficie_m2", "DOUBLE", ""),
            ("geom", "Point(4326)", "GIST"),
        ]),
    ], HEADER_META)

    col3_bottom = stack_column(COL3_X, COL3_W, TOP, [
        ("firms", "amenaza_firms (NASA FIRMS)", [
            ("id", "SERIAL", "PK"),
            ("fecha", "DATE", ""),
            ("confianza", "TEXT", "n/h/l"),
            ("dentro_chile", "BOOLEAN", "Filtro ST_Intersects"),
            ("geom", "Point(4326)", "GIST (Buffer 500m)"),
        ]),
        ("conaf_ignicion", "amenaza_conaf_ignicion", [
            ("id", "SERIAL", "PK"),
            ("label", "INTEGER", "Decil 10..100"),
            ("rango_pi", "TEXT", "ej. '21-30'"),
            ("dia_pronostico", "INTEGER", "0..4 (5 días)"),
            ("geom", "MultiPolygon", "GIST"),
        ]),
        ("conaf_areas", "amenaza_conaf_areas_protegidas", [
            ("id", "SERIAL", "PK"),
            ("nombre", "TEXT", "SNASPE"),
            ("categoria", "TEXT", "Parque/Reserva"),
            ("sup_ha", "DOUBLE", "Superficie"),
            ("geom", "MultiPolygon", "GIST (Exclusión)"),
        ]),
        ("sismos", "amenaza_sismos (USGS FDSN)", [
            ("id", "SERIAL", "PK"),
            ("usgs_id", "TEXT", "UNIQUE"),
            ("magnitud", "DOUBLE", "M >= 2"),
            ("profundidad_km", "DOUBLE", ""),
            ("fecha", "TIMESTAMP", "Epoch ms -> UTC"),
            ("lugar", "TEXT", "Valparaíso"),
            ("geom", "Point(4326)", "GIST (Destino misión)"),
        ]),
    ], HEADER_AMEN)

    canvas_bottom = min(col1_bottom, col2_bottom, col3_bottom) - 0.6
    notes_width = 5.4 if incluir_notas else 3.3
    ax.set_xlim(-0.9, COL3_X + COL3_W + 0.55 + notes_width)
    ax.set_ylim(canvas_bottom, TOP + 0.55)

    def connector(points, color, lw=1.3):
        """Polilínea recta por `points` con una única punta de flecha al final."""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        ax.plot(xs[:-1], ys[:-1], color=color, lw=lw, zorder=6, solid_capstyle="round")
        ax.annotate(
            "", xy=points[-1], xytext=points[-2],
            arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, shrinkA=0, shrinkB=2),
            zorder=6,
        )

    def label(pos, text, color):
        ax.text(pos[0], pos[1], text, fontsize=7, color=color, weight="bold",
                ha="center", va="center", zorder=7,
                bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                          edgecolor=color, linewidth=0.6))

    nodos, aristas, carab = registry["nodos"], registry["aristas"], registry["carabineros"]

    carril1_x = COL1_X - 0.22
    connector([
        (aristas["x"], aristas["rows"]["source"]),
        (carril1_x, aristas["rows"]["source"]),
        (carril1_x, nodos["rows"]["id"]),
        (nodos["x"], nodos["rows"]["id"]),
    ], FK_COLOR)
    label((carril1_x - 0.18, (aristas["rows"]["source"] + nodos["rows"]["id"]) / 2), "source/\ntarget", FK_COLOR)

    carril2_x = COL1_X - 0.42
    connector([
        (carab["x"], carab["rows"]["nodo_vial_id"]),
        (carril2_x, carab["rows"]["nodo_vial_id"]),
        (carril2_x, nodos["rows"]["lon"]),
        (nodos["x"], nodos["rows"]["lon"]),
    ], FK_COLOR)
    label((carril2_x - 0.19, (carab["rows"]["nodo_vial_id"] + nodos["rows"]["lon"]) / 2), "KNN\n(<->)", FK_COLOR)

    relieve = registry["relieve"]
    mid_x = (COL1_X + COL1_W + COL2_X) / 2
    connector([
        (relieve["x"], relieve["rows"]["arista_id"]),
        (mid_x, relieve["rows"]["arista_id"]),
        (mid_x, aristas["rows"]["id"]),
        (aristas["x"] + aristas["width"], aristas["rows"]["id"]),
    ], FK_COLOR)
    label((mid_x, relieve["rows"]["arista_id"] + 0.22), "arista_id", FK_COLOR)

    permisos, poblacion = registry["permisos"], registry["poblacion"]
    carril3_x = COL2_X + COL2_W + 0.22
    connector([
        (permisos["x"] + permisos["width"], permisos["rows"]["geom"]),
        (carril3_x, permisos["rows"]["geom"]),
        (carril3_x, poblacion["rows"]["permisos_count"]),
        (poblacion["x"] + poblacion["width"], poblacion["rows"]["permisos_count"]),
    ], HEADER_META)
    label((carril3_x + 0.26, (permisos["bottom"] + poblacion["rows"]["permisos_count"]) / 2), "ST_Contains\n(join espacial)", HEADER_META)

    NOTES_X = COL3_X + COL3_W + 0.55

    if incluir_notas:
        ax.text(NOTES_X, TOP - 0.05, "ARQUITECTURA RELACIONAL", fontsize=11.5, weight="bold", color=HEADER_INFRA)
        ax.text(NOTES_X, TOP - 0.5,
                "•  Motor: PostgreSQL 18 + PostGIS 3.6\n"
                "•  Algoritmo base: pgr_dijkstra\n"
                "•  k=2 rutas: penalización iterativa de\n    aristas (insp. en Suurballe 1974)\n"
                "•  CRS geométrico: EPSG:4326 (WGS84)\n"
                "•  Índices: GIST espaciales en cada geom",
                fontsize=8.6, color=COLOR_TEXT, va="top", linespacing=1.6)

        ax.text(NOTES_X, TOP - 2.55, "INTERACCIONES DE RUTEO", fontsize=11, weight="bold", color=HEADER_META)
        ax.text(NOTES_X, TOP - 3.0,
                "1.  Origen: nodo_carabineros vinculado\n     por KNN (<->) a la red vial.\n\n"
                "2.  Destino: nodo vial más cercano al\n     epicentro de amenaza_sismos.\n\n"
                "3.  Costo base: length_m en aristas.\n\n"
                "4.  Costo dinámico (cost_resiliente):\n"
                "     ×1.5 tendido eléctrico cercano\n"
                "     ×1.3 zona CONAF alta ignición\n"
                "     ×1000 área protegida / foco FIRMS\n"
                "     ×1000 viento > umbral DJI M300 RTK\n\n"
                "5.  Riesgo condicional (solo si el\n"
                "     destino es un sismo):\n"
                "     costo ×= (1 + 0.3 × indice_\n"
                "     antiguedad_zona) — riesgo de\n"
                "     escombros en edificación antigua.",
                fontsize=8.3, color=COLOR_TEXT, va="top", linespacing=1.55)

        leg_y = canvas_bottom + 1.55
    else:
        leg_y = TOP - 0.3

    ax.text(NOTES_X, leg_y + 0.55, "CONVENCIONES", fontsize=9.5, weight="bold", color="#475569")
    for i, (badge_color, text) in enumerate([(PK_COLOR, "Llave primaria (PK)"), (FK_COLOR, "Llave foránea (FK)")]):
        yy = leg_y + 0.15 - i * 0.32
        ax.add_patch(patches.FancyBboxPatch(
            (NOTES_X, yy - 0.08), 0.3, 0.16, boxstyle="round,pad=0.015,rounding_size=0.04",
            facecolor=badge_color, edgecolor="none"))
        ax.text(NOTES_X + 0.45, yy, text, fontsize=8, color=COLOR_TEXT, va="center")

    fig.suptitle(
        "Modelo de Base de Datos Geoespacial — Ruteo Resiliente de Drones BVLOS (Gran Valparaíso)",
        fontsize=15.5, weight="bold", y=1.0, color="#0f2537")
    fig.text(0.5, 0.965, "PostgreSQL + PostGIS + pgRouting", fontsize=10.5, ha="center", color="#50607a")

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    plt.savefig(salida, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)
    print(f"OK: Diagrama guardado en {salida}")


if __name__ == "__main__":
    generar(os.path.join(AQUI, "diagrama_bd.png"), incluir_notas=True)
    generar(os.path.join(AQUI, "diagrama_bd_simple.png"), incluir_notas=False)
