<!--
title: ELYON QUANT — Trading Engine Bible
id: ENG-001 / ENG-002 (Trading + Smart Money Engine, documento maestro)
owner: Quant Lead
reviewers: [CTO/Principal Architect, ML Lead, Security Lead]
status: draft
version: 0.1
last_updated: 2026-07-28
supersedes: —
-->

# ELYON QUANT — TRADING ENGINE BIBLE

> **Documento maestro del cerebro de ELYON QUANT.** Define *exactamente cómo
> piensa* el motor: cómo lee el mercado, cómo decide, cuándo opera, cómo
> gestiona el riesgo y cómo explica cada decisión.
>
> Autoría conceptual: Quant Research Director · Institutional Trader · Chief AI
> Officer · Lead Software Architect.
>
> **Principio rector:** *toda regla aquí descrita debe ser **objetiva,
> determinista y parametrizable**.* No existe "criterio del trader": existe una
> definición algorítmica con parámetros configurables y valores por defecto.
> Si un concepto no se puede medir, no entra en el motor.

---

## Cómo leer este documento

- **Parámetros**: se nombran en `snake_case` (inglés, según el naming
  convention) y se agrupan en el **Catálogo de Parámetros** (Apéndice A). Cada
  valor por defecto es un punto de partida, **no** un dogma: todo es
  configurable por perfil de mercado y por estrategia.
- **Configurable**: marcado con `⚙️`. **Regla dura / veto**: marcado con `⛔`.
- **Objetividad**: cada concepto SMC se define con una **regla de detección**
  (qué mira), **condiciones de validez** (cuándo cuenta) e **invalidación**
  (cuándo deja de contar).
- Este documento **no contiene código**. Es la especificación normativa que la
  implementación deberá cumplir y que los tests verificarán (trazabilidad
  BLD-003).

---

# 1. Filosofía del motor de trading

ELYON QUANT no opera indicadores; **opera intención institucional**. La premisa
es que el precio se mueve por la necesidad del "dinero inteligente" (bancos,
market makers, instituciones) de **acumular y distribuir posiciones** capturando
la liquidez que dejan los participantes minoristas.

Cuatro creencias fundacionales del motor:

1. **El mercado es un mecanismo de búsqueda de liquidez.** El precio se dirige
   hacia zonas donde hay órdenes en espera (stops, pendientes). Primero se
   *barre* la liquidez, después se *revierte* o *continúa* con desplazamiento.
2. **La estructura manda.** La dirección solo cambia cuando la **estructura de
   mercado** cambia (CHoCH), no cuando lo dice un oscilador.
3. **La confluencia sobre la corazonada.** Ninguna señal aislada opera. El motor
   exige **confluencia puntuada** (Scoring Engine, §26): estructura + liquidez +
   zona de interés + imbalance + contexto temporal.
4. **El riesgo es lo primero.** Antes de "cuánto puedo ganar", el motor calcula
   "cuánto puedo perder y bajo qué condiciones NO debo estar en el mercado"
   (§34–§37). La preservación del capital tiene prioridad sobre la oportunidad.

El motor es, por diseño, **paciente, selectivo y explicable**. Prefiere no
operar a operar mal. Cada decisión —operar o abstenerse— queda registrada y es
auditable (§39–§40).

---

# 2. Objetivos del motor

**Objetivo primario:** generar decisiones de trading de **alta convicción y bajo
riesgo**, basadas en una lectura objetiva de Smart Money Concepts, con una
**expectativa matemática positiva** y un perfil de *drawdown* controlado.

Objetivos medibles (SLOs de estrategia, refinados en Performance Targets OPS-006):

| Objetivo | Métrica | Meta de diseño (⚙️ por perfil) |
|----------|---------|-------------------------------|
| Selectividad | % de setups evaluados que se operan | Bajo por diseño (calidad > cantidad) |
| Expectativa | Expectancy = (Win% × avgWin) − (Loss% × avgLoss) | > 0 sostenido |
| Riesgo/beneficio | RR medio realizado | ≥ `min_realized_rr` (def. 2.0) |
| Consistencia | Máx. drawdown | ≤ `max_drawdown_target` (def. 10 %) |
| Robustez | Estabilidad en walk-forward (out-of-sample) | Sin degradación severa |
| Explicabilidad | % decisiones con explicación completa | **100 %** |
| Determinismo | Reproducibilidad de backtest | **100 % (bit a bit)** |

**Objetivos no-negociables (invariantes):**
- El motor **nunca** abre sin superar el umbral de score y sin pasar los vetos.
- El motor **nunca** arriesga más de lo configurado por operación, día y global.
- El motor **siempre** puede explicar por qué entró o por qué no entró.

---

# 3. Mercados soportados

El motor es **multi-mercado** mediante *perfiles de instrumento* (`instrument_profile`)
que ajustan parámetros a la microestructura de cada activo. La lógica SMC es
común; los umbrales cambian.

| Mercado | Símbolos ejemplo | Estado | Particularidades del perfil |
|---------|------------------|--------|-----------------------------|
| **Forex** | EURUSD, GBPUSD, USDJPY… | Fase 1 | Volumen = tick volume; sesiones FX; spread bajo; pips por par |
| **Oro (XAUUSD)** | XAUUSD | Fase 1 | Alta volatilidad (ATR grande); sensible a noticias USD y sesión NY; spread mayor |
| **Índices** | US30, NAS100, GER40… | Fase 2 | Horario de bolsa; gaps de apertura; killzones de cash open; contratos/puntos |
| **Criptomonedas** | BTCUSDT, ETHUSDT… | **Futuro** | Mercado 24/7 (sin sesiones clásicas); volumen real; alta volatilidad; sin "news window" tradicional pero sí eventos on-chain |

Cada perfil define, como mínimo: `pip_size`/`tick_size`, `max_spread`,
`session_timezone`, `killzones`, `atr_regime_bounds`, `equal_level_tol`,
`displacement_atr_mult`, `news_provider` y `contract_multiplier`. El núcleo del
motor **no** conoce el activo: consume el perfil.

---

# 4. Timeframes utilizados

El motor razona en una **jerarquía de tres niveles** (⚙️ `timeframe_triad`):

| Rol | Nombre | Def. Forex/Oro | Función |
|-----|--------|----------------|---------|
| **HTF** (bias) | *Higher* | H4 (y D1 de contexto) | Sesga la dirección: tendencia, premium/discount, POIs mayores |
| **MTF** (estructura/POI) | *Mid* | M15 (y H1) | Localiza zonas de interés operables y estructura intermedia |
| **LTF** (gatillo) | *Lower* | M1 / M5 | Confirma la entrada: sweep + CHoCH + FVG dentro del POI |

Reglas:
- Las **decisiones de sesgo** solo se toman en HTF; las **entradas** solo en LTF;
  el MTF es el puente que aloja los POIs.
- Solo se usan **velas cerradas** (`use_closed_candles = true` ⛔ por defecto)
  para confirmar estructura; nada se decide sobre la vela en formación salvo
  gestión de posición abierta.
- La tríada es configurable por perfil (p.ej. índices intradía podrían usar
  H1/M5/M1).

---

# 5. Análisis Multi-Timeframe (MTF)

El MTF es el **marco de coherencia**: una entrada solo es válida si está
**alineada de arriba hacia abajo**. Flujo *top-down*:

1. **HTF — Bias.** Determinar tendencia (§6), rango operativo (dealing range) y
   si el precio está en **premium o discount** (§18). Identificar la **liquidez
   objetivo** (§9) y los **POIs HTF** (order blocks/FVG sin mitigar).
2. **MTF — Contexto.** Dentro del bias HTF, localizar el **POI operable** (OB,
   breaker, mitigation, FVG) hacia el que el precio se dirige y confirmar que la
   estructura MTF no contradice al HTF.
3. **LTF — Gatillo.** Al llegar el precio al POI, esperar el **modelo de
   entrada** (§27): barrido de liquidez → CHoCH en LTF → FVG/OB de confirmación
   → entrada.

