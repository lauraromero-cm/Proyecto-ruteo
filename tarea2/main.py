#!/usr/bin/env python3
"""Punto 9 de la rubrica - orquesta todo el pipeline de la Tarea 2:

  1. Levanta la base de datos (Docker: PostGIS + pgRouting) y el sitio web.
  2. Corre cada extractor de infraestructura/metadata/amenazas -> *.json.
  3. Carga cada *.json a su tabla correspondiente en la BD.
  4. Calcula, para cada par origen/destino, un par de rutas por distancia
     pura (pgr_dijkstra) y un par de rutas resilientes (pgr_dijkstra con
     costo ponderado + penalizacion iterativa de aristas, ver sitio_web/
     generar_rutas_comparativas.py y README.md seccion "Algoritmo de ruteo"),
     y exporta las capas para el sitio web (Leaflet).

Uso:  python3 main.py            (requiere Docker y docker compose)
      python3 main.py --sin-docker   (ejecución directa dentro del contenedor o con BD accesible)
"""
import os
import subprocess
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))

PASOS_EXTRACCION = [
    ("infraestructura/extraer_infraestructura.py", "infraestructura"),
    ("infraestructura/extraer_carabineros.py", "infraestructura"),
    ("metadata/extraer_viento.py", "metadata"),
    ("metadata/extraer_tendido_electrico.py", "metadata"),
    ("metadata/extraer_relieve.py", "metadata"),
    ("metadata/extraer_poblacion.py", "metadata"),
    ("metadata/extraer_permisos_edificacion.py", "metadata"),
    ("amenazas/extraer_firms.py", "amenazas"),
    ("amenazas/extraer_conaf.py", "amenazas"),
    ("amenazas/extraer_sismos.py", "amenazas"),
]

PASOS_CARGA = [
    ("infraestructura/cargar_infraestructura.py", "infraestructura"),
    ("infraestructura/cargar_carabineros.py", "infraestructura"),
    ("metadata/cargar_viento.py", "metadata"),
    ("metadata/cargar_tendido_electrico.py", "metadata"),
    ("metadata/cargar_relieve.py", "metadata"),
    ("metadata/cargar_poblacion.py", "metadata"),
    ("metadata/cargar_permisos_edificacion.py", "metadata"),  # requiere metadata_poblacion ya cargada
    ("amenazas/cargar_firms.py", "amenazas"),
    ("amenazas/cargar_conaf.py", "amenazas"),
    ("amenazas/cargar_sismos.py", "amenazas"),
]

PASOS_SITIO = [
    ("sitio_web/generar_rutas_comparativas.py", "sitio_web"),
    ("sitio_web/generar_probabilidad_fallo.py", "sitio_web"),
    ("sitio_web/generar_ruta_ejemplo.py", "sitio_web"),
    ("sitio_web/exportar_capas_web.py", "sitio_web"),
]


def correr(script_rel, cwd_rel, env):
    ruta_script = os.path.join(RAIZ, script_rel)
    if not os.path.exists(ruta_script):
        print(f"  [saltado] {script_rel} (no existe todavía)")
        return
    print(f"  -> {script_rel}")
    resultado = subprocess.run(
        [sys.executable, os.path.basename(script_rel)],
        cwd=os.path.join(RAIZ, cwd_rel),
        env=env,
    )
    if resultado.returncode != 0:
        raise SystemExit(f"Falló: {script_rel} (código {resultado.returncode})")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    sin_docker = "--sin-docker" in sys.argv

    if not sin_docker:
        print("1. Levantando Docker (BD PostgreSQL/PostGIS/pgRouting + Nginx)...")
        subprocess.run(["docker", "compose", "up", "-d", "--build", "db", "sitio_web"], cwd=RAIZ, check=True)
        print("   Esperando que la BD esté healthy...")
        for _ in range(30):
            chequeo = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", "ruteo_db"],
                capture_output=True, text=True,
            )
            if chequeo.stdout.strip() == "healthy":
                break
            time.sleep(2)
        else:
            raise SystemExit("La BD no quedó healthy a tiempo.")

        print("\n2. Ejecutando ETL y cálculo de rutas dentro del contenedor Docker (ruteo_etl)...")
        subprocess.run(["docker", "compose", "run", "--rm", "etl", "python3", "main.py", "--sin-docker"], cwd=RAIZ, check=True)
        print("\n=== Proceso completado con éxito ===")
        print("Sitio disponible en: http://localhost:8080")
        return

    # Si estamos en modo --sin-docker (ej. dentro del contenedor etl):
    env = os.environ.copy()
    env.setdefault("DB_HOST", "db" if os.path.exists("/.dockerenv") else "localhost")
    env.setdefault("DB_PORT", "5432")
    env.setdefault("DB_NAME", "ruteo_drones")
    env.setdefault("DB_USER", "ruteo")
    env.setdefault("DB_PASSWORD", "ruteo")
    env.setdefault("FIRMS_MAP_KEY", "d3739dfce8747115849167ec3fdc5e23")

    print("\n[ETL - Paso 1/3] Extracción (API / Fuentes -> JSON)...")
    for script, carpeta in PASOS_EXTRACCION:
        correr(script, carpeta, env)

    print("\n[ETL - Paso 2/3] Carga (JSON -> PostGIS / pgRouting)...")
    for script, carpeta in PASOS_CARGA:
        correr(script, carpeta, env)

    print("\n[ETL - Paso 3/3] Cálculo de Rutas Resilientes + Generación Sitio Web...")
    for script, carpeta in PASOS_SITIO:
        correr(script, carpeta, env)


if __name__ == "__main__":
    main()
