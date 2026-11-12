const map = L.map("map").setView([-33.03, -71.55], 11);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

function cargarGeoJSON(url, callback) {
  fetch(url)
    .then((r) => r.json())
    .then(callback)
    .catch((err) => console.warn("No se pudo cargar", url, err));
}

// Creación de paneles (Panes) con jerarquía estricta de Z-Index para que las rutas
// siempre brillen por encima del mapa base, polígonos CONAF y tendido eléctrico.
map.createPane("amenazasPane");
map.getPane("amenazasPane").style.zIndex = 410;

map.createPane("respaldoPane");
map.getPane("respaldoPane").style.zIndex = 445;

map.createPane("rutasPane");
map.getPane("rutasPane").style.zIndex = 460;
map.getPane("rutasPane").classList.add("leaflet-rutas-pane");

map.createPane("desviosPane");
map.getPane("desviosPane").style.zIndex = 470;
map.getPane("desviosPane").classList.add("leaflet-desvios-pane");

map.createPane("fallasPane");
map.getPane("fallasPane").style.zIndex = 480;

map.createPane("marcadoresRutaPane");
map.getPane("marcadoresRutaPane").style.zIndex = 490;

// Grupos de capas
let capasRutas = {
  dijkstra: L.featureGroup().addTo(map),
  resiliente: L.featureGroup().addTo(map),
  optimizacion_lp: L.featureGroup().addTo(map),
  aco: L.featureGroup().addTo(map),
};
let capaRespaldoK2 = L.featureGroup(); // Opcional (desactivada por defecto para no saturar las 4 rutas principales)
let capaMarcadoresRuta = L.featureGroup().addTo(map);

let capaCarabineros = L.featureGroup().addTo(map);
let capaSismos = L.featureGroup().addTo(map);
let capaTendido = L.featureGroup().addTo(map);
let capaCONAF = L.featureGroup().addTo(map);
let capaFIRMS = L.featureGroup().addTo(map);
let capaViento = L.featureGroup().addTo(map);

// Variables globales para catálogo y selección activa
let catalogoData = null;
let origenesList = [];
let sismosList = [];
let origenActivo = "8va_florida";
let destinoActivo = "sismo_15";
let vientoMaxActivo = 43.2; // restriccion de usuario (punto 6); 12 m/s, limite despegue/aterrizaje del DJI Matrice 300 RTK

const ALIAS_CUARTEL = {
  17: "8va_florida",
  25: "1ra_sur",
  23: "2da_central",
  21: "3ra_norte",
  20: "4ta_puerto",
  24: "1ra_vina",
  19: "5ta_vina",
  15: "subcom_concon",
  22: "2da_quilpue",
  18: "6ta_villa_alemana",
  2: "reten_placilla",
  28: "reten_laguna_verde",
};
const ID_A_ALIAS = ALIAS_CUARTEL;

// 1. Cargar Orígenes y Sismos (intentar API dinámica, fallback a rutas_catalogo.json)
Promise.all([
  fetch("/api/origenes").then((r) => (r.ok ? r.json() : null)).catch(() => null),
  fetch("/api/sismos").then((r) => (r.ok ? r.json() : null)).catch(() => null),
]).then(([origenesData, sismosData]) => {
  if (origenesData && origenesData.length > 0 && sismosData && sismosData.length > 0) {
    origenesList = origenesData;
    sismosList = sismosData;
    poblarSelectoresDinamicos(origenesData, sismosData);
  } else {
    // Fallback a catálogo estático si la API no estuviera disponible
    cargarGeoJSON("rutas_catalogo.json", (data) => {
      catalogoData = data;
      origenesList = data.origenes || [];
      sismosList = data.destinos || [];
      poblarSelectoresEstaticos(data);
    });
  }

  // Iniciar con la dupla por defecto
  actualizarRuta(origenActivo, destinoActivo);
});

// Poblar selector con los cuarteles y los sismos a <=2km de la red vial
// (unicos validos como objetivo de ruteo BVLOS; ver servidor.py)
function poblarSelectoresDinamicos(origenes, sismos) {
  const selOrigen = document.getElementById("sel-origen");
  const selDestino = document.getElementById("sel-destino");
  if (!selOrigen || !selDestino) return;

  selOrigen.innerHTML = "";
  selDestino.innerHTML = "";

  // Agrupar Orígenes por Comuna
  const origPorComuna = {};
  origenes.forEach((orig) => {
    if (!origPorComuna[orig.comuna]) origPorComuna[orig.comuna] = [];
    origPorComuna[orig.comuna].push(orig);
  });

  Object.keys(origPorComuna).sort().forEach((comuna) => {
    const optgroup = document.createElement("optgroup");
    optgroup.label = `Comuna: ${comuna}`;
    origPorComuna[comuna].forEach((orig) => {
      const opt = document.createElement("option");
      opt.value = orig.key;
      opt.textContent = `${orig.nombre} (${orig.es_comisaria ? "Comisaría Principal" : "Retén / Base"})`;
      if (orig.key === origenActivo) opt.selected = true;
      optgroup.appendChild(opt);
    });
    selOrigen.appendChild(optgroup);
  });

  // Agrupar Destinos: Sismos Terrestres, Sismos Costeros/Marítimos, Focos de Incendio
  const optgroupTerrestres = document.createElement("optgroup");
  optgroupTerrestres.label = "Sismos Continentales en Tierra (USGS)";

  const optgroupMaritimos = document.createElement("optgroup");
  optgroupMaritimos.label = "Sismos en Costa y Océano Pacífico (USGS)";

  const optgroupFocos = document.createElement("optgroup");
  optgroupFocos.label = "Focos Activos de Incendio (NASA FIRMS)";

  // Sismos clasificados
  sismos.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.key;
    const distStr = s.dist_red_m < 1000 ? `${Math.round(s.dist_red_m)}m red` : `${(s.dist_red_m / 1000).toFixed(1)}km red`;
    opt.textContent = `M${s.magnitud} - ${s.lugar} (${distStr})`;
    if (s.key === destinoActivo) opt.selected = true;

    if (s.en_tierra) {
      optgroupTerrestres.appendChild(opt);
    } else {
      optgroupMaritimos.appendChild(opt);
    }
  });

  // Focos activos FIRMS
  const optFirms1 = document.createElement("option");
  optFirms1.value = "firms_1";
  optFirms1.textContent = "Foco Incendio Activo FIRMS #1 (Valparaíso Cerros / Placilla)";
  optgroupFocos.appendChild(optFirms1);

  const optFirms2 = document.createElement("option");
  optFirms2.value = "firms_2";
  optFirms2.textContent = "Foco Incendio Activo FIRMS #2 (Sector Forestal Norte)";
  optgroupFocos.appendChild(optFirms2);

  selDestino.appendChild(optgroupTerrestres);
  selDestino.appendChild(optgroupMaritimos);
  selDestino.appendChild(optgroupFocos);

  // Escuchadores de cambio
  selOrigen.addEventListener("change", (e) => {
    actualizarRuta(e.target.value, selDestino.value);
  });

  selDestino.addEventListener("change", (e) => {
    actualizarRuta(selOrigen.value, e.target.value);
  });
}

