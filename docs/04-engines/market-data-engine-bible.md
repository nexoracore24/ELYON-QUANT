<!--
title: ELYON QUANT — Market Data Engine Bible
id: ENG-000 (Market Data Engine — única fuente oficial de datos, SSOT)
owner: Platform/Data Lead
reviewers: [Quant Lead, CTO/Principal Architect, ML Lead, QA Lead, Security Lead]
status: draft
version: 0.1
last_updated: 2026-07-29
supersedes: formaliza el módulo `market_data` (+ `market-data-ingestor`) de ARC-002
-->

# ELYON QUANT — MARKET DATA ENGINE BIBLE

> **La única fuente oficial de datos de todo ELYON QUANT (Single Source of
> Truth).** Todos los motores —Market Context, Smart Money, Trading, Risk,
> Execution, Backtesting, Decision Replay, IA— consumen datos **exclusivamente** a
> través del Market Data Engine (MDE). **Ningún motor lee directamente del
> broker/proveedor.** El MDE garantiza **consistencia, determinismo, trazabilidad
> y reproducibilidad**: los datos, una vez **confirmados**, son **inmutables** y
> **no se repintan**.

Especificación de ingeniería de nivel institucional: define estructuras, pipeline,
máquinas de estados, contratos y pruebas que la implementación debe cumplir y los
tests deben verificar (BLD-003). No es teoría.

---

## 0. Preámbulo

### 0.1 Reglas obligatorias (invariantes ⛔)
1. **Single Source of Truth (SSOT).** El MDE es el **único** origen de precios,
   velas, spread, volumen, ATR, sesión y snapshots. Prohibido que otro motor
   consulte el broker/proveedor directamente (arquitectura ACL: solo el MDE tiene
   adaptadores a fuentes externas).
2. **Inmutabilidad tras confirmación.** Una vez una vela/tick se marca
   `CONFIRMED`, **no cambia jamás**. Las llegadas tardías no mutan lo confirmado
   (§ Out-of-Order / revisiones).
3. **Sin repintado (no-repaint).** Los consumidores estructurales operan **solo**
   sobre datos `CONFIRMED`. Lo `FORMING` (vela en curso) está marcado como tal y
   nunca se trata como definitivo.
4. **Determinismo total.** Dado el mismo flujo de entrada + misma config, el MDE
   produce **exactamente** las mismas velas/derivados/eventos (mismo orden). Sin
   reloj de pared en la lógica de ensamblado; el tiempo es el **event-time**.
5. **Reproducibilidad completa.** Cualquier estado histórico se puede **reconstruir
   bit a bit** desde el almacén versionado (`dataset_id`/`data_hash`).
6. **Auditable.** Cada dato lleva **procedencia** (proveedor, `seq`, timestamps,
   `data_hash`) y linaje; nada aparece "de la nada".
7. **Verificable por tests unitarios.** Cada componente y regla (dedup, orden,
   límites de vela, gaps…) tiene su caso determinista.

### 0.2 Concepto central: FORMING vs CONFIRMED
```
   ticks →  [ vela FORMING (en curso, MUTABLE, NO estructural) ]
                         │ cruza close_time + watermark
                         ▼
            [ vela CONFIRMED (INMUTABLE, estructural, publicada) ]
```
- **FORMING:** la vela del periodo en curso; cambia con cada tick; **solo** la usan
  gestión de posición viva y visualización — **nunca** los detectores SMC/estructura
  (que exigen `use_closed_candles`, ENG-002 §0.2).
- **CONFIRMED:** al cerrar el periodo y superar el *watermark* de latencia, la vela
  se **congela** e inmutable. Es el único dato que alimenta estructura, contexto y
  decisiones. **Aquí nace el "no-repaint".**

### 0.3 Política de timestamps (Timestamp Policy)
- **Un solo reloj interno: UTC en nanosegundos.** Todo se almacena en UTC; la zona
  horaria es solo presentación (§5).
- Cada dato lleva **tres tiempos**: `event_time` (cuándo ocurrió en la fuente —
  autoridad para ensamblar), `ingest_time` (cuándo lo recibió el MDE),
  `process_time` (cuándo se procesó). **La asignación a velas usa `event_time`.**
- **Fronteras de vela ancladas a una rejilla fija** de epoch UTC (p.ej. M15 en
  `:00/:15/:30/:45`), no al primer tick → determinismo e idempotencia.
