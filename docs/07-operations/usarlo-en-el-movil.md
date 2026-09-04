<!--
title: ELYON QUANT — Usarlo en el móvil, paso a paso
id: OPS-004
owner: Platform Lead
status: draft
version: 0.1
-->

# Usarlo en el móvil, paso a paso

Sin arquitectura, sin decisiones de diseño: qué contratar, qué instalar, qué
escribir y en qué orden. Al final tienes la app en el móvil controlando un motor
que corre 24/7.

**Tiempo:** una tarde la primera vez. **Coste:** unos 15-30 €/mes de VPS.

> Empieza en **PAPER**. No es prudencia decorativa: recién instalado el motor no
> opera *a propósito* —ninguna estrategia está calibrada— y el camino entero de
> abajo funciona igual sin arriesgar nada. Cuando lo tengas andando, calibras, y
> solo entonces se plantea LIVE.

---

## Lo que vas a montar

```
   VPS Windows (24/7)                        Tu móvil
   ┌──────────────────────┐                 ┌──────────────┐
   │  Terminal MT5        │                 │  Navegador   │
   │  Motor ELYON         │◄─  Tailscale ──►│   (la app)   │
   │  elyon serve --login │                 │              │
   └──────────────────────┘                 └──────────────┘
```

**Por qué un VPS y no tu portátil:** el motor tiene que estar vivo mientras el
mercado se mueve. Un portátil que se suspende es un motor que se pierde la vela
que estaba esperando. (Tu propio PC vale si lo dejas encendido — ver el paso 1b.)

**Por qué Windows:** `MetaTrader5` maneja un terminal en ejecución por una API
de Windows. No hay build de Linux. Esto no se puede sortear.

---

## 1. El VPS

Cualquier VPS **Windows** con 2 vCPU y 4 GB vale de sobra. Contabo, Hetzner
(imagen de Windows), Vultr, Contabo, o un "Forex VPS" de los que se anuncian para
MT5 — esos ya vienen con el terminal.

Elige una región **cerca del servidor de tu broker**, no cerca de ti. Tú mandas
cuatro peticiones por minuto desde el móvil; el motor manda órdenes.

Conéctate por Escritorio Remoto (RDP). En el móvil, "Microsoft Remote Desktop"
vale para la instalación; después ya no lo necesitas.

### 1b. La alternativa sin VPS

Tu propio PC con Windows, encendido y sin suspenderse:

```
Configuración → Sistema → Inicio/apagado → Suspensión: Nunca
```

Funciona exactamente igual. Lo que pierdes es que un corte de luz o un reinicio
de Windows Update te para el motor sin avisar.

---

## 2. MetaTrader 5 y tu cuenta

1. Descarga MT5 desde tu broker (Exness: *Descargas → MetaTrader 5 para Windows*).
2. Entra con tu cuenta **demo** primero. Servidor, login y contraseña te los da
   el broker.
3. **Herramientas → Opciones → Asesores Expertos** → marca *Permitir trading
   algorítmico*. Sin esto todas las órdenes se rechazan.
4. Abre el gráfico del símbolo que vas a operar (EURUSD, XAUUSD…) y
   **desplázate hacia atrás** unas cuantas pantallas. MT5 solo descarga
   histórico de los gráficos que le has pedido mostrar, y sin eso el paso 5
   devuelve vacío.
5. Deja el terminal **abierto**. El motor habla con él, no con el broker.

> **El sufijo del símbolo.** Exness Standard y Cent añaden una letra: `EURUSDm`.
> Pro y Raw no. Mira el nombre exacto en la ventana *Observación de mercado* —
> si no coincide, todo lo demás devuelve «símbolo desconocido».

---

## 3. Python y ELYON

En el VPS, PowerShell:

```powershell
# Python 3.11 o superior desde python.org — marca "Add Python to PATH"
python --version

pip install MetaTrader5

git clone https://github.com/nexoracore24/ELYON-QUANT.git
cd ELYON-QUANT\services\platform-api
$env:PYTHONPATH = "src"
```

Comprueba que la máquina sirve:

```powershell
python -m elyon.cli doctor
```