function poblarSelectoresEstaticos(data) {
  const selOrigen = document.getElementById("sel-origen");
  const selDestino = document.getElementById("sel-destino");
  if (!selOrigen || !selDestino) return;

  selOrigen.innerHTML = "";
  selDestino.innerHTML = "";

  const origPorComuna = {};
  data.origenes.forEach((orig) => {
    if (!origPorComuna[orig.comuna]) origPorComuna[orig.comuna] = [];
    origPorComuna[orig.comuna].push(orig);
  });

  Object.keys(origPorComuna).sort().forEach((comuna) => {
    const optgroup = document.createElement("optgroup");
    optgroup.label = `Comuna: ${comuna}`;
    origPorComuna[comuna].forEach((orig) => {
      const opt = document.createElement("option");
      opt.value = orig.key;
      opt.textContent = `${orig.nombre}`;
      if (orig.key === origenActivo) opt.selected = true;
      optgroup.appendChild(opt);
    });
    selOrigen.appendChild(optgroup);
  });

  const optgroupSismos = document.createElement("optgroup");
  optgroupSismos.label = "Terremotos / Sismos (USGS)";
  const optgroupFocos = document.createElement("optgroup");
  optgroupFocos.label = "Focos Incendio (NASA FIRMS)";

  data.destinos.forEach((dest) => {
    const opt = document.createElement("option");
    opt.value = dest.key;
    opt.textContent = dest.descripcion;
    if (dest.key === destinoActivo) opt.selected = true;
    if (dest.tipo === "sismo") optgroupSismos.appendChild(opt);
    else optgroupFocos.appendChild(opt);
  });

  selDestino.appendChild(optgroupSismos);
  selDestino.appendChild(optgroupFocos);

  selOrigen.addEventListener("change", (e) => actualizarRuta(e.target.value, selDestino.value));
  selDestino.addEventListener("change", (e) => actualizarRuta(selOrigen.value, e.target.value));
}

// Búsqueda inteligente de cuarteles
function buscarOrigenCercano(nombre, lat, lon) {
  if (origenesList.length > 0) {
    let encontrado = origenesList.find((o) => (o.nombre || "").toUpperCase() === (nombre || "").toUpperCase());
    if (encontrado) return encontrado.key;
    let minDist = Infinity;
    origenesList.forEach((o) => {
      const d = Math.hypot(o.lat - lat, o.lon - lon);
      if (d < minDist) {
        minDist = d;
        encontrado = o;
      }
    });
    return encontrado ? encontrado.key : "8va_florida";
  }
  return "8va_florida";
}

// Actualización Dinámica de Ruta
function actualizarRuta(origenKey, destinoKey) {
  origenActivo = origenKey;
  destinoActivo = destinoKey;

  // Sincronizar selectores
  const selOrigen = document.getElementById("sel-origen");
  const selDestino = document.getElementById("sel-destino");
  if (selOrigen && selOrigen.value !== origenKey) selOrigen.value = origenKey;
  if (selDestino) {
    if (selDestino.value !== destinoKey) {
      selDestino.value = destinoKey;
      // Si el elemento no existe aún en el desplegable, lo agregamos dinámicamente
      if (!selDestino.value && destinoKey) {
        const opt = document.createElement("option");
        opt.value = destinoKey;
        opt.textContent = `Emergencia seleccionada (${destinoKey})`;
        selDestino.appendChild(opt);
        selDestino.value = destinoKey;
      }
    }
  }

  const infoBox = document.getElementById("info-seleccion");
  if (infoBox) {
    infoBox.innerHTML = `<b>Calculando ruta óptima y resiliente con pgRouting en tiempo real...</b>`;
  }

  // 1. Invocar la API dinámica de pgRouting
  fetch(`/api/calcular_ruta?origen=${encodeURIComponent(origenKey)}&destino=${encodeURIComponent(destinoKey)}&viento_max=${encodeURIComponent(vientoMaxActivo)}`)
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then((data) => {
      renderizarRuta(data);
    })
    .catch((err) => {
      console.warn("API dinámica falló, usando catálogo local de respaldo:", err);
      if (catalogoData && catalogoData.rutas) {
        const parKey = `${origenKey}_${destinoKey}`;
        let fallbackData = catalogoData.rutas[parKey];
        if (!fallbackData) {
          const claves = Object.keys(catalogoData.rutas);
          const coincidente = claves.find((k) => k.startsWith(origenKey) || k.endsWith(destinoKey));
          fallbackData = catalogoData.rutas[coincidente || claves[0]];
        }
        if (fallbackData) renderizarRuta(fallbackData);
      }
    });
}

let ultimoResultado = null;
let rutaEnfocadaActiva = "todas";
const capaFallas = L.featureGroup().addTo(map);
const capasDesvios = {
  dijkstra: L.featureGroup().addTo(map),
  resiliente: L.featureGroup().addTo(map),
  optimizacion_lp: L.featureGroup().addTo(map),
  aco: L.featureGroup().addTo(map),
};
function limpiarDesvios() {
  Object.values(capasDesvios).forEach((g) => g.clearLayers());
}