- **Regla de frontera determinista:** un tick con `event_time == close_time`
  pertenece a la **vela siguiente** (intervalo `[open, close)` semiabierto).
- Sin `now()` de pared en la lógica; el "ahora" en backtest es el `event_time` de la
  reproducción (mismo código live/backtest).

---

## 1. Arquitectura general

### 1.1 Módulos
- `market_data` (módulo del monolito, Clean Architecture): catálogo de
  instrumentos, ensamblado canónico, calidad, almacén histórico, API de consulta,
  publicación de eventos.
- `market-data-ingestor` (servicio separado, alto *throughput*): *feed handlers* en
  streaming, escala por feed/símbolo (arquitectura §00). Empuja ticks normalizados
  al MDE por el bus.

### 1.2 Capas (de la fuente al consumidor)
```
┌──────────── FUENTES EXTERNAS (brokers / data providers) ────────────┐
│  MT5 · IB · Binance · proveedores de históricos · calendario econ.  │
└───────────────────────────────┬─────────────────────────────────────┘
        (ACL: adaptadores por proveedor — ÚNICO punto de contacto externo)
                                 ▼
   1) INGESTION      normaliza al modelo canónico (Tick), sella timestamps, seq
                                 ▼
   2) INTEGRITY      dedup · ordenación por event-time · validación · watermark
                                 ▼
   3) ASSEMBLY       Tick Engine → Candle Builder → Multi-Timeframe Builder
                                 ▼
   4) DERIVATION     Spread · Volume · ATR · Session · Liquidity/Market Snapshot
                                 ▼
   5) STORAGE        Cache (hot) · Historical Store (TS) · Data Lake (parquet, versionado)
                                 ▼
   6) DISTRIBUTION   Event Bus (eventos inmutables) + Query API (as-of, point-in-time)
                                 ▼
        CONSUMIDORES: ENG-011, ENG-002, ENG-001, ENG-005, ENG-006, ENG-004, ENG-009, ENG-003
```

### 1.3 Contrato de acceso (⛔ SSOT)
- Los consumidores **solo** hablan con **Distribution** (bus + query API). No
  conocen proveedores ni brokers.
- Live y backtest usan **la misma** API de distribución; cambia la **fuente** (feed
  vivo vs. dataset versionado), no el contrato → paridad total.

---

## 2. Flujo completo de datos y pipeline

### 2.1 Camino en vivo (live)
```
tick(broker) ─► [ACL normaliza→Tick] ─► [dedup] ─► [order/watermark] ─► [validate]
   ─► [Tick Engine actualiza vela FORMING] ─► (cierre de periodo + watermark)
   ─► [Candle CONFIRMED] ─► [MTF Builder agrega TFs superiores]
   ─► [derivados: spread/volume/ATR/session/liquidity]
   ─► [persistir + publicar BarClosed/TickReceived/SnapshotUpdated]
```

### 2.2 Camino histórico (backtest / replay)
```
dataset(dataset_id) ─► [Historical Data Manager lee versión exacta]
   ─► [reproduce ticks/velas en event-time order] ─► MISMO pipeline de ensamblado
   ─► MISMOS eventos, MISMO orden ─► consumidores (determinismo live≡backtest)
```

### 2.3 Idempotencia del pipeline
Reprocesar el mismo lote de entrada produce el **mismo** resultado (mismas velas,
mismos eventos, mismo `data_hash`). Cada etapa es una función pura del estado +
entrada (event-sourcing), sin efectos de reloj/aleatorios.

---

## 3. Data Lifecycle (ciclo de vida del dato)

### 3.1 Máquina de estados de un dato
```
   RECEIVED ──► NORMALIZED ──► DEDUPED ──► ORDERED ──► VALIDATED
                                                          │
                                       inválido ──► QUARANTINED (registrado, no publicado)
                                                          │ válido
                                                          ▼
                                                   FORMING (vela en curso, mutable)
                                                          │ close_time + watermark
                                                          ▼
                                                   CONFIRMED (INMUTABLE) ──► PERSISTED ──► ARCHIVED
                                                          │
                              llegada tardía / corrección de proveedor
                                                          ▼
                                                   REVISED (nueva VERSIÓN del dato,
                                                   dataset_id nuevo; el CONFIRMED
                                                   original NUNCA se sobrescribe)
```
- **QUARANTINED:** datos que fallan validación → aislados, registrados, **no**
  entran al ensamblado (auditables).
