# ELYON QUANT

**Plataforma profesional de trading algorítmico.** No es un bot de MT5: es un
ecosistema completo para diseñar, validar, ejecutar, monitorizar y monetizar
estrategias cuantitativas sobre múltiples brokers y exchanges, con estándares
de ingeniería de nivel Stripe / Palantir / Google / OpenAI.

> Estado del proyecto: **Núcleo en construcción.**
> La arquitectura está congelada (`v1.0-rc1`) y el primer código del motor ya
> corre: datos de mercado, detectores Smart Money, la estrategia de los seis
> pilares, el catálogo ICT combinable, el gate de contexto con Market DNA,
> backtesting, riesgo, scoring, gestión de posición, el OMS y una sesión
> ejecutable con log persistente, con **760 tests** verdes.
>
> ```bash
> make test          # suite completa
> make demo          # el pipeline decidiendo, y explicándose
> make strategies    # el catálogo y sus tiers
> ```
>
> 📋 [Plan Maestro de Documentación](docs/00-governance/documentation-master-plan.md) ·
> 🧊 [Core Architecture Review v1.0](docs/architecture/core-architecture-review-v1.0.md) ·
> 🔒 [Core Contracts v1.0](docs/06-api/core-contracts-v1.0.md)

---

## Empezar a usarlo

```bash
make install                        # pytest, nada más
make test                           # 760 tests
make demo                           # el pipeline entero, explicándose
```

**1. Mira qué hay.**

```bash
make strategies      # las 13 estrategias, sus familias y sus tiers
make dna             # los 7 perfiles de instrumento
```

**2. Crea una configuración.**

```bash
make config SYMBOL=EURUSD > session.json
```

Sale con `mode: PAPER`, la estrategia de la casa en vivo y el resto en shadow.
`LIVE` hay que escribirlo a mano: un sistema donde el modo peligroso es el que
te toca por no elegir acaba operando dinero real por accidente.

**3. Consigue barras.** CSV con cabecera `time,open,high,low,close[,volume]`.
El tiempo admite epoch en segundos, milisegundos o nanosegundos, o ISO. Los
precios se leen como **string** y se convierten con `dec` — nunca por float,
porque `1.10005` pasado por un float vuelve como `1.1000499999999999` y dos
ejecuciones sobre el mismo fichero dejan de coincidir.

**4. Ejecútalo.**

```bash
make run CONFIG=session.json DATA=bars.csv FLAGS=--learn-dna
```

```
⚠ 1 live strategy(ies) have no calibration (SIX_PILLARS); they cannot open
  a trade alone, so this session will take no trades until they are measured

EURUSD · PAPER · 600 bars
  entries taken   0
  where the pipeline stopped:
    context          480
    playbook          80
    warmup            40
```

**Sí: recién instalado no opera.** Todo está en ⚪ y el gate lo rechaza. Es
deliberado, y el aviso lo dice antes de que te preguntes por qué.

Lo útil de esa tabla es que te dice **en qué etapa se paró** en cada vela. «No
hay trade» no es una respuesta, son ocho, y un sistema que no las distingue no
se puede depurar.

**5. Calibra para desbloquearlo.**

```bash
make calibrate DATA=bars.csv STRATEGY=SIX_PILLARS SAMPLE=OUT_OF_SAMPLE
```

Si la muestra da menos de 30 operaciones, te lo dice en esos términos: *«This
changes nothing: 12 trades is below the 30 needed»*. Nada de «certificado»
junto a un ⚪ — eso se lee como luz verde para algo que no va a hacer nada.

Cuando sí certifica, imprime un bloque que va tal cual a `session.json`:

```json
"calibrations": [
  {"strategy": "SIX_PILLARS", "sampleSize": 180, "wins": 92,
   "expectancyR": "0.42", "dataset": "eurusd-2024-h1"}
]
```

Fíjate en que el fichero da la **muestra**, no el tier. Un config no puede
reclamar un tier que no midió: las mismas reglas lo derivan en todas partes, así
que 180 operaciones con 90% de aciertos y expectancy −0.30 siguen dando 🔴 LOW
por mucho que quien escribió el fichero pensara otra cosa.

**6. Añade el calendario económico.** Sin él, `NEWS_CLEAR` se retiene y el
contexto no puede pasar de 92/100 — el hueco de datos queda visible en vez de
valer ocho puntos gratis.

