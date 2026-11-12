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
"Para que las rutas sean realistas, trabajamos sobre la red vial real del Gran Valparaíso: 126.480 tramos y 52.856 intersecciones, almacenados en PostGIS y ruteados con pgRouting. El dron nunca vuela en línea recta entre dos puntos: sigue el corredor de las calles."
[Mostrar una ruta sobre el mapa]
"¿Por qué sobre las calles? Porque una inspección de daños se hace a 30–60 metros de altura. Más arriba, a 90–120 metros, la imagen pierde resolución y ya no se distinguen grietas. Y a esa altura el dron queda expuesto a amenazas reales del territorio: los cables del tendido eléctrico, que pueden provocar una colisión; las zonas de incendio, donde el fuego y el humo impiden volar; las áreas protegidas, donde el vuelo está prohibido por ley; y los sismos, que dañan edificios y calles justo donde vamos a inspeccionar. Estas amenazas no se esquivan subiendo, y de ellas depende que el dron llegue o no a su destino. Por eso las modelamos una por una y buscamos la ruta que mejor las evite."
"Los orígenes son 29 cuarteles de Carabineros, cada uno conectado a su intersección más cercana. Los destinos son emergencias reales: sismos del servicio geológico de Estados Unidos, USGS, y focos de incendio de la NASA, FIRMS. Solo se aceptan destinos que el dron pueda alcanzar desde la red, según su radio de operación."

## 3. Interfaz y parámetros del usuario (2:45–3:30) — Puntos 3 y 4
[Mostrar selectores y slider]
"El usuario elige el cuartel de origen, la emergencia de destino y la restricción de viento máximo con este control, que parte en 43 km/h, el límite de despegue del DJI Matrice 300 RTK. Si lo baja, las calles con rachas superiores se penalizan fuertemente. Hay que aclarar que en esta zona el viento es bastante parejo, entre 19 y 38 km/h, de modo que al bajar el límite penalizamos casi todas las calles por igual y no existe una zona más tranquila a la que desviarse; por eso la ruta no cambia, y con el límite por defecto de 43 km/h el viento no es un factor en el Gran Valparaíso."
[Panel de capas, activar y desactivar]
"El panel de control tiene checkboxes para cada capa: cuarteles, sismos, tendido eléctrico, áreas protegidas de CONAF, focos FIRMS y estaciones de viento. Las zonas de ignición de CONAF no tienen capa propia en el mapa, pero sí están en el cálculo de rutas, y cuando una ruta las cruza aparecen en el popup de las aristas que fallan al sortear. La información que no es un polígono, como sismos, cuarteles o estaciones, se despliega con popups al hacer clic."

## 4. Probabilidad de fallo (3:30–4:45) — Punto 5
[Mostrar `generar_probabilidad_fallo.py` y el JSON]
"Cada amenaza se modela como una probabilidad de fallo y el script genera un archivo con esa probabilidad para cada enlace y cada nodo. Tendido eléctrico: 0.35, por riesgo de colisión. Zonas de ignición: el decil de CONAF dividido por cien, porque la capa ya trae esa probabilidad. Áreas protegidas: 0.95, por restricción legal. Focos FIRMS: 0.80. Viento: crece linealmente sobre el umbral del dron. Sismo con edificación antigua: es la vulnerabilidad de la zona multiplicada por un factor de intensidad que depende de la magnitud, la profundidad y la distancia al epicentro. Así un sismo débil y profundo no daña edificios y uno fuerte y superficial sí. La probabilidad combinada de una arista es uno menos el producto de uno menos cada probabilidad. La de un nodo es la mayor de sus aristas, porque una intersección queda inutilizable si cualquiera de sus tramos lo está. Los coeficientes de atenuación sísmica son de orden de magnitud; no pertenecen a un modelo publicado, y lo declaramos."

## 5. Las cuatro técnicas de ruteo (4:45–7:00) — Punto 6
[Seleccionar RETEN PLACILLA PENUELAS → M4.4, 17 km SSE de Quilpué]
"Tenemos cuatro técnicas, cada una con su checkbox en el panel."

**Dijkstra por distancia** (rojo): "pgr_dijkstra con el largo en metros como único costo. Es la línea base: la ruta más corta, sin considerar amenazas."

**Dijkstra resiliente** (azul): "pgr_dijkstra con un costo ponderado: tendido por 1.2, ignición por 1.1, áreas protegidas y focos por 1000, que en la práctica los excluye, y las aristas con viento sobre el límite también por 1000."

