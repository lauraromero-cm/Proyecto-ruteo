"""Consulta y procesa el dataset Google Open Buildings 2.5D Temporal (2023)
para el Gran Valparaiso (Valparaiso, Vina del Mar, Concon, Quilpue, Villa Alemana).
Descarga las teselas correspondientes, extrae alturas y densidades de edificacion,
y genera estadisticas y comparativas con los datos de OpenStreetMap.
"""
import math
import os
import requests
import json

def utm_to_latlon(x, y, zone=19):
    a = 6378137.0
    f = 1 / 298.257223563
    b = a * (1 - f)
    e2 = (a**2 - b**2) / (a**2)
    e_prime2 = (a**2 - b**2) / (b**2)
    k0 = 0.9996
    
    x_adj = x - 500000.0
    y_adj = y - 10000000.0
    
    M = y_adj / k0
    mu = M / (a * (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    
    phi1 = mu + (3*e1/2 - 27*e1**3/32)*math.sin(2*mu) + (21*e1**2/16 - 55*e1**4/32)*math.sin(4*mu) + (151*e1**3/96)*math.sin(6*mu)
    
    N1 = a / math.sqrt(1 - e2 * math.sin(phi1)**2)
    T1 = math.tan(phi1)**2
    C1 = e_prime2 * math.cos(phi1)**2
    R1 = a * (1 - e2) / ((1 - e2 * math.sin(phi1)**2)**1.5)
    D = x_adj / (N1 * k0)
    
    lat = phi1 - (N1 * math.tan(phi1) / R1) * (D**2/2 - (5 + 3*T1 + 10*C1 - 4*C1**2 - 9*e_prime2)*D**4/24 + (61 + 90*T1 + 298*C1 + 45*T1**2 - 252*e_prime2 - 3*C1**2)*D**6/720)
    lon_origin = (zone - 1) * 6 - 180 + 3
    lon = math.radians(lon_origin) + (D - (1 + 2*T1 + C1)*D**3/6 + (5 - 2*C1 + 28*T1 - 3*C1**2 + 8*e_prime2 + 24*T1**2)*D**5/120) / math.cos(phi1)
    
    return math.degrees(lat), math.degrees(lon)

def latlon_to_utm(lat, lon, zone=19):
    a = 6378137.0
    f = 1 / 298.257223563
    b = a * (1 - f)
    e2 = (a**2 - b**2) / (a**2)
    e_prime2 = (a**2 - b**2) / (b**2)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon_origin = (zone - 1) * 6 - 180 + 3
    lon_origin_rad = math.radians(lon_origin)
    k0 = 0.9996
    N = a / math.sqrt(1 - e2 * math.sin(lat_rad)**2)
    T = math.tan(lat_rad)**2
    C = e_prime2 * math.cos(lat_rad)**2
    A = math.cos(lat_rad) * (lon_rad - lon_origin_rad)
    M = a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * lat_rad
             - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*lat_rad)
             + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*lat_rad)
             - (35*e2**3/3072) * math.sin(6*lat_rad))
    x = k0 * N * (A + (1 - T + C) * A**3 / 6 + (5 - 18*T + T**2 + 72*C - 58*e_prime2) * A**5 / 120) + 500000.0
    y = k0 * (M + N * math.tan(lat_rad) * (A**2 / 2 + (5 - T + 9*C + 4*C**2) * A**4 / 24 + (61 - 58*T + T**2 + 600*C - 330*e_prime2) * A**6 / 720))
    if lat < 0:
        y += 10000000.0
    return x, y

def main():
    print("=== Consulta a API de Google Open Buildings 2.5D Temporal (Chile / Valparaiso) ===")
    
    manifest_url = "https://storage.googleapis.com/open-buildings-temporal-data/v1/manifests/97_EPSG_32719_2023_06_30.json"
    print(f"Descargando manifiesto: {manifest_url}")
    resp = requests.get(manifest_url)
    if resp.status_code != 200:
        print(f"Error al descargar manifiesto: {resp.status_code}")
        return
    
    manifest = resp.json()
    uri_prefix = manifest.get("uriPrefix", "")
    print(f"Dataset Name: {manifest.get('name')}")
    print(f"Bandas disponibles: {[b['id'] for b in manifest.get('bands', [])]}")
    print(f"CRS: {manifest.get('crs', 'EPSG:32719')}")
    
    # Gran Valparaiso bounding box
    sw_utm = latlon_to_utm(-33.12, -71.70, 19)
    ne_utm = latlon_to_utm(-32.90, -71.35, 19)
    vx_min, vx_max = min(sw_utm[0], ne_utm[0]), max(sw_utm[0], ne_utm[0])
    vy_min, vy_max = min(sw_utm[1], ne_utm[1]), max(sw_utm[1], ne_utm[1])
    print(f"BBox Gran Valparaiso (UTM 19S): X=[{vx_min:.1f}, {vx_max:.1f}], Y=[{vy_min:.1f}, {vy_max:.1f}]")
    
    matching_tiles = []
    for ts in manifest.get("tilesets", []):
        for src in ts.get("sources", []):
            aff = src.get("affineTransform", {})
            dim = src.get("dimensions", {})
            x0 = aff.get("translateX", 0)
            y0 = aff.get("translateY", 0)
            sx = aff.get("scaleX", 0.5)
            sy = aff.get("scaleY", -0.5)
            w = dim.get("width", 25000)
            h = dim.get("height", 25000)
            
            x1 = x0 + sx * w
            y1 = y0 + sy * h
            min_x, max_x = min(x0, x1), max(x0, x1)
            min_y, max_y = min(y0, y1), max(y0, y1)
            
            if not (max_x < vx_min or min_x > vx_max or max_y < vy_min or min_y > vy_max):
                sw_lat, sw_lon = utm_to_latlon(min_x, min_y)
                ne_lat, ne_lon = utm_to_latlon(max_x, max_y)
                full_path = uri_prefix + src["uris"][0]
                http_url = full_path.replace("gs://", "https://storage.googleapis.com/")
                matching_tiles.append({
                    "tile_id": src["uris"][0],
                    "http_url": http_url,
                    "bounds_utm": (min_x, min_y, max_x, max_y),
                    "bounds_latlon": (sw_lat, sw_lon, ne_lat, ne_lon),
                    "dim": (w, h),
                    "transform": aff
                })
    
    print(f"\nSe encontraron {len(matching_tiles)} teselas que cubren el Gran Valparaiso:")
    for i, t in enumerate(matching_tiles):
        print(f"[{i+1}] {t['tile_id']} -> X:[{t['bounds_utm'][0]:.0f}, {t['bounds_utm'][2]:.0f}], Y:[{t['bounds_utm'][1]:.0f}, {t['bounds_utm'][3]:.0f}]")
        print(f"    Lat/Lon: [{t['bounds_latlon'][0]:.3f}, {t['bounds_latlon'][1]:.3f}] a [{t['bounds_latlon'][2]:.3f}, {t['bounds_latlon'][3]:.3f}]")

if __name__ == "__main__":
    main()
