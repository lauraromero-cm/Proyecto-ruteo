// Vista de testing: genera un sismo sintetico y muestra la respuesta del sistema
// (/api/despliegue_sismo con lat/lon/mag/prof, sin sismo de la BD).
const map = L.map("map").setView([-33.03, -71.55], 10);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap contributors" }).addTo(map);
const capa = L.featureGroup().addTo(map);
const COLORES = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080", "#9a6324", "#800000", "#808000", "#000075", "#f032e6", "#469990"];
const $ = (id) => document.getElementById(id);

let pos = { lat: -33.03, lon: -71.62 };
const marcador = L.marker([pos.lat, pos.lon], { draggable: true }).addTo(map);
const fijarPos = (lat, lon) => {
  pos = { lat, lon };
  marcador.setLatLng([lat, lon]);
  $("lbl-pos").textContent = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
};
fijarPos(pos.lat, pos.lon);
map.on("click", (e) => fijarPos(e.latlng.lat, e.latlng.lng));
marcador.on("dragend", () => fijarPos(marcador.getLatLng().lat, marcador.getLatLng().lng));
$("rng-mag").oninput = (e) => ($("lbl-mag").textContent = Number(e.target.value).toFixed(1));
$("rng-prof").oninput = (e) => ($("lbl-prof").textContent = e.target.value);
document.querySelectorAll(".presets button").forEach((b) => (b.onclick = () => {
  const [lat, lon, mag, prof] = b.dataset.p.split(",").map(Number);
  fijarPos(lat, lon);
  $("rng-mag").value = mag; $("rng-mag").oninput({ target: $("rng-mag") });
  $("rng-prof").value = prof; $("rng-prof").oninput({ target: $("rng-prof") });
  map.panTo([lat, lon]);
}));
$("btn-limpiar").onclick = () => { capa.clearLayers(); $("resultado").style.display = "none"; };

// dano 0..1 -> amarillo..rojo
const colorDano = (d) => `hsl(${Math.round(55 - 55 * (d ?? 0))}, 90%, 50%)`;

$("btn-generar").onclick = () => {
  const btn = $("btn-generar");
  btn.disabled = true;
  btn.textContent = "Calculando respuesta de la flota... (10-20 s)";
  const q = new URLSearchParams({
    lat: pos.lat, lon: pos.lon, mag: $("rng-mag").value, prof: $("rng-prof").value,
    drones_por_cuartel: $("inp-n").value, autonomia_km: $("inp-aut").value,
  });
  fetch("/api/despliegue_sismo?" + q)
    .then((r) => r.json().then((d) => (r.ok ? d : Promise.reject(new Error(d.error || r.status)))))
    .then(render)
    .catch((err) => { $("resultado").style.display = "block"; $("log").innerHTML = `<li>Error: ${err.message}</li>`; $("tabla-drones").innerHTML = ""; })
    .finally(() => { btn.disabled = false; btn.textContent = "💥 Generar sismo"; });
};

function render(d) {
  capa.clearLayers();
  const { sismo: s, parametros: p, metricas: k } = d;
  L.circle([s.lat, s.lon], { radius: p.radio_km * 1000, color: "#c0392b", weight: 1, fillOpacity: 0.04, dashArray: "6,6" }).addTo(capa);

  const colorDe = {};
  d.drones.forEach((dr, i) => {
    colorDe[dr.id] = COLORES[i % COLORES.length];
    L.geoJSON(dr.geom, { style: { color: colorDe[dr.id], weight: 4, opacity: 0.85 } })
      .bindPopup(`<b>${dr.id}</b><br>${dr.cuartel} (${dr.comuna})<br>${dr.distancia_km} km, ${dr.manzanas_visitadas.length} objetivos`).addTo(capa);
  });
  // relleno = dano esperado (amarillo->rojo); borde = cubierta por un dron (color del dron) o sin cubrir (gris)
  d.manzanas.forEach((m) => L.circleMarker([m.lat, m.lon], {
    radius: 3 + Math.min(4, m.prioridad / 400), fillColor: colorDano(m.dano), fillOpacity: 0.85,
    color: m.cubierta ? colorDe[m.dron] || "#27ae60" : "#555", weight: m.cubierta ? 2 : 1,
  }).bindPopup(`Manzana ${m.id}<br>Población: ${m.poblacion}<br>Antigüedad: ${m.antiguedad}<br>Daño esperado: ${Math.round((m.dano ?? 0) * 100)}%<br>Prioridad: ${m.prioridad}<br>${m.cubierta ? "Cubierta" : "Sin cubrir"}`).addTo(capa));
  L.marker([s.lat, s.lon]).addTo(capa);
  map.fitBounds(capa.getBounds());

  const top = d.manzanas.reduce((a, b) => (b.prioridad > a.prioridad ? b : a));
  const dañadas = d.manzanas.filter((m) => (m.dano ?? 0) > 0.2).length;
  const pct = (v) => (v == null ? "—" : v + "%");
  $("log").innerHTML = [
    `<b>Detección:</b> sismo M${s.magnitud.toFixed(1)} a ${s.profundidad_km} km de profundidad en (${s.lat.toFixed(3)}, ${s.lon.toFixed(3)}).`,
    `<b>Zona de respuesta:</b> radio ${p.radio_km} km → ${k.manzanas_zona} manzanas pobladas, ${k.poblacion_zona.toLocaleString()} habitantes; ${dañadas} con daño esperado &gt; 20 %.`,
    `<b>Priorización:</b> primera prioridad = manzana ${top.id} (${top.poblacion} hab., antigüedad ${top.antiguedad}, daño ${Math.round((top.dano ?? 0) * 100)} %).`,
    `<b>Despliegue:</b> ${k.drones_desplegados} de ${k.drones_totales} drones salen a la vez, ${k.km_totales} km en total. La manzana prioritaria se inspecciona a los ${k.min_primera_inspeccion_top ?? "—"} min.`,
    `<b>Cobertura:</b> ${pct(k.pct_poblacion_cubierta)} de la población, ${pct(k.pct_prioridad_cubierta)} de la prioridad, ${pct(k.pct_dano_cubierto)} del daño esperado, ${pct(k.pct_antiguas_cubiertas)} de la edificación antigua. ${k.manzanas_cubiertas}/${k.manzanas_zona} manzanas.`,
    `<span style="color:#666">Calculado en ${(d.tiempo_calculo_ms / 1000).toFixed(1)} s.</span>`,
  ].map((x) => `<li>${x}</li>`).join("");
  $("tabla-drones").innerHTML = "<tr><th>Dron (cuartel)</th><th>km</th><th>objetivos</th></tr>" +
    d.drones.map((dr) => `<tr><td style="color:${colorDe[dr.id]}">■ ${dr.cuartel}</td><td>${dr.distancia_km}</td><td>${dr.manzanas_visitadas.length}</td></tr>`).join("");
  $("resultado").style.display = "block";
}