**Regla de alineación (⛔ veto por defecto `require_htf_alignment = true`):** si el
sesgo LTF contradice al HTF, **no se opera** (salvo modo *counter-trend*
explícitamente habilitado). El Scoring Engine premia la alineación de los tres
niveles y penaliza la divergencia.

**Estado MTF** que el motor mantiene por símbolo:
`htf_bias ∈ {bullish, bearish, neutral}`, `htf_zone ∈ {premium, equilibrium, discount}`,
`target_liquidity`, `active_poi`, `ltf_trigger_state`.

---

# 6. Detección automática de tendencia (Market Structure)

La tendencia es **consecuencia de la estructura**, definida objetivamente sobre
*swing points*.

### 6.1 Detección de swings (fractales)
- **Swing High**: vela cuyo `high` es estrictamente mayor que el `high` de las
  `swing_lookback` velas a cada lado (⚙️ def. 3). **Swing Low**: análogo con
  `low`. Solo con velas cerradas.
- Se distinguen **estructura interna** (swings menores, `swing_lookback_internal`,
  def. 2) y **estructura mayor** (`swing_lookback_major`, def. 5). El bias usa la
  mayor; los gatillos usan la interna.

### 6.2 Clasificación de tendencia
Sobre la secuencia de swings mayores:
- **Alcista (bullish):** *Higher Highs* (HH) y *Higher Lows* (HL) consecutivos.
- **Bajista (bearish):** *Lower Highs* (LH) y *Lower Lows* (LL).
- **Neutral / rango:** ausencia de secuencia clara o swings solapados dentro de
  `range_tol_atr`.

### 6.3 Transiciones
La tendencia solo **continúa** con **BOS** (§7) y solo **cambia** con **CHoCH**
(§8). El motor no infiere tendencia por pendiente de media ni por indicadores.

**Salida del módulo:** `trend_state`, lista de swings etiquetados (HH/HL/LH/LL),
último BOS, último CHoCH, y `structure_confidence` (fuerza del patrón).

---

# 7. BOS — Break of Structure

**Definición objetiva:** ruptura de un swing point **en la dirección de la
tendencia vigente**, que **confirma continuación**.

- **BOS alcista:** en tendencia alcista, el precio **cierra** por encima del
  último *swing high* confirmado.
- **BOS bajista:** en tendencia bajista, el precio **cierra** por debajo del
  último *swing low* confirmado.

**Condición de confirmación** (⚙️ `bos_confirmation`):
- `close` — cuerpo cierra más allá del nivel (def., más estricto), **o**
- `wick` — basta con que la mecha lo supere (más permisivo).
Por defecto: **cierre de cuerpo** para reducir falsos rompimientos.

**Displacement requerido (⚙️ `bos_requires_displacement = true`):** un BOS válido
debe producirse con **desplazamiento** (§20): la vela/tramo de ruptura tiene
rango ≥ `displacement_atr_mult × ATR` y preferiblemente deja un **FVG**. Un
"BOS" sin desplazamiento se marca como **débil** (menor score) o se descarta.

**Invalidación:** un BOS es nulo si inmediatamente el precio vuelve y cierra al
otro lado del nivel roto (fakeout) dentro de `bos_failure_bars`.

**Uso:** confirma que el POI en la dirección de la tendencia sigue vigente y
habilita continuaciones.

---

# 8. CHoCH — Change of Character

**Definición objetiva:** **primer** rompimiento de estructura **en contra** de la
tendencia vigente. Es la primera señal de **posible reversión**.

- **CHoCH alcista:** en tendencia bajista, el precio cierra por encima del
  **último *lower high*** relevante → la serie de LL/LH se rompe al alza.
- **CHoCH bajista:** en tendencia alcista, el precio cierra por debajo del
  **último *higher low*** relevante.

**Reglas clave:**
- El CHoCH se mide contra el **swing protegido** (el HL en alcista, el LH en
  bajista), no contra cualquier vela.
- Misma exigencia de **confirmación** (`choch_confirmation`, def. cierre de
  cuerpo) y de **displacement** (`choch_requires_displacement = true`) que el BOS.
  Un CHoCH sin desplazamiento es sospechoso de barrido, no de reversión.
- **CHoCH interno vs. mayor:** un CHoCH en estructura interna anticipa; un CHoCH
  en estructura mayor confirma el cambio de bias.

**Consecuencia:** al confirmarse un CHoCH, el motor **actualiza el bias** del
timeframe correspondiente y redefine el dealing range y los POIs. En el modelo de
entrada LTF, el CHoCH tras un barrido es el **gatillo principal** (§27).

**Distinción crítica BOS vs CHoCH:** BOS = continuación (a favor de tendencia);
CHoCH = cambio (primer rompimiento en contra). Confundirlos invierte el sesgo;
por eso ambos se calculan sobre swings etiquetados, no sobre precios sueltos.

---

# 9. Liquidez (Liquidity)

**Definición:** zonas donde se acumulan órdenes en espera (principalmente
*stop-loss* y pendientes), que el mercado tiende a **buscar y barrer**.

Tipos:
- **Buy-Side Liquidity (BSL):** por **encima** de máximos (swing highs, equal
  highs, máximos de sesión/día/semana). Ahí están los stops de vendedores y
  buy-stops.
- **Sell-Side Liquidity (SSL):** por **debajo** de mínimos (swing lows, equal
  lows, mínimos de sesión/día/semana). Stops de compradores y sell-stops.

Fuentes de liquidez que el motor mapea (⚙️ `liquidity_sources`):
- Swing highs/lows recientes (estructura).
- **Equal highs / equal lows** (§10–§11).
- Máximos/mínimos de **sesión** (Asia, Londres, NY), **día previo** (PDH/PDL),
  **semana previa** (PWH/PWL).
- Líneas de tendencia obvias / *trendline liquidity* (opcional, `enable_trendline_liquidity`).

**Salida del módulo:** conjunto de niveles con `type` (BSL/SSL), `origin`
(equal/session/PDH…), `price`, `strength` (nº de toques, antigüedad, confluencia)
y estado (`intact` / `swept`). La **liquidez objetivo** (`target_liquidity`) es el
POI direccional del motor: el precio suele ir *hacia* la liquidez antes de
respetar un POI.

---

# 10. Equal Highs (EQH)

**Definición objetiva:** dos o más *swing highs* cuyos precios difieren menos que
`equal_level_tol` (⚙️ def. `0.10 × ATR` o su equivalente en pips por perfil).

- Representan un **cluster de BSL**: un imán de precio, porque acumulan buy-stops.
- **Validez:** ≥ `equal_min_touches` (def. 2) máximos dentro de la tolerancia y
  separados por al menos `equal_min_separation_bars` para no contar la misma
  oscilación.
- **Fuerza (`strength`):** aumenta con el nº de toques y la nitidez del nivel.
- **Uso:** los EQH son **objetivos de barrido** (§12) y de *take profit* (§30). El
  motor espera que el precio **tome** los EQH; su barrido suele preceder una
  reversión bajista (si hay confluencia de POI en premium).

**Invalidación:** dejan de ser objetivo una vez **barridos** (pasan a `swept`) o
si el precio cierra decididamente por encima consolidando (dejan de ser techo).

---

# 11. Equal Lows (EQL)

Espejo de §10: dos o más *swing lows* dentro de `equal_level_tol`.

- Cluster de **SSL** (sell-stops): imán a la baja.
- Mismas reglas de validez, fuerza e invalidación que EQH.
- **Uso:** objetivo de barrido a la baja; su sweep con confluencia de POI en
  **discount** suele preceder una reversión alcista, y sirven de **objetivo TP**
  para posiciones cortas.

El par EQH/EQL define frecuentemente los extremos de un **rango** cuyos límites
serán barridos secuencialmente (patrón *turtle soup* / *stop hunt* de rango).

---

# 12. Liquidity Sweeps (barridos de liquidez / stop hunts)

**Definición objetiva:** el precio **penetra** un nivel de liquidez (§9–§11) y
**vuelve a cerrar dentro**, dejando una **mecha de rechazo**. Es la firma de la
manipulación previa al movimiento real.