**Programación lineal con Simplex** (morado): "Aquí planteamos el problema como un modelo matemático. Para cada calle hay una variable que indica si la ruta pasa por ella. El objetivo es minimizar el costo total de la ruta, con dos reglas: el recorrido tiene que ser continuo, desde el origen hasta el destino, y el riesgo de fallo no puede superar el de la ruta resiliente de referencia. Este tipo de problema tiene una propiedad que lo facilita: el Simplex entrega directamente una ruta completa, sin tener que decidir calle por calle. En este caso resolvió 5.266 variables y 2.280 restricciones en 843 iteraciones. No lo corre sobre toda la ciudad, sino sobre un corredor alrededor de las otras rutas, porque el mapa completo es demasiado grande para un modelo así."
"El solver que usamos es CBC, de código abierto, a través de la librería PuLP. Lo elegimos por tres razones. Primero, hace lo mismo que CPLEX o Gurobi: ejecuta el Simplex sobre el mismo modelo y llega a la misma solución; la diferencia es que los comerciales son más rápidos en modelos muy grandes. Segundo, las versiones gratuitas de CPLEX y Gurobi aceptan solo mil o dos mil variables, y nuestro modelo tiene entre cinco mil y veintiún mil; la licencia completa es académica, exige conectarse desde la red de la universidad y no se puede subir al repositorio. Tercero, con CBC cualquiera ejecuta el proyecto con un solo comando, sin licencia. Y si se quisiera usar CPLEX o Gurobi, en PuLP basta cambiar una línea."

**Ant Colony Optimization** (verde azulado): "La cuarta técnica es una metaheurística, es decir, un método que no garantiza la mejor solución pero busca una muy buena de forma rápida. Se inspira en cómo las hormigas encuentran comida. Una hormiga sale a explorar y deja un rastro de feromona por donde pasa. Las demás tienden a seguir los rastros más fuertes, y como los caminos buenos se recorren más, su rastro se refuerza, mientras que el de los malos se evapora. Nosotros lanzamos 30 hormigas virtuales en 40 rondas. En cada ronda, cada hormiga arma una ruta eligiendo calle por calle, con más probabilidad las baratas y las que ya tienen feromona. Al final de la ronda se refuerzan las mejores rutas. Para que converja rápido, partimos con un camino inicial sencillo. Su ventaja es que explora alternativas que la optimización exacta no entrega, y es la más rápida de las cuatro."
[Activar y desactivar las cuatro capas en el mapa]

## 6. Resiliencia, algoritmo y tiempo (7:00–8:00) — Punto 8
[Tabla y leyenda]
"Usamos una sola métrica de resiliencia para las cuatro técnicas: la probabilidad de que la ruta se complete sin que ocurra ninguna amenaza. Se calcula como el producto de uno menos la probabilidad de cada evento distinto que la ruta cruza; una zona de ignición o un área protegida cuenta una sola vez, aunque la ruta pase por varias de sus aristas. Como es la misma fórmula para todas, las técnicas son comparables. Bajo cada checkbox se muestra el algoritmo, el tiempo de cómputo y esta resiliencia. En este caso: Dijkstra 3,6 % y las otras tres técnicas 72,0 %. Las tres encuentran la misma ruta, así que empatan como las más resilientes; hay pares donde el ACO supera a las demás, pero este no es uno. En tiempo, el ACO es la más rápida, 119 milisegundos, y Dijkstra la más lenta, 731, porque busca en toda la red de 126.000 aristas mientras el LP y el ACO trabajan solo sobre un corredor. La fila 'Superv. simulada' repite el sorteo aleatorio 100 veces y valida el número: coincide con el exacto salvo ruido de muestreo."

## 7. Simulación de fallas aleatorias (8:00–8:50) — Punto 7
[Presionar "Sortear fallas"]
"Esta opción sortea un número aleatorio entre 0 y 100 por cada amenaza. Si es menor que la probabilidad por cien, la falla ocurre. Las amenazas que son un solo evento, como una zona de ignición, se sortean una vez y afectan a todas las aristas y rutas por igual, es decir, es un escenario coherente. Las aristas que fallan se dibujan en negro y el popup muestra el número sorteado y el umbral. Debajo se indica qué rutas sobreviven. La fila 'Supervivencia' de la tabla repite este sorteo 100 veces y entrega el porcentaje."

## 8. Caso de ejemplo (8:50–10:10) — Punto 9
[Origen RETEN PLACILLA PENUELAS, destino M4.4 a 17 km SSE de Quilpué]
"Este es nuestro caso de ejemplo. La ruta por distancia mide 44.13 km, pero atraviesa un área protegida, donde el vuelo está prohibido, y una zona de ignición. La ruta resiliente las rodea: mide 45.47 km, solo 1,34 km más. La resiliencia, es decir la probabilidad de completar la ruta sin que ocurra una amenaza, se calcula y es de 19.8 El LP y el ACO encuentran la misma ruta alternativa."
[Presionar "Sortear fallas" varias veces]
"Al sortear fallas, la ruta por distancia se interrumpe casi siempre en el área protegida: en la simulación de 100 escenarios sobrevivió solo 2 veces. La ruta alternativa rara vez falla. Esto muestra que mitigamos las amenazas con un costo mínimo de distancia, que era nuestro objetivo inicial."
"Este sismo es de magnitud 4,4 y está a 48 km de profundidad, por eso su factor de intensidad es cercano a cero y no daña edificios: la amenaza aquí no es el sismo, sino el territorio que hay que cruzar para llegar a él."
"Un caso en que la mejora es mínima: algunos pares cruzan un tendido que es inevitable por la forma de la red; ahí todas las rutas fallan juntas. Lo mostramos porque la solución también tiene límites."