```csv
time,currency,impact,title
2026-01-15T13:30:00+00:00,USD,HIGH,CPI
```

```json
"calendar": "calendar.csv"
```

Una publicación de alto impacto **no es un mercado, es una lotería**: los
spreads se triplican, la liquidez desaparece y un stop es una sugerencia. Por
eso es un veto, no un factor: no baja la nota, para el escaneo.

**7. Deja rastro en disco.**

```bash
make run CONFIG=session.json DATA=bars.csv FLAGS="--journal orders.jsonl"
```

Append-only, una línea JSON por evento, `fsync` por defecto. Un proceso que
muere a mitad de un envío vuelve, reproduce el log y sabe exactamente qué había
hecho — y `recover()` le pregunta al broker qué pasó mientras estaba muerto.

---

## La estrategia: seis pilares

ELYON QUANT opera **una sola tesis**: el precio va a buscar liquidez, la toma, y
después viaja desde una zona institucional en la dirección de la tendencia
superior. Seis cosas tienen que alinearse para que esa historia sea cierta:

| # | Pilar | Pregunta que responde | Dónde vive |
|---|-------|----------------------|------------|
| 1 | **TENDENCIA** | ¿Hacia dónde va realmente el mercado? | `build_structure` |
| 2 | **LIQUIDEZ** | ¿Dónde están las órdenes en reposo, y se las llevaron? | `build_pools`, `detect_sweeps` |
| 3 | **ORDER BLOCK** | ¿Dónde se originó el movimiento? | `detect_order_block` |
| 4 | **FVG** | ¿Dejó un desequilibrio detrás? | `detect_fvg` |
| 5 | **FIBONACCI** | ¿El retroceso se mide contra una pierna real? | `compute_fibonacci` |
| 6 | **ZONA OTE** | ¿El precio está a buen precio dentro de esa pierna? | `Fibonacci.in_ote` |

Los seis se localizan **en una sola pasada** y devuelven un único objeto:

```python
setup = locate_six_pillars(series, atr, symbol="EURUSD")

setup.pillars_found   # 4/6
setup.entry_zone      # order block ∩ banda OTE — la confluencia, no cada uno por su lado
setup.invalidation    # más allá de la liquidez barrida
setup.stop_loss(buf)  # el buffer SIEMPRE amplía: baja en largo, sube en corto
setup.target          # la liquidez hacia la que viaja el movimiento
```

Cada pilar informa **si está y, si no está, exactamente qué faltó** — porque
*"no hay trade"* necesita una razón tan precisa como *"hay trade"*:

```
· TENDENCIA: RANGE (read before the sweep)
✓ LIQUIDEZ: 3 sweep(s) in direction
✓ ORDER_BLOCK: [1.10350, 1.11180] FRESH, confidence 1.0
✓ FVG: [1.11180, 1.11320] CE 1.11250
✓ FIBONACCI: leg 1.10680 → 1.11500
· OTE: price 1.10800 outside [1.10855480, 1.10993240]
```

Fibonacci **nunca genera una entrada por sí solo**: puntúa a través de
premium/discount, así que una pierna medida bajo una compra en premium vale
cero. Y la tendencia se lee de la estructura **anterior al sweep**, porque un
barrido imprime un mínimo más bajo a propósito: leerlo como cambio de tendencia
sería confundir la manipulación con la señal.

---

## Estrategias

Además del modelo de la casa, ELYON QUANT trae un **catálogo de estrategias ICT
y Smart Money** que se pueden activar, desactivar y **combinar** individualmente.

