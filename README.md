# SOADIN · Punto de venta para tablet

Versión web (Flask) del punto de venta de escritorio `pepo_pventaok.py`. Se abre en el
navegador de cualquier tablet o celular y trabaja contra la **misma base MySQL** de SOADIN,
con la misma lógica de precios, redondeo, folios, inventario y cartera.

## Qué hace

- **Acceso:** vendedor y contraseña (`VEARAG01`); solo entra si hay día abierto (`VEARDI01`) y la caja tiene turno abierto (`VEARTN01`). La tablet recuerda su caja.
- **Captura:** por código, por `3*CODIGO` o con lector de código de barras. Respeta varios precios, precio especial por cantidad (`INAREP01`), precio modificable y productos que no se fraccionan.
- **Catálogo de productos:** busca por descripción, inicio, código, número de parte, código de proveedor y presentación. También muestra equivalentes.
- **Partida:** cambia la descripción, la unidad y la clave/unidad SAT solo en esa venta. El catálogo no se toca.
- **Cliente:** búsqueda, alta rápida, saldo, vencido, disponible y cartera con movimientos.
- **Cobro:** efectivo, tarjeta, transferencia y crédito, con las mismas reglas de bloqueo que en escritorio.
- **Documentos:** Nota de venta, Remisión, Factura y Cotización, con sus folios de `VEARFO01`.
- **Ticket:** de 80 mm con el mismo formato que en escritorio, impreso desde el navegador.
- **Respaldo de captura:** si la tablet se apaga o se recarga, la venta en captura se recupera.

El servidor **recalcula y valida todo al grabar** (precios, existencias, crédito), así que lo que mande el navegador no puede alterar importes.

## Archivos

| Archivo | Para qué |
|---|---|
| `pepo_tablet.py` | Servidor Flask: rutas, cálculos y grabado en MySQL |
| `templates/` | Pantallas (login y venta) |
| `static/` | Estilos y lógica de pantalla |
| `requirements.txt`, `Procfile`, `.python-version` | Lo que necesita Railway |
| `.env.example` | Variables de configuración |

## Probar en tu PC

```bash
python -m venv .venv
.venv\Scripts\activate          # en Windows  (en Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           # llena tus datos y pon COOKIE_SEGURA=0
python pepo_tablet.py
```

Abre `http://IP-DE-TU-PC:5000` desde la tablet (misma red Wi-Fi).

## Subir a GitHub

```bash
git init
git add .
git commit -m "Punto de venta tablet"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/pepo-tablet.git
git push -u origin main
```

El `.env` con contraseñas **no se sube** (está en `.gitignore`).

## Publicar en Railway

1. En Railway: **New Project → Deploy from GitHub repo** y elige el repo.
2. En **Variables** captura las de `.env.example` (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `SECRET_KEY`, `EMPRESA_*`).
3. En **Settings → Networking → Generate Domain** para obtener la URL pública.
4. Revisa `https://TU-URL/salud`: debe responder `"bd": true`.

### ⚠️ La base de datos tiene que ser alcanzable desde internet

Railway corre en la nube. Si tu MySQL está en una PC de la tienda, Railway **no la ve**. Opciones:

- **MySQL en la nube** (Railway MySQL, PlanetScale, un VPS, etc.) y que el escritorio también apunte ahí.
- **Abrir el MySQL de la tienda** a internet con IP fija, puerto abierto solo a Railway, usuario con permisos mínimos y conexión cifrada.
- **No usar Railway** y correr esta app en una PC de la tienda; las tablets entran por la red local.

## Pendientes conocidos

- **Permiso para cambiar descripción/unidad:** la función `vendedor_puede_cambiar_descripcion()` deja a todos, igual que en escritorio. Falta definir de dónde sale el permiso.
- **Foto y ficha técnica:** no se incluyeron porque en escritorio se leen de `c:/py/soadin/imagenes`. Para la web tendrían que estar en una URL o en almacenamiento en la nube.
- **Clave y unidad SAT por partida:** se ven y se editan, pero igual que en escritorio no se graban en `VEARMO01`.
