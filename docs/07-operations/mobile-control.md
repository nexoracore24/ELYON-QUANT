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
   │  elyon serve :8787   │                 │   (la app)   │
   └──────────────────────┘                 └──────────────┘
        ejecuta                              mira, ajusta,
                                            arranca y para
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

| Capacidad | Qué permite | Rol que la trae |
|---|---|---|
| `OBSERVE` | Mirar. No cambia nada | `VIEWER` |
| `PROTECT` | Parar. Solo **reduce** exposición | `OPERATOR` |
| `COMMAND` | Arrancar, reconfigurar. Solo **aumenta** | `OWNER` |

**El login no ablanda esta asimetría, le pone nombre.** Un `OPERATOR` para el
motor a las 3am desde el móvil; solo un `OWNER` lo arranca, sube el riesgo o lo
pone en LIVE.

Y a quien no puede arrancar **no se le pinta el botón**, no se le rechaza: un
control que siempre devuelve 403 enseña a la gente a ignorar errores.

---

## 3. Arrancar el servidor

Primero, comprueba que la máquina puede:

```bash
elyon doctor
```

Si estás en algo serverless (Vercel, Lambda, Cloud Run…) te lo dice y para ahí:
el motor es un proceso vivo y esas plataformas no los tienen. Ver
[dónde corre cada cosa](deployment.md).

Después, la cuenta. Una vez, **en la máquina**:

```bash
elyon useradd owner --role OWNER
```

No hay login por defecto. Una credencial por defecto en algo que puede mandar
órdenes no es una comodidad, es una donación.

Después, el servidor:

```bash
elyon serve --config session.json --data bars.csv --login --journal orders.jsonl
```

```
ELYON QUANT control surface

  http://127.0.0.1:8787/

  Sign in with one of the 1 account(s) in operators.json:

  owner                OWNER     watch, stop, configure and start

  The engine is halted. An OWNER starts it from the app,
  after checking the settings.
```

El motor arranca **parado**. «Arrancar» tiene que ser una acción de verdad o es
decoración — y una app cuya primera pantalla muestra el motor ya operando no le
da a nadie la oportunidad de revisar los ajustes antes.

`elyon users` lista las cuentas, `elyon passwd <nombre>` cambia una contraseña,
`elyon userdel <nombre>` la quita. El fichero `operators.json` se escribe con
permisos `0600` y guarda **hashes**, nunca contraseñas: está en `.gitignore` de
todas formas, porque publicarlo le regala a cualquiera una sesión de guessing
offline contra cuentas que pueden mandar órdenes.

Si prefieres el token impreso de antes —para un panel de solo lectura, por
ejemplo— quita `--login` y sigue funcionando igual.

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

Con login esto importa **más**, no menos: un formulario de contraseña sobre HTTP
plano expuesto a internet entrega la contraseña a quien esté escuchando. El túnel
no es opcional.

---

## 5. Qué ves en el móvil

La app tiene tres pestañas — **Estado**, **Ajustes**, **Arrancar** — y el orden
sigue el que una persona realmente pregunta las cosas:

1. **¿Está corriendo, y tiene algo abierto?** Lo primero que busca cualquiera
   es si hay dinero en riesgo ahora mismo.
2. **¿Por qué no está operando?** Casi siempre la pregunta de verdad. El
   desglose por etapa la responde sin que nadie tenga que adivinar.
3. **Parar.** Un toque, siempre visible, **nunca detrás de una pestaña.**

Si hay posición abierta, el número que importa es **`lockedR`**: si es positivo,
el stop ya pasó de la entrada y la operación **ya no puede perder**.

El botón de parar pide **dos toques** — un bolsillo está lleno de toques
accidentales — y el armado caduca a los 4 segundos, para que un móvil olvidado
en una mesa no se quede a un toque de parar la cuenta.

---

## 6. Ajustes: qué puedes cambiar, y cuándo

Un fichero de configuración se edita con todo parado, así que todos los ajustes
son igual de seguros. **Un motor vivo no es así.** Cada ajuste declara un
alcance, y la app marca cada uno:

| Alcance | Cuándo | Por qué |
|---|---|---|
| `LIVE` | En la siguiente vela | Solo afecta a lo que venga después |
| `FLAT_ONLY` | Solo sin posición abierta | Cambia lo que un número *significa* |
| `RESTART` | Sesión nueva | El histórico acumulado pertenece al valor viejo |

El caso que lo justifica: si una posición se dimensionó contra 10.000 € y un
0,5% de riesgo, **editar el equity a 50.000 € con la posición abierta no la
redimensiona.** Solo convierte en mentira cada número que se reporta sobre ella.
Eso no es reconfigurar, es reescribir el significado de una operación ya puesta.

Igual con el símbolo o el timeframe: el constructor de velas está cortado para
uno y el ATR es un valor corriente sobre una ventana fija. Cambiarlos en caliente
no da la configuración nueva, da **un híbrido que nunca existió**.

Lo que la app hace con eso:

- Un ajuste que no se puede tocar **sale bloqueado con el motivo escrito**, no
  desaparece ni falla al pulsarlo. El servidor manda el motivo; la página no lo
  deduce, así que un rechazo nunca llega por sorpresa.
- Las ediciones se **aplican juntas**. Una configuración es válida entera o no lo
  es, y no existe un instante en que la fracción de riesgo entró y la lista de
  estrategias no.
- Cada cambio queda **con un nombre encima**: quién, qué, de qué valor a cuál.
- Y se **escribe de vuelta al fichero** con el que arrancaste, para que sobreviva
  a un reinicio.

### Lo que cambias sobrevive al reinicio

Un ajuste cambiado desde el móvil que desaparece en el siguiente arranque es
**peor que uno rechazado**: el motor vuelve pareciendo correcto y dimensionado
contra otra cosa.

Si la escritura falla, se dice en la misma frase que el éxito, y la app lo pinta
en ámbar, no en verde:

```
1 setting(s) applied. APPLIED BUT NOT SAVED (read-only file system).
It is live now and will be gone after a restart.
```

Los cambios se apilan además en `session.changes.jsonl` — append-only, una línea
por cambio, mismo formato que el log de órdenes, porque la pregunta que se hace
de verdad está ordenada: *¿cómo estaba configurado cuando pasó esa operación?*

```json
{"at":"2026-09-04T23:37:22Z","who":"owner","key":"riskPerTrade",
 "before":"0.005","after":"0.0125"}
```

Con `--no-save` nada se escribe y cada edición dura hasta el siguiente reinicio.
Con `--changelog otra/ruta.jsonl` la bitácora va donde quieras.

### Pasar a LIVE se escribe

```
Switching to LIVE sends orders to a real broker.
Type TRADE REAL MONEY to confirm.
```

Cualquier otro ajuste se deshace volviéndolo a poner. Este no: una orden que
llegó a un broker real no se deshace cambiando el modo después. **Salir** de
LIVE, en cambio, no pide nada — todas las salvaguardas apuntan a que reducir
riesgo sea fácil.

---

## 7. Arrancar

Antes del botón, el preflight:

```
✓ broker      PaperBroker
✕ calibration every live strategy is uncalibrated (SIX_PILLARS); none of them
              can open a trade alone, so this session will take no trades
! feed        feed LIVE
! calendar    no economic calendar; the context score cannot exceed 92/100
```

Pulsar Start en un motor que no puede operar produce **un bot que parece sano y
no hace nada** — el fallo con el que más tiempo se pierde.

La frontera entre ✕ bloqueante y ! aviso es una pregunta: *¿arrancar sería un
error, o solo silencio?* LIVE apuntando a un broker de papel es un error. PAPER
sin nada calibrado es silencio, y el silencio es exactamente cómo una estrategia
se gana un tier.

Hay un `force`, porque una comprobación puede equivocarse y un motor que no se
puede arrancar es peor que uno que avisa. **Queda registrado cuando se usa**: «lo
forzamos» es la primera pregunta después de un mal día.

---

## 8. Qué NO viaja al móvil

