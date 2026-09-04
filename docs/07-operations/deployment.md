<!--
title: ELYON QUANT — Dónde corre cada cosa
id: OPS-003
owner: Platform Lead
status: draft
version: 0.1
-->

# Dónde corre cada cosa

## 0. La respuesta corta

```bash
elyon doctor
```

Te dice si **esta** máquina puede correr el motor, y qué puede hacer si no.

---

## 1. Vercel, Netlify, Lambda y compañía no pueden correr el motor

No es que sea difícil o que falte configurarlo. Es estructural, y conviene
decirlo entero porque el despliegue va a salir en verde igualmente.

### El motor es un proceso vivo, no una función

Una función serverless nace con una petición y muere con la respuesta. El motor
necesita justo lo contrario:

| Necesita | Qué pasa en una función |
|---|---|
| Acumular velas (40 de calentamiento, 120 de lookback) | Se pierden entre invocaciones |
| Mantener una posición abierta y gestionarla vela a vela | No hay nadie vivo entre velas |
| Sondear el feed cada 250 ms | Nadie está pidiendo nada; nadie sondea |
| Un presupuesto de riesgo con reservas pendientes | Desaparece |
| Sesiones de login en memoria | Cada instancia tiene las suyas: entras y te echan al azar |

### Escalar horizontalmente es el bug más caro que puede tener

Estas plataformas levantan instancias en paralelo automáticamente, y lo hacen
bien. Dos instancias de este motor sobre la misma cuenta **es exactamente la
posición duplicada** que el OMS entero existe para impedir ([ADR-0011]).
Duplica el riesgo en silencio: no salta ninguna alarma, la cuenta se ve bien, y
el problema aparece cuando el mercado se mueve en contra al doble de velocidad
de la esperada.

### MetaTrader5 es solo Windows

Vercel corre Linux. El paquete `MetaTrader5` maneja un **terminal en ejecución**
a través de una API de Windows: no hay build de Linux, no hay build de macOS, y
no hay una versión de esto que funcione. Si sigues ese camino, el mensaje ahora
te lo dice por su nombre en vez de mandarte a instalar un paquete que no existe:

> MetaTrader5 is Windows-only and this host is Linux. There is no Linux or
> macOS build — the package drives a running MT5 terminal through a Windows API.

### El disco es de solo lectura, salvo `/tmp`, que se borra

Tres cosas se convierten en no-ops silenciosos:

- **El log de órdenes.** Es lo que recupera un proceso que murió a mitad de un
  envío. En `/tmp` está ahí hasta el momento exacto en que hace falta.
- **`operators.json`.** Tus cuentas.
- **Los ajustes que cambias desde la app.** El motor ya avisa cuando no puede
  guardar (`APPLIED BUT NOT SAVED`), pero enterarse al arrancar es mejor que
  enterarse después de haber cambiado algo.

---

## 2. Para qué **sí** sirve Vercel

Nada de esto es tirar el despliegue: es ponerlo donde suma.

| Pieza | Dónde | Por qué |
|---|---|---|
| **Landing / marketing** | Vercel | Es exactamente su trabajo |
| **Docs públicas** | Vercel | Estáticas, cacheables, sin estado |
| **Panel multi-cuenta, facturación** (futuro) | Vercel | Un control plane sí encaja en serverless: peticiones cortas contra una base de datos |
| **El motor** | VPS Windows 24/7 | Estado, hilos, MT5, y **una sola instancia** |
| **La app de control** | La sirve el propio motor | A propósito: sin CDN, sin build, sin nada que se caiga aparte |

La página del móvil **se sirve desde el motor**, no desde un CDN, y eso es una
decisión: un VPS con firewall no tiene por qué poder alcanzar internet, y la
monitorización de un sistema de trading no debería dejar de funcionar porque un
índice de paquetes tenga un mal día. Desplegarla en Vercel no la mejora — la
separa del único proceso que tiene los datos.

---

## 3. El despliegue que sí funciona

```
   VPS Windows (24/7)                        Tu móvil
   ┌──────────────────────┐                 ┌──────────────┐
   │  Terminal MT5        │                 │  Navegador   │
   │  Motor ELYON         │◄─  Tailscale ──►│   (la app)   │
   │  elyon serve --login │                 │              │
   └──────────────────────┘                 └──────────────┘
     una sola instancia                    mira, ajusta,
     disco durable                        arranca y para
```

**Qué necesita el VPS:** Windows Server o Windows 10/11, el terminal MT5 abierto
y logueado con trading algorítmico habilitado, Python 3.11+, disco durable, y
que no se reinicie solo. Dos vCPU y 4 GB sobran.

**Puesta en marcha:**

```bash
elyon doctor                              # ¿puede esta máquina?
elyon useradd owner --role OWNER          # una vez
elyon serve --config session.json --data bars.csv --login --live \
     --journal orders.jsonl
```

**Llegar desde el móvil:** Tailscale en el VPS y en el móvil, o un túnel SSH.
Nunca `--host 0.0.0.0` a pelo — **no hay TLS en este servidor a propósito**, y
con login eso importa más, no menos: un formulario de contraseña sobre HTTP
plano expuesto a internet entrega la contraseña.

📄 [Guía completa: control desde el móvil](mobile-control.md) ·
📄 [Conectar Exness](connecting-exness.md)

---

## 4. Y una máquina Linux, ¿para qué sirve?

Para bastante, de hecho. Todo menos tocar un broker por MT5:

```bash
make run CONFIG=session.json DATA=bars.csv        # sesiones completas
make calibrate DATA=bars.csv STRATEGY=SIX_PILLARS # ganar un tier
make strategies                                   # el catálogo
elyon serve --config c.json --data bars.csv --login  # la app, en PAPER
```

El reparto habitual: **Windows VPS para operar, tu portátil para investigar.**
La misma configuración, el mismo motor, los mismos resultados bit a bit — eso es
lo que compra el determinismo de [ADR-0006].

---

## 5. Qué comprueba `elyon doctor`

| Comprobación | Bloquea | Qué está mirando |
|---|---|---|
| `runtime` | ✕ | Si estás dentro de algo serverless (Vercel, Lambda, Cloud Run, Azure, Netlify, Cloudflare, Deno) |
| `python` | ✕ | 3.11 o superior |
| `timezones` | ✕ | La base IANA. Las killzones están definidas en hora de Nueva York, y una imagen mínima suele venir sin `tzdata` |
| `filesystem` | ✕ | Que se pueda escribir donde va el log de órdenes |
| `os` | ! | Windows o no — decide si MT5 es alcanzable |
| `metatrader5` | ! | Si el paquete está |
| `durability` | ! | Escribible **no** es lo mismo que duradero: un journal en `/tmp` está ahí hasta que hace falta |
| `disk` | ! | Espacio |

`✕` bloquea, `!` es algo que saber. La diferencia es la misma pregunta que en el
preflight de arrancar: *¿esto sería un error, o solo algo que conviene saber?*

El comando devuelve código de salida 1 si hay bloqueantes, así que sirve en un
script de arranque.

[ADR-0006]: ../adr/0006-deterministic-computing.md
[ADR-0011]: ../adr/0011-oms-duplicate-prevention.md