Regla de detección (⚙️):
- La vela (o secuencia ≤ `sweep_confirm_bars`, def. 1–2) hace un `high`/`low`
  que **supera** el nivel por al menos `sweep_min_penetration` (def. `0.05 × ATR`),
  **pero** el `close` queda **de vuelta** al lado interior del nivel.
- **Mecha dominante:** la mecha que barre debe ser ≥ `sweep_wick_ratio` (def. 0.5)
  del rango de la vela → rechazo real, no cierre más allá.
- El nivel barrido pasa a estado `swept` y se registra el `sweep_event`
  (nivel, dirección, penetración, hora, sesión).

**Interpretación:**
- Sweep de **SSL** (barre mínimos) + estar en **discount** + CHoCH alcista LTF →
  contexto **long**.
- Sweep de **BSL** (barre máximos) + **premium** + CHoCH bajista LTF → contexto
  **short**.

El *liquidity sweep* es, junto al CHoCH, el **corazón del timing** del motor: no
se entra "en soporte/resistencia", se entra **después de que la liquidez opuesta
ha sido tomada**. Un POI que se activa **sin** sweep previo tiene menor score.

---

# 13. Order Blocks (OB)

**Definición objetiva:** la **última vela contraria** antes de un movimiento
**impulsivo con desplazamiento** que rompe estructura (BOS/CHoCH). Marca la zona
donde la institución dejó órdenes.

- **Bullish OB:** última vela **bajista** (close < open) antes de un impulso
  alcista que produce desplazamiento y/o BOS/CHoCH al alza.
- **Bearish OB:** última vela **alcista** antes de un impulso bajista equivalente.

**Zona del OB (⚙️ `ob_zone_mode`):**
- `body` — de `open` a `close` (más estricto), o
- `full` — de `high` a `low` (incluye mechas, def.).
Se puede refinar con el 50 % del OB (`ob_use_mean_threshold`) como nivel de
entrada de precisión.

**Condiciones de validez (⛔ un OB no cuenta si falla):**
1. **Desplazamiento posterior:** el impulso siguiente tiene rango ≥
   `displacement_atr_mult × ATR`.
2. **Rompe estructura o deja FVG:** el impulso genera BOS/CHoCH y/o un **FVG**
   (imbalance) — señal de intención institucional.
3. **Sin mitigar (`unmitigated`):** el precio **no** ha vuelto aún a la zona (o
   solo parcialmente, según `ob_mitigation_threshold`). Un OB ya mitigado pierde
   validez o baja de score.

**Refinamiento MTF:** un OB de HTF se **refina** buscando el OB/FVG de LTF dentro
de su rango para una entrada de menor riesgo.

**Invalidación:** el OB se anula si el precio lo **atraviesa y cierra** al otro
lado con cuerpo (`ob_break_invalidates`) — momento en que puede convertirse en
**breaker** (§14).

---

# 14. Breaker Blocks

**Definición objetiva:** un **order block que falló** y fue **atravesado**, y que
tras el cambio de estructura pasa a actuar en **sentido contrario**.

Formación:
1. Existe un OB (p.ej. **bullish OB**).
2. El precio lo **rompe y cierra** al otro lado (falla como soporte).
3. Se produce un **CHoCH** en esa dirección → cambio de carácter.
4. El antiguo OB, ahora **superado**, se convierte en **bearish breaker**: al
   reaccionar el precio de vuelta a esa zona, se espera rechazo a la baja.

- **Bullish breaker:** nace de un *bearish OB* que fue roto al alza tras CHoCH
  alcista; ofrece soporte.
- **Bearish breaker:** nace de un *bullish OB* roto a la baja tras CHoCH bajista;
  ofrece resistencia.

**Validez:** requiere el CHoCH que confirma el cambio y, preferentemente, un
**sweep** previo de la liquidez que originó la ruptura. Es un POI de **reversión**
de alta calidad porque combina fallo de estructura + toma de liquidez.

**Diferencia con OB:** el OB opera **a favor** de la vela que lo formó; el breaker
opera **en contra** (el OB "se dio la vuelta"). El motor los etiqueta distinto y
les asigna distinta lógica direccional.

---

# 15. Mitigation Blocks

**Definición objetiva:** POI que se forma cuando la estructura cambia **sin haber
tomado** la liquidez previa (no hubo sweep del extremo anterior). La **última
vela contraria** del movimiento fallido queda como zona de **mitigación**: la
institución vuelve a ella para *mitigar* (equilibrar) posiciones antes de
continuar.

- **Bullish mitigation block:** en un contexto alcista, el precio hace un mínimo
  **más alto** (no barre el mínimo previo) y rompe al alza; la última vela
  bajista antes del impulso es el mitigation block de soporte.
- **Bearish mitigation block:** simétrico, con máximo más bajo que no toma el
  máximo previo.

**Clave diferenciadora (OB vs Breaker vs Mitigation):**
| POI | Origen | Liquidez previa | Dirección de uso |
|-----|--------|-----------------|------------------|
| **Order Block** | Última vela contraria antes de impulso que rompe estructura | Indiferente | A favor del impulso |
| **Breaker** | OB **roto** + CHoCH | Suele haber sweep | En contra del OB original |
| **Mitigation** | Cambio de estructura **sin** tomar liquidez previa | **No** hubo sweep | A favor del nuevo impulso |

**Validez e invalidación:** análogas al OB (desplazamiento, sin mitigar, se anula
al cerrarse a través). El motor prioriza OB/Breaker con sweep sobre mitigation
blocks (menor score relativo por ausencia de toma de liquidez).

---

# 16. Fair Value Gaps (FVG)

**Definición objetiva:** **imbalance** de tres velas donde queda un "hueco" de
precio sin negociar por ambos extremos. Representa ineficiencia que el mercado
tiende a **rellenar**.

- **Bullish FVG:** en el patrón de 3 velas `[c1, c2, c3]`, se cumple
  `low(c3) > high(c1)`. La zona del gap es `[high(c1), low(c3)]`.
- **Bearish FVG:** `high(c3) < low(c1)`. Zona `[high(c3), low(c1)]`.

**Filtros de validez (⚙️):**
- **Tamaño mínimo:** amplitud del gap ≥ `fvg_min_size` (def. `0.10 × ATR`) para
  descartar micro-gaps irrelevantes.
- **Con desplazamiento:** el FVG debe nacer de una vela de impulso (c2 con rango ≥
  `displacement_atr_mult × ATR`) → `fvg_requires_displacement = true`.
- **Estado de relleno:** `unfilled` / `partially_filled` (hasta el 50 %,
  `fvg_consequent_encroachment`) / `filled`. El nivel del **50 % del FVG**
  (*consequent encroachment*) es el punto de entrada de precisión.

**Uso:** el FVG es a la vez **POI de entrada** (el precio regresa al gap dentro de
un OB) y **confirmación de intención** (su presencia valida el desplazamiento de
un BOS/CHoCH/OB). Un OB **con** FVG asociado puntúa más que uno sin él.

**Invalidación:** un FVG se considera cerrado (deja de ser POI) cuando el precio
lo **rellena por completo** y cierra al otro lado; ahí puede convertirse en IFVG.

---

# 17. Inverse Fair Value Gaps (IFVG)

**Definición objetiva:** un FVG que es **violado** (rellenado y superado con
cierre) **invierte su polaridad** y pasa a actuar como zona de soporte/resistencia
en **sentido contrario**.

- Un **bullish FVG** que el precio **atraviesa y cierra por debajo** se convierte
  en **bearish IFVG**: al retornar el precio a esa zona, se espera rechazo a la
  baja.
- Un **bearish FVG** superado al alza se convierte en **bullish IFVG** (soporte).

**Validez:** requiere **cierre** a través del FVG original (no solo mecha) y gana
fuerza si coincide con un **CHoCH** (el cambio de carácter y la inversión del FVG
cuentan la misma historia). Es un POI de **reversión/confirmación** muy usado en
LTF para entradas tras el barrido.