- **REVISED:** correcciones (proveedor re-emite, dato tardío aceptado) generan una
  **nueva versión** del dataset con procedencia; el valor confirmado original queda
  intacto → **auditoría e inmutabilidad** coexisten con la corrección.

### 3.2 Reglas de transición (⛔)
- No hay transición `CONFIRMED → (otro valor)`; solo `CONFIRMED → REVISED(nueva versión)`.
- `FORMING` nunca se publica como definitivo (marcado `provisional=true`).
- Toda transición se registra (linaje).

---

## 4. Time Synchronization (sincronización temporal)

- **Autoridad = event-time** de la fuente (cuando el proveedor lo sella). Si la
  fuente no da event-time fiable, se usa `ingest_time` marcando
  `event_time_source=ingest` (menor confianza, señal de calidad).
- **Watermark:** el MDE mantiene un *watermark* por símbolo =
  `max(event_time) − max_lateness`. Un periodo se **confirma** cuando el watermark
  supera su `close_time` → garantiza que (casi) todos los ticks de ese periodo ya
  llegaron antes de congelar. `max_lateness` es configurable por proveedor.
- **Skew de relojes:** servidores sincronizados por **NTP/PTP**; el MDE mide
  `clock_skew = ingest_time − event_time` y lo expone como métrica de calidad;
  skew excesivo → alerta y posible degradación de confianza.
- **Backtest:** no hay reloj de pared; el watermark avanza con el `event_time`
  reproducido → confirmación determinista idéntica a live.

## 5. Gestión de zonas horarias

- **Almacenamiento SIEMPRE en UTC.** La zona horaria es exclusivamente de
  **presentación** y de **fronteras de sesión/día/semana**.
- Conversión con **zonas IANA con reglas DST** (nunca offset fijo) —fuente clásica
  de bugs no deterministas (alineado con ENG-002 D31).
- **Fronteras**: día/semana/mes de negocio se definen en la zona del
  `instrument_profile`/cuenta (`boundary_timezone`), lo que fija los resets de
  Risk (ENG-005 §6–§8) y las sesiones del MCE (ENG-011 §5.F).
- Cambios de DST (semanas de desalineación EEUU/UK) resueltos por la librería IANA
  → killzones y sesiones correctas y reproducibles.

---

## 6–19. Componentes del motor

> Formato por componente: **Objetivo · Contrato I/O · Reglas/Algoritmo ·
> Casos válidos/inválidos · Edge cases**. Todos deterministas, sin `now()` de pared.

### 6. Tick Engine
- **Objetivo.** Recibir el flujo normalizado y mantener el estado por símbolo
  (último bid/ask/mid, volumen acumulado, alimentador de la vela FORMING).
- **I/O.** In: `Tick{symbol, event_time, ingest_time, bid, ask, last?, volume?, provider, seq}`.
  Out: actualización de vela FORMING + `TickReceived` (bus).
- **Reglas.** Solo procesa ticks ya `VALIDATED` y en orden por `event_time`. `mid =
  (bid+ask)/2`. No emite estructura (solo alimenta).
- **Válidos.** Tick con bid≤ask, event_time ≥ watermark-permitido → aplicado.
- **Inválidos.** `bid>ask` (cruzado), precio ≤0, salto > `max_tick_jump` → QUARANTINE.
- **Edge.** Ráfagas de ticks mismo `event_time` → orden estable por `seq`
  (determinista). Tick sin volumen (FX puede dar solo tick-count).

### 7. Candle Builder
- **Objetivo.** Ensamblar velas OHLCV deterministas desde ticks.
- **I/O.** In: ticks + `timeframe` base. Out: `Candle{symbol, tf, open_time,
  close_time, o,h,l,c, volume, tick_count, state, data_hash}`.
