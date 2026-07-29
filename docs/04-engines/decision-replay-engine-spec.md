<!--
title: ELYON QUANT — Decision Replay Engine Specification
id: ENG-009 (Decision Replay Engine — módulo core)
owner: CTO/Principal Architect
reviewers: [Quant Lead, ML Lead, Security Lead, QA Lead]
status: draft
version: 0.1
last_updated: 2026-07-28
supersedes: —
-->

# ELYON QUANT — DECISION REPLAY ENGINE (ENG-009)

> **Módulo core exclusivo.** Registra **absolutamente todas** las decisiones del
> sistema —no solo las operaciones ejecutadas, también **todas las descartadas**—
> con el estado completo del mercado en el instante de la decisión, y permite
> **reproducir cualquier operación o señal descartada** paso a paso, como una
> repetición, mostrando exactamente **por qué** el motor decidió entrar o no
> entrar.

Este documento es prerrequisito del gate D4. El Decision Replay Engine es el
**guardián de la memoria y la auditabilidad** de ELYON QUANT: convierte cada
decisión en un artefacto reproducible, base de la Explicabilidad (ENG-010), del
Backtesting (ENG-004), del ajuste del scoring y del cumplimiento (SEC-000).

---

## 1. Propósito y principios

1. **Todo se registra.** Cada evaluación del motor produce un registro, opere o
   no. La ausencia de un registro es un **bug**, no un estado válido.
2. **Descartes de primera clase.** Las señales **no** ejecutadas se registran con
   el mismo detalle que las ejecutadas (a menudo son más valiosas para aprender).
3. **Reproducibilidad exacta.** Un registro + su `config_hash` + el snapshot de
   datos debe **reconstruir** la decisión bit a bit (determinismo, alineado con
   ENG-002 §0.2 y ENG-004).
4. **Inmutabilidad.** Los registros son **append-only**; nunca se editan ni se
   borran (retención regulatoria; integra con el audit ledger, SEC-000).
5. **Time-travel.** El usuario puede "rebobinar" y avanzar por la decisión vela a
   vela y factor a factor, viendo cómo se formó el score.
6. **Separación de responsabilidades.** El Trading Engine **decide**; el Decision
   Replay Engine **observa y persiste** (no influye en la decisión). Es un
   consumidor, nunca un participante del *hot path*.

---

## 2. Qué se registra: el `DecisionRecord` extendido

El Decision Replay Engine persiste el `DecisionRecord` definido en el Trading
Engine Bible (ENG-001 §39), **extendido** con todo lo necesario para reproducir.
Campos obligatorios (agrupados):

### 2.1 Identidad y contexto
`decision_id` (UUID) · `correlation_id`/`trace_id` · `timestamp` (UTC, ns) ·
`symbol` · `instrument_profile` · `timeframe_triad` · `strategy_version` ·
`config_hash` · `params_hash` (Smart Money detectors) · `engine_version`.

### 2.2 Snapshot del mercado (para reconstruir)
- **Series de velas** relevantes por TF (ventana suficiente para recomputar todos
  los detectores; por referencia a un *data snapshot* inmutable, no copiando GB).
- **Hora / sesión / killzone** (D31): sesión activa, killzone, `in_killzone`.
- **ATR** (D22 ENG-001 / valor por TF), **spread** en el instante, **volumen** y
  su ratio (D30).
- **Noticias**: eventos de alto impacto activos/próximos y ventana de veto (§25).

### 2.3 Features Smart Money (salida de cada detector, ENG-002)
- **Tendencia / estructura**: `trend_state`, swings etiquetados (HH/HL/LH/LL),
  estructura interna/externa (D05/D06).
- **BOS / CHoCH / MSS** (D08–D10): eventos con nivel, índice, displacement.
- **Liquidez** (D11–D15): pools BSL/SSL, equal highs/lows, estados intact/swept.
- **Liquidity sweeps** (D16) e **inducement** (D17).
- **Order Blocks** (D21) y **Mitigation/Breaker/Rejection** (D22–D24): zona,
  estado, confidence.