function renderizarRuta(rutaData) {
  ultimoResultado = rutaData;
  capaFallas.clearLayers();
  limpiarDesvios();
  const _sr = document.getElementById("sim-resultado"); if (_sr) _sr.innerHTML = "";
  if (!rutaData) return;

  // Limpiar capas previas
  capasRutas.dijkstra.clearLayers();
  capasRutas.resiliente.clearLayers();
  capasRutas.optimizacion_lp.clearLayers();
  capasRutas.aco.clearLayers();
  capaRespaldoK2.clearLayers();
  capaMarcadoresRuta.clearLayers();

  const orig = rutaData.origen;
  const dest = rutaData.destino;

  // Helper para dibujar rutas con borde oscuro de alto contraste + núcleo neón brillante
  function agregarTrazoNeon(grupo, geom, optsCore, popupHtml, paneName = "rutasPane") {
    const casingWeight = (optsCore.weight || 6) + 4.5;
    const casing = L.geoJSON(geom, {
      pane: paneName,
      interactive: false,
      style: {
        color: "#090d16",
        weight: casingWeight,
        opacity: optsCore.casingOpacity ?? 0.88,
        lineCap: "round",
        lineJoin: "round",
        dashArray: optsCore.casingDashArray || null,
        dashOffset: optsCore.dashOffset || null,
      },
    });
    const core = L.geoJSON(geom, {
      pane: paneName,
      style: {
        color: optsCore.color,
        weight: optsCore.weight || 6,
        opacity: optsCore.opacity ?? 1.0,
        lineCap: "round",
        lineJoin: "round",
        dashArray: optsCore.dashArray || null,
        dashOffset: optsCore.dashOffset || null,
      },
    }).bindPopup(popupHtml);
    grupo.addLayer(casing);
    grupo.addLayer(core);
  }

  // 1. Dibujar Ruta Dijkstra Principal (Rojo Carmesí Neón) y Respaldo k=2
  if (rutaData.dijkstra_1 && rutaData.dijkstra_1.geom) {
    agregarTrazoNeon(
      capasRutas.dijkstra,
      rutaData.dijkstra_1.geom,
      { color: "#ff1744", weight: 6.5, opacity: 1.0 },
      `<b>Dijkstra Ruta 1 (Distancia Pura)</b><br>Distancia: ${Math.round(rutaData.dijkstra_1.distancia_m)} m<br>Tramos: ${rutaData.dijkstra_1.tramos}`
    );
  }

  if (rutaData.dijkstra_2 && rutaData.dijkstra_2.geom) {
    const l2 = L.geoJSON(rutaData.dijkstra_2.geom, {
      pane: "respaldoPane",
      style: { color: "#ff9100", weight: 3.5, opacity: 0.8, dashArray: "6, 6" },
    }).bindPopup(`<b>Dijkstra Ruta 2 (Bypass Distancia k=2)</b><br>Distancia: ${Math.round(rutaData.dijkstra_2.distancia_m)} m<br>Tramos: ${rutaData.dijkstra_2.tramos}`);
    capaRespaldoK2.addLayer(l2);
  }

  // 2. Dibujar Ruta Resiliente Principal (Cian Eléctrico Brillante) y Respaldo k=2
  if (rutaData.resiliente_1 && rutaData.resiliente_1.geom) {
    agregarTrazoNeon(
      capasRutas.resiliente,
      rutaData.resiliente_1.geom,
      { color: "#00d2ff", weight: 7.0, opacity: 1.0 },
      `<b>Resiliente Ruta 1 (Corredor Seguro)</b><br>Distancia: ${Math.round(rutaData.resiliente_1.distancia_m)} m<br>Tramos: ${rutaData.resiliente_1.tramos}<br><i>Esquiva tendido eléctrico y áreas protegidas</i>`
    );
  }

  if (rutaData.resiliente_2 && rutaData.resiliente_2.geom) {
    const r2 = L.geoJSON(rutaData.resiliente_2.geom, {
      pane: "respaldoPane",
      style: { color: "#38bdf8", weight: 3.5, opacity: 0.85, dashArray: "6, 6" },
    }).bindPopup(`<b>Resiliente Ruta 2 (Bypass Seguro k=2)</b><br>Distancia: ${Math.round(rutaData.resiliente_2.distancia_m)} m<br>Tramos: ${rutaData.resiliente_2.tramos}<br><i>Camino alternativo disjunto seguro</i>`);
    capaRespaldoK2.addLayer(r2);
  }

  // 3. Ruta Programación Lineal — Simplex (Magenta Neón intercalado para verse junto a Resiliente)
  if (rutaData.optimizacion_lp && rutaData.optimizacion_lp.geom) {
    agregarTrazoNeon(
      capasRutas.optimizacion_lp,
      rutaData.optimizacion_lp.geom,
      { color: "#f000ff", weight: 5.5, opacity: 1.0, dashArray: "14, 14", casingDashArray: "14, 14", casingOpacity: 0.75 },
      `<b>Programación Lineal — Simplex</b><br>Distancia: ${Math.round(rutaData.optimizacion_lp.distancia_m)} m<br>Tramos: ${rutaData.optimizacion_lp.tramos}<br>Iteraciones Simplex: ${rutaData.optimizacion_lp.simplex?.iteraciones_simplex ?? "—"}<br>Variables/restricciones: ${rutaData.optimizacion_lp.simplex?.variables ?? "—"} / ${rutaData.optimizacion_lp.simplex?.restricciones ?? "—"}<br><i>mín costo s.a. conservación de flujo</i>`
    );
  }

  // 4. Ruta Metaheurística ACO (Verde Lima Fluorescente con desfase para distinguir solapamientos)
  if (rutaData.aco && rutaData.aco.geom) {
    agregarTrazoNeon(
      capasRutas.aco,
      rutaData.aco.geom,
      { color: "#39ff14", weight: 5.5, opacity: 1.0, dashArray: "8, 12", casingDashArray: "8, 12", dashOffset: "10" },
      `<b>Metaheurística ACO (Ant Colony)</b><br>Distancia: ${Math.round(rutaData.aco.distancia_m)} m<br>Tramos: ${rutaData.aco.tramos}<br><i>20 hormigas × 30 iteraciones</i>`
    );
  }

  // Aplicar estado de enfoque activo si el usuario eligió destacar una técnica
  aplicarEnfoqueVisual(rutaEnfocadaActiva);

  // 5. Marcadores Destacados de Inicio y Destino
  L.circleMarker([orig.lat, orig.lon], {
    pane: "marcadoresRutaPane",
    radius: 10,
    color: "#ffffff",
    fillColor: "#0077ff",
    fillOpacity: 1,
    weight: 3,
  }).bindPopup(`<b>BASE DE OPERACIÓN DRON (ORIGEN)</b><br><b>${orig.nombre}</b><br>Comuna: ${orig.comuna}<br>Punto de carga habilitado`).addTo(capaMarcadoresRuta);

  L.circleMarker([dest.lat, dest.lon], {
    pane: "marcadoresRutaPane",
    radius: 11,
    color: "#ffffff",
    fillColor: dest.tipo === "incendio" ? "#ff6d00" : "#ff1744",
    fillOpacity: 1,
    weight: 3,
  }).bindPopup(`<b>OBJETIVO DE EMERGENCIA (DESTINO)</b><br><b>${dest.descripcion}</b><br>Coordenadas: ${dest.lat}, ${dest.lon}`).addTo(capaMarcadoresRuta);

  // 4. Centrar y encuadrar el mapa en la nueva trayectoria
  try {
    const grupoTotal = L.featureGroup([capasRutas.resiliente, capasRutas.dijkstra, capasRutas.optimizacion_lp, capasRutas.aco, capaMarcadoresRuta]);
    map.fitBounds(grupoTotal.getBounds(), { padding: [55, 55], maxZoom: 13 });
  } catch (err) {
    map.setView([orig.lat, orig.lon], 11);
  }

  // 5. Actualizar Caja Informativa
  const infoBox = document.getElementById("info-seleccion");
  if (infoBox) {
    const msInfo = rutaData.tiempo_calculo_ms ? ` <span style="font-size:10px; background:#e8f4f8; color:#1b4f72; padding:2px 6px; border-radius:3px; font-weight:bold;">pgRouting: ${rutaData.tiempo_calculo_ms} ms</span>` : "";
    const r = rutaData.restricciones;
    const restrInfo = r ? ` <span style="font-size:10px; background:#fff8e1; color:#8a6d00; padding:2px 6px; border-radius:3px;">viento máx: ${r.viento_max_kmh} km/h${r.es_default ? " (default)" : ""}</span>` : "";
    infoBox.innerHTML = `<b>Trayecto Activo:</b> ${orig.nombre} (${orig.comuna}) <br>&rarr; <b>${dest.descripcion}</b>${msInfo}${restrInfo}`;
  }

  // 8. Actualizar Métricas en la Tabla (4 técnicas)
  const m = rutaData.metricas || {};
  const sim = rutaData.simulacion_fallas || {};

  if (m.dijkstra) {
    document.getElementById("m-dist-dijkstra").innerText = `${m.dijkstra.distancia_promedio_km}`;
    document.getElementById("m-riesgo-dijkstra").innerText = `${m.dijkstra.aristas_con_riesgo}`;
    document.getElementById("m-resil-dijkstra").innerText = `${m.dijkstra.resiliencia_score}%`;
    document.getElementById("m-time-dijkstra").innerText = `${m.dijkstra.tiempo_ms || "—"}`;
    document.getElementById("m-sim-dijkstra").innerText = sim.dijkstra ? `${sim.dijkstra.tasa_supervivencia}%` : "—";
  }
  if (m.resiliente) {
    document.getElementById("m-dist-resil").innerText = `${m.resiliente.distancia_promedio_km}`;
    document.getElementById("m-riesgo-resil").innerText = `${m.resiliente.aristas_con_riesgo}`;
    document.getElementById("m-resil-resil").innerText = `${m.resiliente.resiliencia_score}%`;
    document.getElementById("m-time-resil").innerText = `${m.resiliente.tiempo_ms || "—"}`;
    document.getElementById("m-sim-resil").innerText = sim.resiliente ? `${sim.resiliente.tasa_supervivencia}%` : "—";
  }
  if (m.optimizacion_lp && m.optimizacion_lp.resiliencia_score !== undefined) {
    document.getElementById("m-dist-lp").innerText = `${m.optimizacion_lp.distancia_promedio_km || "—"}`;
    document.getElementById("m-riesgo-lp").innerText = `${m.optimizacion_lp.aristas_con_riesgo ?? "—"}`;
    document.getElementById("m-resil-lp").innerText = `${m.optimizacion_lp.resiliencia_score}%`;
    document.getElementById("m-time-lp").innerText = `${m.optimizacion_lp.tiempo_ms || "—"}`;
    document.getElementById("m-sim-lp").innerText = sim.optimizacion_lp ? `${sim.optimizacion_lp.tasa_supervivencia}%` : "—";
  } else {
    ["m-dist-lp","m-riesgo-lp","m-resil-lp","m-time-lp","m-sim-lp"].forEach(id => { document.getElementById(id).innerText = "—"; });
  }
  if (m.aco && m.aco.resiliencia_score !== undefined) {
    document.getElementById("m-dist-aco").innerText = `${m.aco.distancia_promedio_km || "—"}`;
    document.getElementById("m-riesgo-aco").innerText = `${m.aco.aristas_con_riesgo ?? "—"}`;
    document.getElementById("m-resil-aco").innerText = `${m.aco.resiliencia_score}%`;
    document.getElementById("m-time-aco").innerText = `${m.aco.tiempo_ms || "—"}`;
    document.getElementById("m-sim-aco").innerText = sim.aco ? `${sim.aco.tasa_supervivencia}%` : "—";
  } else {
    ["m-dist-aco","m-riesgo-aco","m-resil-aco","m-time-aco","m-sim-aco"].forEach(id => { document.getElementById(id).innerText = "—"; });
  }

  // Leyenda: algoritmo + tiempo de cómputo + resiliencia (punto 8)
  [["dijkstra","dijkstra","Dijkstra (distancia)"],["resil","resiliente","pgr_dijkstra multi-amenaza"],["lp","optimizacion_lp","Simplex (PL)"],["aco","aco","Ant Colony"]].forEach(([id,k,nom]) => {
    const el = document.getElementById("leg-" + id);
    const t = m[k];
    el.innerText = t && t.resiliencia_score !== undefined ? `${nom} · ${t.tiempo_ms} ms · R = ${t.resiliencia_score}%` : `${nom} · sin ruta`;
  });

  // Badge: mejor técnica
  const tecnicas = [
    { nombre: "Dijkstra", score: m.dijkstra?.resiliencia_score || 0 },
    { nombre: "Resiliente", score: m.resiliente?.resiliencia_score || 0 },
    { nombre: "LP Simplex", score: m.optimizacion_lp?.resiliencia_score || 0 },
    { nombre: "ACO", score: m.aco?.resiliencia_score || 0 },
  ].filter(t => t.score > 0);
  const badge = document.getElementById("badge-diferencial");
  if (tecnicas.length > 0) {
    tecnicas.sort((a, b) => b.score - a.score);
    const mejor = tecnicas[0];
    badge.innerText = `Técnica más resiliente: ${mejor.nombre} (R = ${mejor.score}%)`;
    badge.style.background = "#ebfbee";
    badge.style.color = "#2b8a3e";
    badge.style.borderColor = "#b2f2bb";
  }
}