Quieres ver `✓ os  Windows` y `✓ metatrader5  installed`. Si sale `✕` en algo,
eso es lo que hay que arreglar antes de seguir.

---

## 4. Un poco de histórico

El motor necesita velas para calentar: estructura, swings, ATR. Se las pides al
propio terminal:

```powershell
python -m elyon.cli bars --symbol EURUSD --timeframe M5 --count 1500 --out bars.csv
```

```
bars.csv: 1500 bars, 2026-08-27 14:35 UTC → 2026-09-04 21:30 UTC

  Symbol at the venue: EURUSD
  Server clock assumed: UTC+0
```

**Mira esos timestamps y compáralos con una sesión que conozcas.** Londres abre
a las 07:00 UTC en invierno. Si tus barras están tres horas corridas, tu broker
corre el servidor en UTC+3:

```powershell
python -m elyon.cli bars --symbol EURUSD --count 1500 --out bars.csv --server-offset 3
```

Esto importa más de lo que parece: **equivocarse no falla, mueve todas las
killzones** y el motor sigue pareciendo que funciona. Es el único paso de esta
guía donde un error no se ve.

Si tu cuenta lleva sufijo: `--suffix m`.

---

## 5. La configuración

```powershell
python -m elyon.cli config --symbol EURUSD > session.json
```

Sale en `PAPER` con la estrategia de la casa activa. Está bien así — lo demás lo
vas a tocar desde el móvil.

**Un ajuste que sí conviene mirar ahora:** si operas algo que no sea un par FX
estándar (oro, índices, cripto), el `valuePerPriceUnit` por defecto está mal y
eso dimensiona mal **todas** las operaciones. Para oro suele ser `100`. Mira el
tamaño de contrato en las especificaciones del símbolo en MT5. También se puede
cambiar desde la app.

---

## 6. Tu cuenta de acceso

```powershell
python -m elyon.cli useradd owner --role OWNER
```

Te pide la contraseña dos veces. Mínimo 12 caracteres. No hay login por defecto:
una credencial por defecto en algo que puede mandar órdenes no es una comodidad.

**Roles:** `OWNER` puede arrancar, configurar y cambiar el riesgo. `OPERATOR`
solo mira y **para**. Si vas a tener la app en un móvil que llevas por ahí y solo
quieres poder frenarlo, crea también un OPERATOR y usa ese en el móvil.

---

## 7. Tailscale: el móvil y el VPS en la misma red

Esta es la parte que la gente se salta y no debe.

