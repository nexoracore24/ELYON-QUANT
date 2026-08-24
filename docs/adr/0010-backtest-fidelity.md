# ADR-0010: Fidelidad del backtest — qué se asume cuando no se sabe

- **Estado:** Accepted
- **Fecha:** 2026-08-24
- **Decisores:** CTO/Principal Architect, Quant Research Director, Risk Lead
- **Relacionado:** [ADR-0009](0009-strategy-catalog-tiers-and-combination.md)
  (el catálogo depende de esta calibración), ENG-009 (Decision Replay),
  [ADR-0006](0006-deterministic-computing.md)

## Contexto

ADR-0009 dejó al producto en un estado deliberado: **nada opera** hasta que
exista una `Calibration`. El Backtesting Engine es lo que la produce, lo que lo
pone en el camino crítico y lo convierte en el componente del que depende cada
decisión de riesgo del sistema.

Un backtest es una afirmación sobre lo que un sistema *habría* hecho. Hay cuatro
formas conocidas de que esa afirmación sea falsa, y las cuatro son fáciles de
cometer sin darse cuenta porque todas hacen que los resultados mejoren.

## Decisión

### 1. Look-ahead: imposible por construcción, no por disciplina

Una estrategia solo recibe `series.window(i, lookback)`. No puede leer la vela
`i+1` porque no está en el objeto. La garantía es estructural: no depende de que
nadie recuerde no hacerlo.

Test que lo fija: truncar los datos después de la vela *k* no cambia ninguna
operación que se cerró antes de *k*.

### 2. Ambigüedad intrabar: se asume siempre lo malo

Cuando el rango de una vela contiene el stop **y** el objetivo, el OHLC no dice
cuál se tocó primero. La respuesta honesta es que no se sabe, y la única
suposición segura es la mala: **se asume el stop**.

Sin parámetro para desactivarlo. Ese parámetro es exactamente por donde un
backtest empieza a mentir, y un sistema perdedor con la resolución favorable
imprime una curva de capital preciosa.

Un hueco que salta el stop rellena **en la apertura** (peor que el stop) y se
etiqueta `GAP_THROUGH_STOP`, distinto de `STOP`: el riesgo de hueco no es riesgo
de stop, y confundirlos subestima la cola.

### 3. Costes: siempre en contra

Spread y slippage se aplican en cada fill y siempre contra la operación — el
comprador llena más caro, el vendedor más barato. El default **no es cero**; un
default sin costes es cómo un backtest se convierte discretamente en un
argumento de venta.

### 4. In-sample: se declara y no certifica

Medir una estrategia sobre los datos con los que se diseñó muestra un edge
exista o no. El simulador no puede detectarlo, así que `Sample` obliga a
declararlo y `calibration_from` **rechaza** un `IN_SAMPLE`. `tier_of` permite
previsualizar sin certificar, para decidir si merece la pena una corrida seria.

### 5. Ventana acotada

`lookback_bars` (120 por defecto) limita la historia que ve una estrategia. Es a
la vez rendimiento —mantiene la corrida lineal en el número de velas en vez de
cuadrática— y modelado: un swing de hace ocho meses no es estructura contra la
que el mercado opera hoy.

### 6. R:R con techo, no solo con suelo

`max_reward_risk` (8 por defecto). Un R:R sin límite no es un premio, es una
alarma: un objetivo a 20R es un nivel que el precio casi nunca alcanza, así que
la operación siempre sale por tiempo y se contabiliza la deriva que hubiera.
**Una sola operación así puede sostener un backtest entero.**

### 7. Operaciones sin resolver se reportan, no se descartan

Una operación abierta cuando acaban los datos sale como `END_OF_DATA` y cuenta.
Descartarla sesgaría la muestra hacia las que se resolvieron.

### 8. `expectancy_ex_best` en todos los reportes

La expectancy quitando la mejor operación. Si el edge desaparece al quitar una
sola, no es un edge: es un outlier, y los outliers no se repiten a demanda. El
resumen lo marca con `⚠ carried by one trade`.

### 9. El bypass de investigación es explícito y está confinado

Calibrar exige que la estrategia opere, pero el gate en vivo rechaza estrategias
sin calibrar — el deadlock en el que vive todo el sistema de tiers.
`research_config()` lo rompe otorgando un registro provisional etiquetado
`__research__`, imposible de confundir con una medición real. Vive en el módulo
de backtesting y no debe ser alcanzable desde el camino en vivo.

## Alternativas descartadas

- **Resolver el intrabar por proximidad al open.** Suena razonable y es una
  heurística sin base: en una vela con mecha a ambos lados no informa de nada y
  sesga sistemáticamente a favor.
- **Simular tick a tick para resolver la ambigüedad.** Correcto, y requiere
  datos de tick históricos que la plataforma no tiene todavía. Cuando existan,
  esta ADR se revisa.
- **Costes cero por defecto, «que el usuario los ponga».** El usuario que no los
  pone es exactamente el que necesita que estén.
- **Historia completa en cada vela.** Cuadrático (fatal sobre un año de M1) y
  además peor modelado.
- **Permitir certificar in-sample con una advertencia.** Las advertencias se
  ignoran. Un rechazo no.
- **Posiciones concurrentes.** Necesitan un presupuesto de riesgo compartido
  entre ellas; sin él las cifras en R no son comparables. Una posición a la vez
  hasta que exista el modelo de cartera.

## Consecuencias

**Positivas.** Los números que salen son defendibles: cada corrida lleva el hash
de sus datos, de su configuración y del registro de estrategias activas. El
sistema no puede certificar accidentalmente un resultado inflado.

**Negativas.** Los resultados son sistemáticamente **peores** que los de casi
cualquier otro backtester, y eso se leerá como que el motor es malo. Es al
revés: la diferencia es el margen que los demás se regalan. Hay que explicarlo
en el onboarding.

**Deuda registrada.** Sin datos de tick, la resolución intrabar es pesimista en
vez de exacta. Sin modelo de cartera, no hay posiciones concurrentes. El
generador sintético sirve para probar el simulador, **no** para validar
estrategias — un modelo corrido sobre datos fabricados para contener sus setups
no demuestra nada sobre el modelo.