| Estrategia | Familia | Tesis | Tier declarado | Tier real |
|---|---|---|---|---|
| **Six Pillars** | Liquidity raid | Los seis pilares alineados. El modelo de la casa | 🟢 HIGH | ⚪ |
| **ICT 2022 Model** | Structure shift | Sweep → MSS → entrada en el FVG que dejó | 🟢 HIGH | ⚪ |
| **Unicorn Model** | Block mitigation | Breaker y FVG ocupando los mismos precios | 🟢 HIGH | ⚪ |
| **SMT Divergence** | Correlation | Instrumentos correlacionados que discrepan en un extremo | 🟢 HIGH | ⚪ |
| **Silver Bullet** | Session timing | Una hora, un FVG, en dirección del sesgo | 🟡 MEDIUM | ⚪ |
| **Turtle Soup** | Liquidity raid | Ruptura falsa de un extremo antiguo que cierra dentro | 🟡 MEDIUM | ⚪ |
| **Judas Swing** | Session timing | El movimiento de apertura es mentira | 🟡 MEDIUM | ⚪ |
| **Power of 3 (AMD)** | Session timing | Acumulación → manipulación → distribución | 🟡 MEDIUM | ⚪ |
| **Breaker Retest** | Block mitigation | El nivel que no aguantó es el que ahora rechaza | 🟡 MEDIUM | ⚪ |
| **Equal Level Raid** | Liquidity raid | Máximos/mínimos iguales son un anuncio | 🟡 MEDIUM | ⚪ |
| **Asian Range Sweep** | Session timing | Londres barre un lado del rango asiático | 🟡 MEDIUM | ⚪ |
| **Optimal Trade Entry** | Premium/discount | Retroceso a 0.618–0.786. Nunca sola | 🔴 LOW | ⚪ |
| **Balanced Price Range** | Imbalance | Dos FVG opuestos solapados | 🔴 LOW | ⚪ |

```python
registry = (
    StrategyRegistry.default()               # casa en vivo, resto observando
    .live(StrategyId.ICT_2022_MODEL)         # activar
    .shadow(StrategyId.ICT_SILVER_BULLET)    # observar sin operar
    .off(StrategyId.BALANCED_PRICE_RANGE)    # desactivar
)
verdict = evaluate(context, registry)
```

### Por qué la columna «tier real» está en ⚪

**Un tier se gana, nunca se declara.** El tier declarado es la hipótesis del
autor; el que el motor obedece sale de una calibración real:

```python
Calibration(sample_size=180, wins=92, expectancy_r=dec("0.42"))  # → 🟢 HIGH
```

Tres reglas que salen de ahí, y que están cubiertas por tests:

- **Muestra < 30 → ⚪ UNPROVEN**, por bueno que se vea. Doce operaciones son una
  anécdota, no evidencia.
- **90% de aciertos con expectancy negativa → 🔴 LOW.** Ganar a menudo y ganar
  dinero son afirmaciones distintas, y solo la segunda paga.
- **⚪ UNPROVEN nunca abre una operación sola.** Solo puede corroborar a otra.
  Esa regla es lo que hace seguro publicar trece estrategias a la vez.

Por eso el catálogo se envía con todo en ⚪ y con **shadow mode**: la estrategia
se evalúa y se registra en cada vela pero no toca la operación. Es la única
salida al círculo vicioso de «necesita datos para operar, necesita operar para
tener datos».

### Cómo se combinan

- **La confluencia cuenta familias, no estrategias.** Cinco estrategias leyendo
  el mismo FVG son una evidencia vista cinco veces. Añadir una estrategia nunca
  puede, por sí solo, hacer que un setup existente parezca mejor.
- **El desacuerdo es un veto, no un promedio.** Si dos estrategias en vivo
  quieren lados opuestos, promediarlas pondría una posición pequeña en la que
  gritara más y ocultaría que el motor no tenía lectura. Se planta y lo dice.
  (Configurable: `VETO` por defecto, `STRONGEST_WINS`, `MAJORITY`.)
- **El tier decide quién actúa solo:** 🟢 sola · 🟡 con 1 familia · 🔴 con 2 ·
  ⚪ nunca.
- **La confluencia satura.** El bonus por familias de acuerdo es decreciente y
  tiene tope: un gráfico concurrido no es una certeza.
- **El hash del registro viaja con cada decisión**, así que un replay puede
  probar qué estrategias estaban activas cuando se tomó la operación.

Las *killzones* se definen en **hora de Nueva York**, no en UTC: la de Londres
cae a las 07:00 UTC en invierno y a las 06:00 en verano, y un sistema que
fija UTC está equivocado la mitad del año.

---

## El gate de contexto y MARKET DNA

**El primer motor que se ejecuta.** Antes de buscar un solo order block, decide
si este es un mercado en el que deberíamos siquiera estar mirando. Si el gate
falla, el Smart Money Engine **no se ejecuta** — y *por qué no se ejecutó* queda
registrado igual de bien que por qué se operó.