**Uso combinado:** el modelo de entrada premium (§27) frecuentemente busca:
*sweep → CHoCH → un FVG que se invierte (IFVG) → entrada en el IFVG*.

---

# 18. Premium y Discount (Equilibrio)

El motor **nunca** compra caro ni vende barato: pondera dónde está el precio
dentro del **dealing range** vigente.

**Dealing range:** definido por el swing high y swing low relevantes del
timeframe (por defecto el último *leg* impulsivo o el rango del bias HTF).
Se traza un Fibonacci sobre él:

| Zona | Rango (% del range) | Sesgo operativo |
|------|---------------------|-----------------|
| **Premium** | 50 % – 100 % (parte alta) | Favorece **ventas** |
| **Equilibrium** | ~50 % (± `equilibrium_band`, def. 5 %) | Zona neutra; evitar operar |
| **Discount** | 0 % – 50 % (parte baja) | Favorece **compras** |

**Reglas (⚙️/⛔):**
- `require_discount_for_longs = true`: los **longs** solo se validan si el POI
  está en **discount** (o al menos por debajo del equilibrio).
- `require_premium_for_shorts = true`: los **shorts** solo en **premium**.
- Operar en `equilibrium` está penalizado o vetado (`avoid_equilibrium`).

Esto filtra entradas "a favor de tendencia pero a mal precio" (p.ej. comprar en
premium dentro de una tendencia alcista), que son las de peor RR.

---

# 19. Zonas OTE (Optimal Trade Entry)

**Definición objetiva:** subzona de retroceso de Fibonacci sobre el *leg*
impulsivo donde el motor busca la entrada de mejor riesgo/beneficio.

- **OTE window:** retroceso **`0.618` – `0.786`** del impulso (⚙️ `ote_low=0.618`,
  `ote_high=0.786`), con ***sweet spot*** en **`0.705`** (`ote_optimal`).
- En **long**: se mide del mínimo (0.0) al máximo (1.0) del impulso alcista; la
  OTE cae en la parte baja (discount) → zona de compra.
- En **short**: espejo, OTE en la parte alta (premium).

**Confluencia requerida:** la OTE **no** es señal por sí sola. Puntúa alto cuando
**coincide** con un POI (OB/breaker/FVG/IFVG) **dentro de discount/premium** y
tras un **sweep**. La intersección `OTE ∩ POI ∩ (discount|premium)` es el
"golden pocket" del motor y uno de los mayores aportes al score.

**Fibonacci institucional (fuente de la OTE).** La OTE es la subzona `0.618–0.786`
de un **Fibonacci anclado a estructura**, no de un Fibonacci trazado a mano. Su
especificación completa —swing origen/destino, detección automática del leg,
cuándo (y cuándo **no**) recalcular, niveles `0/0.5/0.618/0.705/0.786/1/1.272/1.618/
2.0/2.618` y combinación con OB, FVG, sweeps y BOS/CHoCH— está en el **Smart Money
Engine Bible, detector D32 (Fibonacci Institucional)**. **Regla dura (⛔):**
`fib_standalone_entry = false` — **Fibonacci nunca genera una entrada por sí solo**;
es siempre una **confirmación adicional dentro del Scoring Engine** (§26). Los
niveles de proyección `1.272/1.618/2.0/2.618` alimentan los objetivos de TP (§30),
subordinados a la liquidez.

---

# 20. Impulsos y retrocesos (Displacement vs. Retracement)

Distinguir **movimiento institucional** (impulso/desplazamiento) de **corrección**
(retroceso) es esencial para no confundir un barrido con una reversión.

**Impulso / Displacement (objetivo):**
- Rango del tramo ≥ `displacement_atr_mult × ATR` (⚙️ def. 1.5).
- Velas con **cuerpos dominantes** (body/range ≥ `displacement_body_ratio`, def.
  0.6) y **consecutivas** en la misma dirección.
- Genera **FVG** y/o rompe estructura (BOS/CHoCH).
- Opcional: pico de **volumen** (§21) confirmando.

**Retroceso / Retracement (objetivo):**
- Velas **solapadas**, cuerpos pequeños, sin FVG relevante.
- Profundidad medida en % del impulso (para OTE, §19).
- El motor asume que tras un impulso viene un retroceso hacia un POI, y ahí busca
  reanudar en la dirección del impulso.

**Uso:** solo se operan **continuaciones tras retroceso a POI** o **reversiones
tras sweep + CHoCH**. Nunca se persigue el impulso "a mercado" fuera del POI
(`no_chasing = true`).

---

# 21. Volumen

El volumen **confirma**, no lidera.

- **Fuente por perfil:** Forex/Oro usan **tick volume** (proxy de actividad);
  Índices/Cripto pueden usar **volumen real**. El perfil declara `volume_source`.
- **Uso principal:** validar **desplazamiento**. Un impulso/BOS/CHoCH acompañado
  de **spike de volumen** (`volume >= volume_ma × volume_spike_mult`, def. 1.5,
  sobre `volume_ma_period`, def. 20) suma score; sin volumen, el desplazamiento es
  más sospechoso.
- **Divergencias:** volumen decreciente en la extensión + sweep puede reforzar la
  tesis de reversión.
- **Limitación reconocida:** en FX el tick volume no es volumen real; por eso el
  volumen es **factor de apoyo de bajo peso**, nunca condición dura. En cripto su
  peso puede subir por perfil.

---

# 22. ATR (Average True Range)

El ATR es el **termómetro de volatilidad** y la **unidad de medida** del motor.

- Cálculo: `atr_period` (⚙️ def. 14) sobre el timeframe relevante (normalmente
  MTF/LTF para gestión, HTF para régimen).
- **Usos:**
  1. **Unidad universal**: casi todos los umbrales se expresan en múltiplos de
     ATR (tolerancias, tamaños de FVG, buffers de SL) → el motor se **adapta**
     solo a cada activo y régimen.
  2. **Dimensionar el Stop Loss** (§29): `SL = swing/POI ± atr_sl_mult × ATR`.
  3. **Filtro de régimen** (§35): si el ATR está **fuera** de
     `[atr_regime_min, atr_regime_max]` (⚙️, relativo a su media), el mercado está
     demasiado plano o demasiado errático → penalización o veto.
  4. **Objetivos** (§30): proyección mínima de TP en múltiplos de ATR además de
     los objetivos de liquidez.

---

# 23. Spread

El spread es coste y señal de condiciones de mercado.

- **Medición:** spread actual del símbolo en tiempo real.
- **Filtro duro (⛔ `max_spread` por perfil):** si `spread > max_spread` (absoluto
  en pips **o** `> max_spread_atr × ATR`, lo que aplique al perfil), **no se
  ejecuta** ninguna entrada. Protege de aperturas, baja liquidez y picos de
  noticias.
- **Ajuste de SL/TP:** el spread se incorpora al cálculo de niveles y a la
  distancia mínima al SL (evitar SL demasiado ajustado que el spread dispararía).
- **Registro:** el spread en el momento de la decisión se guarda en el decision
  record (§39) para análisis de *slippage* y calidad de ejecución.

---

# 24. Horarios institucionales (Sessions & Killzones)

El **cuándo** es tan importante como el **qué**. El motor opera preferentemente en
las ventanas de mayor actividad institucional (⚙️ `killzones`, en `session_timezone`
del perfil; referencia por defecto: hora de Nueva York / Londres).

| Sesión / Killzone | Ventana (ref. NY) | Carácter |
|-------------------|-------------------|----------|
| **Asia** | ~20:00 – 00:00 | Acumulación / rango; define liquidez para Londres |
| **London Killzone** | ~02:00 – 05:00 | Alta expansión; barridos de Asia; setups A+ |
| **New York Killzone** | ~07:00 – 10:00 | Segunda expansión; solapamiento con Londres; noticias USD |
| **Silver Bullet** | 10:00 – 11:00 | Ventana de precisión intradía |
| **London Close** | ~10:00 – 12:00 | Reversiones / toma de beneficios |

