"""Fuente 1 - Open-Meteo API (viento, rachas, visibilidad)."""
import json
import requests

URL = "https://api.open-meteo.com/v1/forecast"
params = {
    "latitude": -33.0472,
    "longitude": -71.6127,
    "hourly": "wind_speed_10m,wind_gusts_10m,visibility",
    "timezone": "America/Santiago",
}

resp = requests.get(URL, params=params, timeout=15)
resp.raise_for_status()
data = resp.json()

print("HTTP", resp.status_code)
print("URL final:", resp.url)
print(json.dumps({k: data[k] for k in ("latitude", "longitude", "hourly_units")}, indent=2, ensure_ascii=False))
print("Primeras 3 horas de viento (km/h):", data["hourly"]["wind_speed_10m"][:3])
print("Primeras 3 horas de rachas (km/h):", data["hourly"]["wind_gusts_10m"][:3])

with open("evidencia/datos/open_meteo_response.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("Guardado en evidencia/datos/open_meteo_response.json")
