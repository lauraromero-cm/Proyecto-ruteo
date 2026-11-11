# Guion del video — Tarea 3: Ruteo resiliente de drones BVLOS, Gran Valparaíso

Duración estimada: 9–11 min. Entre corchetes, lo que se muestra en pantalla. Las cifras entre paréntesis son las de la última ejecución; si en pantalla salen levemente distintas (la simulación es aleatoria), leer lo que muestra la pantalla.

---

## 0. Introducción (0:00–0:40)
"Nuestro problema es enviar un dron BVLOS desde un cuartel de Carabineros hasta el lugar de una emergencia en el Gran Valparaíso, por una ruta que resista las amenazas del territorio: líneas eléctricas, zonas de incendio, áreas protegidas, sismos y viento. No buscamos solo la ruta más corta, sino la que tenga mayor probabilidad de completarse."

## 1. Ejecución desde el main (0:40–1:30) — Punto 1
[Terminal en la raíz del repositorio]
"Todo el proyecto se levanta con un solo comando: `python3 main.py`. Este script levanta la base de datos PostgreSQL con PostGIS y pgRouting en Docker, ejecuta los extractores de cada fuente, carga los datos a la base, calcula los costos de ruteo y genera el archivo de probabilidades de fallo. Después sirve la página en el puerto 8080. Aceleramos esta parte del video; lo importante es que cualquiera que clone el repositorio puede replicar la ejecución con ese comando."
[Abrir http://localhost:8080]

## 2. Infraestructura y realismo de las rutas (1:30–2:45) — Punto 2
"La infraestructura es la red vial real de la zona, con 126.480 aristas y 52.856 nodos, almacenada en PostGIS y ruteada con pgRouting. Las rutas siguen calles reales: el dron nunca traza una línea recta entre dos puntos, porque seguimos el corredor vial, que es donde puede inspeccionar daños a 30–60 metros de altura. Subir más no es una opción: a 90–120 metros la resolución de la imagen ya no permite ver grietas, y además los cables de tendido eléctrico se ubican justamente en esa franja, por eso los modelamos como amenaza y no los 'esquivamos subiendo'. Los orígenes son 29 cuarteles de Carabineros, cada uno vinculado a su nodo vial más cercano. Los destinos son emergencias reales: sismos de USGS y focos de incendio de NASA FIRMS, y solo se aceptan destinos que estén dentro de un radio alcanzable desde la red."

## 3. Interfaz y parámetros del usuario (2:45–3:30) — Puntos 3 y 4
[Mostrar selectores y slider]
"El usuario elige el cuartel de origen, la emergencia de destino y la restricción de viento máximo con este control, que parte en 43 km/h, el límite de despegue del DJI Matrice 300 RTK. Si lo baja, las aristas con rachas superiores se penalizan fuertemente."
[Panel de capas, activar y desactivar]
"El panel de control tiene checkboxes para cada capa: cuarteles, sismos, tendido eléctrico, áreas protegidas de CONAF, focos FIRMS y estaciones de viento. La información que no es un polígono, como sismos, cuarteles o estaciones, se despliega con popups al hacer clic."

## 4. Probabilidad de fallo (3:30–4:45) — Punto 5
[Mostrar `generar_probabilidad_fallo.py` y el JSON]
"Cada amenaza se modela como una probabilidad de fallo y el script genera un archivo con esa probabilidad para cada enlace y cada nodo. Tendido eléctrico: 0.35, por riesgo de colisión. Zonas de ignición: el decil de CONAF dividido por cien, porque la capa ya trae esa probabilidad. Áreas protegidas: 0.95, por restricción legal. Focos FIRMS: 0.80. Viento: crece linealmente sobre el umbral del dron. Sismo con edificación antigua: es la vulnerabilidad de la zona multiplicada por un factor de intensidad que depende de la magnitud, la profundidad y la distancia al epicentro. Así un sismo débil y profundo no daña edificios y uno fuerte y superficial sí. La probabilidad combinada de una arista es uno menos el producto de uno menos cada probabilidad. La de un nodo es la mayor de sus aristas, porque una intersección queda inutilizable si cualquiera de sus tramos lo está. Los coeficientes de atenuación sísmica son de orden de magnitud; no pertenecen a un modelo publicado, y lo declaramos."

## 5. Las cuatro técnicas de ruteo (4:45–7:00) — Punto 6
[Seleccionar 8VA. COM. FLORIDA → M5.9, 13 km NNW de Viña del Mar]
"Tenemos cuatro técnicas, cada una con su checkbox en el panel."

**Dijkstra por distancia** (rojo): "pgr_dijkstra con el largo en metros como único costo. Es la línea base: la ruta más corta, sin considerar amenazas."

**Dijkstra resiliente** (azul): "pgr_dijkstra con un costo ponderado: tendido por 1.2, ignición por 1.1, áreas protegidas y focos por 1000, que en la práctica los excluye, y las aristas con viento sobre el límite también por 1000."

**Programación lineal con Simplex** (morado): "Modelamos el problema formalmente. Variable x_e entre 0 y 1 por arista. Minimizamos la suma del costo por x_e, sujeto a conservación de flujo en cada nodo, un nodo de salida, uno de llegada, y a una restricción de riesgo: la probabilidad acumulada de fallo no puede superar la de la ruta resiliente de referencia. La matriz de incidencia nodo-arco es totalmente unimodular, por eso el Simplex entrega directamente una solución entera sin necesidad de variables binarias. Usamos el solver CBC mediante PuLP, que es de código abierto y no requiere licencia; el modelo es el mismo que en CPLEX o Gurobi y cambiar de solver es una línea. En este caso resolvió 20.995 variables y 9.054 restricciones en 5.826 iteraciones del Simplex. Trabaja sobre un corredor alrededor de las otras rutas, porque el grafo completo es muy grande para un LP."

**Ant Colony Optimization** (verde azulado): "Metaheurística de colonia de hormigas: 30 hormigas, 40 iteraciones, con feromonas que refuerzan los caminos de menor costo. Se arranca con un camino greedy para que converja, y explora alternativas que la optimización exacta no entrega."
[Activar y desactivar las cuatro capas en el mapa]

## 6. Resiliencia, algoritmo y tiempo (7:00–8:00) — Punto 8
[Tabla y leyenda]
"Usamos una sola métrica de resiliencia para las cuatro técnicas: la probabilidad de que la ruta se complete sin que ocurra ninguna amenaza. Se calcula como el producto de uno menos la probabilidad de cada evento distinto que la ruta cruza; una zona de ignición o un área protegida cuenta una sola vez, aunque la ruta pase por varias de sus aristas. Como es la misma fórmula para todas, las técnicas son comparables. Bajo cada checkbox se muestra el algoritmo, el tiempo de cómputo y esta resiliencia. En este caso: Dijkstra 3,3 %, resiliente 55,6 %, LP 55,6 % y ACO 70,8 %. La técnica más resiliente es el ACO, y así lo indica la insignia verde. En tiempo, el LP es la más lenta, algo más de un segundo, y el ACO la más rápida. La fila 'Superv. simulada' repite el sorteo aleatorio 100 veces y valida el número: coincide con el exacto salvo ruido de muestreo."

## 7. Simulación de fallas aleatorias (8:00–8:50) — Punto 7
[Presionar "Sortear fallas"]
"Esta opción sortea un número aleatorio entre 0 y 100 por cada amenaza. Si es menor que la probabilidad por cien, la falla ocurre. Las amenazas que son un solo evento, como una zona de ignición, se sortean una vez y afectan a todas las aristas y rutas por igual, es decir, es un escenario coherente. Las aristas que fallan se dibujan en negro y el popup muestra el número sorteado y el umbral. Debajo se indica qué rutas sobreviven. La fila 'Supervivencia' de la tabla repite este sorteo 100 veces y entrega el porcentaje."

## 8. Caso de ejemplo (8:50–10:10) — Punto 9
[Origen 8VA. COM. FLORIDA, destino M5.9 a 13 km NNW de Viña del Mar]
"Aquí la ruta por distancia mide 25,15 km y atraviesa un área protegida SNASPE, donde el vuelo está prohibido. La ruta resiliente la rodea: mide 25,58 km, solo 430 metros más, un 1,7 %. La resiliencia, es decir la probabilidad de completar la ruta, sube de 3,3 % a 55,6 %, y el ACO llega a 70,8 %."
[Presionar "Sortear fallas" varias veces]
"Al sortear fallas, la ruta por distancia se interrumpe casi siempre en el área protegida, mientras la resiliente rara vez. Esto muestra que mitigamos las amenazas con un costo mínimo de distancia, que era nuestro objetivo inicial. El LP y el ACO también encuentran rutas que evitan el área protegida."
"Un caso en que la mejora es mínima: algunos pares cruzan un tendido que es inevitable por la forma de la red; ahí todas las rutas fallan juntas. Lo mostramos porque la solución también tiene límites."

## 9. Calibración de pesos (10:10–11:10) — Justificación
"Los pesos no son arbitrarios: los probamos y elegimos el óptimo. Primero hicimos un barrido de 125 combinaciones de peso de tendido, ignición y antigüedad usando la métrica de resiliencia. Pero esa métrica usa la misma función de costo que el ruteo, así que decidimos validar con un criterio independiente: la supervivencia calculada de forma analítica sobre 48 pares origen-destino, exigiendo como máximo un 15 % de distancia extra. Dijkstra por distancia sobrevive en promedio un 21 %. Con tendido 1.2 e ignición 1.1 la ruta resiliente llega a 32,3 % con solo 4,7 % de distancia extra. Subir el peso del tendido a 20 solo suma 0,7 puntos con 14,8 % más de distancia, y a 100 supera el tope. Variar el peso de antigüedad entre 0.1 y 1.0 casi no cambia nada. Por eso mantuvimos 1.2, 1.1 y 0.3, que son el óptimo entre los probados."

## 10. Cierre (11:10–11:30)
"Resumiendo: infraestructura vial real, amenazas modeladas como probabilidades, cuatro técnicas de ruteo comparadas con una métrica de resiliencia, tiempo de cómputo y simulación de fallas, con pesos calibrados. Todo se replica con `python3 main.py`. Gracias."

---

## Preguntas probables y respuestas cortas
- **¿Por qué CBC y no CPLEX o Gurobi?** Mismo modelo formal; CBC es abierto y corre en Docker sin licencia. Cambiar de solver es cambiar una línea en PuLP.
- **¿Por qué el LP no entrega mejor ruta que Dijkstra?** Porque el camino más corto en una red es un problema de flujo y su óptimo exacto coincide con el de Dijkstra sobre el mismo costo; el valor del LP es que admite restricciones adicionales, como el riesgo acumulado.
- **¿Por qué los pesos son tan bajos (1.2 y 1.1)?** Los probamos hasta 100; subirlos casi no mejora la supervivencia porque la mayoría de los cruces de tendido son inevitables en esta red, y cuesta mucha distancia.
- **¿De dónde salen los coeficientes del sismo?** Son de orden de magnitud, calibrados para que un M6.5 a 10 km dé intensidad VII y un M4.4 a 48 km de profundidad dé intensidad I–II; no pertenecen a un modelo publicado.
- **¿Y el relieve?** Está cargado como metadato, pero no entra al costo de ruteo ni tiene capa en el mapa.