```
✓ REGIME            15/22  RANGE, ER 0.364929
· MTF_ALIGNMENT      0/16  no directional structure (slow RANGE, fast RANGE)
✓ MARKET_QUALITY    16/16  efficiency 0.364929, clean
✓ VOLATILITY        12/12  NORMAL (1.1901× typical)
✓ SESSION           12/12  NY_AM, an efficient window for EURUSD
✓ LIQUIDITY         10/10  spread 0.00010 (1.0000× typical)
· NEWS_CLEAR         0/8   no economic calendar connected; news risk unknown
✓ NO_MANIPULATION    4/4   no raid pattern in progress
                    69/100   threshold 60 → PASS
```

Dos fallos opuestos y ambos descalificantes: un mercado **muerto** no ofrece
nada que capturar, y uno **extremo** se mueve tanto que cualquier stop sensato
es ruido. Y el más caro de los tres: **CHURN** — movimiento sin dirección, que
parece oportunidad y no lo es.

**Un veto no es una nota baja.** Spread reventado, volatilidad ingobernable,
feed parado: son condiciones bajo las que el número deja de significar algo, y
fallan el gate con cualquier score.

**El contexto nunca puntúa la entrada** (ADR-0008): los dos conjuntos de
factores son disjuntos por construcción. Contar killzone en ambos sitios sería
pagar dos veces por la misma evidencia.

**El calendario que falta se ve.** Sin feed de noticias, `NEWS_CLEAR` se
**retiene** en vez de darse por bueno — el techo alcanzable baja a 92/100. Dar
esos 8 puntos gratis haría invisible una fuente de datos ausente.

### MARKET DNA

Un ATR de 0.0008 es un mercado muerto en oro y uno normal en EURUSD. Todo umbral
se expresa en **unidades relativas al instrumento**, nunca en precios absolutos.

| Activo | Clase | ATR típico | Spread (máx) | Horas eficientes |
|---|---|---|---|---|
| EURUSD | FX major | 0.00100 | 0.00010 (0.00040) | London · NY AM · London close |
| GBPUSD | FX major | 0.00140 | 0.00015 (0.00060) | London · NY AM |
| XAUUSD | Metal | 2.50 | 0.30 (1.20) | NY AM · London close |
| NAS100 | Índice | 25.0 | 1.5 (6.0) | NY cash open |
| US30 | Índice | 120.0 | 2.0 (10.0) | NY cash open |
| BTCUSD | Cripto | 350.0 | 8.0 (60.0) | 24/7 |
| ETHUSD | Cripto | 28.0 | 1.2 (9.0) | 24/7 |

**Regla inviolable: el DNA adapta filtros, NUNCA reglas.** Qué *es* un BOS, qué
*es* un order block, cómo se compone el score — idéntico en todos los activos.
Lo que cambia por activo es cuánto vale «igual», cuánta penetración cuenta como
barrido, qué spread se tolera. Se aplica estructuralmente: un perfil solo puede
llevar números, y el motor los lee con `dna.override ?? engine_default`. No hay
gancho para que un perfil aporte comportamiento.

Y como con los tiers: **un perfil escrito a mano es una conjetura.** Los siete
perfiles de referencia salen con `is_calibrated = False`, y `learn_dna()` deriva
uno real de velas reales — usando la **mediana**, para que un solo pico de
volatilidad no redefina lo que es normal el mes siguiente. Lo que se aprende son
los números medibles; las horas eficientes y las sensibilidades siguen siendo
decisiones de research y no las reescribe ningún ajuste.

---

## Gestionar la posición

Entrar es la mitad fácil. Lo que decide si una estrategia con ventaja acaba
componiendo es lo que pasa después: cuándo se mueve el stop, cuándo sale una
parte, cuándo se cierra una operación que no va a ningún sitio.

**Una regla domina todo:**

> **El stop nunca se mueve en contra de la posición. Nunca.**

Un stop «dinámico» que puede ensancharse no es un stop dinámico: es un stop que
alguien movió porque no le gustaba estar equivocado, y es la forma más fiable
de convertir una pérdida acotada en una ilimitada. Toda función que devuelve un
stop se compara con el que sustituye, y un movimiento hacia atrás se **rechaza**,
no se registra.

| Regla | Por defecto | Detalle |
|---|---|---|
| **Break-even** | a 1.0R | Va **más allá** de la entrada, no *a* la entrada: un stop justo en la entrada sigue perdiendo el spread |
| **Trailing** | desde 1.5R, a 1.5×ATR | Nunca antes del break-even, o el primer trail movería el stop hacia atrás |
| **Parcial** | 50% a 1.5R | Se contabiliza al **precio del disparo**, no al cierre de la vela: la orden estaba puesta ahí |
| **Time stop** | 40 velas sin llegar a 0.3R | Capital atado a algo que no funciona es capital que no está en algo que sí |