- **Algoritmo.**
```
def onTick(tick, tf):
    bucket = floor(tick.event_time / tf.duration) * tf.duration     # rejilla fija UTC
    c = forming[symbol][tf]
    if c is null or bucket != c.open_time:
        if c: confirm(c)                                            # cierra la anterior
        c = new Candle(open=bucket, close=bucket+tf.duration, o=h=l=price(tick))
    price = cfg.candle_price(tick)          # bid|ask|mid (fijo por dataset)
    c.h = max(c.h, price); c.l = min(c.l, price); c.c = price
    c.volume += tick.volume; c.tick_count += 1
    forming[symbol][tf] = c

def confirm(c):     # al superar watermark el close_time
    c.state = CONFIRMED; c.data_hash = hash(c); persist(c); publish(BarClosed(c))
```
- **Reglas (⛔).** `candle_price` (bid/ask/mid) **fijo por dataset** (no cambiar a
  media sesión). OHLC solo de ticks del bucket. Confirmación por watermark, no por
  "primer tick del siguiente bucket".
- **Válidos.** Serie de ticks → velas alineadas a rejilla, `h≥o,c` y `l≤o,c`.
- **Inválidos.** Vela con `h<l` o sin ticks marcada CONFIRMED (ver Missing Data).
- **Edge.** **Vela sin ticks** (mercado quieto) → política `empty_candle_policy`:
  `skip` (no existe), o `synthetic` (o=h=l=c=último close, `tick_count=0`,
  `synthetic=true` — marcada, nunca "inventada" en silencio). Gap de fin de semana →
  no se rellenan buckets inexistentes.

### 8. Multi-Timeframe Builder
- **Objetivo.** Derivar TFs superiores (M5→M15→H1→H4→D1) de forma consistente.
- **I/O.** In: velas confirmadas del TF base (o de un TF inferior). Out: velas
  confirmadas de TFs superiores.
- **Reglas (⛔).** Un TF superior se **agrega desde TFs inferiores ya CONFIRMED**
  (no desde ticks en paralelo) → **coherencia total** entre TFs (el H1 es
  exactamente las cuatro M15 que lo componen). Alineación de fronteras anidada.
- **Válidos.** 4×M15 confirmadas → 1×H1 con `o=primera.o, c=última.c, h=max, l=min,
  vol=Σ`.
- **Inválidos.** H1 confirmado antes de tener sus 4 M15 → prohibido (espera).
- **Edge.** TF que no divide exacto (p.ej. sesiones de índices) → fronteras por
  calendario de sesión (§9). D1 con `boundary_timezone`.

### 9. Market Session Manager
- **Objetivo.** Fuente canónica de sesión/killzone y de máximos/mínimos de
  sesión/día/semana (liquidez temporal).
- **I/O.** In: `event_time`, `instrument_profile`/Market DNA. Out:
  `SessionContext{session, killzone, in_killzone, session_high/low, PDH/PDL, PWH/PWL}`.
- **Reglas.** Mismas definiciones que ENG-002 D31 / ENG-011 §5.F, pero el **MDE es
  la fuente**: los demás motores **consumen** de aquí (no recalculan). IANA/DST.
- **Edge.** Festivos/medio día (`holiday_calendar`); cripto 24/7 → `activity_windows`.

### 10. Spread Manager
- **Objetivo.** Serie de spread canónica y su estado.
- **I/O.** In: bid/ask por tick. Out: `spread`, `spread_state`, estadísticos
  (media/varianza por ventana).
- **Reglas.** `spread = ask − bid`; `spread_state` vs umbrales del Market DNA (ok/
  wide/blowout). Es la **fuente** para el veto de spread de Risk/MCE (no lo recalculan).
- **Edge.** Spread negativo/cero (feed cruzado) → QUARANTINE + señal de calidad.

### 11. Volume Manager
- **Objetivo.** Volumen canónico según la clase de activo.
- **I/O.** In: volumen/tick-count por tick. Out: `volume` por vela + `volume_source`.
- **Reglas.** `volume_source ∈ {tick, real}` por Market DNA (FX/Oro=tick;
  índices/cripto=real). Coherencia: la misma vela expone el volumen bajo su fuente.
- **Edge.** Proveedor sin volumen → `volume=null`, `volume_available=false` (los
  consumidores lo tratan como `UNAVAILABLE`, ENG-002 D30).

### 12. ATR Provider
- **Objetivo.** ATR canónico por símbolo/TF (unidad universal del sistema).
- **I/O.** In: velas confirmadas + `atr_period`. Out: `ATR` por vela confirmada
  (Wilder), `TR` incremental.
- **Reglas (⛔).** El ATR se calcula **solo sobre velas CONFIRMED** y es la
  **fuente única** para MCE/SMC/Risk (nadie recalcula ATR por su cuenta → evita
  discrepancias). `insufficient_data` durante las primeras `atr_period` velas.
