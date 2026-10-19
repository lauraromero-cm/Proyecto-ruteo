"""Autotest de la logica pura del despliegue (sin BD): python3 test_despliegue.py"""
from despliegue import asignar_flota, radio_zona_km, prioridad

assert radio_zona_km(3.2) == 5 and radio_zona_km(4.0) == 10 and radio_zona_km(6.5) == 50
assert prioridad(100, 1, 0, 10) == 200 and prioridad(100, 0, 10, 10) == 50

# Linea de nodos 1-2-3-4-5, 1 km entre vecinos. Dron A sale de 1, dron B de 5.
N = [1, 2, 3, 4, 5]
largo = {(a, b): abs(a - b) * 1000.0 for a in N for b in N if a != b}
costo = dict(largo)
cands = {"x": {"nodo": 2, "prioridad": 100}, "y": {"nodo": 4, "prioridad": 90},
         "lejos": {"nodo": 3, "prioridad": 1}}
ruta = lambda a, b: ([f"{a}-{b}"], largo[(a, b)])
cubre = lambda edges: set()
drones = [{"id": "A", "nodo": 1}, {"id": "B", "nodo": 5}]

est, cub = asignar_flota(drones, cands, costo, largo, ruta, cubre, 10_000)
assert cub["x"][0] == "A" and cub["y"][0] == "B", cub  # cada dron toma lo mas cercano de alta prioridad
assert set(cub) == {"x", "y", "lejos"}

# autonomia corta: ni siquiera alcanza ida y vuelta -> nadie sale
est, cub = asignar_flota(drones, cands, costo, largo, ruta, cubre, 500)
assert cub == {} and not est["A"]["legs"]

# cobertura por camara: el tramo hacia "x" tambien cubre "lejos" -> A no necesita visitarla
est, cub = asignar_flota(drones, cands, costo, largo, ruta, lambda e: {"lejos"}, 10_000)
assert cub["lejos"][0] in ("A", "B") and "lejos" not in est["A"]["visitas"] + est["B"]["visitas"]
print("OK")