- **FVG / IFVG / BPR** (D18–D20): zonas y estados.
- **Fibonacci** (D32): origen, destino, span, niveles, y **OTE** (D29).
- **Premium/Discount/Equilibrium** (D26–D28): `pos` en el dealing range (D25).

### 2.4 Decisión y razonamiento (núcleo de la explicabilidad)
- **Score** por lado (long/short), **desglose factor a factor** (puntos otorgados
  y condición que los generó), **umbral** aplicado.
- **Reglas activadas** (qué confirmaciones dispararon) y **vetos evaluados**
  (cuáles bloquearon, cuáles pasaron).
- **Riesgo**: `risk_per_trade_pct`, tamaño calculado, RR esperado, límites
  diarios/globales vigentes y su estado.
- **Resolución**: `action ∈ {enter_long, enter_short, no_trade}` y **motivo
  exacto** (`veto:<x>` | `score_below_threshold` | `entered`).
- Si opera: parámetros de orden (entry, SL, TP(s), size), y **ciclo de vida**
  posterior (BE, trailing, parciales, cierre) con causa y timestamp.

> La lista mínima exigida por producto — *estado del mercado, tendencia, liquidez,
> BOS, CHoCH, Order Block, FVG, Fibonacci, OTE, score, riesgo, spread, ATR,
> noticias, hora, motivo exacto* — está **íntegramente cubierta** por 2.2–2.4.

---

## 3. Arquitectura del módulo

### 3.1 Ubicación y estilo
- Es el módulo `decision_replay` del monolito modular (Clean Architecture: domain
  / application / infrastructure / interfaces), **candidato temprano a extracción
  a servicio** por su perfil de alto volumen de escritura y consulta analítica
  (criterio de extracción, ARC/roadmap).
- **Event-driven y asíncrono:** consume eventos `DecisionEvaluated`
  (y `TradeLifecycleUpdated`) emitidos por el Trading/Execution Engine vía el bus
  (Kafka). **No** está en el *hot path* de la decisión → nunca añade latencia ni
  puede bloquear una operación.
- **Outbox** en el productor garantiza que ninguna decisión se pierda
  (at-least-once) + **idempotencia** por `decision_id` en el consumidor.

### 3.2 Modelo de almacenamiento
- **Store append-only** particionado por tiempo y símbolo. Metadatos y features
  estructuradas → columnar/OLAP (ClickHouse) para consulta analítica masiva;
  índice transaccional (PostgreSQL) por `decision_id`/`trace_id`.
- **Data snapshot inmutable:** las series de velas se referencian por
  `snapshot_ref` a un almacén versionado (object storage / data lake), no se
  duplican en cada registro (control de volumen).
- **Integridad:** hash encadenado (append-only, verificable) enlazado al **audit
  ledger** (SEC-000) para las decisiones que tocan dinero.

### 3.3 Volumen y retención
- Alta cardinalidad (muchas evaluaciones "no_trade"): políticas de **retención por
  clase** (`retention_policy`): decisiones ejecutadas → retención larga/regulatoria;
  descartes → retención configurable con *downsampling* del snapshot para las más
  antiguas (se conserva siempre el `DecisionRecord` estructurado; el snapshot de
  velas puede comprimirse/tier a frío).
- Compresión y *tiering* caliente→templado→frío (S3) por edad.

---

## 4. Replay: reproducir una decisión paso a paso

### 4.1 Modos de reproducción
- **Deterministic Replay (fiel):** recarga el `snapshot_ref` + `config_hash` y
  **re-ejecuta** los detectores y el scoring → debe producir **exactamente** el
  mismo `DecisionRecord`. Es la prueba de determinismo (si difiere → regresión).
- **Record Playback (visual):** reproduce el registro almacenado sin recomputar
  (rápido), para revisión/UX.

### 4.2 Timeline de reproducción (paso a paso)
El motor reconstruye la decisión como una secuencia ordenada de **pasos
explicables**, que el Dashboard (DES-006) reproduce como una "repetición":