- **Edge.** Gaps: `TR` usa cierre previo real; primera vela tras gap → TR grande
  (correcto). Cambio de `atr_period` = nueva serie derivada (versionada).

### 13. Liquidity Snapshot
- **Objetivo.** Foto puntual de la liquidez disponible (para MCE/SMC/Execution).
- **I/O.** Out: `LiquiditySnapshot{spread, session_extremes, PDH/PDL/PWH/PWL,
  depth_class?, target_pools?}` en un `event_time`.
- **Reglas.** Compone Spread (§10) + Session (§9) + niveles. Si el proveedor da
  profundidad (order book), se resume en `depth_class`; si no, se omite (no se
  inventa).
- **Edge.** Baja liquidez (rollover/festivo) reflejada, no ocultada.

### 14. Market Snapshot
- **Objetivo.** **Foto consistente y atómica** del mercado en un instante:
  velas confirmadas por TF + FORMING actual + spread + volumen + ATR + sesión +
  liquidez. Es lo que consumen los motores para decidir en un `event_time` dado.
- **I/O.** Out: `MarketSnapshot{symbol, as_of_event_time, candles_by_tf(confirmed),
  forming, spread, volume, atr, session, liquidity, data_version}`.
- **Reglas (⛔).** **Atomicidad temporal:** todos los componentes del snapshot
  corresponden **al mismo `as_of`** (no mezcla un ATR de t con velas de t+1) →
  consistencia. Marca claramente `forming` como provisional.
- **Edge.** Consulta `as_of` en medio de una vela → incluye la FORMING marcada; los
  detectores estructurales ignoran la FORMING por contrato.

### 15. Historical Data Manager
- **Objetivo.** Custodio de la historia **versionada, inmutable y reproducible**.
- **I/O.** In: cargas de históricos (proveedores), correcciones. Out: consultas
  **point-in-time / as-of** por `dataset_id`.
- **Reglas (⛔).** Cada dataset tiene `dataset_id` + `data_hash`; las correcciones
  crean **nueva versión** (no sobrescriben). Consulta reproducible: "dame EURUSD M15
  del rango R **según** `dataset_id=X`" → siempre el mismo resultado.
- **Edge.** Backfill parcial → marca de cobertura/gaps; fusión de dos proveedores →
  procedencia por dato; *survivorship*/ajustes (índices) versionados.

### 16. Live Data Manager
- **Objetivo.** Gestionar conexiones vivas, reconexión, *failover* de proveedor y
  el estado de "salud" del feed.
- **I/O.** In: streams de proveedores. Out: ticks normalizados al pipeline +
  `FeedHealth`.
- **Reglas.** Reconexión con *backfill* del hueco al reconectar (rellena desde
  histórico/secundario, marcando procedencia). *Failover* a proveedor secundario con
  continuidad de `seq`/procedencia. Nunca fabrica datos.
- **Estados (feed):** `CONNECTED → DEGRADED → DISCONNECTED → RECONNECTING`.
- **Edge.** Desconexión durante vela FORMING → esa vela se marca `partial/degraded`;
  al reconectar, se completa o se marca gap (no se confirma "a ciegas").

### 17. Cache
- **Objetivo.** Servir datos calientes con baja latencia sin violar la SSOT.
- **I/O.** Out: velas recientes/snapshot desde memoria/Redis.
- **Reglas (⛔).** La cache es una **vista derivada** del almacén, **no** una fuente
  alternativa: **invalidación por evento** (al confirmar/persistir) y **coherencia**
  con el histórico. Nunca sirve un valor `CONFIRMED` distinto al persistido.
- **Edge.** Miss → lee del store (no del broker). Cache fría al arrancar → se
  hidrata del store, no del feed.

### 18. Event Bus
- **Objetivo.** Distribuir datos como **eventos inmutables y ordenados**.
- **Eventos (contratos):** `TickReceived`, `BarClosed{symbol,tf,candle}`,
  `SnapshotUpdated`, `SessionChanged`, `DataQualityAlert`, `DataRevised{dataset_id}`.
- **Reglas (⛔).** Particionado por `symbol` (orden garantizado por símbolo);
  `event_id` idempotente; envelope con `event_time`, `data_version`, `data_hash`.
  Entrega at-least-once + idempotencia en consumidores (alineado con ARC-004).
