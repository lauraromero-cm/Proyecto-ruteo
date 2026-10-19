FROM postgres:16
# postgres:16 es multi-arquitectura (amd64 y arm64, ej. Mac Apple Silicon);
# pgrouting/pgrouting solo publica amd64. PostGIS y pgRouting se instalan
# desde el repo PGDG que la imagen oficial ya trae configurado (bookworm).
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 postgresql-16-pgrouting \
    && rm -rf /var/lib/apt/lists/*
# Imagen oficial que ya trae PostgreSQL + PostGIS + pgRouting compilados y
# listos (evita depender del repo apt.postgresql.org para la version de
# pgRouting, que se encontro caido/archivado para Debian bullseye).

COPY init_extensions.sql /docker-entrypoint-initdb.d/01_extensions.sql
COPY schema.sql /docker-entrypoint-initdb.d/02_schema.sql