El snapshot que se envía **no tiene un campo** para credenciales, número de
cuenta, nombre de servidor ni rutas de fichero. No es que se filtren y se
oculten: no existen en la forma del objeto, y hay un test que lo comprueba.

Consecuencia práctica: **una captura de esa pantalla en un chat no es un
incidente.** Todo lo que se ve es datos públicos de mercado o el estado
agregado de tu propia cuenta.

La contraseña **no se guarda en ningún sitio**: se cambia una vez por un token
de sesión, y el campo se vacía en cuanto se ha usado. Lo que queda en
`localStorage` es esa sesión, que caduca sola y no sale de ahí más que hacia tu
propio motor.

---

## 9. En vivo

```bash
elyon serve --config session.json --data bars.csv --live
```

Sin `--live`, la sesión corre sobre el fichero de barras y luego sirve el
resultado — útil para revisar lo que pasó. Con `--live`, las barras son el
**calentamiento** y el motor sigue consumiendo ticks del terminal MT5.

La página pone la salud del feed **arriba del todo**, antes que ningún número:

```
Feed STALLED · 94s silent
no tick for 94s. The market may be closed, or the connection may be gone
```

Un feed parado con una posición abierta es el peor estado del que no enterarse,
y un precio viejo no se ve viejo. Por eso se dice, no se deja inferir.

Si el feed se cae del todo, el motor **para solo** (`DISCONNECTED` + halt) y
**no cierra nada**: no abrir riesgo nuevo a ciegas es prudente, cerrar a ciegas
durante un corte es operar en el peor momento posible. En la página lo verás
como `Halted` con el motivo `market data feed lost: …`.

Volver a arrancar después de un corte sigue necesitando `COMMAND`: es una
decisión, no una recuperación automática. La diferencia con antes es que ahora
esa decisión la puede tomar un `OWNER` desde el móvil, después de mirar el
preflight — no que el motor se reanude solo.

📄 Detalle de las decisiones: [ADR-0012](../adr/0012-live-market-data-feed.md).

---

## 10. Sesiones: lo que caduca y lo que se revoca

Entrar **cambia** la contraseña por un token de sesión, y es el token el que
viaja después. La contraseña cruza la red una vez por sesión en vez de cada cinco
segundos.

| | |
|---|---|
| Caducidad absoluta | 12 horas |
| Inactividad | 30 minutos |
| Cerrar sesión | Acaba solo esa sesión |
| Reiniciar el motor | Acaba todas |

**Una contraseña no se puede retirar; una sesión sí.** Es la razón de que exista
el intercambio: si pierdes el móvil, reiniciar el motor invalida lo que hubiera
en él sin que tengas que cambiar nada que te sepas de memoria.

Adivinar cuesta: 5 intentos fallidos por IP y el bloqueo se dobla con cada uno
más. El contador **por cuenta** es a propósito mucho más generoso (50), porque un
bloqueo por cuenta apretado es una denegación de servicio que cualquiera puede
apuntarte: fallan tu login cinco veces y no llegas a tu propio motor con una
posición abierta.

Y usuario incorrecto y contraseña incorrecta dan **el mismo mensaje y tardan lo
mismo**. Si no, el formulario es un enumerador de cuentas.

📄 Detalle de las decisiones:
[ADR-0013](../adr/0013-login-and-remote-configuration.md).

---

## 11. Límite actual

Un símbolo por proceso. Varios instrumentos a la vez —lo que SMT Divergence
necesita— piden un feed multi-símbolo que todavía no existe. Y un reinicio
recalienta desde el fichero de barras, no desde donde se quedó el stream.

Del lado del login: **sin 2FA**, y **sin recuperación de contraseña** — esto
último a propósito, porque un flujo de recuperación es una segunda puerta, y en
un sistema de un solo dueño la puerta es el acceso a la máquina. Si te quedas
fuera, `elyon passwd` en el VPS. Las sesiones viven en memoria, así que reiniciar
el motor echa a todo el mundo; es aceptable en un proceso único y evita persistir
tokens en disco.