- **Edge.** Reproceso de un evento ya visto → sin efecto (idempotente). Los
  consumidores nunca reciben datos FORMING como `BarClosed`.

### 19. Data Contracts (modelo canónico de entrada/salida)
```
Tick   { symbol, event_time(UTC ns), ingest_time, process_time,
         bid, ask, last?, volume?, provider, seq, event_time_source }
Candle { symbol, timeframe, open_time, close_time, o, h, l, c,
         volume, tick_count, state∈{FORMING,CONFIRMED,REVISED},
         synthetic?, provider_lineage, data_hash, dataset_id }
MarketSnapshot { symbol, as_of_event_time, candles_by_tf(confirmed),
         forming(provisional), spread, volume, atr, session, liquidity, data_version }
Events { TickReceived, BarClosed, SnapshotUpdated, SessionChanged,
         DataQualityAlert, DataRevised }   // envelope: event_id, event_time, data_version, data_hash
```
- **Entrada** (desde ACL de proveedor) → siempre `Tick`/`RawCandle` normalizado.
- **Salida** (a consumidores) → `Candle` CONFIRMED, `MarketSnapshot`, y eventos.

---

## 20. Calidad e integridad de los datos

### 20.1 Data Validation (validación de entrada)
Reglas deterministas aplicadas **antes** de ensamblar (fallo → `QUARANTINED`):
- **Estructura:** campos requeridos presentes; tipos y escala correctos.
- **Precio:** `bid>0`, `ask>0`, `bid≤ask` (no cruzado), dentro de bandas del símbolo.
- **Salto:** `|price − last_price| ≤ max_tick_jump` (Market DNA) → filtra pinchazos.
- **Tiempo:** `event_time` monótono dentro de `max_lateness`; no futuro > `max_skew`.
- **Salida:** `valid` | `quarantined(reason)` (registrado, auditable).

### 20.2 Data Integrity (integridad)
- **Hash + procedencia:** cada vela CONFIRMED lleva `data_hash` (función de sus
  ticks/valores) y `provider_lineage`. Reconstrucción → mismo hash o discrepancia
  detectada.
- **Continuidad de secuencia:** `seq` por proveedor detecta huecos/duplicados.
- **Cadena versionada:** `dataset_id` encadena versiones (inmutable + revisiones).

### 20.3 Data Quality (calidad — métricas)
Score de calidad por símbolo/periodo (consumible por MCE §5.I y como señal):
`completeness` (ticks esperados vs recibidos), `timeliness` (latencia/skew),
`gap_ratio`, `quarantine_ratio`, `spread_stability`, `provider_agreement` (si hay
varias fuentes). `DataQualityAlert` cuando cae bajo umbral.

### 20.4 Missing Data Handling (datos faltantes)
- **Detección de gaps:** hueco temporal > `expected_interval × gap_tolerance`.
- **Política (⛔ nunca fabricar en silencio):**
  - Vela sin ticks → `empty_candle_policy` (`skip` | `synthetic` marcada).
  - Gap histórico → **marcar** el gap; opcional relleno desde **fuente secundaria**
    con procedencia (`filled_from=secondary`), **nunca** interpolación inventada como
    dato real.
  - Gap en vivo (desconexión) → backfill al reconectar (§16); mientras, datos
    marcados `degraded`.
- **Contrato:** los consumidores reciben la marca de gap/synthetic y deciden
  (los detectores estructurales tratan gaps con cautela, ENG-002).

### 20.5 Out-of-Order Events (fuera de orden)
- **Buffer de reordenación** por `event_time` dentro de `max_lateness` (watermark).
- Tick tardío que llega **antes** de confirmar su periodo → se **inserta** en orden
  (la vela FORMING aún es mutable) → resultado idéntico a si hubiera llegado en orden.
- Tick tardío que llega **después** de confirmar → **no muta** lo CONFIRMED; va a
  `late_data`: se descarta+registra (`late_drop`) o genera **revisión versionada**
  (`revision`), según `late_data_policy`. **Nunca repinta en vivo.**
- **Pseudocódigo.**
```
def onLateTick(tick):
    if tick.event_time >= watermark_of(tick.period) and not confirmed(tick.period):
        insertInOrder(tick)                 # aún FORMING → determinista
    else:
        record(late_data, tick)
        if policy == REVISION: emitRevision(tick.period)   # nueva versión, no repaint
        else: drop(tick)                                   # log
```