**Reglas (⚙️):**
- `trade_only_in_killzones = true` (def.): fuera de killzone, el score se penaliza
  o se veta según `outside_killzone_policy`.
- Se registran **máximos/mínimos de sesión** como liquidez (§9).
- **Perfil cripto (futuro):** al ser 24/7, las killzones clásicas se sustituyen o
  complementan por ventanas de mayor volumen observado (`crypto_activity_windows`).

---

# 25. Filtro de noticias económicas

Las noticias de alto impacto rompen la microestructura: spreads, gaps, barridos
erráticos. El motor las trata como **veto temporal**.

- **Fuente:** calendario económico (`news_provider` por perfil) con impacto
  (alto/medio/bajo) y divisas afectadas.
- **Ventana de bloqueo (⛔):** no abrir nuevas posiciones desde
  `news_block_before` (def. 15 min) **antes** hasta `news_block_after` (def. 15 min)
  **después** de un evento de **alto impacto** que afecte a las divisas del símbolo.
- **Gestión de posiciones abiertas durante noticias** (⚙️ `news_open_position_policy`):
  opciones: *mantener con SL protegido*, *reducir a BE*, o *cerrar* antes del
  evento. Por defecto: mover a **break-even** si es posible y **prohibir nuevas
  entradas**.
- **Registro:** cada veto por noticias queda en el decision log con el evento que
  lo motivó (explicabilidad, §40).

---

# 26. Scoring Engine (Sistema de puntuación) — **núcleo de la decisión**

El motor **no** opera por reglas binarias aisladas: agrega la evidencia en un
**score de confluencia** (0–100). Cada criterio aporta puntos; solo se opera si el
total **supera un umbral configurable** `entry_score_threshold` (⚙️ def. 70) **y**
se cumplen todos los **vetos duros** (§35/§37).

### 26.1 Principios del scoring
- **Ponderado y configurable:** cada factor tiene un peso (`weight_*`) editable por
  perfil/estrategia. La suma de pesos máximos = 100.
- **Direccional:** el score se calcula por lado (long/short). Se opera el lado con
  score válido; si ambos, gana el mayor y solo si el otro está por debajo de
  `opposite_max_score`.
- **Vetos ≠ puntos:** los vetos (⛔) **anulan** la operación aunque el score sea
  alto (no restan; **bloquean**).
- **Explicable:** el desglose por factor se guarda íntegro (§40).

### 26.2 Tabla de puntuación (pesos por defecto, ⚙️)

| # | Factor | Condición objetiva para puntuar | Peso máx. |
|---|--------|----------------------------------|-----------|
| 1 | **HTF Bias alignment** | Dirección alineada con bias HTF (§5–§6) | 15 |
| 2 | **Estructura LTF (CHoCH/BOS)** | CHoCH (reversión) o BOS (continuación) confirmado con desplazamiento (§7–§8) | 15 |
| 3 | **Liquidity sweep** | Barrido de la liquidez opuesta antes del setup (§12) | 12 |
| 4 | **Calidad del POI** | OB/Breaker/Mitigation válido y sin mitigar (§13–§15) | 12 |
| 5 | **Imbalance (FVG/IFVG)** | FVG/IFVG con desplazamiento en confluencia con el POI (§16–§17) | 10 |
| 6 | **Premium/Discount** | POI en discount (long) / premium (short) (§18) | 8 |
| 7 | **OTE / Fibonacci** | Entrada en la ventana OTE 0.618–0.786 del Fibonacci institucional (§19, D32). Fibonacci solo **confirma**, nunca gatilla | 6 |
| 8 | **Killzone / sesión** | Setup dentro de killzone institucional (§24) | 8 |
| 9 | **Régimen ATR + Spread OK** | ATR en rango operable y spread aceptable (§22–§23) | 6 |
| 10 | **Volumen confirmando** | Spike de volumen en el desplazamiento (§21) | 4 |
| 11 | **Contexto de liquidez objetivo** | Existe liquidez clara como objetivo/TP en la dirección (§9) | 4 |
|   | **TOTAL** | | **100** |

> Los pesos son un punto de partida institucional (estructura + liquidez + POI
> concentran ~66 % del peso, que es donde vive la ventaja). Se recalibran con
> backtesting/walk-forward (ENG-004) y quedan versionados como configuración.

### 26.3 Vetos duros (⛔ — anulan la operación con cualquier score)
- Spread > `max_spread` (§23).
- Ventana de noticias de alto impacto activa (§25).
- Fuera de killzone con `outside_killzone_policy = veto` (§24).
- ATR fuera de régimen (`atr_regime`) (§22/§35).
- Conflicto de bias HTF con `require_htf_alignment = true` (§5).
- Límite de riesgo alcanzado: pérdida diaria máxima, nº máx. de operaciones,
  bloqueo por drawdown (§34).
- Datos degradados / feed no fiable (§38).

### 26.4 Umbrales y modos (⚙️)
- `entry_score_threshold` (def. 70): mínimo para operar.
- `high_conviction_threshold` (def. 85): habilita tamaño de riesgo superior
  (dentro de los límites) y/o objetivos más ambiciosos.
- Bandas: **< 55** descartar · **55–69** *watchlist* (no operar, seguir) ·
  **70–84** operar estándar · **≥ 85** alta convicción.

### 26.5 Ejemplo trabajado (long)
> Setup: sweep de EQL en discount durante London KZ, CHoCH alcista LTF con FVG,
> POI = bullish OB sin mitigar en OTE 0.705, ATR normal, spread OK, sin noticias.
>
> Puntos: HTF align 15 · CHoCH 15 · sweep 12 · POI 12 · FVG 10 · discount 8 ·
> OTE 6 · killzone 8 · ATR/spread 6 · volumen 4 · liquidez objetivo 4 = **100**.
> Vetos: ninguno. → **Score 100 ≥ 85 → entrada de alta convicción.** El desglose
> completo se guarda para explicabilidad.

---

# 27. Gestión de entrada (Entry Management)

**Modelo de entrada canónico (A+):** una vez el precio alcanza el POI dentro del
bias, la secuencia LTF es:

1. **Sweep** de la liquidez opuesta al POI (§12).
2. **CHoCH** en LTF confirmando el giro (o **BOS** si es continuación) (§7–§8).
3. **Retorno a POI** (OB/breaker/mitigation) en **discount/premium** y, si es
   posible, en **OTE** (§18–§19).
4. **Confirmación de imbalance**: FVG a favor o **IFVG** (§16–§17).
5. **Score ≥ umbral** y **sin vetos** (§26).

**Tipos de orden de entrada (⚙️ `entry_execution_mode`):**
- **`confirmation` (def.):** entrada a mercado tras el cierre de la vela de
  confirmación (CHoCH/FVG) → menos entradas falsas, algo más de coste.
- **`limit`:** orden límite en el 50 % del OB/FVG (*mean threshold* / consequent
  encroachment) → mejor precio, riesgo de no ejecución.
- **`stop`:** entrada por ruptura tras confirmar desplazamiento → para
  continuaciones agresivas.

**Reglas:**
- **Sin perseguir** (`no_chasing`): si el precio se aleja del POI más de
  `max_entry_distance_atr`, se anula el setup.
- **Una entrada por POI** (`one_entry_per_poi`) salvo escalado configurado.
- **Timeout de setup** (`setup_expiry_bars`): si tras el sweep no llega la
  confirmación en N velas, el setup caduca.
- El precio de entrada, tipo de orden, score y contexto se registran (§39).

---

# 28. Gestión de salida (Exit Management)

La salida es un **sistema**, no un único TP. Combina objetivos de liquidez,
múltiplos de riesgo (R) y gestión dinámica.

Componentes (detallados en §29–§33):
- **Stop Loss** estructural (§29).
- **Take Profit** en niveles de liquidez / RR (§30).
- **Break-even** al alcanzar un múltiplo de R o nueva estructura (§31).
- **Trailing** por estructura o ATR (§32).
- **Cierre parcial** escalonado (§33).

