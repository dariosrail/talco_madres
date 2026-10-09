# -*- coding: utf-8 -*-
# =====================================================================
#  SOADIN · PUNTO DE VENTA PARA TABLET (Flask)
#
#  Versión web del punto de venta de escritorio (pepo_pventaok.py).
#  Usa las mismas tablas MySQL y la misma lógica de cálculo y grabado:
#    INARMA01 / INARAR01 / INARIP01 / INAREP01 / INAREQ01  (productos)
#    VEARAG01 (vendedores)  VEARDI01 (día)  VEARTN01 (turnos)
#    VEARFO01 (folios)  VEARMA01 / VEARMO01 (ventas)  INARMV01 (kardex)
#    CAARMA01 (clientes)  CAARFA01 / CAARMO01 (cartera)
#
#  Configuración por variables de entorno (ver .env.example).
# =====================================================================
import os
import re
import secrets
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from functools import wraps

import pymysql
from pymysql.cursors import DictCursor
from flask import (
    Flask, jsonify, redirect, render_template, request, session, url_for,
)

# =====================================================================
#  CONFIGURACIÓN
# =====================================================================
try:                                   # en tu PC lee el archivo .env; en Railway no hace falta
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SEGURA", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=14),
)


def _env(*nombres, default=None):
    for n in nombres:
        v = os.environ.get(n)
        if v not in (None, ""):
            return v
    return default


def _datos_url():
    """Lee MYSQL_URL / DATABASE_URL (formato mysql://usuario:clave@host:puerto/base)."""
    url = _env("MYSQL_URL", "DATABASE_URL", "MYSQL_PUBLIC_URL")
    if not url:
        return {}
    from urllib.parse import urlparse, unquote
    u = urlparse(url)
    return {
        "host": u.hostname, "port": u.port or 3306,
        "user": unquote(u.username or ""), "password": unquote(u.password or ""),
        "database": (u.path or "/").lstrip("/"),
    }


def config_mysql():
    url = _datos_url()
    return {
        "host": _env("DB_HOST", "MYSQLHOST", default=url.get("host") or "localhost"),
        "port": int(_env("DB_PORT", "MYSQLPORT", default=str(url.get("port") or 3306))),
        "user": _env("DB_USER", "MYSQLUSER", default=url.get("user") or "root"),
        "password": _env("DB_PASSWORD", "MYSQLPASSWORD", default=url.get("password") or ""),
        "database": _env("DB_NAME", "MYSQLDATABASE", default=url.get("database") or "soadin"),
        "charset": "utf8mb4",
        "init_command": "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci",
        "cursorclass": DictCursor,
        "autocommit": False,
        "connect_timeout": 10,
    }


def datos_empresa():
    return {
        "empresa": _env("EMPRESA_NOMBRE", default="MI EMPRESA"),
        "rfc": _env("EMPRESA_RFC", default=""),
        "direccion": _env("EMPRESA_DIRECCION", default=""),
        "telefono": _env("EMPRESA_TELEFONO", default=""),
        "logo": _env("EMPRESA_LOGO_URL", default=""),
    }


@contextmanager
def bd(commit=False):
    """Conexión + cursor DictCursor. Con commit=True confirma al salir sin error."""
    conn = pymysql.connect(**config_mysql())
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# =====================================================================
#  UTILIDADES (mismas reglas que el escritorio)
# =====================================================================
def to_float(v, default=0.0):
    if v is None:
        return default
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or default)
    except ValueError:
        return default


def redondear_moneda(valor):
    """Redondeo del punto de venta: <.25 baja, <.70 a .50, si no sube al peso."""
    pesos = int(valor)
    centavos = valor - pesos
    if centavos < 0.25:
        return float(pesos)
    if centavos < 0.70:
        return pesos + 0.5
    return pesos + 1.0


def txt(v):
    return "" if v is None else str(v)


def fecha_txt(v):
    return str(v)[:10] if v else ""


def parse_fecha(texto):
    texto = (texto or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            pass
    return None


def vendedor_puede_cambiar_descripcion(vendedor):
    """PENDIENTE: definir de dónde sale el permiso (igual que en escritorio).
    Mientras tanto se permite a todos."""
    return True


class ErrorVenta(Exception):
    """Error de validación que se muestra tal cual al usuario."""


# =====================================================================
#  SESIÓN
# =====================================================================
def login_requerido(f):
    @wraps(f)
    def envoltura(*args, **kwargs):
        if not session.get("vendedor"):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="La sesión terminó. Vuelve a entrar."), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return envoltura


