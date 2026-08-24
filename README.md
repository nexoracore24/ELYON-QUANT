# ELYON QUANT

**Plataforma profesional de trading algorítmico.** No es un bot de MT5: es un
ecosistema completo para diseñar, validar, ejecutar, monitorizar y monetizar
estrategias cuantitativas sobre múltiples brokers y exchanges, con estándares
de ingeniería de nivel Stripe / Palantir / Google / OpenAI.

> Estado del proyecto: **Núcleo en construcción.**
> La arquitectura está congelada (`v1.0-rc1`) y el primer código del motor ya
> corre: datos de mercado, detectores Smart Money, la estrategia de los seis
> pilares, el catálogo ICT combinable, backtesting, riesgo y scoring, con
> **455 tests** verdes.
>
> ```bash
> make test    # suite completa
> make demo    # el pipeline decidiendo, y explicándose
> ```
>
> 📋 [Plan Maestro de Documentación](docs/00-governance/documentation-master-plan.md) ·
> 🧊 [Core Architecture Review v1.0](docs/architecture/core-architecture-review-v1.0.md) ·
> 🔒 [Core Contracts v1.0](docs/06-api/core-contracts-v1.0.md)

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
| `trading` | Scoring Engine, DecisionRecord, explicabilidad | ✅ |
| `execution` | OMS: ciclo de vida de la orden, idempotencia, recovery | ⬜ especificado |
| `market_context` | Gate de contexto, Market DNA | ⬜ especificado |
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
