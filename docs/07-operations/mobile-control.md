<!--
title: ELYON QUANT — Control desde el móvil
id: OPS-002
owner: Platform Lead
status: draft
version: 0.1
-->

# Control desde el móvil

## 1. El bot no corre en el móvil

Conviene decirlo antes de nada porque cambia todo lo demás. El paquete
`MetaTrader5` es **solo Windows** y necesita el terminal abierto y logueado. No
hay forma de que el motor viva en un iPhone o un Android.

La arquitectura real es:

```
   VPS Windows (24/7)                        Tu móvil
   ┌──────────────────────┐                 ┌──────────────┐
   │  Terminal MT5        │                 │  Navegador   │
   │  Motor ELYON         │◄────  VPN  ────►│  (la página) │
   │  elyon serve :8787   │                 │              │
   └──────────────────────┘                 └──────────────┘
        ejecuta                                  observa
                                                 y para
```

El móvil es un **mando a distancia**, no un host.

---

## 2. Parar es seguro. Arrancar no.

Esta asimetría es la que define todo el diseño de permisos, y merece
entenderla.

Exponer el motor a tu móvil es exponerlo a **quien tenga el token**. Un móvil
robado, un token filtrado, un QR mirado por encima del hombro. Cualquiera de
esos debería poder **parar** el bot y cerrar posiciones — eso es molesto, como
mucho. Ninguno debería poder reanudar, subir el riesgo, cambiar a LIVE o activar
una estrategia. Esas son las acciones que pierden dinero.

Por eso las capacidades se gradúan por **hacia dónde mueven el riesgo**, no por
lo «administrativas» que suenen:

| Capacidad | Qué permite | ¿La tiene el móvil? |
|---|---|---|
| `OBSERVE` | Mirar. No cambia nada | ✅ |
| `PROTECT` | Parar, aplanar. Solo **reduce** exposición | ✅ |
| `COMMAND` | Reanudar, reconfigurar. Solo **aumenta** | ❌ |

`COMMAND` no es un flag del token del móvil: es una **función distinta**
(`command_token()`), para que concederlo sea algo que alguien escribió, no algo
en lo que se cayó por defecto.

Y no está solo rechazado: **el hook de reanudar ni siquiera se cablea** salvo que
arranques con `--allow-command`.

---

## 3. Arrancar el servidor

En el VPS, junto al motor:

```bash
elyon serve --config session.json --data bars.csv --journal orders.jsonl
```

```
ELYON QUANT control surface

  http://127.0.0.1:8787/

  Paste this token into the page. It is printed once:

    Xk3n_9pQ7wZ2mR8vT4yL...

  The phone can watch and can stop. It cannot resume, cannot
  change risk, and cannot enable a strategy -- those stay here.
```

El token se imprime **una vez**. Cópialo al móvil por un canal que no sea un
chat que quede archivado.

---

## 4. Llegar desde el móvil

Por defecto escucha en `127.0.0.1`: **no es accesible desde fuera de la
máquina**, y es a propósito. Un endpoint que puede aplanar posiciones no se
publica en internet por descuido.

**Recomendado: Tailscale.** Instálalo en el VPS y en el móvil, ambos quedan en
la misma red privada, y entras por la IP de Tailscale sin abrir un solo puerto.

**Alternativa: túnel SSH** desde el móvil (Termius, Blink):

```bash
ssh -L 8787:127.0.0.1:8787 usuario@tu-vps
```

**Lo que no debes hacer:** `--host 0.0.0.0` sin nada delante. El servidor te
avisa si lo intentas:

> ⚠ binding to 0.0.0.0 exposes a control endpoint beyond this machine, over
> plain HTTP. Anyone who reaches it and holds a token can stop your bot and
> close your positions.

**No hay TLS en este servidor, y es deliberado.** Escribir tu propio TLS es peor
que no tenerlo, porque un TLS a medias parece terminado. Ponlo detrás de un
túnel o de un proxy que mantenga otro.

---

## 5. Qué ves en el móvil

La página sigue el orden en el que una persona realmente pregunta las cosas:

1. **¿Está corriendo, y tiene algo abierto?** Lo primero que busca cualquiera
   es si hay dinero en riesgo ahora mismo.
2. **¿Por qué no está operando?** Casi siempre la pregunta de verdad. El
   desglose por etapa la responde sin que nadie tenga que adivinar.
3. **Parar.** Un toque, siempre visible, nunca detrás de un menú.

Si hay posición abierta, el número que importa es **`lockedR`**: si es positivo,
el stop ya pasó de la entrada y la operación **ya no puede perder**.

**No hay botón de arrancar.** Reanudar necesita `COMMAND`, el móvil no lo tiene,
y pintar un control que solo devolvería 403 enseña a la gente a ignorar errores.

El botón de parar pide **dos toques** — un bolsillo está lleno de toques
accidentales — y el armado caduca a los 4 segundos, para que un móvil olvidado
en una mesa no se quede a un toque de parar la cuenta.

---

## 6. Qué NO viaja al móvil

El snapshot que se envía **no tiene un campo** para credenciales, número de
cuenta, nombre de servidor ni rutas de fichero. No es que se filtren y se
oculten: no existen en la forma del objeto, y hay un test que lo comprueba.

Consecuencia práctica: **una captura de esa pantalla en un chat no es un
incidente.** Todo lo que se ve es datos públicos de mercado o el estado
agregado de tu propia cuenta.

El token se guarda en `localStorage` del navegador y no sale de ahí más que
hacia tu propio motor.

---

## 7. Límite actual

`elyon serve` corre la sesión **sobre un fichero de barras** y luego sirve el
resultado. Para operar en vivo falta el feed de datos en tiempo real: el bucle
de la sesión ya consume ticks (`session.on_tick`), pero todavía no hay nada que
se los dé desde MT5.

Es decir: hoy el móvil sirve para **observar y parar** una sesión sobre datos
históricos, y toda la seguridad ya está construida y probada. Cuando exista el
feed en vivo, el mismo servidor sirve sin tocar nada.