### 20.6 Duplicate Events (duplicados)
- **Dedup determinista** por clave `(provider, symbol, seq)` o, si no hay `seq`,
  `(symbol, event_time, price_hash)`. Ventana de dedup acotada.
- Idempotencia en el bus (`event_id`) → un duplicado no altera velas ni re-publica.
- **Edge.** Dos proveedores con el "mismo" tick → se cuentan una vez por regla de
  `provider_agreement` (no doble volumen).

### 20.7 Latency Control (control de latencia)
- **Métricas:** `ingest_latency = ingest_time − event_time`, `watermark_lag`,
  `confirm_delay`. SLOs por proveedor.
- **Backpressure:** si el ingestor se satura, se aplica contrapresión al feed y se
  marca `DEGRADED` (no se descartan datos en silencio).
- **max_lateness** equilibra latencia de confirmación vs. captura de tardíos
  (config por proveedor): mayor → menos revisiones, más retardo de confirmación.

---

## 21. Versionado de datos y reproducibilidad histórica

- **`dataset_id` + `data_hash`:** todo histórico es un **dataset inmutable y
  versionado**. Correcciones/revisiones crean **nuevas versiones**; las anteriores
  se conservan (auditoría).
- **Consultas point-in-time / as-of:** "dame la serie **tal como era** en la versión
  X / en el instante T" → base de la reproducibilidad.
- **Reproducibilidad (⛔):** un backtest/replay se ejecuta **contra un `dataset_id`
  fijo**; re-ejecutar produce **exactamente** los mismos datos → mismos eventos →
  mismas decisiones (encadena con ENG-004/ENG-009). El `dataset_id` entra en el
  `config_hash` de cada decisión.
- **Linaje:** de cada vela se puede rastrear proveedor, ticks origen, versión y
  transformaciones aplicadas.

---

## 22. Sincronización con Backtesting, Replay e IA

### 22.1 Con Backtesting (ENG-004)
- **Mismo pipeline, misma API de Distribution**; la fuente es un `dataset_id`
  versionado reproducido en **event-time**. El watermark avanza con los datos
  reproducidos → confirmación **idéntica** a live. Cero look-ahead: el backtest solo
  ve datos con `event_time ≤ t` (as-of).

### 22.2 Con Decision Replay (ENG-009)
- El replay usa el **`dataset_id` + `data_version`** que registró la decisión
  original → reconstruye el `MarketSnapshot` **exacto** de aquel instante. Sin esto,
  el replay no sería fiel; con esto, es reproducible bit a bit.

### 22.3 Con IA (ENG-003)
- **Feature store consistente:** las features de ML se derivan **del MDE** (mismas
  velas/derivados) → *training/serving skew* eliminado (se entrena y se sirve con la
  misma fuente y las mismas reglas as-of).
- **Datasets etiquetados y versionados** (`dataset_id`) para entrenamiento
  reproducible; sin look-ahead (as-of estricto). La IA **consume**, no accede a
  fuentes externas.

---

## 23. Máquina de estados (consolidada)

- **Dato:** `RECEIVED→NORMALIZED→DEDUPED→ORDERED→VALIDATED→(QUARANTINED |
  FORMING→CONFIRMED→PERSISTED→ARCHIVED | →REVISED)` (§3).
- **Feed (Live Data Manager):** `CONNECTED→DEGRADED→DISCONNECTED→RECONNECTING`
  (§16).
- **Vela:** `FORMING (mutable) → CONFIRMED (inmutable) → [REVISED = nueva versión]`.

---

## 24. Casos válidos / inválidos y de prueba (deterministas)

**Válidos.**
- Ticks en orden → velas alineadas a rejilla, TFs superiores coherentes con
  inferiores, `data_hash` estable.
- Tick tardío antes de confirmar → insertado, misma vela que en orden.

**Inválidos (rechazo/cuarentena).**
- Tick cruzado (`bid>ask`) → QUARANTINE. Salto > `max_tick_jump` → QUARANTINE.
- Confirmar un H1 sin sus 4 M15 → prohibido.

**Casos de prueba.**
- **T1 determinismo:** mismo flujo de ticks × 2 → velas/eventos/`data_hash`
  idénticos.
- **T2 frontera:** tick en `event_time == close_time` → cuenta en la vela siguiente.
- **T3 no-repaint:** tick tardío tras CONFIRMED → la vela confirmada **no cambia**;
  se emite `late_drop`/`DataRevised` según política.
