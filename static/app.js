/* =====================================================================
   SOADIN · Punto de venta tablet — lógica de pantalla
   El servidor recalcula y valida todo al grabar; aquí solo se captura.
   ===================================================================== */
(() => {
  "use strict";

  // ------------------------------------------------------------------ utilidades
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tactil = window.matchMedia("(pointer: coarse)").matches;
  const fmt = (n, d = 2) => Number(n || 0).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });
  const num = (v) => { const n = parseFloat(String(v ?? "").replace(/[,$\s]/g, "")); return isNaN(n) ? 0 : n; };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const resaltar = (texto, termino) => {
    const t = String(texto ?? ""); const q = String(termino ?? "").trim();
    if (!q) return esc(t);
    const i = t.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(t);
    return esc(t.slice(0, i)) + "<mark>" + esc(t.slice(i, i + q.length)) + "</mark>" + esc(t.slice(i + q.length));
  };
  const enfocar = (el) => { if (!tactil && el) setTimeout(() => el.focus(), 30); };

  function aviso(msg, tipo = "") {
    const t = document.createElement("div");
    t.className = "toast " + tipo; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), tipo === "error" ? 4500 : 2500);
  }

  async function llamar(url, opciones = {}) {
    const cfg = { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...opciones };
    if (cfg.body && typeof cfg.body !== "string") cfg.body = JSON.stringify(cfg.body);
    let r;
    try { r = await fetch(url, cfg); }
    catch { throw new Error("Sin conexión con el servidor."); }
    if (r.status === 401) { location.href = "/login"; throw new Error("Sesión terminada"); }
    let data;
    try { data = await r.json(); } catch { throw new Error(`Respuesta inválida del servidor (${r.status}).`); }
    return data;
  }

  // ------------------------------------------------------------------ modales
  const abrir = (id) => $("#" + id).classList.add("abierto");
  const cerrar = (id) => $("#" + id).classList.remove("abierto");
  const hayModal = () => $$(".velo.abierto").length > 0;
  $$("[data-cerrar]").forEach((b) => b.addEventListener("click", () => {
    b.closest(".velo").classList.remove("abierto"); enfocar($("#tx-codigo"));
  }));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const v = $$(".velo.abierto").pop();
      if (v && v.id !== "m-ticket") { v.classList.remove("abierto"); enfocar($("#tx-codigo")); }
    }
  });

  function confirmar(texto, titulo = "Confirmar") {
    return new Promise((ok) => {
      $("#cf-titulo").textContent = titulo; $("#cf-texto").textContent = texto;
      abrir("m-confirmar");
      const fin = (v) => { cerrar("m-confirmar"); $("#cf-si").onclick = $("#cf-no").onclick = null; ok(v); };
      $("#cf-si").onclick = () => fin(true);
      $("#cf-no").onclick = () => fin(false);
    });
  }

  // ------------------------------------------------------------------ estado
  const estado = {
    carrito: [],          // [{entrada:{...}, p:{partida calculada por el servidor}}]
    sel: -1,
    cliente: null,
    forma: "efectivo",
    producto: null,       // producto en captura
    pendiente: {},        // cambios de descripción/SAT antes de agregar
    folios: {},
    turno: {},
  };

  const guardar = () => {
    try {
      localStorage.setItem("pos_venta", JSON.stringify({
        carrito: estado.carrito, cliente: estado.cliente?.codigo || "000000", forma: estado.forma,
      }));
    } catch { /* almacenamiento no disponible */ }
  };
  const recuperar = () => {
    try { return JSON.parse(localStorage.getItem("pos_venta") || "null"); } catch { return null; }
  };

  // ------------------------------------------------------------------ reloj y turno
  const tic = () => { $("#reloj").textContent = new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", hour12: false }); };
  tic(); setInterval(tic, 5000);

  async function cargarTurno() {
    try {
      const r = await llamar("/api/turno");
      if (!r.ok) { aviso(r.error, "error"); return; }
      estado.turno = r.turno; estado.folios = r.folios || {};
      $("#t-fecha").textContent = r.turno.fecha; $("#t-caja").textContent = r.turno.caja;
      $("#t-turno").textContent = r.turno.turno; $("#t-cajero").textContent = r.turno.cajero;
      for (const k of ["NO", "RE", "FA", "CO"]) $("#f-" + k).textContent = estado.folios[k] ? `Folio ${estado.folios[k]}` : "";
    } catch (e) { aviso(e.message, "error"); }
  }

  // ------------------------------------------------------------------ captura de producto
  const txCodigo = $("#tx-codigo");

  async function buscarCodigo() {
    let texto = txCodigo.value.trim().toUpperCase();
    if (!texto) { if (tactil) abrirBuscador(""); return; }
    let cantidad = null;
    if (texto.includes("*")) {
      const [a, b] = texto.split("*", 2);
      const c = parseFloat(a.trim());
      texto = (b || "").trim();
      if (!isNaN(c) && c > 0) cantidad = c;
    }
    if (!texto) return;
    const r = await llamar("/api/producto/" + encodeURIComponent(texto));
    if (!r.ok) {
      if (r.no_existe) { txCodigo.value = ""; abrirBuscador(texto); return; }
      aviso(r.error, "error"); return;
    }
    mostrarProducto(r.producto, cantidad);
    // Captura rápida cantidad*código con un solo precio: se agrega directo (como el escritorio)
    if (cantidad && r.producto.precios.length <= 1 && r.producto.modiprecio !== "1") agregarProducto();
  }

  function mostrarProducto(p, cantidad) {
    estado.producto = p; estado.pendiente = {};
    txCodigo.value = p.codigo;
    $("#p-nombre").textContent = p.descripcion;
    $("#p-codigo").textContent = p.codigo;
    $("#p-unidad").textContent = p.unidad || "—";
    const ex = $("#p-exis");
    ex.textContent = fmt(p.existencia, 2); ex.className = p.existencia <= 0 ? "neg" : "";
    $("#p-eqv").textContent = p.equivalentes;
    $("#bt-p-eqv").disabled = !p.equivalentes;
    $("#bt-p-web").disabled = !p.ligaw;

    const cont = $("#p-precios"); cont.innerHTML = "";
    const precios = p.precios.length ? p.precios : [{ lista: 1, precio: 0 }];
    precios.forEach((pr, i) => {
      const b = document.createElement("button");
      b.className = "chip" + (i === 0 ? " sel" : "");
      b.innerHTML = `<small>L${pr.lista}</small>$${fmt(pr.precio, 2)}`;
      b.onclick = () => { $$(".chip", cont).forEach((c) => c.classList.remove("sel")); b.classList.add("sel"); ponerPrecio(pr.precio); };
      cont.appendChild(b);
    });
    cont.classList.toggle("oculto", precios.length <= 1);
    const txPrecio = $("#tx-precio");
    txPrecio.readOnly = p.modiprecio !== "1";
    ponerPrecio(precios[0].precio);
    $("#tx-cantidad").value = cantidad ?? 1;
    $("#tx-pdesc").value = "0";
    revisarCantidad();
    $("#producto").classList.add("activo");
    if (p.modiprecio === "1") enfocar(txPrecio); else enfocar($("#tx-cantidad"));
    if (!tactil) $("#tx-cantidad").select();
  }

  // El precio de lista se guarda exacto (3 decimales, como el escritorio) y se muestra a 2
  function ponerPrecio(v) {
    estado.precioLista = v;
    $("#tx-precio").value = estado.producto?.modiprecio === "1" ? (+v).toFixed(2) : fmt(v, 2);
  }
  const precioCapturado = () => (estado.producto?.modiprecio === "1" ? num($("#tx-precio").value) : estado.precioLista);

  function revisarCantidad() {
    const p = estado.producto; if (!p) return;
    const c = num($("#tx-cantidad").value);
    let msg = "";
    if (p.fraccionar === 0 && c !== Math.trunc(c)) msg = "Este producto no se puede fraccionar.";
    else if (c > p.existencia) msg = `Existencia insuficiente (hay ${fmt(p.existencia, 2)}).`;
    if (p.modiprecio === "1") msg = (msg ? msg + " " : "") + "Precio modificable.";
    $("#p-aviso").textContent = msg;
  }

  function limpiarProducto() {
    estado.producto = null; estado.pendiente = {};
    $("#producto").classList.remove("activo");
    txCodigo.value = "";
    enfocar(txCodigo);
  }

  async function agregarProducto() {
    const p = estado.producto; if (!p) return;
    const cantidad = num($("#tx-cantidad").value);
    if (cantidad <= 0) { aviso("Debe ingresar una cantidad mayor a 0.", "error"); return; }
    if (p.fraccionar === 0 && cantidad !== Math.trunc(cantidad)) { aviso("Este producto NO se puede fraccionar.", "error"); return; }
    const entrada = {
      codigo: p.codigo, cantidad, precio: precioCapturado(), pdesc: num($("#tx-pdesc").value),
      ...estado.pendiente,
    };
    const bt = $("#bt-agregar"); bt.disabled = true;
    try {
      const r = await llamar("/api/partida", { method: "POST", body: entrada });
      if (!r.ok) { aviso(r.error, "error"); return; }
      estado.carrito.push({ entrada, p: r.partida });
      estado.sel = estado.carrito.length - 1;
      if (r.partida.bandera === "2") aviso("Se aplicó precio especial por cantidad.", "ok");
      limpiarProducto(); pintarCarrito();
    } catch (e) { aviso(e.message, "error"); }
    finally { bt.disabled = false; }
  }

  txCodigo.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); buscarCodigo().catch((x) => aviso(x.message, "error")); } });
  $("#bt-buscar-codigo").onclick = () => buscarCodigo().catch((x) => aviso(x.message, "error"));
  $("#bt-buscar").onclick = () => abrirBuscador(txCodigo.value.trim());
  $("#bt-precios").onclick = () => abrirBuscador(txCodigo.value.trim());
  $("#bt-agregar").onclick = agregarProducto;
  $("#bt-p-cancelar").onclick = limpiarProducto;
  $("#tx-cantidad").addEventListener("input", revisarCantidad);
  ["#tx-cantidad", "#tx-precio", "#tx-pdesc"].forEach((s) =>
    $(s).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); agregarProducto(); } }));
  const paso = (d) => {
    const p = estado.producto; const inp = $("#tx-cantidad");
    let v = num(inp.value) + d; if (v < (p?.fraccionar ? 0.001 : 1)) v = p?.fraccionar ? 0.001 : 1;
    inp.value = p?.fraccionar ? +v.toFixed(3) : Math.round(v); revisarCantidad();
  };
  $("#bt-mas").onclick = () => paso(1);
  $("#bt-menos").onclick = () => paso(-1);
  $("#bt-p-eqv").onclick = () => estado.producto && abrirEquivalentes(estado.producto.codigo, estado.producto.descripcion);
  $("#bt-p-web").onclick = () => estado.producto?.ligaw && abrirWeb(estado.producto.ligaw);
  $("#bt-p-partida").onclick = () => {
    const p = estado.producto; if (!p) return;
    abrirPartida({
      descripcion: estado.pendiente.descripcion || p.descripcion, unidad: estado.pendiente.unidad || p.unidad,
      codsat: estado.pendiente.codsat || p.codsat, umesat: estado.pendiente.umesat || p.umesat,
      original: { descripcion: p.descripcion, unidad: p.unidad },
    }, (v) => { estado.pendiente = v; $("#p-nombre").textContent = v.descripcion; $("#p-unidad").textContent = v.unidad; });
  };

  const abrirWeb = (url) => { const u = /^https?:\/\//i.test(url) ? url : "http://" + url; window.open(u, "_blank", "noopener"); };

  // ------------------------------------------------------------------ carrito
  function totales() {
    const t = { cantidad: 0, importe: 0, descuento: 0, iva: 0, neto: 0 };
    for (const { p } of estado.carrito) {
      t.cantidad += p.cantidad; t.importe += p.cantidad * p.precio; t.descuento += p.descuento;
      t.iva += p.iva; t.neto += p.total;
    }
    t.subtotal = t.importe - t.descuento;
    t.neto2 = Math.round(t.neto * 100) / 100;
    return t;
  }

  function pintarCarrito() {
    const cont = $("#partidas");
    $$(".partida", cont).forEach((n) => n.remove());
    $("#partidas-vacio").classList.toggle("oculto", estado.carrito.length > 0);
    estado.carrito.forEach(({ p }, i) => {
      const d = document.createElement("div");
      d.className = "partida" + (i === estado.sel ? " sel" : "");
      const modDesc = p.descripcion !== p.descripcion_original || p.unidad !== p.unidad_original;
      const falta = p.cantidad > p.existencia;
      d.innerHTML = `
        <div class="n">${i + 1}</div>
        <div>
          <div class="d">${modDesc ? `<span class="mod">${esc(p.descripcion)}</span>` : esc(p.descripcion)}</div>
          <div class="s"><span class="${falta ? "falta" : ""}">${fmt(p.cantidad, 3)}</span> ${esc(p.unidad)} × $${fmt(p.precio_final, 2)}
            · ${esc(p.codigo)}${p.pdesc ? ` · desc ${fmt(p.pdesc, 2)}%` : ""}${p.bandera === "2" ? " · precio especial" : ""}
            · SAT ${esc(p.codsat)}/${esc(p.umesat)}</div>
        </div>
        <div class="t">$${fmt(p.total, 2)}</div>
        <div class="acciones-partida">
          <button class="btn chico rojo" data-a="quitar">Quitar</button>
          <button class="btn chico" data-a="partida">Descripción / SAT</button>
          <button class="btn chico" data-a="eqv">Equivalentes</button>
        </div>`;
      d.addEventListener("click", (e) => {
        const a = e.target.closest("[data-a]")?.dataset.a;
        if (a === "quitar") return quitarPartida(i);
        if (a === "partida") return editarPartida(i);
        if (a === "eqv") return abrirEquivalentes(p.codigo, p.descripcion);
        estado.sel = estado.sel === i ? -1 : i; pintarCarrito();
      });
      cont.appendChild(d);
    });
    if (estado.sel >= 0) $$(".partida", cont)[estado.sel]?.scrollIntoView({ block: "nearest" });

    const t = totales();
    $("#tt-piezas").textContent = fmt(t.cantidad, 3);
    $("#tt-importe").textContent = fmt(t.importe, 2);
    $("#tt-desc").textContent = fmt(t.descuento, 2);
    $("#tt-sub").textContent = fmt(t.subtotal, 2);
    $("#tt-iva").textContent = fmt(t.iva, 2);
    $("#tt-total").textContent = "$" + fmt(t.neto, 2);
    guardar();
  }

  async function quitarPartida(i) {
    if (!(await confirmar(`¿Quitar "${estado.carrito[i].p.descripcion}" de la venta?`, "Quitar partida"))) return;
    estado.carrito.splice(i, 1); estado.sel = -1; pintarCarrito();
  }

  function editarPartida(i) {
    const it = estado.carrito[i];
    abrirPartida({
      descripcion: it.p.descripcion, unidad: it.p.unidad, codsat: it.p.codsat, umesat: it.p.umesat,
      original: { descripcion: it.p.descripcion_original, unidad: it.p.unidad_original },
    }, (v) => {
      Object.assign(it.entrada, v); Object.assign(it.p, v); pintarCarrito();
    });
  }

  // ------------------------------------------------------------------ modal partida
  let alAceptarPartida = null, originalPartida = null;
  function abrirPartida(v, alAceptar) {
    alAceptarPartida = alAceptar; originalPartida = v.original;
    $("#pt-desc").value = v.descripcion || ""; $("#pt-unidad").value = v.unidad || "";
    $("#pt-clave").value = v.codsat || ""; $("#pt-umesat").value = v.umesat || "";
    abrir("m-partida"); enfocar(POS.puedeDescripcion ? $("#pt-desc") : $("#pt-clave"));
  }
  $("#pt-restaurar").onclick = () => {
    if (!originalPartida) return;
    $("#pt-desc").value = originalPartida.descripcion || ""; $("#pt-unidad").value = originalPartida.unidad || "";
  };
  $("#pt-aceptar").onclick = () => {
    const descripcion = $("#pt-desc").value.trim();
    const clave = $("#pt-clave").value.trim();
    if (!descripcion) return aviso("La descripción no puede quedar vacía.", "error");
    if (!/^\d{8}$/.test(clave)) return aviso("La clave SAT debe tener 8 dígitos.", "error");
    const umesat = $("#pt-umesat").value.trim().toUpperCase();
    if (!umesat) return aviso("Captura la unidad SAT.", "error");
    alAceptarPartida?.({ descripcion, unidad: $("#pt-unidad").value.trim().toUpperCase(), codsat: clave, umesat });
    cerrar("m-partida"); enfocar(txCodigo);
  };

  // ------------------------------------------------------------------ buscador de productos
  let modoBusqueda = "descripcion";
  function abrirBuscador(texto) {
    $("#b-texto").value = texto || ""; abrir("m-buscar");
    if (texto) buscarProductos(); else { $("#b-lista").innerHTML = '<div class="vacio">Escribe lo que buscas.</div>'; enfocar($("#b-texto")); }
  }
  $$("#b-modos .chip").forEach((c) => c.onclick = () => {
    $$("#b-modos .chip").forEach((x) => x.classList.remove("sel")); c.classList.add("sel");
    modoBusqueda = c.dataset.modo; if ($("#b-texto").value.trim()) buscarProductos();
  });
  $("#b-ir").onclick = () => buscarProductos();
  $("#b-texto").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); buscarProductos(); } });

  async function buscarProductos() {
    const q = $("#b-texto").value.trim(); if (!q) return;
    const lista = $("#b-lista"); lista.classList.add("cargando");
    try {
      const r = await llamar(`/api/productos?modo=${modoBusqueda}&q=${encodeURIComponent(q)}`);
      if (!r.ok) return aviso(r.error, "error");
      if (!r.productos.length) { lista.innerHTML = `<div class="vacio">Sin resultados para “${esc(q)}”.</div>`; return; }
      const campo = { descripcion: "descripcion", inicial: "descripcion", codigo: "codigo" }[modoBusqueda];
      lista.innerHTML = r.productos.map((p, i) => `
        <button class="item" data-i="${i}">
          <div>
            <div class="a">${campo === "descripcion" ? resaltar(p.descripcion, q) : esc(p.descripcion)}</div>
            <div class="b">${campo === "codigo" ? resaltar(p.codigo, q) : esc(p.codigo)}
              ${p.parte ? " · Parte " + (modoBusqueda === "parte" ? resaltar(p.parte, q) : esc(p.parte)) : ""}
              ${p.codprovee && modoBusqueda === "codprovee" ? " · Prov " + resaltar(p.codprovee, q) : ""}
              ${p.presentacion ? " · " + (modoBusqueda === "presentacion" ? resaltar(p.presentacion, q) : esc(p.presentacion)) : ""}
              ${p.ubicacion ? " · Ubic " + esc(p.ubicacion) : ""}
              ${p.equivalentes ? ` · <b>${p.equivalentes} eqv.</b>` : ""}</div>
          </div>
          <div class="c"><b>$${fmt(p.precio, 2)}</b><span class="${p.existencia <= 0 ? "neg" : ""}">Exis ${fmt(p.existencia, 2)}</span></div>
        </button>`).join("") + (r.limite ? '<div class="vacio">Se muestran los primeros 300. Afina la búsqueda.</div>' : "");
      $$(".item", lista).forEach((b) => b.onclick = async () => {
        const p = r.productos[+b.dataset.i];
        cerrar("m-buscar");
        const rp = await llamar("/api/producto/" + encodeURIComponent(p.codigo));
        if (rp.ok) mostrarProducto(rp.producto, null); else aviso(rp.error, "error");
      });
    } catch (e) { aviso(e.message, "error"); }
    finally { lista.classList.remove("cargando"); }
  }

  // ------------------------------------------------------------------ equivalentes
  async function abrirEquivalentes(codigo, descripcion) {
    $("#e-sub").textContent = `${codigo} · ${descripcion || ""}`;
    $("#e-lista").innerHTML = '<div class="vacio">Cargando…</div>'; abrir("m-eqv");
    try {
      const r = await llamar("/api/equivalentes/" + encodeURIComponent(codigo));
      if (!r.ok) return aviso(r.error, "error");
      if (!r.equivalentes.length) { $("#e-lista").innerHTML = '<div class="vacio">Este producto no tiene equivalentes.</div>'; return; }
      $("#e-lista").innerHTML = r.equivalentes.map((p, i) => `
        <button class="item" data-i="${i}">
          <div><div class="a">${esc(p.descripcion)}</div>
            <div class="b">${esc(p.codigo)}${p.parte ? " · Parte " + esc(p.parte) : ""}${p.presentacion ? " · " + esc(p.presentacion) : ""}
              ${p.ultima_venta ? " · Últ. venta " + esc(p.ultima_venta) : ""}</div></div>
          <div class="c"><b>$${fmt(p.precio, 2)}</b><span class="${p.existencia <= 0 ? "neg" : ""}">Exis ${fmt(p.existencia, 2)}</span></div>
        </button>`).join("");
      $$("#e-lista .item").forEach((b) => b.onclick = async () => {
        const p = r.equivalentes[+b.dataset.i];
        cerrar("m-eqv");
        const rp = await llamar("/api/producto/" + encodeURIComponent(p.codigo));
        if (rp.ok) mostrarProducto(rp.producto, null); else aviso(rp.error, "error");
      });
    } catch (e) { aviso(e.message, "error"); }
  }

  // ------------------------------------------------------------------ cliente
  async function ponerCliente(codigo, avisar = true) {
    const r = await llamar("/api/cliente/" + encodeURIComponent(codigo || "000000"));
    if (!r.ok) { aviso(r.error, "error"); return false; }
    const c = r.cliente; estado.cliente = c;
    $("#c-nombre").textContent = c.cliente || "—";
    $("#c-rfc").textContent = `${c.codigo} · ${c.rfc || ""}`;
    const cr = $("#c-credito");
    if (c.limite > 0 || c.saldo > 0) {
      cr.innerHTML = `Saldo $${fmt(c.saldo)} · Disponible <b class="${c.disponible > 0 ? "ok" : "mal"}">$${fmt(c.disponible)}</b>`
        + (c.vencido > 0 ? ` · <b class="mal">Vencido $${fmt(c.vencido)}</b>` : "")
        + (c.saldo > 0 ? ` · <a href="#" id="ver-cartera">ver cartera</a>` : "");
      const v = $("#ver-cartera"); if (v) v.onclick = (e) => { e.preventDefault(); abrirCartera(c); };
    } else cr.textContent = "";
    if (avisar) {
      if (c.limite > 0 && c.saldo > c.limite) aviso(`Límite de crédito excedido: saldo $${fmt(c.saldo)} de $${fmt(c.limite)}.`, "error");
      else if (c.dias_vencidos > 0 && c.vencido > 0) aviso(`Crédito bloqueado: $${fmt(c.vencido)} vencidos (${c.dias_vencidos} días).`, "error");
    }
    guardar();
    return true;
  }

  $("#bt-cliente").onclick = () => {
    $("#cl-nuevo-form").classList.add("oculto"); $("#cl-lista").classList.remove("oculto");
    $("#cl-texto").value = ""; $("#cl-lista").innerHTML = '<div class="vacio">Busca un cliente.</div>';
    abrir("m-cliente"); enfocar($("#cl-texto"));
  };
  $("#cl-publico").onclick = async () => { if (await ponerCliente("000000", false)) cerrar("m-cliente"); };
  $("#cl-ir").onclick = () => buscarClientes();
  $("#cl-texto").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); buscarClientes(); } });

  async function buscarClientes() {
    const q = $("#cl-texto").value.trim(); if (!q) return;
    const lista = $("#cl-lista"); lista.classList.add("cargando");
    try {
      const r = await llamar("/api/clientes?q=" + encodeURIComponent(q));
      if (!r.ok) return aviso(r.error, "error");
      if (!r.clientes.length) { lista.innerHTML = `<div class="vacio">Sin clientes para “${esc(q)}”.</div>`; return; }
      lista.innerHTML = r.clientes.map((c, i) => `
        <button class="item" data-i="${i}">
          <div><div class="a">${resaltar(c.cliente, q)}</div>
            <div class="b">${resaltar(c.codigo, q)} · ${resaltar(c.rfc, q)}${c.direccion ? " · " + esc(c.direccion) : ""}</div></div>
          <div class="c"><span>${c.ultimacompra ? "Últ. compra<br>" + esc(c.ultimacompra) : ""}</span></div>
        </button>`).join("");
      $$("#cl-lista .item").forEach((b) => b.onclick = async () => {
        if (await ponerCliente(r.clientes[+b.dataset.i].codigo)) cerrar("m-cliente");
      });
    } catch (e) { aviso(e.message, "error"); }
    finally { lista.classList.remove("cargando"); }
  }

  $("#cl-nuevo").onclick = () => {
    $$("#cl-nuevo-form input").forEach((i) => i.value = "");
    $("#cl-nuevo-form").classList.remove("oculto"); $("#cl-lista").classList.add("oculto");
    enfocar($("#n-cliente"));
  };
  $("#n-cancelar").onclick = () => { $("#cl-nuevo-form").classList.add("oculto"); $("#cl-lista").classList.remove("oculto"); };
  $("#n-grabar").onclick = async () => {
    const d = {}; ["cliente", "rfc", "telefono", "email", "calle", "exterior", "colonia", "cp", "municipio", "estado"]
      .forEach((k) => d[k] = $("#n-" + k).value.trim());
    if (!d.cliente) return aviso("Captura el nombre del cliente.", "error");
    const bt = $("#n-grabar"); bt.disabled = true;
    try {
      const r = await llamar("/api/cliente", { method: "POST", body: d });
      if (!r.ok) return aviso(r.error, "error");
      aviso(`Cliente ${r.codigo} registrado.`, "ok");
      if (await ponerCliente(r.codigo, false)) cerrar("m-cliente");
    } catch (e) { aviso(e.message, "error"); }
    finally { bt.disabled = false; }
  };

  // ------------------------------------------------------------------ cartera
  async function abrirCartera(c) {
    $("#ca-sub").textContent = `${c.codigo} · ${c.cliente}`;
    $("#ca-kpis").innerHTML = [
      ["Límite", c.limite, ""], ["Saldo", c.saldo, ""], ["Vencido", c.vencido, "mal"],
      ["Por vencer", c.por_vencer, ""], ["Disponible", c.disponible, c.disponible > 0 ? "ok" : "mal"],
    ].map(([t, v, k]) => `<div class="kpi"><span>${t}</span><b class="${k}">$${fmt(v)}</b></div>`).join("");
    $("#ca-docs").innerHTML = '<tr><td colspan="6" class="vacio">Cargando…</td></tr>';
    $("#ca-movs").innerHTML = "";
    abrir("m-cartera");
    try {
      const r = await llamar("/api/cartera/" + encodeURIComponent(c.codigo));
      if (!r.ok) return aviso(r.error, "error");
      if (!r.documentos.length) { $("#ca-docs").innerHTML = '<tr><td colspan="6" class="vacio">Sin documentos con saldo.</td></tr>'; return; }
      $("#ca-docs").innerHTML = r.documentos.map((d, i) => `
        <tr class="clic" data-i="${i}"><td>${esc(d.documento)}</td><td>${esc(d.fecha)}</td><td>${esc(d.vencimiento)}</td>
        <td class="num">$${fmt(d.saldo)}</td><td class="num ${d.dias > 0 ? "mal" : ""}">${d.dias}</td><td>${esc(d.rango)}</td></tr>`).join("");
      $$("#ca-docs tr.clic").forEach((tr) => tr.onclick = async () => {
        const d = r.documentos[+tr.dataset.i];
        const m = await llamar(`/api/cartera/${encodeURIComponent(c.codigo)}/${encodeURIComponent(d.documento)}`);
        if (!m.ok) return aviso(m.error, "error");
        $("#ca-movs").innerHTML = `<div class="panel-t" style="padding:0 0 6px">Movimientos de ${esc(d.documento)}</div>
          <table class="tabla"><thead><tr><th>Mov</th><th>Tipo</th><th>Fecha</th><th class="num">Importe</th><th>Referencia</th><th>Observaciones</th></tr></thead>
          <tbody>${m.movimientos.length ? "" : '<tr><td colspan="6" class="vacio">Sin movimientos registrados.</td></tr>'}${m.movimientos.map((x) => `<tr><td>${esc(x.mov)}</td><td>${esc(x.tipo)}</td><td>${esc(x.fecha)}</td>
          <td class="num">$${fmt(x.importe)}</td><td>${esc(x.referencia)}</td><td>${esc(x.observaciones)}</td></tr>`).join("")}</tbody></table>`;
        $("#ca-movs").scrollIntoView({ block: "nearest" });
      });
    } catch (e) { aviso(e.message, "error"); }
  }

  // ------------------------------------------------------------------ forma de pago
  $$("#forma-pago .op").forEach((b) => b.onclick = () => {
    $$("#forma-pago .op").forEach((x) => x.classList.remove("sel")); b.classList.add("sel");
    estado.forma = b.dataset.forma; guardar();
  });

  // ------------------------------------------------------------------ cobro
  let docActual = null;
  $$(".doc").forEach((b) => b.onclick = () => iniciarCobro(b.dataset.doc));

  async function iniciarCobro(tipo) {
    if (!estado.carrito.length) return aviso("Debes capturar al menos un producto antes de cobrar.", "error");
    docActual = tipo;
    try {
      const r = await llamar("/api/existencias", { method: "POST", body: { partidas: estado.carrito.map((x) => x.p) } });
      if (!r.ok) return aviso(r.error, "error");
      if (r.faltantes.length) {
        const lista = r.faltantes.map((f) => `• ${f.codigo}: hay ${fmt(f.existencia)}, pides ${fmt(f.pedida)}`).join("\n");
        if (tipo !== "Cotizacion") return confirmar("Existencia insuficiente:\n\n" + lista + "\n\nCorrige las cantidades antes de grabar.", "Inventario")
          .then(() => {});
        if (!(await confirmar("Existencia insuficiente:\n\n" + lista + "\n\nSe permite continuar por ser cotización. ¿Continuar?", "Aviso"))) return;
      }
      if (!estado.cliente) await ponerCliente("000000", false);
      // refrescar saldos del cliente antes de cobrar
      await ponerCliente(estado.cliente.codigo, false);
    } catch (e) { return aviso(e.message, "error"); }

    const neto = totales().neto2;
    $("#co-titulo").textContent = { "Nota de Venta": "Cobro · Nota de venta", Remision: "Cobro · Remisión", Factura: "Cobro · Factura", Cotizacion: "Cotización" }[tipo];
    $("#co-neto").textContent = "$" + fmt(neto);
    $("#co-cliente").textContent = `${estado.cliente.codigo} · ${estado.cliente.cliente}`;
    $("#co-efectivo").value = estado.forma === "efectivo" ? neto.toFixed(2) : "0.00";
    $("#co-tarjeta").value = estado.forma === "tarjeta" ? neto.toFixed(2) : "0.00";
    $("#co-transferencia").value = estado.forma === "transferencia" ? neto.toFixed(2) : "0.00";
    $("#co-ref").value = ""; $("#co-obs").value = ""; $("#co-omite").checked = true;
    // Billetes sugeridos: exacto y redondeos hacia arriba a 10, 50, 100, 500 y 1000
    const arriba = [10, 50, 100, 500, 1000].map((m) => Math.ceil(neto / m) * m).filter((v) => v > neto);
    const rapidos = [neto, ...new Set(arriba)].slice(0, 5);
    $("#co-rapidos").innerHTML = rapidos.map((v, i) => `<button class="btn chico" data-v="${v}">${i === 0 ? "Exacto" : "$" + fmt(v, 0)}</button>`).join("");
    $$("#co-rapidos button").forEach((b) => b.onclick = () => { $("#co-efectivo").value = (+b.dataset.v).toFixed(2); calcularCambio(); });
    calcularCambio(true);
    abrir("m-cobro");
    enfocar($("#co-efectivo"));
    if (!tactil) $("#co-efectivo").select();
  }

  function calcularCambio(inicial = false) {
    const neto = totales().neto2;
    const recibido = num($("#co-efectivo").value) + num($("#co-tarjeta").value) + num($("#co-transferencia").value);
    const cambio = Math.round((recibido - neto) * 100) / 100;
    const c = $("#co-cambio");
    c.textContent = fmt(cambio); c.className = "cambio " + (cambio < 0 ? "mal" : "ok");
    const box = $("#co-credito-box"), ck = $("#co-credito"), cli = estado.cliente;
    const cotiz = docActual === "Cotizacion";
    if (cambio < 0 && !cotiz) {
      box.classList.remove("oculto");
      $("#co-credito-monto").textContent = "$" + fmt(-cambio);
      if (cli.credito_bloqueado) {
        ck.checked = false; ck.disabled = true; box.className = "credito-box bloqueado";
        $("#co-credito-info").innerHTML = cli.dias_vencidos > 0
          ? `<b class="mal">Crédito bloqueado:</b> $${fmt(cli.vencido)} vencidos (${cli.dias_vencidos} días).`
          : `<b class="mal">Sin crédito disponible</b> (disponible $${fmt(cli.disponible)}).`;
      } else {
        ck.disabled = false; if (!ck.dataset.tocado) ck.checked = true;
        box.className = "credito-box" + (ck.checked ? " activo" : "");
        $("#co-credito-info").innerHTML = `Disponible $${fmt(cli.disponible)} · ${cli.diascredito} días de crédito`;
      }
    } else {
      box.classList.add("oculto"); ck.checked = false;
    }
    const pagoOk = cambio >= 0 || ck.checked || cotiz;
    $("#co-grabar").disabled = !pagoOk;
    $("#co-grabar").textContent = cotiz ? "Grabar cotización" : (ck.checked && cambio < 0 ? "Grabar a crédito" : "Grabar");
  }
  ["#co-efectivo", "#co-tarjeta", "#co-transferencia"].forEach((s) => $(s).addEventListener("input", () => calcularCambio()));
  $("#co-credito").addEventListener("change", (e) => { e.target.dataset.tocado = "1"; calcularCambio(); });
  $("#co-efectivo").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("#co-grabar").disabled) { e.preventDefault(); grabarVenta(); } });
  $("#co-grabar").onclick = () => grabarVenta();

  let grabando = false;
  async function grabarVenta() {
    if (grabando) return;
    grabando = true;
    const bt = $("#co-grabar"); bt.disabled = true; const textoBt = bt.textContent; bt.textContent = "Grabando…";
    try {
      const cotiz = docActual === "Cotizacion";
      const neto = totales().neto2;
      const cuerpo = {
        tipo_documento: docActual,
        cliente: estado.cliente.codigo,
        partidas: estado.carrito.map((x) => x.entrada),
        pago: {
          efectivo: cotiz ? neto : num($("#co-efectivo").value),
          tarjeta: cotiz ? 0 : num($("#co-tarjeta").value),
          transferencia: cotiz ? 0 : num($("#co-transferencia").value),
          credito: !cotiz && $("#co-credito").checked,
          refepago: $("#co-ref").value, observacionespago: $("#co-obs").value,
          omite_comprobante: $("#co-omite").checked,
        },
      };
      const r = await llamar("/api/venta", { method: "POST", body: cuerpo });
      if (!r.ok) { aviso(r.error, "error"); return; }
      cerrar("m-cobro");
      mostrarTicket(r.ticket, r.folio);
      if (r.imprimir) setTimeout(imprimirTicket, 300);
      estado.carrito = []; estado.sel = -1; limpiarProducto(); pintarCarrito();
      await ponerCliente("000000", false);
      cargarTurno();
    } catch (e) { aviso(e.message, "error"); }
    finally { grabando = false; bt.disabled = false; bt.textContent = textoBt; $("#co-credito").dataset.tocado = ""; }
  }

  // ------------------------------------------------------------------ ticket (mismo formato que el escritorio)
  function textoTicket(t) {
    const sep = "-".repeat(40) + "\n";
    const pad = (s, n) => String(s).slice(0, n).padEnd(n);
    const izq = (s, n) => String(s).padStart(n);
    const m = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    let x = sep + `      ${t.documento}: ${t.folio}\n` + sep;
    x += `Fecha: ${t.fecha}\n`;
    x += `Caja : ${t.caja}  |  Vend: ${String(t.vendedor).slice(0, 15)}\n` + sep;
    x += `${pad("Cant.", 5)} ${pad("Unid", 4)} ${pad("P.Unit", 11)} ${izq("Importe", 17)}\n` + sep;
    for (const p of t.partidas) {
      x += String(p.descripcion).slice(0, 38) + "\n";
      x += `${pad(Number(p.cantidad).toFixed(3), 5)} ${pad(p.unidad || "", 4)} ${pad(m(p.precio), 11)} ${izq(m(p.total), 17)}\n`;
    }
    x += sep + `${pad("Total a Pagar :", 15)} $ ${izq(m(t.total), 19)}\n`;
    if (t.efectivo > 0) {
      x += `${pad("Recibido:", 15)} $ ${izq(m(t.efectivo), 19)}\n`;
      if (t.cambio >= 0) x += `${pad("Cambio  :", 15)} $ ${izq(m(t.cambio), 19)}\n`;
    }
    x += sep;
    if (t.cliente_credito) {
      x += t.credito ? "  VENTA  A CRÉDITO\n" : "  VENTA AL CONTADO\n";
      x += `Cliente: ${String(t.cliente_credito).slice(0, 30)}\n\n\n  Firma: ________________________\n`;
      x += `             ${String(t.cliente_credito).slice(0, 20)}\n` + sep;
    }
    x += "    *** GRACIAS POR SU COMPRA ***\n      NO ES COMPROBANTE FISCAL\n" + sep;
    return x;
  }
  function htmlTicket(t) {
    const e = t.empresa || {};
    return `<div class="ticket"><div class="cab">${e.logo ? `<img src="${esc(e.logo)}" style="max-width:120px"><br>` : ""}
      <b>${esc(e.empresa)}</b><br><small>${esc(e.rfc)}<br>${esc(e.direccion)}${e.telefono ? "<br>TEL: " + esc(e.telefono) : ""}</small></div>
      <pre>${esc(textoTicket(t))}</pre></div>`;
  }
  let ultimoTicket = null;
  function mostrarTicket(t, folio) {
    ultimoTicket = t;
    $("#tk-titulo").textContent = `${t.documento} ${folio} grabada`;
    $("#tk-vista").innerHTML = htmlTicket(t);
    abrir("m-ticket");
  }
  function imprimirTicket() {
    if (!ultimoTicket) return;
    $("#zona-impresion").innerHTML = htmlTicket(ultimoTicket);
    window.print();
  }
  $("#tk-imprimir").onclick = imprimirTicket;
  $("#tk-nueva").onclick = () => { cerrar("m-ticket"); enfocar(txCodigo); };

  // ------------------------------------------------------------------ limpiar / salir
  $("#bt-limpiar").onclick = async () => {
    if (estado.carrito.length && !(await confirmar("¿Limpiar la venta y borrar todos los productos?", "Limpiar venta"))) return;
    estado.carrito = []; estado.sel = -1; limpiarProducto(); pintarCarrito();
    await ponerCliente("000000", false);
  };
  $("#bt-salir").onclick = async () => {
    if (estado.carrito.length && !(await confirmar("Hay productos capturados. ¿Salir de todos modos?", "Salir"))) return;
    try { localStorage.removeItem("pos_venta"); } catch { }
    location.href = "/logout";
  };

  // Teclas como en el escritorio (con teclado físico)
  document.addEventListener("keydown", (e) => {
    if (hayModal()) return;
    const mapa = { F1: () => abrirBuscador(txCodigo.value.trim()), F2: () => $("#bt-limpiar").click(),
      F9: () => iniciarCobro("Cotizacion"), F10: () => iniciarCobro("Remision"),
      F11: () => iniciarCobro("Factura"), F12: () => iniciarCobro("Nota de Venta") };
    if (mapa[e.key]) { e.preventDefault(); mapa[e.key](); }
  });

  // ------------------------------------------------------------------ inicio
  (async () => {
    await cargarTurno();
    const prev = recuperar();
    if (prev?.carrito?.length) {
      estado.carrito = prev.carrito;
      aviso("Se recuperó la venta que estaba en captura.", "ok");
    }
    if (prev?.forma) $(`#forma-pago .op[data-forma="${prev.forma}"]`)?.click();
    try { await ponerCliente(prev?.cliente || "000000", false); } catch (e) { aviso(e.message, "error"); }
    pintarCarrito();
    enfocar(txCodigo);
  })();
})();