**Filosofía:** asegurar que un porcentaje de operaciones ganadoras se protege
pronto (BE + parcial) mientras se deja correr una porción hacia objetivos de
liquidez lejanos para elevar el RR medio. Toda modificación de la posición
(mover SL, parcial, trailing) se **registra con su motivo** (§39–§40).

---

# 29. Stop Loss

**Colocación objetiva (⚙️ `sl_mode`):**
- **`structure` (def.):** justo **más allá del extremo que invalida el setup**:
  el otro lado del OB/POI o la mecha del **sweep** que originó la entrada, con un
  **buffer** = `atr_sl_mult × ATR` (def. 0.3) + spread.
  - Long: `SL = min(POI_low, sweep_low) − buffer`.
  - Short: `SL = max(POI_high, sweep_high) + buffer`.
- **`atr`:** `SL = entry ∓ atr_sl_mult × ATR` (cuando la estructura no ofrece un
  nivel limpio).

**Reglas:**
- **Distancia mínima** (`min_sl_distance`) para no ser barrido por spread/ruido.
- **Distancia máxima** (`max_sl_atr`): si el SL estructural es demasiado amplio,
  el setup se descarta o se refina en LTF (protege el RR).
- El SL define el **1R** de la operación y es la base del *position sizing* (§34).
- El SL **nunca se amplía** una vez abierto (`never_widen_sl = true` ⛔); solo se
  reduce (BE/trailing).

---

# 30. Take Profit

**Objetivos, en orden de prioridad (⚙️ `tp_strategy`):**
1. **Liquidez opuesta** (§9): el TP natural es la **próxima piscina de liquidez**
   (EQH/EQL, PDH/PDL, swing opuesto) hacia la que se dirige el precio. Es el
   objetivo institucional real.
2. **Múltiplos de R:** TP escalonados en `tp_r_multiples` (def. `[1R, 2R, 3R]`)
   para el cierre parcial (§33).
3. **Proyección estructural:** siguiente swing/POI relevante en la dirección.
4. **Mínimo de calidad (⛔):** no se opera si el objetivo de liquidez más cercano
   ofrece `RR < min_rr` (def. 2.0) → filtra setups sin recorrido.

**Reglas:**
- **TP final** en la liquidez mayor coherente con el bias HTF.
- Si aparece **liquidez contraria** relevante antes del TP (nuevo POI opuesto de
  alto score), el motor puede **cerrar anticipadamente** (`smart_tp_on_opposite_poi`).
- Todos los TP y su lógica se registran (§39).

---

# 31. Break Even

**Disparador (⚙️ `be_trigger`):**
- **Por R:** al alcanzar `be_at_r` (def. 1.0R) de beneficio no realizado, mover el
  SL a **entrada + `be_offset`** (def. spread + `0.1 × ATR`, para cubrir costes).
- **Por estructura:** al formarse un nuevo HL (long) / LH (short) que confirma
  continuación, mover SL bajo/sobre ese nuevo swing.

**Reglas:**
- BE **no** se activa antes del disparador (evita ahogar la operación en el ruido
  inicial).
- Compatible con cierre parcial: habitualmente el **parcial en TP1** y el **BE**
  se ejecutan juntos, dejando el resto "a riesgo cero".
- El evento BE se registra con su causa.

---

# 32. Trailing Stop

Solo tras BE y/o primer parcial (⚙️ `trailing_enabled`, `trailing_mode`):

- **`structure` (def.):** el SL se arrastra por **debajo de cada nuevo HL** (long)
  / **encima de cada nuevo LH** (short) confirmado. Sigue la lógica SMC, no una
  distancia fija.
- **`atr`:** SL = precio − `atr_trail_mult × ATR` (def. 1.5), recalculado por vela
  cerrada; solo se mueve a favor (nunca en contra).
- **`fvg`:** arrastra el SL detrás del último FVG/IFVG a favor mientras el impulso
  crea imbalances.

**Reglas:** el trailing **solo aprieta** (monótono). Se registra cada ajuste. Se
desactiva cerca del TP final para no cortar el objetivo por ruido.

---

# 33. Cierre parcial (Scaling Out)

**Esquema por defecto (⚙️ `partial_plan`):**

| Nivel | Disparador | Acción |
|-------|-----------|--------|
| TP1 | `1R` o primera liquidez menor | Cerrar `partial_1_pct` (def. 50 %) + mover SL a **BE** |
| TP2 | `2R` o liquidez intermedia | Cerrar `partial_2_pct` (def. 25 %) + activar **trailing** |
| Runner | Liquidez mayor / TP final | Dejar correr el resto (def. 25 %) con trailing por estructura |

**Objetivo:** maximizar la **expectativa** — asegurar beneficio temprano
(mejora win-rate efectivo) y capturar movimientos extendidos con el *runner*
(mejora RR medio). Los porcentajes y niveles son configurables por perfil y
por convicción (score ≥ `high_conviction_threshold` puede dejar *runner* mayor).
Cada parcial se registra (cantidad, precio, R alcanzado, motivo).

---

# 34. Gestión del riesgo

El riesgo se gobierna en **tres capas**: por operación, por día y global. Es la
autoridad suprema: **cualquier límite alcanzado es un veto (⛔)** que ignora
cualquier score.

### 34.1 Por operación
- **Riesgo fijo fraccional:** `risk_per_trade_pct` del equity (⚙️ def. 0.5 %;
  máx. recomendado 1 %). Alta convicción puede subir hasta `risk_per_trade_max_pct`
  (def. 1 %) sin superar el tope.
- **Position sizing** (determinista):
  `size = (equity × risk_per_trade_pct) / (sl_distance_in_price × value_per_unit)`.
  El tamaño se deriva del **SL**, nunca al revés.
- **RR mínimo** `min_rr` (def. 2.0) exigido para abrir.

### 34.2 Por día / sesión
- **Pérdida diaria máxima** `max_daily_loss_pct` (def. 2–3 %): alcanzada →
  **stop-trading** hasta el próximo día (⛔).
- **Máx. operaciones/día** `max_trades_per_day`.
- **Objetivo diario opcional** `daily_profit_lock`: al alcanzarlo, reducir riesgo
  o detener (proteger el día verde).

### 34.3 Global / cartera
- **Drawdown lockout** `max_drawdown_pct` (def. 10 %): alcanzado → bloqueo total y
  alerta (requiere intervención/revisión) (⛔).
- **Máx. posiciones concurrentes** `max_open_positions`.
- **Riesgo correlacionado** `max_correlated_risk`: limitar exposición agregada en
  símbolos correlacionados (p.ej. EURUSD y GBPUSD, o Oro y DXY) para no multiplicar
  el mismo riesgo.
- **Riesgo total abierto** `max_total_open_risk_pct`: suma de riesgos vivos acotada.

### 34.4 Kill-switch
Mecanismo de parada de emergencia (global, por símbolo o por estrategia) que
cierra/deja de abrir ante anomalías (§38) o por orden manual (integra con el
kill-switch de plataforma, SEC/OPS).

---

# 35. Situaciones donde el bot NO debe operar

Lista de **vetos y penalizaciones** (⛔ = veto duro):

- ⛔ Ventana de **noticias** de alto impacto (§25).
- ⛔ **Spread** por encima de `max_spread` (§23).
- ⛔ **ATR fuera de régimen**: mercado **plano** (`ATR < atr_regime_min`) o
  **caótico** (`ATR > atr_regime_max`) (§22).
- ⛔ **Fuera de killzone** si `outside_killzone_policy = veto` (§24).
- ⛔ **Conflicto de bias HTF/LTF** con alineación requerida (§5).
- ⛔ **Límites de riesgo** alcanzados: pérdida diaria, drawdown, nº de operaciones,
  riesgo total (§34).
- ⛔ **Datos degradados**: gaps de feed, velas ausentes, cotización obsoleta
  (`stale_quote`), desconexión (§38).
- ⛔ **Estructura ambigua**: sin swings claros, rango estrecho/solapado
  (`structure_confidence < min_structure_confidence`).