Todo en **R**, así que las mismas reglas valen sin tocar nada para un stop de 5
pips en EURUSD y uno de 30 dólares en oro. Y **1R no se rebasea** cuando el stop
se mueve: hacerlo haría que una operación pareciese mejor solo por haber sido
gestionada.

Cuando una vela contiene stop *y* objetivo, se asume el stop — misma regla que
el backtester, por la misma razón.

---

## El OMS y el problema de la orden duplicada

**Toda ejecución pasa por el OMS.** Una orden colocada fuera de él no tiene log
de eventos, ni clave de idempotencia, ni conciliación — nadie puede decir
después qué pasó ni por qué.

El estado nunca se asigna: es un **fold sobre un log inmutable**. Un proceso que
muere a mitad de un envío vuelve, reproduce el log y sabe exactamente qué había
hecho ya.

```
#1 CREATED
#2 VALIDATED (structural checks passed)
#3 RISK_APPROVED (risk approved)
#4 QUEUED
#5 SENT
#6 RECOVERY_STARTED (send failed: no response from venue)
#7 RECONCILED (adopted broker state ACKNOWLEDGED)
```

### El bug más caro que puede tener un OMS

**Un envío que da timeout tiene resultado desconocido.** La orden puede estar
descansando en el broker, puede haberse ejecutado, o puede no haber llegado
nunca. Reenviar sobre un «quizá» es cómo una posición se convierte en dos — y
duplica el riesgo en silencio: la cuenta se ve bien hasta que deja de verse.

La respuesta es no adivinar nunca:

```
query(client_order_id)
  existe  → adoptarla; la orden ya estaba puesta
  ausente → reenviar, con el MISMO client order id para que el broker deduplique
```

| Escenario | Resultado | Colocaciones en el venue |
|---|---|---|
| Timeout, la orden **sí** llegó | adoptada | **1** (no 2) |
| Timeout, la orden **no** llegó | reenviada con el mismo id | 1 |
| El broker se cae durante la conciliación | **OMS halted** | 0 |
| Enviar la misma orden dos veces | rechazado por la máquina | 1 |

Tres defensas independientes:

1. **La máquina de estados.** `QUEUED` es el **único** estado que llega a `SENT`.
   Un segundo envío no está desaconsejado: es imposible.
2. **El `client_order_id` es determinista.** Derivado del `correlation_id`, no de
   un reloj ni de un random — un reintento tiene que ser reconociblemente la
   *misma* orden, o el broker no puede deduplicar.
3. **Preguntar antes de reenviar.** Y cuando la respuesta sigue siendo
   desconocida, se hace lo único siempre seguro: dejar de enviar y proteger lo
   abierto. **Parar no es cerrar** — cerrar durante una caída es operar a ciegas
   en el peor momento.

### Lo demás

- **Fills parciales** se agregan, y el precio medio es **ponderado por volumen**:
  promediar los precios de un fill de 0.09 y otro de 0.01 como si pesaran igual
  falsea la entrada y todo el riesgo derivado de ella.
- **Exactly-once lógico**: entrega at-least-once + dedup por `broker_event_id`.
  Un execution report reentregado se aplica una vez.
- **Un over-fill no se promedia**: el broker dice que tenemos más de lo que
  pedimos, eso es una discrepancia que tiene que ver un humano. Va a la DLQ y el
  OMS para.
- **Circuit breakers por dependencia**, no globales: una caída de market data no
  puede impedir *cerrar* una posición, y un breaker global no distingue.
- **Outbox** contra el dual-write: el evento se persiste antes de publicarse. Un
  publish fallido se conserva — la entrega puede ser lenta, no silenciosa.
- **DLQ con motivo obligatorio**: una cola de muertos cuyas entradas no se pueden
  explicar es descartar eventos con pasos extra.

### El log en disco

Append-only, una línea JSON por evento, `fsync` por defecto — «persistido» que
sobrevive a un crash de proceso pero no a un corte de luz no es la garantía en
la que se apoya el OMS. Formato elegido por lo que cuesta cuando algo va mal a
las tres de la mañana: se puede `grep`, `tail`, `diff`, no necesita servidor, y
un fichero corrupto se repara a mano.