def api(f):
    """Convierte excepciones en JSON {ok:false, error}."""
    @wraps(f)
    def envoltura(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ErrorVenta as e:
            return jsonify(ok=False, error=str(e)), 400
        except pymysql.MySQLError as e:
            app.logger.exception("Error MySQL")
            return jsonify(ok=False, error=f"Error de base de datos: {e}"), 500
        except Exception as e:
            app.logger.exception("Error")
            return jsonify(ok=False, error=f"Error: {e}"), 500
    return envoltura


def turno_abierto(cur, caja):
    cur.execute("""
        SELECT * FROM VEARTN01
        WHERE caja=%s AND status='0'
        ORDER BY turno DESC LIMIT 1
    """, (caja,))
    return cur.fetchone()


def datos_turno(cur):
    """Equivale a checa_cajaabierta(): fecha, cajero, caja y turno vigentes."""
    res = turno_abierto(cur, session.get("caja"))
    if not res:
        raise ErrorVenta("No hay caja abierta. Verifique con el administrador del sistema.")
    return {
        "fecha": fecha_txt(res.get("fecha")),
        "cajero": txt(res.get("cajero")).strip(),
        "caja": txt(res.get("caja")).zfill(4),
        "turno": txt(res.get("turno")).zfill(2),
    }


# =====================================================================
#  LOGIN
# =====================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    cajas, dia = [], None
    try:
        with bd() as cur:
            cur.execute("SELECT dia_abrio FROM VEARDI01 WHERE status='ABIERTO' ORDER BY id DESC LIMIT 1")
            dia = cur.fetchone()
            cur.execute("""
                SELECT caja, MAX(turno) AS turno
                FROM VEARTN01 WHERE status='0'
                GROUP BY caja ORDER BY caja
            """)
            cajas = [{"caja": txt(r["caja"]), "turno": txt(r["turno"]).zfill(2)} for r in cur.fetchall()]
    except Exception as e:
        error = f"No se pudo conectar a la base de datos: {e}"

    if request.method == "POST" and not error:
        codigo = request.form.get("codigo", "").strip().zfill(4)
        password = request.form.get("password", "").strip()
        caja = request.form.get("caja", "").strip()
        if not dia:
            error = "No hay día abierto. Verifique con el administrador del sistema."
        elif not caja or caja not in [c["caja"] for c in cajas]:
            error = "Selecciona una caja con turno abierto."
        else:
            with bd() as cur:
                cur.execute("""
                    SELECT vendedor, password FROM VEARAG01
                    WHERE codigo=%s AND status='ACTIVO'
                """, (codigo,))
                v = cur.fetchone()
            if not v:
                error = "Vendedor no encontrado o inactivo."
            elif str(v.get("password") or "").strip().lower() != password.lower():
                error = "La contraseña es incorrecta."
            else:
                session.clear()
                session.permanent = True
                session["vendedor"] = f"{codigo} {v['vendedor']}"
                session["vendedor_codigo"] = codigo
                session["caja"] = caja
                resp = redirect(url_for("venta"))
                resp.set_cookie("caja_tablet", caja, max_age=60 * 60 * 24 * 365, samesite="Lax")
                return resp

    return render_template(
        "login.html", empresa=datos_empresa(), cajas=cajas, error=error,
        dia=fecha_txt(dia["dia_abrio"]) if dia else "",
        caja_guardada=request.cookies.get("caja_tablet", ""),
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =====================================================================
#  PANTALLA PRINCIPAL
# =====================================================================
@app.route("/")
@login_requerido
def venta():
    try:
        with bd() as cur:
            t = datos_turno(cur)
    except ErrorVenta as e:
        session.clear()
        return render_template("login.html", empresa=datos_empresa(), cajas=[], error=str(e),
                               dia="", caja_guardada="")
    return render_template(
        "venta.html", empresa=datos_empresa(), vendedor=session["vendedor"], turno=t,
        puede_descripcion=vendedor_puede_cambiar_descripcion(session["vendedor"]),
    )


@app.route("/salud")
def salud():
    """Para Railway: revisa que la app y la base respondan."""
    try:
        with bd() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        return jsonify(ok=True, bd=True)
    except Exception as e:
        return jsonify(ok=True, bd=False, error=str(e)), 200


@app.route("/api/turno")
@login_requerido
@api
def api_turno():
    with bd() as cur:
        t = datos_turno(cur)
        cur.execute("SELECT codigo, folio FROM VEARFO01")
        folios = {r["codigo"]: int(r["folio"] or 0) + 1 for r in cur.fetchall()}
    return jsonify(ok=True, turno=t, folios=folios)


# =====================================================================
#  PRODUCTOS
# =====================================================================
SQL_PRODUCTO = """
    SELECT C.tasa, A.codigo, A.descripcion, A.unidad, A.codiva,
           A.lista1, A.lista2, A.lista3, A.lista4, A.lista5, A.lista6,
           A.ligaw, A.codsat, A.umesat, A.modiprecio, A.fraccionar,
           (IFNULL(B.saldoinicial,0) + IFNULL(B.entradas,0) - IFNULL(B.salidas,0)) AS existencia
    FROM INARMA01 A
    LEFT JOIN INARAR01 B ON A.codigo = B.codigo
    LEFT JOIN INARIP01 C ON A.codiva = C.codigo
    WHERE A.codigo = %s
"""


def leer_producto(cur, codigo):
    cur.execute(SQL_PRODUCTO, (codigo,))
    row = cur.fetchone()
    if not row:
        return None
    tasa = float(row.get("tasa") or 0)
    precios = []
    for i in range(1, 7):
        base = float(row.get(f"lista{i}") or 0)
        if base > 0:
            precios.append({"lista": i, "precio": round(base * (1 + tasa / 100), 3)})
    cur.execute("SELECT COUNT(*) AS total FROM INAREQ01 WHERE codigo_principal=%s", (codigo,))
    eq = cur.fetchone()
    return {
        "codigo": txt(row.get("codigo")) or codigo,
        "descripcion": txt(row.get("descripcion")),
        "unidad": txt(row.get("unidad")),
        "tasa": tasa,
        "lista1": float(row.get("lista1") or 0),
        "precios": precios,
        "existencia": float(row.get("existencia") or 0),
        "fraccionar": int(row.get("fraccionar") or 0),
        "modiprecio": str(row.get("modiprecio") or "0"),
        "codsat": txt(row.get("codsat")) or "01010101",
        "umesat": txt(row.get("umesat")) or "H87",
        "ligaw": txt(row.get("ligaw")).strip(),
        "equivalentes": int(eq["total"]) if eq else 0,
    }


def precio_especial(cur, codigo, cantidad):
    """INAREP01: precio por rango de cantidad. Regresa el precio o None."""
    if not codigo or cantidad <= 0:
        return None
    cur.execute("""
        SELECT precio1, desde1, hasta1, precio2, desde2, hasta2,
               precio3, desde3, hasta3, precio4, desde4, hasta4
        FROM INAREP01
        WHERE TRIM(codigo) = %s
          AND (caducidad IS NULL OR caducidad <= '1900-01-01' OR caducidad >= CURDATE())
        LIMIT 1
    """, (codigo,))
    row = cur.fetchone()
    if not row:
        return None
    for n in range(1, 5):
        desde = float(row.get(f"desde{n}") or 0)
        hasta = float(row.get(f"hasta{n}") or 0)
        precio = float(row.get(f"precio{n}") or 0)
        if precio > 0 and desde <= cantidad <= hasta:
            return precio
    return None


def calcular_partida(cantidad, precio_con_iva, tasa_iva, pdesc):
    """Mismo cálculo que agrega_grid() del escritorio, con los mismos redondeos."""
    precio_unitario = round(precio_con_iva, 3) / (1 + tasa_iva / 100)
    pdesc = round(pdesc, 2)
    importe = round(precio_unitario * cantidad, 3)
    descuento = round((pdesc / 100) * importe, 3)
    importe_neto = round(importe - descuento, 3)
    iva = round(importe_neto * (tasa_iva / 100), 3)
    total = redondear_moneda(round(importe_neto + iva, 3))
    precio_final = round(total / cantidad, 2) if cantidad else 0.0
    # Valores tal como quedan en el grid (texto con 3 decimales)
    return {
        "cantidad": round(cantidad, 3),
        "precio": round(precio_unitario, 3),
        "pdesc": pdesc,
        "descuento": descuento,
        "tasa": round(tasa_iva, 2),
        "iva": iva,
        "precio_final": precio_final,
        "total": round(total, 3),
    }


def armar_partida(cur, entrada):
    """Valida y calcula una partida a partir de lo capturado.
    entrada: codigo, cantidad, precio (con IVA), pdesc, y opcionalmente
             descripcion, unidad, codsat, umesat (cambios de la partida)."""
    codigo = txt(entrada.get("codigo")).strip().upper()
    prod = leer_producto(cur, codigo)
    if not prod:
        raise ErrorVenta(f"El producto {codigo} no existe.")
    cantidad = to_float(entrada.get("cantidad"))
    if cantidad <= 0:
        raise ErrorVenta("Debe ingresar una cantidad mayor a 0.")
    if prod["fraccionar"] == 0 and cantidad != int(cantidad):
        raise ErrorVenta(f"{prod['descripcion']}: este producto NO se puede fraccionar.")

    pdesc = to_float(entrada.get("pdesc"))
    bandera = "0"
    especial = precio_especial(cur, codigo, cantidad)
    if especial:
        precio = especial
        pdesc = 0.0
        bandera = "2"
    else:
        precio = round(to_float(entrada.get("precio")), 3)
        if prod["modiprecio"] != "1":
            validos = [p["precio"] for p in prod["precios"]] or [0.0]
            if not any(abs(precio - v) < 0.0006 for v in validos):
                precio = validos[0]
    if pdesc < 0 or pdesc > 100:
        raise ErrorVenta("El % de descuento debe estar entre 0 y 100.")

    calc = calcular_partida(cantidad, precio, prod["tasa"], pdesc)
    permiso = vendedor_puede_cambiar_descripcion(session.get("vendedor"))
    desc = txt(entrada.get("descripcion")).strip() if permiso else ""
    unidad = txt(entrada.get("unidad")).strip().upper() if permiso else ""
    calc.update({
        "codigo": codigo,
        "descripcion": desc or prod["descripcion"],
        "descripcion_original": prod["descripcion"],
        "unidad": unidad or prod["unidad"],
        "unidad_original": prod["unidad"],
        "codsat": txt(entrada.get("codsat")).strip() or prod["codsat"],
        "umesat": txt(entrada.get("umesat")).strip().upper() or prod["umesat"],
        "lista1": prod["lista1"],
        "precio_capturado": precio,
        "bandera": bandera,
        "existencia": prod["existencia"],
        "modiprecio": prod["modiprecio"],
        "precios": prod["precios"],
    })
    return calc


def calcular_totales(partidas):
    """Igual que calcular_totales() del escritorio."""
    t = {k: 0.0 for k in ("exento", "grabado", "cantidad", "importe", "descuento", "iva", "neto")}
    for p in partidas:
        sub = p["cantidad"] * p["precio"] - p["descuento"]
        if p["iva"] == 0:
            t["exento"] += sub
        else:
            t["grabado"] += sub
        t["cantidad"] += p["cantidad"]
        t["importe"] += p["cantidad"] * p["precio"]
        t["descuento"] += p["descuento"]
        t["iva"] += p["iva"]
        t["neto"] += p["total"]
    t["subtotal"] = t["importe"] - t["descuento"]
    t["cuadre"] = t["subtotal"] + t["iva"]
    return {k: round(v, 3) for k, v in t.items()}


@app.route("/api/producto/<path:codigo>")
@login_requerido
@api
def api_producto(codigo):
    codigo = codigo.strip().upper()
    with bd() as cur:
        prod = leer_producto(cur, codigo)
    if not prod:
        return jsonify(ok=False, no_existe=True, error=f"No existe el código {codigo}")
    return jsonify(ok=True, producto=prod)


@app.route("/api/partida", methods=["POST"])
@login_requerido
@api
def api_partida():
    with bd() as cur:
        p = armar_partida(cur, request.get_json(force=True) or {})
    return jsonify(ok=True, partida=p)


MODOS_BUSQUEDA = {
    "descripcion": ("descripcion", False),
    "inicial": ("descripcion", True),
    "codigo": ("codigo", False),
    "parte": ("parte", False),
    "codprovee": ("codprovee", False),
    "presentacion": ("presentacion", False),
}


@app.route("/api/productos")
@login_requerido
@api
def api_productos():
    termino = request.args.get("q", "").strip()
    modo = request.args.get("modo", "descripcion")
    if not termino:
        return jsonify(ok=True, productos=[])
    campo, inicial = MODOS_BUSQUEDA.get(modo, MODOS_BUSQUEDA["descripcion"])
    patron = f"{termino}%" if inicial else f"%{termino}%"
    with bd() as cur:
        cur.execute(f"""
            SELECT A.codigo, A.descripcion, A.lista1, A.lista2, A.parte, A.presentacion,
                   A.ubicacion, A.codprovee, C.tasa,
                   (IFNULL(B.saldoinicial,0) + IFNULL(B.entradas,0) - IFNULL(B.salidas,0)) AS existencia
            FROM INARMA01 A
            LEFT JOIN INARAR01 B ON A.codigo = B.codigo
            LEFT JOIN INARIP01 C ON A.codiva = C.codigo
            WHERE LOWER(A.{campo}) LIKE LOWER(%s)
            ORDER BY A.descripcion
            LIMIT 300
        """, (patron,))
        filas = cur.fetchall()
        eqv = {}
        codigos = [r["codigo"] for r in filas]
        if codigos:
            marcas = ",".join(["%s"] * len(codigos))
            cur.execute(f"""
                SELECT codigo_principal, COUNT(*) AS total FROM INAREQ01
                WHERE codigo_principal IN ({marcas}) GROUP BY codigo_principal
            """, codigos)
            eqv = {r["codigo_principal"]: int(r["total"]) for r in cur.fetchall()}
    productos = []
    for r in filas:
        tasa = float(r.get("tasa") or 0)
        productos.append({
            "codigo": txt(r["codigo"]),
            "descripcion": txt(r.get("descripcion")),
            "precio": round(float(r.get("lista1") or 0) * (1 + tasa / 100), 2),
            "existencia": float(r.get("existencia") or 0),
            "parte": txt(r.get("parte")),
            "presentacion": txt(r.get("presentacion")),
            "ubicacion": txt(r.get("ubicacion")),
            "codprovee": txt(r.get("codprovee")),
            "equivalentes": eqv.get(r["codigo"], 0),
        })
    return jsonify(ok=True, productos=productos, limite=len(productos) >= 300)


@app.route("/api/equivalentes/<path:codigo>")
@login_requerido
@api
def api_equivalentes(codigo):
    with bd() as cur:
        cur.execute("SELECT codigo_equivalente FROM INAREQ01 WHERE codigo_principal=%s", (codigo,))
        codigos = [r["codigo_equivalente"] for r in cur.fetchall()]
        filas = []
        if codigos:
            marcas = ",".join(["%s"] * len(codigos))
            cur.execute(f"""
                SELECT A.codigo, A.descripcion, A.parte, A.lista1, A.presentacion, C.tasa,
                       (IFNULL(B.saldoinicial,0) + IFNULL(B.entradas,0) - IFNULL(B.salidas,0)) AS existencia,
                       B.ultima_venta, B.ultima_compra
                FROM INARMA01 A
                LEFT JOIN INARAR01 B ON A.codigo = B.codigo
                LEFT JOIN INARIP01 C ON A.codiva = C.codigo
                WHERE A.codigo IN ({marcas})
            """, codigos)
            filas = cur.fetchall()
    out = [{
        "codigo": txt(r["codigo"]),
        "descripcion": txt(r.get("descripcion")),
        "parte": txt(r.get("parte")),
        "precio": round(float(r.get("lista1") or 0) * (1 + float(r.get("tasa") or 0) / 100), 2),
        "existencia": float(r.get("existencia") or 0),
        "ultima_venta": fecha_txt(r.get("ultima_venta")),
        "ultima_compra": fecha_txt(r.get("ultima_compra")),
        "presentacion": txt(r.get("presentacion")),
    } for r in filas]
    return jsonify(ok=True, equivalentes=out)


@app.route("/api/existencias", methods=["POST"])
@login_requerido
@api
def api_existencias():
    datos = request.get_json(force=True) or {}
    faltantes = revisar_existencias(datos.get("partidas") or [])
    return jsonify(ok=True, faltantes=faltantes)


def revisar_existencias(partidas, cur=None):
    """verificar_existencias_en_tabla(): suma por código y compara contra INARAR01."""
    cantidades = defaultdict(float)
    for p in partidas:
        cantidades[txt(p.get("codigo")).strip().upper()] += to_float(p.get("cantidad"))
    if not cantidades:
        return []

    def revisar(c):
        faltan = []
        for codigo, pedida in cantidades.items():
            c.execute("""
                SELECT (IFNULL(saldoinicial,0) + IFNULL(entradas,0) - IFNULL(salidas,0)) AS existencia
                FROM INARAR01 WHERE codigo=%s
            """, (codigo,))
            row = c.fetchone()
            exis = float(row["existencia"]) if row else 0.0
            if pedida > exis:
                faltan.append({"codigo": codigo, "existencia": exis, "pedida": pedida})
        return faltan

    if cur is not None:
        return revisar(cur)
    with bd() as c:
        return revisar(c)


# =====================================================================
#  CLIENTES Y CARTERA
# =====================================================================
@app.route("/api/clientes")
@login_requerido
@api
def api_clientes():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(ok=True, clientes=[])
    patron = f"%{q}%"
    with bd() as cur:
        cur.execute("""
            SELECT codigo, rfc, cliente,
                   CONCAT_WS(' ', calle, exterior, interior, colonia) AS direccion,
                   codigopostal, ultimacompra
            FROM CAARMA01
            WHERE cliente LIKE %s OR rfc LIKE %s OR codigo LIKE %s
            ORDER BY cliente LIMIT 200
        """, (patron, patron, patron))
        filas = cur.fetchall()
    return jsonify(ok=True, clientes=[{
        "codigo": txt(r["codigo"]), "rfc": txt(r.get("rfc")), "cliente": txt(r.get("cliente")),
        "direccion": txt(r.get("direccion")), "cp": txt(r.get("codigopostal")),
        "ultimacompra": fecha_txt(r.get("ultimacompra")),
    } for r in filas])


def estado_cliente(cur, codigo):
    """Datos del cliente + saldos, como buscar_cliente() de ClienteVentas."""
    codigo = txt(codigo).strip().zfill(6)
    cur.execute("""
        SELECT codigo, cliente, nombrecorto, rfc, telefono, email, calle, exterior, interior,
               colonia, municipio, estado, codigopostal, diascredito, limitecredito,
               regimenfiscal, usocfdi
        FROM CAARMA01 WHERE codigo=%s
    """, (codigo,))
    cli = cur.fetchone()
    if not cli:
        return None
    cur.execute("""
        SELECT COALESCE(SUM(cargos - abonos), 0) AS saldo
        FROM CAARFA01 WHERE cliente=%s AND status='0'
    """, (codigo,))
    saldo = float((cur.fetchone() or {}).get("saldo") or 0)
    cur.execute("""
        SELECT COALESCE(SUM(cargos - abonos), 0) AS por_vencer
        FROM CAARFA01 WHERE cliente=%s AND status='0'
          AND DATE(fecha_vencimiento) >= CURDATE()
    """, (codigo,))
    por_vencer = float((cur.fetchone() or {}).get("por_vencer") or 0)
    cur.execute("""
        SELECT COALESCE(SUM(cargos - abonos), 0) AS saldo_vencido,
               MAX(DATEDIFF(CURDATE(), DATE(fecha_vencimiento))) AS dias_vencidos
        FROM CAARFA01 WHERE cliente=%s AND status='0'
          AND DATE(fecha_vencimiento) < CURDATE()
    """, (codigo,))
    v = cur.fetchone() or {}
    vencido = float(v.get("saldo_vencido") or 0)
    dias_vencidos = int(v.get("dias_vencidos") or 0)
    limite = float(cli.get("limitecredito") or 0)
    disponible = limite - saldo
    direccion = ", ".join(x for x in [
        " ".join(txt(cli.get(k)).strip() for k in ("calle", "exterior", "interior")).strip(),
        txt(cli.get("colonia")).strip(), txt(cli.get("municipio")).strip(),
        txt(cli.get("estado")).strip(),
        f"CP {txt(cli.get('codigopostal')).strip()}" if cli.get("codigopostal") else "",
    ] if x)
    return {
        "codigo": codigo,
        "cliente": txt(cli.get("cliente")),
        "rfc": txt(cli.get("rfc")),
        "telefono": txt(cli.get("telefono")),
        "direccion": direccion,
        "diascredito": int(cli.get("diascredito") or 0),
        "limite": limite,
        "saldo": saldo,
        "por_vencer": por_vencer,
        "vencido": vencido,
        "dias_vencidos": dias_vencidos,
        "disponible": disponible,
        # Misma regla que calcular_cambio(): sin crédito si hay vencido o no hay disponible
        "credito_bloqueado": dias_vencidos > 0 or disponible <= 0,
    }


@app.route("/api/cliente/<codigo>")
@login_requerido
@api
def api_cliente(codigo):
    with bd() as cur:
        c = estado_cliente(cur, codigo)
    if not c:
        return jsonify(ok=False, error="Cliente no encontrado")
    return jsonify(ok=True, cliente=c)


RANGOS = ["Más de 180", "121 a 180", "91 a 120", "61 a 90", "31 a 60", "16 a 30", "0 a 15", "Por vencer"]


def rango_dias(dias):
    if dias < 0:
        return "Por vencer"
    for limite, nombre in ((15, "0 a 15"), (30, "16 a 30"), (60, "31 a 60"), (90, "61 a 90"),
                           (120, "91 a 120"), (180, "121 a 180")):
        if dias <= limite:
            return nombre
    return "Más de 180"


@app.route("/api/cartera/<codigo>")
@login_requerido
@api
def api_cartera(codigo):
    codigo = codigo.strip().zfill(6)
    with bd() as cur:
        cur.execute("""
            SELECT documento, fecha_documento, fecha_vencimiento,
                   COALESCE(cargos,0) AS cargos, COALESCE(abonos,0) AS abonos,
                   COALESCE(cargos - abonos,0) AS saldo,
                   DATEDIFF(CURDATE(), DATE(fecha_vencimiento)) AS dias, status
            FROM CAARFA01 WHERE cliente=%s
            ORDER BY fecha_vencimiento ASC, documento ASC
        """, (codigo,))
        filas = cur.fetchall()
    resumen = {r: {"docs": 0, "importe": 0.0} for r in RANGOS}
    docs = []
    for r in filas:
        saldo = float(r.get("saldo") or 0)
        if saldo <= 0 or txt(r.get("status")) == "1":
            continue
        dias = int(r.get("dias") or 0)
        rango = rango_dias(dias)
        resumen[rango]["docs"] += 1
        resumen[rango]["importe"] += saldo
        docs.append({
            "documento": txt(r["documento"]),
            "fecha": fecha_txt(r.get("fecha_documento")),
            "vencimiento": fecha_txt(r.get("fecha_vencimiento")),
            "cargos": float(r.get("cargos") or 0),
            "abonos": float(r.get("abonos") or 0),
            "saldo": saldo, "dias": dias, "rango": rango,
        })
    return jsonify(ok=True, documentos=docs,
                   resumen=[{"rango": k, **v} for k, v in resumen.items()])


@app.route("/api/cartera/<codigo>/<path:documento>")
@login_requerido
@api
def api_cartera_documento(codigo, documento):
    with bd() as cur:
        cur.execute("""
            SELECT movimientos, tmov, concepto, fecha_documento, importe, referencia, observaciones
            FROM CAARMO01 WHERE cliente=%s AND documento=%s ORDER BY movimientos
        """, (codigo.strip().zfill(6), documento))
        filas = cur.fetchall()
    return jsonify(ok=True, movimientos=[{
        "mov": txt(r.get("movimientos")),
        "tipo": "Cargo" if txt(r.get("tmov")) == "C" else "Abono",
        "concepto": txt(r.get("concepto")),
        "fecha": fecha_txt(r.get("fecha_documento")),
        "importe": float(r.get("importe") or 0),
        "referencia": txt(r.get("referencia")),
        "observaciones": txt(r.get("observaciones")),
    } for r in filas])


@app.route("/api/cliente", methods=["POST"])
@login_requerido
@api
def api_cliente_nuevo():
    """Alta rápida de cliente (mismos valores por defecto que ClienteInicio)."""
    d = request.get_json(force=True) or {}
    nombre = txt(d.get("cliente")).strip().upper()
    if not nombre:
        raise ErrorVenta("Debe capturar el nombre del cliente.")
    rfc = txt(d.get("rfc")).strip().upper() or "XAXX010101000"
    with bd(commit=True) as cur:
        cur.execute("SELECT MAX(CAST(codigo AS UNSIGNED)) AS n FROM CAARMA01")
        n = (cur.fetchone() or {}).get("n")
        codigo = "000001" if n is None else f"{int(n) + 1:06d}"
        ahora = datetime.now()
        cur.execute("""
            INSERT INTO CAARMA01 (
                codigo, cliente, calle, exterior, interior, colonia, localidad,
                referencia, municipio, estado, pais, codigopostal,
                rfc, curp, email, telefono, grupo, agente, fecha_alta,
                diascredito, limitecredito, forma_pago, nombrecorto,
                regimenfiscal, usocfdi, lista1, lista2, lista3, lista4, lista5, lista6,
                observaciones, status, bitacora
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            codigo, nombre, txt(d.get("calle")).strip(), txt(d.get("exterior")).strip(), "",
            txt(d.get("colonia")).strip(), txt(d.get("municipio")).strip(), "",
            txt(d.get("municipio")).strip(), txt(d.get("estado")).strip(), "MEXICO",
            txt(d.get("cp")).strip(), rfc, "", txt(d.get("email")).strip(),
            txt(d.get("telefono")).strip(), "", "", ahora.date(), 0, 0.0, "", nombre[:30],
            "616 - Sin obligaciones fiscales", "S16 - Sin efectos fiscales",
            "1", "0", "0", "0", "0", "0", "", "0", ahora.strftime("%Y-%m-%d %H:%M:%S"),
        ))
    return jsonify(ok=True, codigo=codigo)


# =====================================================================
#  GRABAR VENTA  (port de grabar_venta_en_mysql)
# =====================================================================
MAPEO_FOLIO = {"Factura": "FA", "Remision": "RE", "Nota de Venta": "NO", "Cotizacion": "CO"}
NOMBRE_DOC = {"C": "COTIZACION", "R": "REMISION", "F": "FACTURA", "N": "NOTA"}


def generar_folio(cur, tipo_documento):
    cod = MAPEO_FOLIO.get(tipo_documento, "RE")
    cur.execute("SELECT folio FROM VEARFO01 WHERE codigo=%s FOR UPDATE", (cod,))
    row = cur.fetchone()
    if not row:
        raise ErrorVenta(f"No existe el folio {cod} en VEARFO01.")
    siguiente = int(row["folio"]) + 1
    cur.execute("UPDATE VEARFO01 SET folio=%s WHERE codigo=%s", (siguiente, cod))
    return f"{cod[0]}{str(siguiente).zfill(9)}"


@app.route("/api/venta", methods=["POST"])
@login_requerido
@api
def api_venta():
    d = request.get_json(force=True) or {}
    tipo_documento = d.get("tipo_documento")
    if tipo_documento not in MAPEO_FOLIO:
        raise ErrorVenta("Tipo de documento no válido.")
    entradas = d.get("partidas") or []
    if not entradas:
        raise ErrorVenta("Debes capturar al menos un producto.")
    pago = d.get("pago") or {}
    cotizacion = tipo_documento == "Cotizacion"

    conn = pymysql.connect(**config_mysql())
    try:
        cur = conn.cursor()
        turno = datos_turno(cur)

        # 1) Recalcular partidas en el servidor (no se confía en el navegador)
        partidas = [armar_partida(cur, e) for e in entradas]

        # 2) Existencias (cotización solo avisa)
        faltan = revisar_existencias(partidas, cur)
        if faltan and not cotizacion:
            detalle = "; ".join(f"{f['codigo']}: hay {f['existencia']:,.2f}, pides {f['pedida']:,.2f}"
                                for f in faltan)
            raise ErrorVenta(f"Existencia insuficiente. {detalle}")

        # 3) Totales como los pasa el escritorio a ClienteVentas
        tot = calcular_totales(partidas)
        importe = round(tot["importe"], 2)
        descuentos = round(tot["descuento"], 2)
        impuestos = round(tot["iva"], 2)
        neto = round(tot["neto"], 2)
        subtotal = importe - descuentos
        grabado = round(tot["grabado"], 2)
        exento = round(tot["exento"], 2)

        # 4) Cliente y pago (reglas de ClienteVentas)
        cli = estado_cliente(cur, d.get("cliente") or "000000")
        if not cli:
            raise ErrorVenta("Cliente no encontrado.")
        efectivo = round(to_float(pago.get("efectivo")), 2)
        tarjeta = round(to_float(pago.get("tarjeta")), 2)
        transferencia = round(to_float(pago.get("transferencia")), 2)
        if min(efectivo, tarjeta, transferencia) < 0:
            raise ErrorVenta("Los importes de pago no pueden ser negativos.")
        recibido = efectivo + tarjeta + transferencia
        es_credito = bool(pago.get("credito")) and recibido < neto
        if recibido < neto and not es_credito:
            raise ErrorVenta("El pago es menor al total de la venta.")
        if es_credito and cli["credito_bloqueado"]:
            raise ErrorVenta("El crédito de este cliente está bloqueado (saldo vencido o sin disponible).")

        fecha_docto = parse_fecha(turno["fecha"]) or datetime.now()
        if es_credito:
            vencimiento = (fecha_docto + timedelta(days=cli["diascredito"])).strftime("%Y-%m-%d")
            dias_credito = str(cli["diascredito"])
        else:
            vencimiento = turno["fecha"]
            dias_credito = "0"

        cliente_data = {
            "codigo": cli["codigo"], "rfc": cli["rfc"], "cliente": cli["cliente"],
            "importe": importe, "descuentos": descuentos, "subtotal": subtotal,
            "pdesc": 0.0, "descuentosp": 0.0, "subtotal2": 0.0, "impuestos": impuestos,
            "neto": neto, "total": neto, "efectivo": efectivo, "tarjeta": tarjeta,
            "transferencia": transferencia,
            "credito": round(neto - recibido, 2) if es_credito else 0.0,
            "vencimiento": vencimiento, "dias_credito": dias_credito,
            "cliente_credito": cli["cliente"] if es_credito else "",
            "status_credito": "1" if es_credito else "0",
            "refepago": txt(pago.get("refepago")).strip(),
            "observacionespago": txt(pago.get("observacionespago")).strip(),
            "omite_comprobante": bool(pago.get("omite_comprobante", True)),
        }

        folio, abono = grabar(cur, tipo_documento, turno, partidas, tot, cliente_data,
                              grabado, exento, cotizacion)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    imprimir = (not cliente_data["omite_comprobante"]) or abs(abono - neto) > 0.0001
    return jsonify(ok=True, folio=folio, imprimir=imprimir,
                   ticket=datos_ticket(folio, turno, partidas, cliente_data))


def grabar(cur, tipo_documento, turno, partidas, tot, cd, grabado, exento, cotizacion):
    """Mismos INSERT/UPDATE que grabar_venta_en_mysql() del escritorio."""
    ahora = datetime.now()
    fecha_sql = ahora.strftime("%Y-%m-%d %H:%M:%S")
    hora_actual = ahora.strftime("%H:%M:%S")
    fecha_base = parse_fecha(turno["fecha"]) or ahora
    fecha_turno = datetime.combine(fecha_base.date(), ahora.time().replace(microsecond=0))

    folio_maestro = generar_folio(cur, tipo_documento)
    caja = turno["caja"].zfill(2)
    num_turno = turno["turno"]
    vendedor = session["vendedor"]
    cajero = turno["cajero"].zfill(4)
    cliente = cd["codigo"]
    nombre_cliente = cd["cliente"]
    st_credito = cd["status_credito"]
    cliente_credito = cd["cliente_credito"] if st_credito == "1" else ""
    fecha_venc = cd["vencimiento"]
    corte_caja = f"{fecha_turno:%Y-%m-%d}{caja}{num_turno}"

    detalles, tcostoprom, tcostoult = [], 0.0, 0.0
    for n, p in enumerate(partidas, start=1):
        codigo, cant = p["codigo"], p["cantidad"]
        cur.execute("""
            SELECT saldoinicial, entradas, salidas, impocostoinic, impoentradas,
                   imposalidas, ultimo_costo, folio_movi
            FROM INARAR01 WHERE codigo=%s LIMIT 1
        """, (codigo,))
        rc = cur.fetchone() or {}
        saldo_ini = float(rc.get("saldoinicial") or 0)
        entradas = float(rc.get("entradas") or 0)
        salidas = float(rc.get("salidas") or 0)
        existencia = saldo_ini + entradas - salidas
        impcostinic = float(rc.get("impocostoinic") or 0)
        costo_ent = float(rc.get("impoentradas") or 0)
        costo_sal = float(rc.get("imposalidas") or 0)
        folio_movi = int(rc.get("folio_movi") or 0)
        costo_ult = float(rc.get("ultimo_costo") or 0)
        costo_prom = (((impcostinic * saldo_ini) + costo_ent) - costo_sal) / existencia if existencia > 0 else 0.0
        if costo_ult == 0:
            costo_ult = costo_prom
        tcostoprom += costo_prom * cant
        tcostoult += costo_ult * cant
        folio_movi += 1

        if not cotizacion:
            cur.execute("""
                UPDATE INARAR01
                SET salidas = IFNULL(salidas,0) + %s, ultima_venta = %s,
                    imposalidas = IFNULL(imposalidas,0) + %s, folio_movi = %s
                WHERE codigo = %s
            """, (cant, fecha_turno, costo_prom * cant, folio_movi, codigo))

        folio_mov = f"{folio_maestro}{str(n).zfill(3)}"
        precioan = p["precio"]
        descuento = p["descuento"]
        # Igual que el escritorio: usa la columna "Descuento" del grid
        precio_sub = round(precioan * (1 - (descuento / 100)), 6)
        tipo_precio = p["bandera"]

        if tipo_precio == "2" and not cotizacion:
            cur.execute("""
                SELECT IFNULL(cantprecio,0) AS cantprecio, IFNULL(aplicadas,0) AS aplicadas
                FROM INAREP01 WHERE TRIM(codigo)=%s LIMIT 1
            """, (codigo,))
            of = cur.fetchone()
            if of:
                disponibles = float(of["cantprecio"]) - float(of["aplicadas"])
                if cant > disponibles:
                    raise ErrorVenta(f"Producto {codigo} excede la oferta disponible. Disponibles: {disponibles}")
                cur.execute("UPDATE INAREP01 SET aplicadas = IFNULL(aplicadas,0) + %s WHERE TRIM(codigo)=%s",
                            (cant, codigo))

        if not cotizacion:
            cur.execute("""
                INSERT INTO INARMV01(
                    llavem, folio_mov, fecha, concepto, tipoes, numero_documento, fecha_documento,
                    clie_provee, tipocp, dispo1, dispo2, codigo_producto, cantidad, costo_unitario,
                    precio_venta, precio_lista1, factor, usuario, descripcion,
                    nombre_clieprovee, status, llavemm, bitacora
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                folio_maestro, str(n), fecha_turno, "0001 VENTA", "S", folio_maestro, fecha_turno,
                cliente, "C", "", "", codigo, cant, costo_ult,
                precioan, p["lista1"], "0", vendedor, p["descripcion"],
                nombre_cliente, "ACTIVO", folio_maestro, fecha_sql,
            ))

        detalles.append({
            "folio_mov": folio_mov, "fechat": fecha_turno, "caja": caja, "turno": num_turno,
            "cajero": cajero, "cliente": cliente, "vendedor": vendedor, "tipo_precio": tipo_precio,
            "obra": "000", "cantidadv": cant, "surtido": cant, "codigo": codigo, "cantidad": cant,
            "precio": precio_sub, "precioan": precioan, "precioan1": p["lista1"], "descl": 0,
            "pdesc": descuento, "ival": p["tasa"], "ivapesos": p["iva"], "cospro": costo_prom,
            "ultc": costo_ult, "predesc": 0.0, "preciox": p["precio_final"], "cortecaja": corte_caja,
            "pesp": 0, "flogi": fecha_turno, "tlogi": "", "hora": hora_actual, "bandera": 0,
            "codigoc": codigo, "peso": 0.0, "anticipo": 0, "notacred": 0, "logisticab": 0,
            "hora_logi": hora_actual, "progol": 0, "ordxx": 0, "boniefe": 0,
            "descripcion": p["descripcion"], "nombre_cliente": nombre_cliente, "fecha_mov": fecha_sql,
        })

    cur.executemany("""
        INSERT INTO VEARMO01(
            folio_mov, fechat, caja, turno, cajero, cliente, vendedor,
            tipo_precio, obra, cantidadv, surtido, codigo, cantidad, precio,
            precioan, precioan1, descl, pdesc, ival, ivapesos,
            cospro, ultc, predesc, preciox, cortecaja, pesp,
            flogi, tlogi, hora, bandera, codigoc, peso,
            anticipo, notacred, logisticab, hora_logi,
            progol, ordxx, boniefe, descripcion, nombre_cliente, fecha_mov
        ) VALUES (
            %(folio_mov)s, %(fechat)s, %(caja)s, %(turno)s, %(cajero)s, %(cliente)s, %(vendedor)s,
            %(tipo_precio)s, %(obra)s, %(cantidadv)s, %(surtido)s, %(codigo)s, %(cantidad)s, %(precio)s,
            %(precioan)s, %(precioan1)s, %(descl)s, %(pdesc)s, %(ival)s, %(ivapesos)s,
            %(cospro)s, %(ultc)s, %(predesc)s, %(preciox)s, %(cortecaja)s, %(pesp)s,
            %(flogi)s, %(tlogi)s, %(hora)s, %(bandera)s, %(codigoc)s, %(peso)s,
            %(anticipo)s, %(notacred)s, %(logisticab)s, %(hora_logi)s,
            %(progol)s, %(ordxx)s, %(boniefe)s, %(descripcion)s, %(nombre_cliente)s, %(fecha_mov)s
        )
    """, detalles)

    neto = cd["neto"]
    efectivo, tarjeta, transferencia = cd["efectivo"], cd["tarjeta"], cd["transferencia"]
    credito = neto
    if efectivo >= neto:
        abono, forma_pago = neto, "Efectivo"
    else:
        abono, forma_pago = efectivo, "Credito"
    if tarjeta == neto:
        abono, forma_pago = tarjeta, "Tarjeta"
    if transferencia == neto:
        abono, forma_pago = transferencia, "Transferencia"

    # Cuentas por cobrar
    if not cotizacion and st_credito == "1":
        documento = folio_maestro.strip()
        observaciones = f"{tipo_documento} A CREDITO"
        usuario = cajero or "0000"
        abono_ini = min(efectivo + tarjeta + transferencia, neto)
        saldo_pend = neto - abono_ini
        status_cxc = "1" if saldo_pend <= 0 else "0"
        cur.execute("SELECT id FROM CAARFA01 WHERE documento=%s LIMIT 1", (documento,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO CAARFA01 (
                    llave, cliente, documento, movimientos, cargos, abonos,
                    fecha_documento, fecha_vencimiento, status, referencia,
                    observaciones, usuario, forma_pago, bitacora
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (f"{cliente}{documento}", cliente, documento, 1 if abono_ini == 0 else 2,
                  neto, abono_ini, fecha_turno, fecha_venc, status_cxc, documento,
                  observaciones, usuario, forma_pago, ahora))
        cur.execute("SELECT COALESCE(MAX(movimientos),0) + 1 AS siguiente FROM CAARMO01 WHERE documento=%s",
                    (documento,))
        mov = int((cur.fetchone() or {}).get("siguiente") or 1)
        mov = max(mov, 1)
        sql_mo = """
            INSERT INTO CAARMO01 (
                llave, cliente, documento, movimientos, concepto, tmov,
                fecha_documento, fecha_vencimiento, importe, referencia,
                observaciones, usuario, forma_pago, bitacora
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        cur.execute(sql_mo, (f"{cliente}{documento}{mov:03d}", cliente, documento, mov, "001", "C",
                             fecha_turno, fecha_venc, neto, documento, observaciones, usuario,
                             forma_pago, ahora))
        if abono_ini > 0:
            mov += 1
            cur.execute(sql_mo, (f"{cliente}{documento}{mov:03d}", cliente, documento, mov, "002", "A",
                                 fecha_turno, fecha_venc, abono_ini, documento, "ABONO INICIAL",
                                 usuario, forma_pago, ahora))

    cur.execute("""
        INSERT INTO VEARMA01(
            folio, fechat, caja, turno, cajero, cliente, vendedor, cortecaja,
            obra, vandelog, formapago, importe, descuentos, subtotal, pdesc,
            descuentosp, subtotal2, impuestos, neto, efectivo, tarjeta, transferencia,
            refepago, rowlis, prods, credito, abono, diascred, vencimiento,
            banderax, observacionespago, hora, bandera, foliox, peso, anticipo,
            notacred, notacredps, costoprom, costoultimo, logisticab, tlogi, hora_logi,
            progol, ordxx, boniefe, grabado, exento, nombre_cliente, fecha_mov
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s,%s,%s,%s,%s
        )
    """, (
        folio_maestro, fecha_turno, caja, num_turno, cajero, cliente, vendedor, corte_caja,
        "0000", "0", forma_pago, cd["importe"], cd["descuentos"], cd["subtotal"], cd["pdesc"],
        cd["descuentosp"], cd["subtotal2"], cd["impuestos"], neto, efectivo, tarjeta, transferencia,
        cd["refepago"], len(partidas), f"{tot['cantidad']:,.3f}", credito, abono, 0, fecha_venc,
        "0", cd["observacionespago"], hora_actual, "0", folio_maestro, 0, 0,
        0, 0, tcostoprom, tcostoult, "0", "0", hora_actual,
        0, 0, 0, grabado, exento, nombre_cliente, fecha_sql,
    ))
    if not cotizacion:
        cur.execute("UPDATE CAARMA01 SET ultimacompra=%s WHERE codigo=%s", (fecha_sql, cliente))
    return folio_maestro, abono


def datos_ticket(folio, turno, partidas, cd):
    return {
        "empresa": datos_empresa(),
        "folio": folio,
        "documento": NOMBRE_DOC.get(folio[:1].upper(), "TICKET"),
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "caja": turno["caja"],
        "vendedor": session.get("vendedor", ""),
        "partidas": [{
            "descripcion": p["descripcion"], "unidad": p["unidad"],
            "cantidad": p["cantidad"], "precio": p["precio"], "total": p["total"],
        } for p in partidas],
        "total": cd["total"], "efectivo": cd["efectivo"],
        "cambio": round(cd["efectivo"] - cd["neto"], 2),
        "credito": cd["status_credito"] == "1",
        "cliente_credito": cd["cliente_credito"],
    }


if __name__ == "__main__":
    app.config["SESSION_COOKIE_SECURE"] = False
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