- **T4 dedup:** tick duplicado `(provider,symbol,seq)` → una sola vez; volumen no se
  dobla.
- **T5 out-of-order:** ticks desordenados dentro de `max_lateness` → misma vela que
  ordenados.
- **T6 gap:** hueco de datos → vela `skip`/`synthetic` marcada; `DataQualityAlert`.
- **T7 MTF coherencia:** `H1 == agregación exacta de sus 4×M15`.
- **T8 reproducibilidad:** consulta as-of sobre `dataset_id=X` (dos veces) → idéntico.
- **T9 watermark:** confirmación ocurre solo tras superar `close_time + max_lateness`.
- **T10 live≡backtest:** el mismo dataset por feed simulado vs. histórico → mismos
  `BarClosed`.
- **T11 timezone/DST:** frontera de día en semana de cambio DST → correcta (IANA).

---

## 25. Integración con todos los motores

| Motor | Qué consume del MDE | Contrato |
|-------|---------------------|----------|
| **Market Context (ENG-011)** | velas CONFIRMED por TF, ATR, sesión, spread, volumen, liquidez, calidad | `MarketSnapshot` + `BarClosed`; el MCE **no** recalcula ATR/sesión/spread (fuente única) |
| **Smart Money (ENG-002)** | velas CONFIRMED (⛔ nunca FORMING para estructura), ATR, volumen, sesión | `use_closed_candles`; determinismo/no-repaint heredados |
| **Trading (ENG-001)** | snapshot para gestión (incluye FORMING solo para posición viva), precios | `MarketSnapshot(as_of)` |
| **Risk (ENG-005)** | precios/valuación, `fx_snapshot` derivado de tasas del MDE | tasas versionadas → aritmética determinista |
| **Execution (ENG-006)** | precio/spread de referencia, conciliación de fills contra datos oficiales | el MDE es la referencia de precio "oficial" |
| **Backtesting (ENG-004)** | `dataset_id` versionado, reproducción event-time | as-of estricto, sin look-ahead |
| **Decision Replay (ENG-009)** | `dataset_id`+`data_version` de la decisión | reconstrucción exacta del snapshot |
| **IA (ENG-003)** | feature store desde el MDE, datasets versionados | sin training/serving skew, sin look-ahead |
| **Analytics/Portfolio (ENG-007)** | precios de valuación, series históricas | fuente única para PnL/valuación |

**Regla transversal (⛔):** cualquier motor que necesite un dato de mercado lo pide
al MDE. Si un motor "necesitara" ir al broker, es un **error de diseño**.

---

## 26. Garantías y reglas obligatorias (checklist)

- ✅ **SSOT:** único origen; nadie más habla con proveedores (ACL exclusivo del MDE).
- ✅ **Inmutable tras confirmar:** `CONFIRMED` nunca muta; correcciones = versión nueva.
- ✅ **Sin repaint:** estructura solo sobre CONFIRMED; FORMING marcado y aislado.
- ✅ **Determinismo total:** event-time + rejilla fija + watermark + sin `now()` →
  mismas velas/eventos (T1, T2, T5, T9).
- ✅ **Reproducibilidad completa:** datasets versionados, as-of, `data_hash` (T8, T10).
- ✅ **Auditable:** procedencia/linaje/hash por dato; QUARANTINE y REVISED registrados.
- ✅ **Verificable por tests unitarios:** T1–T11 + property-based (p.ej. "MTF superior
  ≡ agregación de inferiores", "un dato CONFIRMED nunca cambia de valor").

**Relaciones:** es el **cimiento** (ENG-000); lo consumen ENG-011/002/001/005/006/
004/009/003/007; se ancla al audit ledger (SEC-000) para linaje; su calidad alimenta
el Market Quality del MCE (ENG-011 §5.I).

> **Versión 0.1 — Borrador (🟨).** Especificación oficial del Market Data Engine —
> única fuente de verdad de datos de ELYON QUANT. Aprobación (🟩) requiere revisión
> de Platform/Data Lead, Quant, CTO, ML y QA; prerrequisito del gate D4 y **cimiento**
> de todos los demás motores. Cambios de política (watermark, empty-candle,
> late-data, versionado) vía RFC + validación con golden datasets (ENG-004).