1. Cuenta gratis en [tailscale.com](https://tailscale.com).
2. Instálalo **en el VPS** y entra.
3. Instálalo **en el móvil** (App Store / Play Store) y entra con la misma
   cuenta.
4. En la app de Tailscale del móvil verás el VPS con una IP tipo `100.x.y.z`.
   Apúntala.

Ya está. Sin abrir puertos, sin firewall, sin IP pública. Los dos dispositivos se
ven como si estuvieran en la misma red.

> **Lo que NO debes hacer:** poner `--host 0.0.0.0` y abrir el puerto en el
> router. **Este servidor no tiene TLS a propósito** — un TLS a medias parece
> terminado y es peor que ninguno. Un formulario de contraseña sobre HTTP plano
> expuesto a internet entrega la contraseña a quien esté escuchando. El túnel no
> es opcional.

---

## 8. Arrancar

```powershell
python -m elyon.cli serve --config session.json --data bars.csv `
    --live --login --journal orders.jsonl --host 0.0.0.0
```

`--host 0.0.0.0` aquí **sí** es correcto: dentro de Tailscale el servidor solo es
alcanzable desde tus propios dispositivos. Te saldrá un aviso; es el aviso
genérico, y en esta configuración estás bien.

```
ELYON QUANT control surface

  http://0.0.0.0:8787/

  Sign in with one of the 1 account(s) in operators.json:

  owner                OWNER     watch, stop, configure and start

  The engine is halted. An OWNER starts it from the app,
  after checking the settings.
```

**El motor arranca parado.** Es a propósito: vas a mirar los ajustes antes de
soltarlo.

---

## 9. El móvil

Abre el navegador y ve a:

```
http://100.x.y.z:8787
```

(la IP de Tailscale del VPS, del paso 7)

Entra con tu usuario y contraseña. Y luego, para que quede como una app de
verdad:

- **iPhone:** Compartir → *Añadir a pantalla de inicio*
- **Android:** menú ⋮ → *Añadir a pantalla de inicio*

Queda con su icono, a pantalla completa, sin barra del navegador.

### Lo que verás

| Pestaña | Para qué |
|---|---|
| **Estado** | Si opera, qué tiene abierto, y **en qué etapa se paró** cada vela |
| **Ajustes** | Riesgo, estrategias, gestión de posición — con lo que no se puede tocar ahora marcado y explicado |
| **Arrancar** | El preflight y el botón |

Y **Parar** siempre abajo, en todas las pestañas. Pide dos toques.

---

## 10. Que siga vivo cuando cierres el RDP

Si cierras la sesión de Escritorio Remoto, el proceso se muere con ella. La forma
sencilla en Windows es una tarea programada que arranque al iniciar sesión:

```powershell
$accion = New-ScheduledTaskAction -Execute "python" `
  -Argument "-m elyon.cli serve --config session.json --data bars.csv --live --login --host 0.0.0.0" `
  -WorkingDirectory "C:\ELYON-QUANT\services\platform-api"

$disparador = New-ScheduledTaskTrigger -AtLogOn

Register-ScheduledTask -TaskName "ELYON QUANT" -Action $accion `
  -Trigger $disparador -RunLevel Highest
```

Y en el VPS, configura el inicio de sesión automático para que MT5 y el motor
levanten solos después de un reinicio de Windows Update.

**Nota honesta:** una tarea programada reinicia el proceso, no recupera la
sesión. El motor vuelve *parado*, relee la configuración del fichero y espera a
que lo arranques. Es deliberado — que un motor vuelva solo a operar después de
un reinicio que no viste es peor que uno que espera.

---

## 11. El primer día

Recién montado **no va a operar**, y eso es correcto:

```
✕ calibration  every live strategy is uncalibrated (SIX_PILLARS); none of
               them can open a trade alone, so this session will take no trades
```

Una estrategia se gana el derecho a operar midiéndola:

```powershell
python -m elyon.cli bars --symbol EURUSD --timeframe M5 --count 20000 --out historico.csv
python -m elyon.cli calibrate --data historico.csv --strategy SIX_PILLARS --sample OUT_OF_SAMPLE
```

Si la muestra da menos de 30 operaciones te lo dice en esos términos y no
certifica nada. Cuando sí certifica, imprime un bloque que copias a
`session.json`, reinicias, y ya tiene tier.

Mientras tanto, la pestaña **Estado** te dice en qué etapa se para cada vela.
«No opera» no es una respuesta: son ocho, y ahí ves cuál.

---

## Cuando algo no va

| Síntoma | Casi siempre es |
|---|---|
| `the terminal does not know 'EURUSD'` | Sufijo de cuenta. Prueba `--suffix m` |
| `bars` devuelve vacío | Abre el gráfico en MT5 y desplázate atrás; solo descarga lo que le pides mostrar |
| `Feed STALLED` en fin de semana | Correcto. El mercado está cerrado |
| `Feed DISCONNECTED` | El terminal se cerró o perdió sesión. El motor se paró solo y **no cerró nada** |
| No carga desde el móvil | Tailscale caído en uno de los dos. Comprueba que ambos aparecen conectados |
| `session expired; sign in again` | Normal: 12h absolutas, 30min de inactividad |
| Todas las killzones parecen raras | El `--server-offset` del paso 4 |
| Órdenes rechazadas en LIVE | Trading algorítmico deshabilitado en el terminal (paso 2.3) |

Y antes de nada, siempre:

```powershell
python -m elyon.cli doctor
```

---

📄 [Dónde corre cada cosa](deployment.md) ·
📄 [Control desde el móvil, en detalle](mobile-control.md) ·
📄 [Conectar Exness](connecting-exness.md)