Un crash a mitad de escritura deja **la última línea rota**: ese evento nunca se
completó, así que descartarla es la lectura correcta. Una línea rota en
**cualquier otro sitio** es daño, y cargar falla en voz alta — reconstruir una
posición desde un agujero es peor que negarse a abrir el fichero.

---

## Backtesting: cómo se gana un tier

Un backtest es una afirmación sobre lo que un sistema *habría* hecho, y hay
cuatro formas conocidas de que esa afirmación sea falsa. El simulador rechaza
tres **estructuralmente**:

| Mentira | Cómo se impide |
|---|---|
| **Look-ahead** | Una estrategia solo recibe `series.window(i, lookback)`. No puede ver la vela `i+1` porque no está en el objeto que se le pasó |
| **Optimismo intrabar** | Si una vela contiene stop *y* objetivo, el OHLC no dice cuál se tocó antes. Se asume el **stop**, siempre, y sin opción de desactivarlo |
| **Fills sin coste** | Spread y slippage se aplican en cada fill y **siempre en contra**: el comprador llena más caro, el vendedor más barato |

La cuarta —medir sobre los datos con los que diseñaste la estrategia— no se
puede detectar, así que hay que **declararla**, y certificar un `IN_SAMPLE` está
prohibido:

```
SIX_PILLARS was measured in-sample on 'synthetic-m1'; an in-sample result
cannot certify a tier. Hold data back and re-run, or mark the run
OUT_OF_SAMPLE only when it genuinely was.
```

El reporte incluye **`ex-best trade`**: la expectancy quitando la mejor
operación. Es la comprobación más útil que hay — si el edge desaparece al
quitar una sola operación, no es un edge, es un *outlier*, y los outliers no se
repiten a demanda. El resumen lo marca con `⚠ carried by one trade`.

```python
trades = simulate(series, registry, config=SimulationConfig(), playbook=research_config((HOUSE,)))
report = report_from(trades, sample=Sample.OUT_OF_SAMPLE, ...)
calibration = calibration_from(report)      # ← lo único que mueve un tier
config = PlaybookConfig(calibrations={StrategyId.SIX_PILLARS: calibration})
```

Otras dos decisiones que importan: un **R:R sin techo es una alarma, no un
premio** (un objetivo a 20R es un nivel que el precio no alcanza, así que la
operación siempre sale por tiempo y se contabiliza la deriva que hubiera), y
las operaciones que quedan abiertas al acabar los datos **se reportan, no se
descartan** — descartarlas sesgaría la muestra hacia las que se resolvieron.

---

## Estado del código

| Módulo | Qué hace | Estado |
|--------|----------|--------|
| `shared_kernel/edcs` | Decimal canónico, cuantización, JSON canónico, hashing, IDs estables | ✅ |
| `market_data` | Ticks → velas (FORMING/CONFIRMED), watermark, ATR, Efficiency Ratio | ✅ |
| `smart_money` | Displacement, swings, estructura, BOS/CHoCH/MSS, liquidez, sweeps, FVG, order blocks, premium/discount, OTE, Fibonacci | ✅ |
| `strategy` | **Los seis pilares** + catálogo ICT (13 estrategias), tiers por calibración, activación tri-estado, playbook de combinación, killzones | ✅ |
| `risk` | Presupuesto con reserva atómica (CAS), position sizing, riesgo dinámico | ✅ |
| `trading` | Scoring Engine, DecisionRecord, explicabilidad, gestión de posición (BE, trailing, parciales, time stop) | ✅ |
| `session` | Configuración, runner tick→decisión→orden, diagnóstico por etapa | ✅ |
| `execution/store` | Log append-only en JSONL, `fsync`, tolerante a escritura rota, restauración | ✅ |
| `execution/conformance` | Suite ejecutable del contrato de adapter, tolerante a adapters rotos | ✅ |
| `market_context/calendar` | Calendario económico, ventanas de blackout asimétricas, mapa divisa↔instrumento | ✅ |
| `execution` | OMS event-sourced: máquina de estados, idempotencia, query-before-resend, circuit breakers, outbox, DLQ, recovery | ✅ |
| `market_context` | Context Score 0–100, gate con histéresis, regímenes, **Market DNA** de 7 activos | ✅ |
| `backtesting` | Simulación walk-forward sin look-ahead, costes, reporte y calibración de tiers | ✅ |