// Funciones globales invocadas desde los popups al hacer clic en el mapa
window.fijarOrigen = function (key) {
  actualizarRuta(key, destinoActivo);
};

window.fijarDestino = function (key) {
  actualizarRuta(origenActivo, key);
};

// 2. Capa Carabineros (con botón interactivo para fijar como origen)
cargarGeoJSON("data/carabineros.geojson", (geojson) => {
  L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => {
      const esCom = feature.properties.es_comisaria;
      return L.circleMarker(latlng, {
        radius: esCom ? 7.5 : 4.5,
        color: "#1a252f",
        weight: 1.5,
        fillColor: esCom ? "#2980b9" : "#7f8c8d",
        fillOpacity: 0.9,
      });
    },
    onEachFeature: (f, layer) => {
      const p = f.properties;
      const coords = f.geometry.coordinates;
      layer.on("click", () => {
        const keyCuartel = p.id ? (ID_A_ALIAS[p.id] || `cuartel_${p.id}`) : buscarOrigenCercano(p.nombre, coords[1], coords[0]);
        layer.bindPopup(`
          <b>${p.nombre}</b><br>
          Comuna: ${p.comuna}<br>
          <i>${p.es_comisaria ? "Comisaría Principal" : "Retén / Tenencia"} · Base de Carga</i><br>
          <button class="btn-popup btn-origen" onclick="window.fijarOrigen('${keyCuartel}')" style="margin-top:6px; width:100%;">
            Fijar como Inicio del Dron
          </button>
        `).openPopup();
      });
    },
  }).addTo(capaCarabineros);
});