## 9. Calibración de pesos (10:10–11:10) — Justificación
"Los pesos no son arbitrarios: los probamos y elegimos el óptimo. Primero hicimos un barrido de 125 combinaciones que tardo un tiempo aproximado de 5 horas, de lo que seria el peso de tendido, ignición y antigüedad usando la métrica de resiliencia. Pero esa métrica usa la misma función de costo que el ruteo, así que decidimos validar con un criterio independiente: la supervivencia calculada de forma analítica sobre 48 pares origen-destino, exigiendo como máximo un 15 % de distancia extra. Dijkstra por distancia sobrevive en promedio un 21 %. Con tendido 1.2 e ignición 1.1 la ruta resiliente llega a 32,3 % con solo 4,7 % de distancia extra. Subir el peso del tendido a 20 solo suma 0,7 puntos con 14,8 % más de distancia, y a 100 supera el tope. Variar el peso de antigüedad entre 0.1 y 1.0 casi no cambia nada. Por eso mantuvimos 1.2, 1.1 y 0.3, que son el óptimo entre los probados."

## 10. Cierre (11:10–11:50)
"Más allá de la tarea, ¿para qué sirve esto? En una emergencia, quien decide el despacho de un dron no tiene tiempo de revisar mapas de amenazas. Con esta herramienta recibe una respuesta concreta: por dónde volar y qué probabilidad hay de llegar. Esa cifra permite decidir si enviar el dron, o si conviene mandar otro medio, en el momento en que cada minuto cuenta."
"Hay varios pasos naturales para llevarlo más lejos. Primero, incorporar el relieve, que ya está cargado en la base de datos pero todavía no entra al costo de la ruta, para considerar la altitud real del terreno. Segundo, actualizar las amenazas en tiempo real: los focos de incendio y los sismos ya vienen de servicios en línea, y el viento podría alimentarse del pronóstico. Tercero, reemplazar las probabilidades de criterio experto por frecuencias observadas, con datos históricos de fallas, y modelar que algunas amenazas se influyen entre sí, como el viento y el fuego. Y por último, usar un solver comercial para resolver modelos más grandes en menos tiempo."
"Todo lo que vimos se replica con un solo comando, `python3 main.py`. Muchas gracias."

---

## Preguntas probables y respuestas cortas
- **¿Por qué CBC y no CPLEX o Gurobi?** (1) Es lo mismo: CBC, CPLEX y Gurobi son solvers que ejecutan el método Simplex sobre el mismo modelo, y un LP tiene un único valor óptimo, así que los tres entregan la misma solución. (2) Las licencias gratuitas de CPLEX (1000 variables/restricciones) y Gurobi (2000) no alcanzan para nuestro LP (entre ~5.000 y ~21.000 variables, según el par); las licencias completas son académicas, requieren IP de la universidad y no se pueden distribuir en el repositorio. (3) Con CBC el proyecto corre en Docker con un solo comando para cualquiera, sin licencia. (4) PuLP desacopla modelo y solver: usar CPLEX/Gurobi es cambiar una línea. Difieren en velocidad (los comerciales son más rápidos en modelos muy grandes), pero el resultado óptimo es el mismo con cualquiera de los tres.
- **¿Por qué el LP no entrega mejor ruta que Dijkstra?** Porque el camino más corto en una red es un problema de flujo y su óptimo exacto coincide con el de Dijkstra sobre el mismo costo; el valor del LP es que admite restricciones adicionales, como el riesgo acumulado.
- **¿Por qué los pesos son tan bajos (1.2 y 1.1)?** Los probamos hasta 100; subirlos casi no mejora la supervivencia porque la mayoría de los cruces de tendido son inevitables en esta red, y cuesta mucha distancia.
- **¿De dónde salen los coeficientes del sismo?** Son de orden de magnitud, calibrados para que un M6.5 a 10 km dé intensidad VII y un M4.4 a 48 km de profundidad dé intensidad I–II; no pertenecen a un modelo publicado.
- **¿Y el relieve?** Está cargado como metadato, pero no entra al costo de ruteo ni tiene capa en el mapa.