**Garantías demostradas por tests, no prometidas:** determinismo bit a bit ·
no-repaint (una vela confirmada nunca muta) · orden de llegada irrelevante ·
sin look-ahead (leer un prefijo nunca ve las velas que vienen) · un veto vence a
cualquier score · el stop nunca cae del lado equivocado · una estrategia sin
calibrar nunca opera sola · añadir una estrategia nunca mejora un setup
existente · truncar el futuro no cambia el pasado · un backtest in-sample no
certifica nada · el riesgo tiene la última palabra · toda decisión se explica desde
su propio registro.

---

## Qué es ELYON QUANT

Un ecosistema SaaS multi-tenant que cubre el ciclo de vida completo del trading
cuantitativo:

1. **Strategy Lab** — autoría, versionado y validación de estrategias.
2. **Market Data Platform** — ingesta y normalización de datos de mercado
   (tick, OHLCV, order book) en tiempo real e histórico.
3. **Backtesting Engine** — simulación histórica reproducible y de alta fidelidad.
4. **Paper Trading** — simulación en vivo sin capital real.
5. **Execution & OMS** — gestión del ciclo de vida de órdenes y ruteo.
6. **Risk Management** — control de riesgo pre-trade / post-trade y *kill-switch*.
7. **Portfolio & Analytics** — posiciones, PnL, métricas y *tearsheets*.
8. **Marketplace** — publicación, suscripción y monetización de estrategias.
9. **Connectivity** — adaptadores a MT5, Interactive Brokers, Binance, etc.
10. **Decision Replay Engine** — registro de **todas** las decisiones (ejecutadas y
    descartadas) y reproducción paso a paso de cualquier señal.
11. **Market Context Engine** — **primer motor que se ejecuta**: determina el
    contexto/régimen del mercado y emite un **Context Score (0–100)**; si no supera
    el mínimo, el resto del motor **ni busca entradas**. Incluye **Market DNA**
    (perfil por activo que adapta filtros, no reglas).

El motor SMC incluye **Fibonacci Institucional** (anclado a estructura, nunca
indicador independiente; provee la OTE) y es **explicable por diseño**: nunca
responde *"entró porque sí"*. El pipeline se abre siempre con el **gate de
contexto** (Market Context Engine).

---

## Conectar un broker real

Son **tres métodos**: `place`, `query`, `cancel`. Y `query` no es una
optimización — es lo único que separa un envío con timeout de una posición
duplicada.

Equivocarse en cualquiera de los tres de forma sutil no aparece en pruebas:
aparece la primera vez que la red hipa con dinero real dentro. Por eso el
contrato no está escrito en prosa, está escrito como comprobaciones ejecutables:

```bash
elyon conformance --adapter mibroker:build
```

```
✓ query on an unknown order: returns exists=False
✓ place then query: the venue reports the order (broker id B-0001)
✓ duplicate place is deduplicated: the same client order id maps to one broker order
✓ errors are typed: could not provoke a rejection; verify by hand
✓ cancel is reflected in query: a cancelled order no longer reports as live

All critical checks passed. The OMS's guarantees hold against this adapter.
```

Las comprobaciones van a propósito a los casos incómodos, no al camino feliz.
Un adapter que coloca órdenes bien y responde `query` mal pasa cualquier prueba
casual y pierde dinero al primer timeout. Los fallos que detecta, con el motivo:

| Adapter que… | Consecuencia |
|---|---|
| dice que existe una orden que nunca se colocó | el OMS adopta un fantasma y la orden real nunca se envía |
| no encuentra una orden que acaba de aceptar | tras un timeout el OMS concluye que no se colocó nada y **manda una segunda** |
| no deduplica el mismo `client_order_id` | un reenvío de recuperación dobla la posición |
| lanza excepciones sin tipar | el OMS no distingue rechazo de timeout y no concilia |
| tipa un rechazo como timeout | reintenta para siempre una orden que el venue nunca aceptará |
| no responde a `query` | el OMS se planta — seguro, pero nunca se recupera |

Y la suite **no revienta con un adapter roto**: informar del mal comportamiento
*es* el trabajo. Un kit de conformidad que se cae con lo que debe detectar no
sirve de nada.

> **Estas comprobaciones colocan órdenes.** Contra una cuenta demo.

---

## Qué falta para producción