- Penalización (no veto): setup **sin sweep** previo, POI **ya mitigado**,
  entrada en **equilibrium**, ausencia de FVG en el desplazamiento, volumen que no
  confirma → todo esto **baja el score** y suele dejar el setup por debajo del
  umbral.

---

# 36. Condiciones de mercado ideales

El "A+ setup" que el motor busca (score alto, RR alto):

- Bias HTF **claro** (estructura HH/HL o LH/LL nítida) y precio retrocediendo a un
  **POI HTF sin mitigar**.
- Precio en **discount** (para long) / **premium** (para short) del dealing range.
- **Barrido** reciente de liquidez opuesta (EQH/EQL, sesión, PDH/PDL) con **mecha
  de rechazo**.
- **CHoCH** LTF con **desplazamiento** y **FVG** a favor.
- POI de calidad: **OB o breaker** con FVG, idealmente en **OTE**.
- Dentro de **killzone** (London/NY).
- **ATR** en régimen normal, **spread** bajo, **sin noticias** inminentes.
- **Objetivo de liquidez** claro y lejano → RR ≥ 2–3.

En estas condiciones el score tiende a ≥ 85 (alta convicción) y se autoriza el
tamaño de riesgo superior dentro de los límites.

---

# 37. Condiciones prohibidas

Contextos donde operar es sistemáticamente desfavorable (⛔ por defecto):

- **Consolidación/rango sin extremos claros** (chop): estructura solapada, ATR
  bajo → alta probabilidad de barridos dobles.
- **Volatilidad extrema no direccional**: velas enormes en ambos sentidos, ATR
  por encima del régimen (post-noticia, pánico).
- **Baja liquidez**: cierre de viernes, festivos, fin/inicio de sesión sin
  actividad, roll-over → spreads amplios y movimientos erráticos.
- **Contra el bias HTF** sin un CHoCH mayor que lo justifique (no *counter-trend*
  a ciegas).
- **Persecución de precio** lejos del POI (§27).
- **Setups redundantes/correlacionados** que superan el riesgo correlacionado
  (§34).
- **Antes de eventos macro clave** (decisiones de tipos, NFP, CPI) según la
  política de noticias.

---

# 38. Gestión de errores (robustez operativa)

El motor asume que **la infraestructura falla** y define comportamiento seguro
ante cada fallo. Principio: **ante la duda, no operar y proteger lo abierto.**

| Situación | Detección | Acción del motor |
|-----------|-----------|------------------|
| **Gap / vela ausente** en el feed | Discontinuidad temporal en la serie | Marcar datos `degraded`, **veto de nuevas entradas**, recalcular al normalizarse |
| **Cotización obsoleta** (`stale_quote`) | Sin ticks > `stale_quote_ms` | Suspender decisiones; no ejecutar |
| **Desconexión de broker/feed** | Heartbeat perdido | Pausar; al reconectar, **reconciliar** posiciones y órdenes antes de reanudar |
| **Rechazo de orden / requote** | Respuesta del broker | Reintentar con política (`order_retry_policy`), respetar **idempotencia** (no duplicar) |
| **Fill parcial** | Cantidad < solicitada | Ajustar gestión al tamaño real; recalcular SL/size |
| **Slippage excesivo** | Precio de fill fuera de `max_slippage` | Registrar; si supera umbral, alertar y revisar el setup |
| **Desфase de estado** (posición broker ≠ interna) | Reconciliación periódica | Corregir hacia el estado del broker; alertar; posible kill-switch |
| **Excepción interna** del motor | Try/guard en cada fase | Fallar de forma segura: no abrir; mantener SL de lo abierto; log + alerta |

Toda anomalía se registra con severidad y puede activar el **kill-switch** (§34.4).
La idempotencia de órdenes (una orden nunca se ejecuta dos veces) es un invariante
compartido con el Execution Engine (ENG-006) y la seguridad (SEC-000).

---

# 39. Registro completo de decisiones (Decision Log)

**Cada evaluación del motor —opere o no— genera un `DecisionRecord` inmutable.**
Es la base de la explicabilidad (§40), del backtesting reproducible y de la
auditoría (audit ledger, SEC-000).

> **Persistencia y reproducción → Decision Replay Engine (ENG-009).** El
> `DecisionRecord` descrito aquí es el **artefacto**; el módulo core
> **Decision Replay Engine** lo persiste (append-only), lo extiende con el
> *snapshot* de mercado necesario para reconstruir la decisión, y permite
> **reproducir paso a paso** cualquier operación **o señal descartada** como una
> repetición. Todas las decisiones —incluidas las **no ejecutadas**— se registran
> con el mismo detalle. Especificación completa en
> `04-engines/decision-replay-engine-spec.md`.

**Contenido del `DecisionRecord` (esquema conceptual):**
- **Identidad y contexto:** `decision_id`, `timestamp` (UTC), `symbol`,
  `instrument_profile`, `strategy_version`, `config_hash` (hash de la config y
  pesos usados → reproducibilidad).
- **Snapshot de mercado:** timeframes, últimas velas relevantes, `atr`, `spread`,
  `volume`, sesión/killzone activa, estado de noticias.
- **Features SMC calculadas:** `trend_state`, swings, último BOS/CHoCH, POIs
  activos (tipo, zona, estado), FVG/IFVG, niveles de liquidez y su estado,
  premium/discount, OTE, sweep events.
- **Scoring:** score por lado, **desglose factor a factor** (puntos otorgados y
  por qué), umbral aplicado, **vetos evaluados** (cuáles se dispararon).
- **Resolución:** `action ∈ {enter_long, enter_short, no_trade}`, y si no opera,
  **el motivo primario** (`veto:<x>` o `score_below_threshold`).
- **Si opera:** parámetros de la orden (tipo, entry, SL, TP(s), size, riesgo %,
  RR esperado).
- **Ciclo de vida posterior (se anexa):** modificaciones (BE, trailing, parciales)
  con causa y timestamp, y **resultado final** (precio de salida, R realizado,
  duración, MAE/MFE).

**Propiedades:** append-only, versionado, correlacionable por `trace_id`,
consultable por la UI (Dashboard, DES-006) y exportable. **Nada se decide sin
dejar rastro.**

---

# 40. Explicabilidad (Explainability)

Requisito **no negociable**: después de cada decisión —entrar o no entrar— el
motor debe poder **explicar exactamente por qué**, en lenguaje claro y en datos
estructurados. Es lo que convierte a ELYON QUANT de "caja negra" en **herramienta
institucional auditable**.

> **Estándar de núcleo → Explainable AI (ENG-010).** La explicabilidad es un
> **invariante transversal** de ELYON QUANT, no una función de este motor. El
> sistema **nunca** responde *"entró porque sí"*: toda decisión explica **qué
> detectó, qué confirmó, qué descartó, el peso de cada criterio, el score, las
> reglas activadas y las reglas que bloquearon la entrada**. El motor es
> explicable **por diseño** (el scoring es una suma ponderada transparente; cada
> detector es una regla determinista), por lo que la contribución de cada factor
> es **exacta**, no una aproximación post-hoc. Todo componente de ML entra como un
> **factor explicable más**, nunca como *override* opaco. Especificación completa
> en `04-engines/explainable-ai-spec.md`.

### 40.1 Dos formatos, misma verdad
1. **Estructurado** (para máquina/UI/auditoría): el `DecisionRecord` (§39) con el
   desglose de score y vetos.
2. **Narrativo** (para humano): una explicación generada a partir del mismo
   registro, siguiendo una plantilla determinista. El Chief AI Officer puede,
   además, habilitar un **resumen en lenguaje natural** por LLM (AI Engine,
   ENG-003) **estrictamente derivado** del DecisionRecord — el LLM **narra**, no
   decide, y se le prohíbe introducir factores no presentes en el registro
   (guardrail de fidelidad).