// 3. Capa Sismos (TODOS los 106, informativa; solo los que cumplen el
//    umbral dinamico magnitud+viento son fijables como destino, ver
//    destino_valido. "en_tierra" es un hecho geografico INDEPENDIENTE:
//    un sismo en el mar puede igual ser destino_valido si es grande y el
//    viento de su zona es favorable - no confundir ambos campos.)
cargarGeoJSON("data/sismos.geojson", (geojson) => {
  L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => {
      const p = feature.properties;
      const mag = p.magnitud || 2;
      const valido = p.destino_valido;
      return L.circleMarker(latlng, {
        radius: Math.max(4.5, mag * 2.3),
        color: valido ? "#962d00" : "#1b4f72",
        weight: 1.4,
        fillColor: valido ? "#e67e22" : "#3498db",
        fillOpacity: 0.75,
      });
    },
    onEachFeature: (f, layer) => {
      const p = f.properties;
      const sismoKey = `sismo_${p.id}`;
      const distStr = p.dist_red_m < 1000 ? `${Math.round(p.dist_red_m)}m` : `${(p.dist_red_m / 1000).toFixed(1)}km`;
      const umbralStr = p.umbral_valido_km != null ? `${p.umbral_valido_km}km` : "—";
      const zonaStr = p.en_tierra ? "Epicentro en tierra" : "Epicentro en el mar";
      const ubicacionStr = `${zonaStr}, a ${distStr} de la red vial — alcance máximo aquí: ${umbralStr} (según magnitud del sismo y viento actual)`;
      const botonDestino = p.destino_valido
        ? `<button class="btn-popup btn-destino" onclick="window.fijarDestino('${sismoKey}')" style="margin-top:6px; width:100%;">
             Fijar como Objetivo de Emergencia
           </button>`
        : `<div class="tip-box" style="margin-top:6px;">No seleccionable: está a ${distStr} de la calle más cercana, pero con este sismo (M${p.magnitud}) y el viento actual en esa zona (${p.racha_zona_kmh ?? "—"} km/h) el dron solo puede alejarse hasta ${umbralStr} de la red vial. Al estar más lejos que eso, no es alcanzable.</div>`;
      layer.on("click", () => {
        layer.bindPopup(`
          <div style="font-size:12.5px; line-height:1.4;">
            <b style="font-size:13.5px; color:#c0392b;">Sismo M${p.magnitud}</b><br>
            <b>Lugar:</b> ${p.lugar}<br>
            <b>Profundidad:</b> ${p.profundidad_km != null ? p.profundidad_km + ' km' : 'No registrada'}<br>
            <b>Zona:</b> ${ubicacionStr}<br>
            ${botonDestino}
          </div>
        `).openPopup();
      });
    },
  }).addTo(capaSismos);
});

// 4. Capa Tendido Eléctrico (en amenazasPane, tono pizarra sutil para no competir con las rutas)
cargarGeoJSON("data/tendido_electrico.geojson", (geojson) => {
  L.geoJSON(geojson, {
    pane: "amenazasPane",
    style: { color: "#64748b", weight: 1.8, opacity: 0.5, dashArray: "3, 5" },
    onEachFeature: (f, layer) => {
      layer.bindPopup(`<b>Línea de Tendido Eléctrico (${f.properties.tipo})</b><br>Riesgo físico de colisión BVLOS<br>Penalización en ruta resiliente: x1.54 costo`);
    },
  }).addTo(capaTendido);
});

// 5. Capa CONAF Áreas Protegidas (en amenazasPane, relleno translúcido suave)
cargarGeoJSON("data/conaf_areas_protegidas.geojson", (geojson) => {
  L.geoJSON(geojson, {
    pane: "amenazasPane",
    style: { color: "#15803d", fillColor: "#22c55e", fillOpacity: 0.14, weight: 1.5 },
    onEachFeature: (f, layer) => {
      layer.bindPopup(`<b>${f.properties.nombre}</b><br>${f.properties.categoria}<br>Zona de Exclusión DAN 151 / SNASPE<br>Costo de penalización: x1000`);
    },
  }).addTo(capaCONAF);
});

// 6. Capa NASA FIRMS (Focos de calor)
cargarGeoJSON("data/firms.geojson", (geojson) => {
  L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => {
      return L.circleMarker(latlng, {
        radius: 7,
        color: "#78281f",
        weight: 1.5,
        fillColor: "#d35400",
        fillOpacity: 0.9,
      });
    },
    onEachFeature: (f, layer) => {
      const fKey = `firms_${f.properties.id || 1}`;
      layer.on("click", () => {
        layer.bindPopup(`
          <b>Foco Activo de Incendio (NASA FIRMS)</b><br>
          Fecha detección: ${f.properties.fecha}<br>
          Corte operacional por humo/calor: 500m<br>
          <button class="btn-popup btn-destino" onclick="window.fijarDestino('${fKey}')" style="margin-top:6px; width:100%;">
            Fijar como Incendio a Evaluar
          </button>
        `).openPopup();
      });
    },
  }).addTo(capaFIRMS);
});

// 7. Capa Viento
cargarGeoJSON("data/viento.geojson", (geojson) => {
  L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => {
      return L.circleMarker(latlng, {
        radius: 8,
        color: "#1b4f72",
        weight: 1.5,
        fillColor: "#3498db",
        fillOpacity: 0.8,
      });
    },
    onEachFeature: (f, layer) => {
      const p = f.properties;
      layer.bindPopup(`<b>Estación Meteorológica: ${p.comuna}</b><br>${p.sector}<br>Viento medio: ${p.viento_medio_kmh || p.viento_kmh} km/h<br>Racha promedio (usada como restricción operacional): ${p.racha_media_kmh} km/h<br>Racha máx. pronosticada (peor caso, informativo): ${p.racha_max_kmh} km/h<br>Elevación: ${p.elevacion_m} m`);
    },
  }).addTo(capaViento);
});