Honestidad sobre el estado real, porque lo que existe está probado y lo que no,
no:

| Falta | Qué implica |
|---|---|
| **Adaptador de broker real** | El `BrokerAdapter` es un `Protocol` y hay un `PaperBroker` que sabe fallar como fallan los reales. Conectar MT5/IB/Binance es implementar tres métodos — `place`, `query`, `cancel` — pero necesita credenciales y un entorno que aquí no existe |
| **Feed de datos en vivo** | La sesión consume ticks; falta quien se los dé |
| **Calendario poblado** | El motor lo lee; hay que traer los eventos de un proveedor |
| **Posiciones concurrentes** | Una a la vez. Varias necesitan modelo de cartera y presupuesto de riesgo compartido |
| **SMT Divergence** | Necesita feed de un instrumento correlacionado |
| **Saga durable / gateway en Rust** | ADR-EXE-3 y ADR-EXE-7, fuera del alcance actual |

Lo que **sí** puedes hacer hoy: correr sesiones completas sobre datos
históricos, calibrar estrategias, comparar configuraciones, y ver exactamente
por qué el motor decidió lo que decidió en cada vela.

---

## Principios de ingeniería

- **Clean Architecture** — dependencias apuntando hacia el dominio.
- **Domain-Driven Design (DDD)** — diseño estratégico + táctico.
- **SOLID + Clean Code** — código legible, testeable y mantenible.
- **Event-Driven** donde aporta valor (integración entre *bounded contexts*).
- **Modular Monolith** listo para evolucionar a **microservicios**.
- **Secure by design** y **Scalable by design**.
- **Explainable by design** — toda decisión de trading es trazable y explicable
  (qué detectó, confirmó, descartó; pesos; score; reglas y vetos).
- **Fully replayable** — cada decisión, ejecutada o descartada, es reproducible.

---

## Índice de documentación de arquitectura

| # | Documento | Contenido |
|---|-----------|-----------|
| 00 | [Visión y Arquitectura General](docs/architecture/00-vision-y-arquitectura-general.md) | Visión, C4, estilo arquitectónico |
| 01 | [Organización del Repositorio](docs/architecture/01-organizacion-repositorio.md) | Monorepo, carpetas, estructura por capas |
| 02 | [Módulos y Bounded Contexts](docs/architecture/02-modulos-y-bounded-contexts.md) | Subdominios, agregados, context map |
| 03 | [Dependencias entre Módulos](docs/architecture/03-dependencias-entre-modulos.md) | Reglas, grafo de dependencias, contratos |
| 04 | [Stack Tecnológico](docs/architecture/04-stack-tecnologico.md) | Tecnologías recomendadas y por qué |
| 05 | [Roadmap Técnico](docs/architecture/05-roadmap-tecnico.md) | Fases, hitos, evolución a microservicios |
| 06 | [Convenciones de Desarrollo](docs/architecture/06-convenciones-desarrollo.md) | Estilo, errores, logging, DI |
| 07 | [Naming Convention](docs/architecture/07-naming-convention.md) | Nomenclatura por lenguaje y capa |
| 08 | [Git Strategy](docs/architecture/08-git-strategy.md) | Trunk-based, PRs, versionado, releases |
| 09 | [Testing Strategy](docs/architecture/09-testing-strategy.md) | Pirámide de tests, contract testing |
| 10 | [Deployment Strategy](docs/architecture/10-deployment-strategy.md) | CI/CD, GitOps, entornos, progresivo |
| 11 | [Seguridad](docs/architecture/11-seguridad.md) | AuthN/Z, secretos, cumplimiento |
| 12 | [Escalabilidad](docs/architecture/12-escalabilidad.md) | Escalado horizontal, datos, resiliencia |
| 13 | [Plan de Ejecución paso a paso](docs/architecture/13-plan-de-ejecucion.md) | Cómo construirlo incrementalmente |

**Decisiones de arquitectura (ADR):** ver [`docs/adr/`](docs/adr/).

---

## Lectura recomendada

1. Empieza por **[00 — Visión](docs/architecture/00-vision-y-arquitectura-general.md)**.
2. Sigue con **[02 — Bounded Contexts](docs/architecture/02-modulos-y-bounded-contexts.md)**.
3. Cuando quieras empezar a construir, ve directo a
   **[13 — Plan de Ejecución](docs/architecture/13-plan-de-ejecucion.md)**.