### 40.2 Plantilla de explicación (entrada)
> **[LONG EURUSD · 2026-07-28 08:14 UTC · score 92/100 · alta convicción]**
> Entramos en largo porque: el **bias H4 es alcista** (HH/HL) y el precio retrocedió
> a un **bullish order block sin mitigar** en zona **discount** (0.71 OTE). Antes de
> la entrada se **barrió la sell-side liquidity** bajo los mínimos iguales de la
> sesión asiática (mecha de rechazo), y en M5 se confirmó un **CHoCH alcista con
> desplazamiento** que dejó un **FVG** a favor. Estamos en **London killzone**, el
> **ATR es normal**, el **spread es bajo** y **no hay noticias** de alto impacto.
> Objetivo: **buy-side liquidity** en los máximos del día previo (**RR 3.1**).
> SL bajo la mecha del barrido (1R). *Factores: bias 15, CHoCH 15, sweep 12, POI 12,
> FVG 10, discount 8, OTE 6, killzone 8, ATR/spread 6, volumen 0, liquidez 0.*

### 40.3 Plantilla de explicación (no-entrada)
> **[NO-TRADE GBPUSD · 2026-07-28 09:02 UTC · score 58/100]**
> No operamos: aunque hubo **CHoCH alcista** y **sweep de mínimos**, el precio está
> en **premium** (no en discount), el **POI ya estaba mitigado** y **no hay FVG** en
> el desplazamiento → score 58 < umbral 70. Además faltaban **11 minutos para un
> evento de alto impacto (GBP)**, lo que activó el **veto de noticias**. Seguimos el
> símbolo en *watchlist*.

### 40.4 Garantías
- **Fidelidad:** toda afirmación de la explicación **mapea** a un campo del
  DecisionRecord. Prohibido explicar con factores no registrados.
- **Cobertura 100 %:** cada decisión tiene explicación; ausencia = bug.
- **Trazable y consultable** desde el Dashboard, por operación y de forma agregada
  (p.ej. "¿por qué el motor rechazó el 70 % de los setups hoy?").

---

# Apéndice A — Catálogo de Parámetros (extracto normativo)

> Todos los parámetros son **configurables por `instrument_profile` y por
> `strategy_version`**, versionados y con `config_hash` para reproducibilidad. Los
> valores son *defaults* de diseño, a calibrar con backtesting/walk-forward.

| Grupo | Parámetro | Def. | Descripción |
|-------|-----------|------|-------------|
| Timeframes | `timeframe_triad` | H4/M15/M1 | Tríada HTF/MTF/LTF |
| Estructura | `swing_lookback_major` / `_internal` | 5 / 2 | Fractal de swings |
| Estructura | `bos_confirmation` / `choch_confirmation` | close | Cierre vs mecha |
| Displacement | `displacement_atr_mult` | 1.5 | Umbral de impulso en ATR |
| Displacement | `displacement_body_ratio` | 0.6 | Cuerpo/rango mínimo |
| Liquidez | `equal_level_tol` | 0.10·ATR | Tolerancia EQH/EQL |
| Liquidez | `equal_min_touches` | 2 | Toques para nivel igual |
| Sweep | `sweep_min_penetration` | 0.05·ATR | Penetración mínima |
| Sweep | `sweep_wick_ratio` | 0.5 | Mecha dominante |
| OB/FVG | `ob_zone_mode` | full | body/full |
| OB/FVG | `fvg_min_size` | 0.10·ATR | Tamaño mínimo de FVG |
| Premium/Discount | `equilibrium_band` | 0.05 | Banda neutra |
| OTE | `ote_low`/`ote_optimal`/`ote_high` | 0.618/0.705/0.786 | Ventana OTE |
| Volatilidad | `atr_period` | 14 | Periodo ATR |
| Volatilidad | `atr_regime_min`/`max` | por perfil | Régimen operable |
| Spread | `max_spread` / `max_spread_atr` | por perfil | Filtro de spread |
| Sesiones | `killzones`, `session_timezone` | por perfil | Ventanas institucionales |
| Noticias | `news_block_before`/`after` | 15 / 15 min | Ventana de veto |
| Scoring | `weight_*` (11 factores) | ver §26.2 | Pesos configurables |
| Scoring | `entry_score_threshold` | 70 | Umbral de entrada |
| Scoring | `high_conviction_threshold` | 85 | Umbral alta convicción |
| Entrada | `entry_execution_mode` | confirmation | confirmation/limit/stop |
| Entrada | `max_entry_distance_atr` | 1.0 | Anti-persecución |
| SL | `atr_sl_mult` | 0.3 | Buffer de SL |
| SL | `never_widen_sl` | true | Invariante |
| TP | `min_rr` | 2.0 | RR mínimo |
| TP | `tp_r_multiples` | 1R/2R/3R | Niveles de parcial |
| BE | `be_at_r` | 1.0 | Disparador BE |
| Trailing | `trailing_mode` / `atr_trail_mult` | structure / 1.5 | Modo y factor |
| Parciales | `partial_1_pct`/`_2_pct` | 50 % / 25 % | Escalado |
| Riesgo | `risk_per_trade_pct` / `_max_pct` | 0.5 % / 1 % | Riesgo por operación |
| Riesgo | `max_daily_loss_pct` | 2–3 % | Stop diario |
| Riesgo | `max_drawdown_pct` | 10 % | Lockout global |
| Riesgo | `max_open_positions` / `max_correlated_risk` | por perfil | Límites de cartera |

---

# Apéndice B — Máquina de estados de una decisión/operación

```
        ┌─────────────┐
        │  SCANNING   │  (mapea estructura, liquidez, POIs, bias MTF)
        └──────┬──────┘
               │ precio llega a POI en bias
        ┌──────▼──────┐
        │  ARMED      │  (esperando modelo de entrada en POI)
        └──────┬──────┘
     sweep + CHoCH/BOS + retorno a POI + FVG
        ┌──────▼──────┐        score < umbral / veto
        │  SCORING    ├──────────────┐
        └──────┬──────┘              │
   score ≥ umbral & sin vetos        ▼
        ┌──────▼──────┐        ┌────────────┐
        │  ENTERING   │        │  NO_TRADE  │ (registrar + explicar)
        └──────┬──────┘        └────────────┘
               │ fill
        ┌──────▼──────┐   BE / trailing / parciales (registrados)
        │  MANAGING   │◄──────────────┐
        └──────┬──────┘               │
        SL / TP / cierre inteligente  │
        ┌──────▼──────┐               │
        │  CLOSED     │ (resultado + R realizado + explicación final)
        └─────────────┘
               │
        ┌──────▼──────┐
        │  SCANNING   │  (vuelve a empezar)
        └─────────────┘

  * En cualquier estado: anomalía de datos / límite de riesgo / kill-switch
    → transición a SAFE_HALT (protege lo abierto, veta nuevas entradas).
```

---

# Apéndice C — Trazabilidad y relación con otros documentos

- **Domain Model (ARC-006)** y **Event Catalog (ARC-007):** los conceptos aquí
  (Order, POI, LiquidityLevel, DecisionRecord, Signal) se formalizan como
  agregados/eventos.
- **Smart Money Engine (ENG-002):** §6–§20 son su especificación de detección.
- **Backtesting Engine (ENG-004):** valida reproducibilidad y calibra pesos/umbral.
- **Risk Engine (ENG-005) y Execution Engine (ENG-006):** §29–§34 y §38 son su
  contrato de gestión y ejecución.
- **AI Engine (ENG-003):** §40 (narrativa) y posibles factores ML como **entradas
  al scoring** (nunca como *override* opaco; siempre explicables).
- **Testing (ENGX-005) y Traceability Matrix (BLD-003):** cada regla ⛔/⚙️ debe
  tener un test asociado.

---

> **Versión 0.1 — Borrador (🟨).** Este documento es la base normativa del cerebro
> de ELYON QUANT. Su aprobación (🟩) requiere revisión de Quant Lead, CTO, ML Lead
> y Security Lead, y es prerrequisito del gate de la fase D4. Todo cambio de
> reglas o pesos posterior se gestiona vía RFC/ADR y se re-valida en backtesting.