// Control de Checkboxes de Capas
function toggleCapa(id, grupo, grupoExtra = null) {
  const chk = document.getElementById(id);
  if (chk) {
    chk.addEventListener("change", (e) => {
      if (e.target.checked) {
        map.addLayer(grupo);
        if (grupoExtra) map.addLayer(grupoExtra);
      } else {
        map.removeLayer(grupo);
        if (grupoExtra) map.removeLayer(grupoExtra);
      }
    });
  }
}

toggleCapa("chk-capa-dijkstra", capasRutas.dijkstra, capasDesvios.dijkstra);
toggleCapa("chk-capa-resil", capasRutas.resiliente, capasDesvios.resiliente);
toggleCapa("chk-capa-lp", capasRutas.optimizacion_lp, capasDesvios.optimizacion_lp);
toggleCapa("chk-capa-aco", capasRutas.aco, capasDesvios.aco);
toggleCapa("chk-capa-k2", capaRespaldoK2);
toggleCapa("chk-capa-carabineros", capaCarabineros);
toggleCapa("chk-capa-sismos", capaSismos);
toggleCapa("chk-capa-tendido", capaTendido);
toggleCapa("chk-capa-conaf", capaCONAF);
toggleCapa("chk-capa-firms", capaFIRMS);
toggleCapa("chk-capa-viento", capaViento);

// Sistema de Enfoque / Spotlight para distinguir y evaluar cada una de las 4 rutas sin confusión
function aplicarEnfoqueVisual(keyEnfoque) {
  const estilosBase = {
    dijkstra: { weight: 6.5, dashArray: null },
    resiliente: { weight: 7.0, dashArray: null },
    optimizacion_lp: { weight: 5.5, dashArray: "14, 14" },
    aco: { weight: 5.5, dashArray: "8, 12" },
  };
  Object.entries(capasRutas).forEach(([k, grupo]) => {
    const esFoco = keyEnfoque === "todas" || keyEnfoque === k;
    const base = estilosBase[k];
    const sublayers = grupo.getLayers();
    sublayers.forEach((layer, idx) => {
      if (layer._esDescartada) {
        layer.setStyle({ opacity: esFoco ? 0.35 : 0.06 });
        return;
      }
      const esCasing = idx === 0;
      if (esFoco) {
        const extra = keyEnfoque === k ? 2.0 : 0.0;
        layer.setStyle({
          opacity: esCasing ? 0.92 : 1.0,
          weight: (esCasing ? base.weight + 4.5 : base.weight) + extra,
          dashArray: keyEnfoque === k ? null : base.dashArray,
        });
        if (keyEnfoque === k && layer.bringToFront) layer.bringToFront();
      } else {
        layer.setStyle({
          opacity: esCasing ? 0.08 : 0.14,
          weight: esCasing ? base.weight + 2 : base.weight - 1,
          dashArray: base.dashArray,
        });
      }
    });
  });

  // Aplicar también sobre las rutas alternativas recalculadas (capasDesvios)
  Object.entries(capasDesvios).forEach(([k, grupo]) => {
    const esFoco = keyEnfoque === "todas" || keyEnfoque === k;
    const base = estilosBase[k];
    const sublayers = grupo.getLayers();
    sublayers.forEach((layer, idx) => {
      const esCasing = idx === 0;
      if (esFoco) {
        const extra = keyEnfoque === k ? 2.0 : 0.0;
        layer.setStyle({
          opacity: esCasing ? 0.92 : 1.0,
          weight: (esCasing ? base.weight + 4.5 : base.weight) + extra,
        });
        if (keyEnfoque === k && layer.bringToFront) layer.bringToFront();
      } else {
        layer.setStyle({
          opacity: esCasing ? 0.06 : 0.12,
          weight: esCasing ? base.weight + 1 : base.weight - 1.5,
        });
      }
    });
  });
}

window.enfocarRuta = function (key) {
  rutaEnfocadaActiva = key;
  document.querySelectorAll("#bar-enfoque .pill-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-focus") === key);
  });
  aplicarEnfoqueVisual(key);
};

// Hover rápido sobre las filas de la leyenda para previsualizar cada ruta
document.querySelectorAll(".route-row").forEach((row) => {
  const rKey = row.getAttribute("data-route");
  row.addEventListener("mouseenter", () => aplicarEnfoqueVisual(rKey));
  row.addEventListener("mouseleave", () => aplicarEnfoqueVisual(rutaEnfocadaActiva));
});

// Selector de modo de contraste del mapa base
const selMapaModo = document.getElementById("sel-mapa-modo");
if (selMapaModo) {
  selMapaModo.addEventListener("change", (e) => {
    const mapEl = document.getElementById("map");
    mapEl.classList.remove("map-atenuado", "map-oscuro", "map-normal");
    mapEl.classList.add(e.target.value);
  });
}

// Control de Slider de Viento (restricción de usuario, punto 6 de la rúbrica)
const rngViento = document.getElementById("rng-viento");
if (rngViento) {
  rngViento.addEventListener("input", (e) => {
    const v = Number(e.target.value);
    const nota = v >= 54 ? "límite físico DJI M300 RTK en vuelo" : v >= 43 ? "DJI M300 RTK, despegue/aterrizaje" : "más conservador que el límite del fabricante";
    document.getElementById("lbl-viento").innerText = `${v} km/h (${nota})`;
  });
  rngViento.addEventListener("change", (e) => {
    // Se dispara al soltar el slider (no en cada pixel), recalcula la ruta
    // resiliente con el nuevo umbral: aristas en zonas cuya racha pronosticada
    // supera este valor quedan fuertemente penalizadas (x1000) en esa ruta.
    vientoMaxActivo = Number(e.target.value);
    actualizarRuta(origenActivo, destinoActivo);
  });
}


// Punto 7, 8 y 9: sorteo aleatorio [0, 100] a lo largo de la ruta (0% = inicio, 100% = destino),
// descarte del tramo original restante tras el punto de falla, recálculo por calles alternativas
// y re-evaluación de las métricas comparativas de las 4 técnicas.
const btnSimReset = document.getElementById("btn-sim-reset");
if (btnSimReset) {
  btnSimReset.addEventListener("click", () => {
    if (ultimoResultado) {
      renderizarRuta(ultimoResultado);
    } else {
      capaFallas.clearLayers();
      limpiarDesvios();
      const sr = document.getElementById("sim-resultado");
      if (sr) sr.innerHTML = "";
    }
  });
}

