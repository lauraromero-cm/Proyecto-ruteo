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

// Grupos de capas
let capasRutas = {
  dijkstra: L.featureGroup().addTo(map),
  resiliente: L.featureGroup().addTo(map),
};
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
  optgroupTerrestres.label = "🏔️ Sismos Continentales en Tierra (USGS)";

  const optgroupMaritimos = document.createElement("optgroup");
  optgroupMaritimos.label = "🌊 Sismos en Costa y Océano Pacífico (USGS)";

  const optgroupFocos = document.createElement("optgroup");
  optgroupFocos.label = "🔥 Focos Activos de Incendio (NASA FIRMS)";

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
    infoBox.innerHTML = `<b>Calculando ruta óptima y resiliente con pgRouting en tiempo real...</b> <span style="font-size:12px; color:#2980b9;">⚙️</span>`;
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

function renderizarRuta(rutaData) {
  if (!rutaData) return;

  // Limpiar capas previas
  capasRutas.dijkstra.clearLayers();
  capasRutas.resiliente.clearLayers();
  capaMarcadoresRuta.clearLayers();

  const orig = rutaData.origen;
  const dest = rutaData.destino;

  // 1. Dibujar Rutas Dijkstra (k=2)
  if (rutaData.dijkstra_1 && rutaData.dijkstra_1.geom) {
    const l1 = L.geoJSON(rutaData.dijkstra_1.geom, {
      style: { color: "#e74c3c", weight: 4.5, opacity: 0.85 },
    }).bindPopup(`<b>Dijkstra Ruta 1 (Distancia Pura)</b><br>Distancia: ${Math.round(rutaData.dijkstra_1.distancia_m)} m<br>Tramos: ${rutaData.dijkstra_1.tramos}`);
    capasRutas.dijkstra.addLayer(l1);
  }

  if (rutaData.dijkstra_2 && rutaData.dijkstra_2.geom) {
    const l2 = L.geoJSON(rutaData.dijkstra_2.geom, {
      style: { color: "#e67e22", weight: 3.5, opacity: 0.9, dashArray: "6, 6" },
    }).bindPopup(`<b>Dijkstra Ruta 2 (Bypass Distancia)</b><br>Distancia: ${Math.round(rutaData.dijkstra_2.distancia_m)} m<br>Tramos: ${rutaData.dijkstra_2.tramos}`);
    capasRutas.dijkstra.addLayer(l2);
  }

  // 2. Dibujar Rutas Resilientes (k=2)
  if (rutaData.resiliente_1 && rutaData.resiliente_1.geom) {
    const r1 = L.geoJSON(rutaData.resiliente_1.geom, {
      style: { color: "#2980b9", weight: 5.5, opacity: 0.9 },
    }).bindPopup(`<b>Resiliente Ruta 1 (Corredor Seguro)</b><br>Distancia: ${Math.round(rutaData.resiliente_1.distancia_m)} m<br>Tramos: ${rutaData.resiliente_1.tramos}<br><i>Esquiva tendido eléctrico y áreas protegidas</i>`);
    capasRutas.resiliente.addLayer(r1);
  }

  if (rutaData.resiliente_2 && rutaData.resiliente_2.geom) {
    const r2 = L.geoJSON(rutaData.resiliente_2.geom, {
      style: { color: "#27ae60", weight: 4, opacity: 0.95, dashArray: "6, 6" },
    }).bindPopup(`<b>Resiliente Ruta 2 (Bypass Seguro)</b><br>Distancia: ${Math.round(rutaData.resiliente_2.distancia_m)} m<br>Tramos: ${rutaData.resiliente_2.tramos}<br><i>Camino alternativo disjunto seguro</i>`);
    capasRutas.resiliente.addLayer(r2);
  }

  // 3. Marcadores Destacados de Inicio y Destino
  L.circleMarker([orig.lat, orig.lon], {
    radius: 10,
    color: "#1a365d",
    fillColor: "#2980b9",
    fillOpacity: 1,
    weight: 3,
  }).bindPopup(`<b>🎯 BASE DE OPERACIÓN DRON (ORIGEN)</b><br><b>${orig.nombre}</b><br>Comuna: ${orig.comuna}<br>Punto de carga habilitado`).addTo(capaMarcadoresRuta);

  L.circleMarker([dest.lat, dest.lon], {
    radius: 11,
    color: "#78281f",
    fillColor: dest.tipo === "incendio" ? "#d35400" : "#c0392b",
    fillOpacity: 1,
    weight: 3,
  }).bindPopup(`<b>🚨 OBJETIVO DE EMERGENCIA (DESTINO)</b><br><b>${dest.descripcion}</b><br>Coordenadas: ${dest.lat}, ${dest.lon}`).addTo(capaMarcadoresRuta);

  // 4. Centrar y encuadrar el mapa en la nueva trayectoria
  try {
    const grupoTotal = L.featureGroup([capasRutas.resiliente, capasRutas.dijkstra, capaMarcadoresRuta]);
    map.fitBounds(grupoTotal.getBounds(), { padding: [55, 55], maxZoom: 13 });
  } catch (err) {
    map.setView([orig.lat, orig.lon], 11);
  }

  // 5. Actualizar Caja Informativa
  const infoBox = document.getElementById("info-seleccion");
  if (infoBox) {
    const msInfo = rutaData.tiempo_calculo_ms ? ` <span style="font-size:10px; background:#e8f4f8; color:#1b4f72; padding:2px 6px; border-radius:3px; font-weight:bold;">⚡ pgRouting: ${rutaData.tiempo_calculo_ms} ms</span>` : "";
    const r = rutaData.restricciones;
    const restrInfo = r ? ` <span style="font-size:10px; background:#fff8e1; color:#8a6d00; padding:2px 6px; border-radius:3px;">💨 viento máx: ${r.viento_max_kmh} km/h${r.es_default ? " (default)" : ""}</span>` : "";
    infoBox.innerHTML = `<b>Trayecto Activo:</b> ${orig.nombre} (${orig.comuna}) <br>➔ <b>${dest.descripcion}</b>${msInfo}${restrInfo}`;
  }

  // 6. Actualizar Métricas en la Tabla
  const m = rutaData.metricas || {};
  if (m.dijkstra && m.resiliente) {
    document.getElementById("m-dist-dijkstra").innerText = `${m.dijkstra.distancia_promedio_km} km`;
    document.getElementById("m-dist-resil").innerText = `${m.resiliente.distancia_promedio_km} km`;
    document.getElementById("m-disy-dijkstra").innerText = `${m.dijkstra.disyuncion_pct}%`;
    document.getElementById("m-disy-resil").innerText = `${m.resiliente.disyuncion_pct}%`;
    document.getElementById("m-riesgo-dijkstra").innerText = `${m.dijkstra.aristas_con_riesgo} tramos`;
    document.getElementById("m-riesgo-resil").innerText = `${m.resiliente.aristas_con_riesgo} tramos`;
    document.getElementById("m-resil-dijkstra").innerText = `${m.dijkstra.resiliencia_score}%`;
    document.getElementById("m-resil-resil").innerText = `${m.resiliente.resiliencia_score}%`;

    const diff = m.diferencial_resiliencia;
    const badge = document.getElementById("badge-diferencial");
    badge.innerText = diff >= 0
      ? `Diferencial: +${diff}% de Resiliencia Ganada`
      : `Diferencial: ${diff}% (Dijkstra resultó más resiliente para este par)`;
    badge.style.background = diff >= 0 ? "#ebfbee" : "#fff5f5";
    badge.style.color = diff >= 0 ? "#2b8a3e" : "#c53030";
    badge.style.borderColor = diff >= 0 ? "#b2f2bb" : "#feb2b2";
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
            🎯 Fijar como Inicio del Dron
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
      const zonaStr = p.en_tierra ? "🏔️ Epicentro en tierra" : "🌊 Epicentro en el mar";
      const ubicacionStr = `${zonaStr}, a ${distStr} de la red vial — alcance máximo aquí: ${umbralStr} (según magnitud del sismo y viento actual)`;
      const botonDestino = p.destino_valido
        ? `<button class="btn-popup btn-destino" onclick="window.fijarDestino('${sismoKey}')" style="margin-top:6px; width:100%;">
             🚨 Fijar como Objetivo de Emergencia
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

// 4. Capa Tendido Eléctrico
cargarGeoJSON("data/tendido_electrico.geojson", (geojson) => {
  L.geoJSON(geojson, {
    style: { color: "#8e44ad", weight: 2.5, opacity: 0.7, dashArray: "4, 4" },
    onEachFeature: (f, layer) => {
      layer.bindPopup(`<b>Línea de Tendido Eléctrico (${f.properties.tipo})</b><br>Riesgo físico de colisión BVLOS<br>Penalización en ruta resiliente: +50% costo`);
    },
  }).addTo(capaTendido);
});

// 5. Capa CONAF Áreas Protegidas
cargarGeoJSON("data/conaf_areas_protegidas.geojson", (geojson) => {
  L.geoJSON(geojson, {
    style: { color: "#27ae60", fillColor: "#2ecc71", fillOpacity: 0.25, weight: 2 },
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
            🔥 Fijar como Incendio a Evaluar
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
function toggleCapa(id, grupo) {
  const chk = document.getElementById(id);
  if (chk) {
    chk.addEventListener("change", (e) => {
      if (e.target.checked) map.addLayer(grupo);
      else map.removeLayer(grupo);
    });
  }
}

toggleCapa("chk-capa-resil", capasRutas.resiliente);
toggleCapa("chk-capa-dijkstra", capasRutas.dijkstra);
toggleCapa("chk-capa-carabineros", capaCarabineros);
toggleCapa("chk-capa-sismos", capaSismos);
toggleCapa("chk-capa-tendido", capaTendido);
toggleCapa("chk-capa-conaf", capaCONAF);
toggleCapa("chk-capa-firms", capaFIRMS);
toggleCapa("chk-capa-viento", capaViento);

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