```
Paso 1  Contexto        → símbolo, hora, sesión/killzone, ATR, spread, noticias
Paso 2  Bias HTF         → tendencia y estructura (HH/HL…), premium/discount
Paso 3  Liquidez         → pools BSL/SSL, equal highs/lows, objetivo
Paso 4  Manipulación     → liquidity sweep + inducement detectados
Paso 5  Estructura LTF   → CHoCH/BOS con displacement (+ FVG)
Paso 6  POI              → Order Block / Breaker / FVG en la zona
Paso 7  Valoración       → Fibonacci + OTE (0.705), discount/premium
Paso 8  Scoring          → suma factor a factor hasta el score total
Paso 9  Vetos            → reglas duras evaluadas (spread/news/riesgo/…)
Paso 10 Resolución       → ENTER (long/short) o NO-TRADE + motivo exacto
Paso 11 Gestión (si op.) → entrada, SL, TP, BE, trailing, parciales, cierre
```

Cada paso expone su **evidencia** (los campos del registro que lo sustentan) y su
**contribución** (puntos al score o veto). El usuario puede detenerse, avanzar,
retroceder y ver el gráfico con las zonas dibujadas en cada paso.

### 4.3 Consultas y descubrimiento
- Reproducir **cualquier** operación ejecutada o **señal descartada** por
  `decision_id`.
- Búsqueda/filtrado: "todas las no-entradas de hoy por veto de noticias",
  "setups con score 55–69 (watchlist)", "descartes por falta de FVG", etc.
- Agregados: distribución de motivos de rechazo, contribución media de cada factor,
  *hit-rate* por rango de score (insumo para calibrar pesos en ENG-004).

---

## 5. API (contratos, sin implementación)

- **Ingesta (async):** consume `DecisionEvaluated`, `TradeLifecycleUpdated` del bus.
- **Consulta (REST/Query):**
  - `GET /decisions/{decision_id}` → DecisionRecord completo.
  - `GET /decisions?filters…` → búsqueda paginada.
  - `GET /decisions/{id}/replay` → timeline de pasos (§4.2).
  - `POST /decisions/{id}/replay:deterministic` → re-ejecución fiel + diff.
  - `GET /decisions/{id}/explanation` → explicación (delegada a ENG-010).
- Contratos versionados en `/contracts` (OpenAPI/AsyncAPI). Multi-tenant y
  autorizado (un tenant solo ve sus decisiones; RLS, SEC-000).

---

## 6. Garantías y verificación

- **Cobertura 100 %:** toda evaluación del Trading Engine ⇒ exactamente un
  `DecisionRecord` (test de invariante: nº de evaluaciones == nº de registros).
- **Determinismo:** `deterministic replay` reproduce el registro original; un
  test de regresión ejecuta replay sobre un *golden set* y compara byte a byte.
- **No intrusión:** medición de que el módulo **no** añade latencia al hot path
  (consumo async; el productor solo escribe un evento en su outbox).
- **Inmutabilidad:** intentos de modificar/borrar un registro fallan; verificación
  de la cadena de hash.
- **Trazabilidad:** cada `decision_id` correlaciona con órdenes (Execution),
  eventos y explicación (BLD-003).

---

## 7. Relaciones

- **Trading Engine (ENG-001 §39):** productor del `DecisionRecord`; esta spec lo
  extiende y persiste.
- **Smart Money Engine (ENG-002):** todas las features registradas son salidas de
  sus detectores (incluido Fibonacci D32).
- **Explainable AI (ENG-010):** consume el registro para generar la explicación
  humana; el replay **muestra** esa explicación paso a paso.
- **Backtesting (ENG-004):** comparte el mecanismo de snapshot/determinismo; los
  registros alimentan la calibración de pesos/umbral.
- **Audit & Compliance (SEC-000):** los registros de decisiones con impacto en
  capital se anclan al ledger inmutable.
- **Dashboard (DES-006):** UI de reproducción y descubrimiento.

> **Versión 0.1 — Borrador (🟨).** Aprobación (🟩) requiere revisión de CTO, Quant
> Lead, ML Lead, Security y QA. Prerrequisito del gate D4.