document.getElementById("btn-sim").addEventListener("click", () => {
  if (!ultimoResultado) return;
  capaFallas.clearLayers();
  limpiarDesvios();

  const colores = { dijkstra: "#ff1744", resiliente: "#0088cc", optimizacion_lp: "#c000d6", aco: "#00a846" };
  const coloresTrazo = { dijkstra: "#ff1744", resiliente: "#00d2ff", optimizacion_lp: "#f000ff", aco: "#39ff14" };
  const nombres = { dijkstra: "Dijkstra", resiliente: "Resiliente", optimizacion_lp: "LP Simplex", aco: "ACO" };
  const etiquetaAm = {
    tendido: "cruce de tendido eléctrico",
    ignicion: "celda de ignición CONAF",
    areas_protegidas: "área protegida SNASPE",
    firms: "foco activo FIRMS",
    viento: "racha de viento sobre umbral",
    sismo_edificacion: "manzana con edificación antigua",
  };

  // Sorteo aleatorio entre 0 y 100 de progreso en la ruta (acotado al tramo en vuelo [20%, 82%]
  // para que exista tanto tramo recorrido como tramo restante a recalcular por vía alternativa).
  const pctSorteo = Math.round((20 + Math.random() * 62) * 10) / 10;
  const pctRestanteEst = (100 - pctSorteo).toFixed(1);

  const srEl = document.getElementById("sim-resultado");
  srEl.innerHTML = `<div style="color:#2b6cb0; font-weight:600;">🎲 Sorteo de falla en ruta: <b>${pctSorteo}%</b> del trayecto (resta <b>${pctRestanteEst}%</b>).<br><span style="font-weight:normal; font-style:italic; color:#4a5568;">Descartando resto de ruta original y recalculando calles alternativas y métricas...</span></div>`;

  fetch(`/api/desvio_falla?origen=${encodeURIComponent(origenActivo)}&destino=${encodeURIComponent(destinoActivo)}&viento_max=${encodeURIComponent(vientoMaxActivo)}&pct_fallo=${encodeURIComponent(pctSorteo)}`)
    .then(r => (r.ok ? r.json() : null))
    .then(desvData => {
      if (!desvData || !desvData.desvios) {
        srEl.innerHTML = `<div style="color:#c53030;">No fue posible recalcular el desvío.</div>`;
        return;
      }

      const desvios = desvData.desvios;
      const mRec = desvData.metricas_recalculadas || {};
      const simRec = desvData.simulacion_recalculada || {};

      // Limpiar trazos previos de las 4 rutas para reemplazarlos por:
      // (a) Tramo ya recorrido (0% -> X%) en trazo continuo neón
      // (b) Tramo original descartado (X% -> 100%) en línea tenue gris/roja tachada
      // (c) Nueva ruta alternativa recalculada (X% -> 100%) en trazo neón punteado brillante
      Object.values(capasRutas).forEach(g => g.clearLayers());
      capaFallas.clearLayers();
      limpiarDesvios();

      const puntosVistos = new Set();
      let html = `<div style="background:#edf2f7; padding:5px 8px; border-radius:5px; margin-bottom:6px; font-size:11px; color:#1a202c;">` +
        `<b>🎲 Sorteo aleatorio [0–100]: ${desvData.pct_fallo_sorteado}% del camino</b><br>` +
        `Desde el punto de quiebre se <b>descarta el resto de la ruta original</b> y se toma una calle alternativa recalculando las métricas:</div>`;

      for (const k of Object.keys(nombres)) {
        const d = desvios[k];
        if (!d) continue;
        const grupoBase = capasRutas[k];
        const grupoDesv = capasDesvios[k];

        // 1. Tramo ya recorrido antes de la falla (0% -> X%)
        if (d.geom_recorrida) {
          const casingRec = L.geoJSON(d.geom_recorrida, {
            pane: "rutasPane",
            interactive: false,
            style: { color: "#090d16", weight: 10.5, opacity: 0.9, lineCap: "round", lineJoin: "round" },
          });
          const coreRec = L.geoJSON(d.geom_recorrida, {
            pane: "rutasPane",
            style: { color: coloresTrazo[k], weight: 6, opacity: 1.0, lineCap: "round", lineJoin: "round" },
          }).bindPopup(
            `<b>${nombres[k]} — Tramo ya recorrido (0% → ${d.pct_recorrido}%)</b><br>` +
            `Distancia recorrida antes del fallo: <b>${(d.distancia_recorrida_m / 1000).toFixed(2)} km</b> (${d.tramos_recorridos} tramos)`
          );
          grupoBase.addLayer(casingRec);
          grupoBase.addLayer(coreRec);
        }

        // 2. Resto de la ruta original preestablecida DESCARTADA (X% -> 100%)
        if (d.geom_descartada) {
          const descLayer = L.geoJSON(d.geom_descartada, {
            pane: "respaldoPane",
            style: { color: coloresTrazo[k], weight: 3, opacity: 0.28, dashArray: "3, 7" },
          }).bindPopup(
            `<b>✗ ${nombres[k]} — Tramo Original DESCARTADO (${d.pct_recorrido}% → 100%)</b><br>` +
            `Este ${d.pct_restante}% restante de la ruta preestablecida fue descartado tras la falla en la arista #${d.arista_fallada_id}.`
          );
          descLayer._esDescartada = true;
          grupoBase.addLayer(descLayer);
        }

        // 3. Marcador y tramo de falla en el punto de quiebre (X% de la ruta)
        if (d.geom_falla) {
          L.geoJSON(d.geom_falla, { pane: "fallasPane", style: { color: "#000000", weight: 12, opacity: 0.95 } }).addTo(capaFallas);
          L.geoJSON(d.geom_falla, { pane: "fallasPane", style: { color: "#ffea00", weight: 6, opacity: 1.0, dashArray: "5, 5" } })
            .bindPopup(
              `<b>⚠ FALLA EN EL ${d.pct_recorrido}% DE LA RUTA (${nombres[k]})</b><br>` +
              `Arista bloqueada: <b>#${d.arista_fallada_id}</b><br>` +
              `Amenaza: <b>${etiquetaAm[d.amenaza] || d.amenaza}</b> (sorteo <b>${d.aleatorio_umbral}</b> &lt; umbral <b>${d.umbral_pct}%</b>)<br>` +
              `Resta por recorrer: <b>${d.pct_restante}%</b> → recalculado por calle alternativa`
            )
            .addTo(capaFallas);
        }

        if (d.punto_quiebre && d.punto_quiebre.length === 2) {
          const pKey = `${d.punto_quiebre[0].toFixed(4)}_${d.punto_quiebre[1].toFixed(4)}`;
          if (!puntosVistos.has(pKey)) {
            puntosVistos.add(pKey);
            L.circleMarker(d.punto_quiebre, {
              pane: "fallasPane",
              radius: 9,
              color: "#000000",
              weight: 3,
              fillColor: "#ffea00",
              fillOpacity: 1.0,
            }).bindPopup(
              `<b>⚠ PUNTO DE QUIEBRE Y RE-RUTEO (${d.pct_recorrido}% del camino)</b><br>` +
              `Avance completado: <b>${d.pct_recorrido}%</b> · Restante: <b>${d.pct_restante}%</b><br>` +
              `Amenaza activada: <b>${etiquetaAm[d.amenaza] || d.amenaza}</b><br>` +
              `<i>Desde este nodo se descarta la ruta original y se toma la calle alternativa hacia el destino.</i>`
            ).addTo(capaFallas);
          }
        }

        // 4. Nueva Ruta Alternativa Recalculada desde el punto de falla hasta el destino (X% -> 100%)
        if (d.geom_alternativa) {
          const deltaKm = ((d.delta_distancia_m || 0) / 1000).toFixed(2);
          const signo = (d.delta_distancia_m || 0) >= 0 ? "+" : "";
          const mk = mRec[k] || {};
          const sk = simRec[k] || {};
          const casingAlt = L.geoJSON(d.geom_alternativa, {
            pane: "desviosPane",
            interactive: false,
            style: { color: "#090d16", weight: 11, opacity: 0.92, lineCap: "round", lineJoin: "round" },
          });
          const coreAlt = L.geoJSON(d.geom_alternativa, {
            pane: "desviosPane",
            style: { color: coloresTrazo[k], weight: 6.5, opacity: 1.0, dashArray: "10, 8", lineCap: "round", lineJoin: "round" },
          }).bindPopup(
            `<b>↻ NUEVA RUTA ALTERNATIVA (${nombres[k]}) — ${d.pct_recorrido}% → 100%</b><br>` +
            `Toma vía alternativa descartando los ${d.tramos_descartados} tramos restantes de la ruta original.<br>` +
            `Tramo alternativo nuevo: <b>${(d.distancia_alternativa_m / 1000).toFixed(2)} km</b> (${d.tramos_alternativos} tramos)<br>` +
            `Distancia total final: <b>${(d.distancia_m / 1000).toFixed(2)} km</b> (${signo}${deltaKm} km vs original)<br>` +
            `Nueva Resiliencia: <b>R = ${mk.resiliencia_score ?? "—"}%</b> · Supervivencia: <b>${sk.tasa_supervivencia ?? "—"}%</b>`
          );
          grupoDesv.addLayer(casingAlt);
          grupoDesv.addLayer(coreAlt);

          html += `<div style="color:${colores[k]}; margin-bottom:4px; padding:4px 6px; background:#fffaf0; border-left:3px solid ${coloresTrazo[k]}; border-radius:4px;">` +
            `<b>${nombres[k]}</b>: falla al <b>${d.pct_recorrido}%</b> (resta <b>${d.pct_restante}%</b>, ${etiquetaAm[d.amenaza] || d.amenaza}) → ` +
            `<b>↻ Recalculada por vía alternativa</b>: total <b>${(d.distancia_m / 1000).toFixed(2)} km</b> (${signo}${deltaKm} km) · <b>R=${mk.resiliencia_score ?? "—"}%</b> · Sup=<b>${sk.tasa_supervivencia ?? "—"}%</b></div>`;
        }
      }

      srEl.innerHTML = html;
      aplicarEnfoqueVisual(rutaEnfocadaActiva);

      // 5. Actualizar la Tabla de Comparación (Sección 3), Leyenda y Badge con las métricas recalculadas post-desvío
      const mapIds = [
        ["dijkstra", "dijkstra", "Dijkstra (desvío)"],
        ["resil", "resiliente", "Resiliente (desvío)"],
        ["lp", "optimizacion_lp", "Simplex (desvío)"],
        ["aco", "aco", "ACO (desvío)"],
      ];
      mapIds.forEach(([suf, k, nom]) => {
        const mk = mRec[k];
        const sk = simRec[k];
        if (mk) {
          document.getElementById(`m-dist-${suf}`).innerText = `${mk.distancia_promedio_km}`;
          document.getElementById(`m-riesgo-${suf}`).innerText = `${mk.aristas_con_riesgo}`;
          document.getElementById(`m-resil-${suf}`).innerText = `${mk.resiliencia_score}%`;
          document.getElementById(`m-time-${suf}`).innerText = `${mk.tiempo_ms || "—"}`;
          document.getElementById(`m-sim-${suf}`).innerText = sk ? `${sk.tasa_supervivencia}%` : "—";
          const legEl = document.getElementById(`leg-${suf}`);
          if (legEl) {
            legEl.innerText = `${nom} · ${mk.tiempo_ms} ms · R = ${mk.resiliencia_score}% · Sup = ${sk ? sk.tasa_supervivencia : "—"}%`;
          }
        }
      });

      const tecnicasPost = [
        { nombre: "Dijkstra", score: mRec.dijkstra?.resiliencia_score || 0, sup: simRec.dijkstra?.tasa_supervivencia || 0 },
        { nombre: "Resiliente", score: mRec.resiliente?.resiliencia_score || 0, sup: simRec.resiliente?.tasa_supervivencia || 0 },
        { nombre: "LP Simplex", score: mRec.optimizacion_lp?.resiliencia_score || 0, sup: simRec.optimizacion_lp?.tasa_supervivencia || 0 },
        { nombre: "ACO", score: mRec.aco?.resiliencia_score || 0, sup: simRec.aco?.tasa_supervivencia || 0 },
      ].filter(t => t.score > 0);
      const badge = document.getElementById("badge-diferencial");
      if (badge && tecnicasPost.length > 0) {
        tecnicasPost.sort((a, b) => (b.score - a.score) || (b.sup - a.sup));
        const mejor = tecnicasPost[0];
        badge.innerText = `Más resiliente tras desvío (${desvData.pct_fallo_sorteado}%): ${mejor.nombre} (R = ${mejor.score}%, Sup = ${mejor.sup}%)`;
        badge.style.background = "#fff3bf";
        badge.style.color = "#b45309";
        badge.style.borderColor = "#fcd34d";
      }
    })
    .catch((err) => {
      console.error("Error en desvio_falla:", err);
      srEl.innerHTML = `<div style="color:#c53030;">Error al recalcular ruta alternativa.</div>`;
    });
});
