<!--
title: ELYON QUANT — Core Contracts v1.0
id: API-CORE-001 (autoridad oficial de contratos inter-motor; congela C1–C9)
owner: CTO/Principal Architect
reviewers: [Platform/Data Lead, Quant Lead, Risk Lead, Execution Lead, ML Lead, Security Lead, QA Lead]
status: frozen-candidate
version: 1.0
last_updated: 2026-07-29
closes: Core Architecture Review v1.0 — P0-B (contratos), P0-D (provenance)
-->

# ELYON QUANT — CORE CONTRACTS v1.0

> **Autoridad oficial de todos los contratos entre motores.** Este documento
> **congela las interfaces públicas internas** (C1–C9) antes de comenzar el
> desarrollo. Un contrato congelado es un **compromiso vinculante**: los motores
> evolucionan por dentro libremente **mientras respeten estos contratos**. Cambiarlos
> exige el proceso de §1 (no se editan "a mano").

Cierra el bloqueador **P0-B** (contratos sin versionar) y **P0-D** (provenance) de la
[Core Architecture Review v1.0](../architecture/core-architecture-review-v1.0.md).

---

## 0. Preámbulo

### 0.1 Alcance: los 9 contratos
| # | Contrato | Producer | Consumers |
|---|----------|----------|-----------|
| C1 | `market-data.v1` | Market Data (ENG-000) | todos |
| C2 | `market-context.v1` | Market Context (ENG-011) | SMC, Trading, Risk, AI, Replay |
| C3 | `smart-money.v1` | Smart Money (ENG-002) | Trading, Replay |
| C4 | `trade-intent.v1` | Trading (ENG-001) | Risk, Execution, Replay |
| C5 | `risk.v1` | Risk (ENG-005) | Trading, Execution, Replay, Portfolio |
| C6 | `execution.v1` | Execution/OMS (ENG-006) | Portfolio, Risk, Replay, XAI |
| C7 | `decision-record.v1` | todos los motores de decisión | Decision Replay (ENG-009) |
| C8 | `explanation.v1` | todos | Explainable AI (ENG-010), UI |
| C9 | `market-dna.v1` | Config/Platform | MCE, SMC, Risk |

### 0.2 Transporte y serialización
- **Eventos** (async, Kafka): esquema **Avro** en Schema Registry (AsyncAPI 2.6).
- **APIs síncronas** (REST/gRPC internas): **OpenAPI 3.1** / **Protobuf**.
- Los nombres de campo se documentan aquí en **`camelCase`** (representación JSON);
  la generación a Avro/proto mapea 1:1. Los tipos generados viven en
  `packages/py/elyon-contracts` y `packages/ts/api-client` (no se escriben a mano).

### 0.3 Vocabulario de tipos (canónico)
| Tipo | Definición |
|------|-----------|
| `string` | UTF-8 | `bool` | booleano |
| `int32`/`int64` | entero con signo |
| `decimal(p,s)` | **decimal exacto** (nunca float) para dinero/precio/tamaño |
| `timestampNs` | `int64` UTC en **nanosegundos** (event-time) |
| `uuid` | identificador único (UUID v7, ordenable por tiempo) |
| `enum{...}` | conjunto cerrado y versionado de valores |
| `symbol` | `string` normalizado (p.ej. `EURUSD`) |
| `money` | `{ amount: decimal, currency: string(ISO-4217) }` |
| `price` | `decimal(p,s)` según `instrumentProfile` |
| `hash` | `string` hex (sha-256) |
| `array<T>` / `map<K,V>` | colecciones |
Reglas: **precios/dinero/tamaños siempre `decimal`** (P0-A / ENG-005 §0.2); **tiempos
siempre `timestampNs` UTC**; **enums cerrados** (añadir valor = cambio MINOR con
regla §1.2).

### 0.4 Envelope común de evento (obligatorio en C1, C2, C5, C6, C7, C8)
Todo evento de dominio viaja envuelto en el **Event Envelope** (los campos de
provenance son **obligatorios** — cierre de P0-D):
```
EventEnvelope {
  schemaName:    string      (req)   // p.ej. "execution.v1"
  schemaVersion: string      (req)   // SemVer, p.ej. "1.0.0"
  eventId:       uuid        (req)   // idempotencia (dedup por eventId)
  eventType:     string      (req)   // p.ej. "OrderFilled"
  eventTime:     timestampNs (req)   // event-time UTC
  producer:      string      (req)   // motor emisor (ENG-xxx)
  tenantId:      uuid        (req)   // aislamiento multi-tenant
  correlationId: uuid        (req)   // hilo decisión→orden→fill→…
  dataVersion:   string      (req)   // dataset_id/snapshot del MDE usado (provenance)
  configHash:    hash        (req)   // hash de config/params/pesos usados
  dnaHash:       hash        (opt)   // Market DNA usado, si aplica
  payload:       <schema>    (req)   // cuerpo tipado del contrato
  payloadHash:   hash        (req)   // integridad del payload
}
```
**Invariante global (⛔):** `eventId` único; reprocesar un evento con el mismo
`eventId` **no** tiene efecto (idempotencia en todos los consumidores).

---

## 1. Gobierno de contratos (aplica a C1–C9)

### 1.1 Schema Versioning (SemVer por contrato)
Cada contrato versiona con **`MAJOR.MINOR.PATCH`**:
- **PATCH** (`1.0.x`): aclaraciones de documentación/restricciones **sin** cambiar la
  forma (p.ej. precisar un rango). Sin impacto en serialización.
- **MINOR** (`1.x.0`): **adiciones retrocompatibles** — nuevos campos **opcionales**,
  nuevos valores de enum tolerados por consumidores, nuevos tipos de evento. Los
  consumidores antiguos siguen funcionando.
- **MAJOR** (`x.0.0`): **cambio incompatible** (breaking) — ver §1.5. Requiere nuevo
  namespace (`execution.v2`) y convivencia.
El **nombre del contrato** incluye el MAJOR (`market-data.v1`); MINOR/PATCH viven en
`schemaVersion` del envelope y en el Schema Registry.

### 1.2 Compatibility Rules (registro de esquemas)
- Política del Schema Registry: **`BACKWARD`** (un consumidor con el esquema `vN` puede
  leer datos escritos con `vN` **o** `vN+MINOR`).
- **Permitido en MINOR/PATCH:** añadir campo **opcional** (con default), añadir evento
  nuevo, añadir valor de enum **si** los consumidores tienen cláusula *default/unknown*,
  relajar una restricción, documentar.
- **Prohibido sin MAJOR (⛔ breaking):** eliminar/renombrar un campo, cambiar su tipo,
  hacer obligatorio un campo antes opcional, estrechar una restricción, cambiar
  semántica de un valor, quitar un valor de enum.
- **Enums:** los consumidores **deben** tratar valores desconocidos con una rama
  `UNKNOWN` (tolerancia hacia delante) → añadir valores es MINOR.

### 1.3 Compatibilidad hacia atrás (event sourcing / upcasting)
Como los eventos son **inmutables para siempre**, leer historia antigua exige
**upcasters**: funciones puras `vOld → vNew` que rellenan campos nuevos con defaults
al **leer** (nunca reescriben el evento almacenado). Cada contrato mantiene su
**cadena de upcasters**. Esto hace que Replay/Backtesting (ENG-009/004) reproduzcan
historia de cualquier antigüedad. (Cierra R5 de la review.)

### 1.4 Deprecation Policy
- Un campo/valor/evento se marca **`deprecated`** en el registro con `deprecatedSince`
  y `sunsetVersion`. Sigue presente y funcional durante el **periodo de convivencia**
  (mínimo `deprecation_window`, def. 2 versiones MINOR o 90 días, el mayor).
- Se elimina **solo** en un MAJOR posterior al sunset, con anuncio y migración de
  consumidores documentada.
- Nada se retira en silencio; toda deprecación se anota en el `CHANGELOG` del contrato.

### 1.5 Breaking Changes Policy
1. Un cambio incompatible (§1.2) **exige** RFC + ADR (revisión de owner + consumers).
2. Se publica un **nuevo MAJOR** (`x+1.0.0`) en **namespace nuevo**; el anterior no se
   toca.
3. **Convivencia:** productor emite ambos (dual-write) o el consumer usa upcaster,
   durante el periodo de migración; se rastrea la adopción por consumidor.
4. El MAJOR viejo se retira solo cuando **todos** los consumidores migraron
   (verificado por contract tests) y venció el sunset.
5. **Nunca** se rompe un contrato congelado "en caliente" sin este proceso.

### 1.6 Contract Testing Strategy
- **Fuente de verdad:** los esquemas en `/contracts` (Avro/OpenAPI/proto). El código se
  **genera** desde ahí; prohibido escribir DTOs a mano que diverjan.
- **Producer tests:** cada productor valida que su salida cumple el esquema (schema
  validation en CI) y publica ejemplos "golden".
- **Consumer-driven contracts (Pact):** cada consumidor declara qué campos usa; un
  cambio que rompa a un consumidor **falla el build** del productor.
- **Compatibility gate:** el Schema Registry rechaza en CI cualquier cambio que viole
  `BACKWARD`.
- **Upcaster tests:** todo upcaster tiene test `vOld→vNew` sobre eventos golden.
- **Ejemplos válidos/inválidos** de este documento se convierten en **fixtures de
  test** (cada contrato aporta ≥1 válido y ≥1 inválido, §2+).
- **Cobertura:** un contrato no se considera "congelado" hasta tener producer + al
  menos un consumer test verdes (parte del gate a v1.0 GA).

---

## 2. C1 · `market-data.v1`

- **Propósito.** Distribuir datos de mercado canónicos (SSOT). Contrato de salida del
  MDE hacia todos los motores.
- **Owner.** Platform/Data Lead. **Producer.** ENG-000. **Consumers.** todos.
- **Versionado.** `market-data.v1` (SemVer en envelope). Reglas §1.
- **Mensajes.** `Candle`, `MarketSnapshot` (payloads); eventos `BarClosed`,
  `TickReceived`, `SessionChanged`, `DataRevised`, `DataQualityAlert`.

**`Candle` (campos):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| symbol | symbol | ✔ | del catálogo |
| timeframe | enum{M1,M5,M15,H1,H4,D1,…} | ✔ | cerrado |
| openTime / closeTime | timestampNs | ✔ | `closeTime > openTime`; alineados a rejilla |
| open/high/low/close | price | ✔ | `high≥max(open,close)`, `low≤min(open,close)`, `high≥low` |
| volume | decimal | ○ | ≥0; `null` si `volumeAvailable=false` |
| volumeAvailable | bool | ✔ | — |
| tickCount | int32 | ✔ | ≥0 |
| state | enum{FORMING,CONFIRMED,REVISED} | ✔ | estructural solo con CONFIRMED |
| synthetic | bool | ○ | default false |
| datasetId / dataHash | string / hash | ✔ | provenance/reproducibilidad |

- **Restricciones.** OHLC coherente; `state=CONFIRMED ⇒` inmutable. `FORMING` prohibido
  para consumidores estructurales (ENG-002 `use_closed_candles`).
- **Invariantes (⛔).** Una `Candle` CONFIRMED nunca cambia (`dataHash` estable). MTF
  superior = agregación exacta de inferiores.
- **Compatibilidad hacia atrás / evolución.** Según §1.2–§1.3. Añadir un derivado nuevo
  (p.ej. `vwap`) = campo opcional (MINOR).
- **Ejemplo válido.**
```json
{ "symbol":"EURUSD","timeframe":"M15","openTime":1690000000000000000,
  "closeTime":1690000900000000000,"open":"1.08500","high":"1.08640",
  "low":"1.08470","close":"1.08610","volume":"1234","volumeAvailable":true,
  "tickCount":842,"state":"CONFIRMED","synthetic":false,
  "datasetId":"eurusd-2026-07","dataHash":"a1b2c3" }
```
- **Ejemplo inválido.** `high < close` (viola coherencia OHLC) o `state:"CONFIRMED"`
  con `dataHash` ausente (falta provenance) → **rechazado**.

---

## 3. C2 · `market-context.v1`

- **Propósito.** Publicar el contexto/gate de mercado (ENG-011). Fuente única de
  contexto para el resto.
- **Owner.** Quant Lead. **Producer.** ENG-011. **Consumers.** SMC, Trading, Risk, AI,
  Replay. **Evento.** `MarketContextEvaluated`.

**`MarketContext` (campos principales):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| symbol | symbol | ✔ | — |
| asOf | timestampNs | ✔ | instante del snapshot (as-of) |
| dnaRef | hash | ✔ | `dnaHash` del perfil usado |
| regime | enum{TREND_UP,TREND_DOWN,RANGE,EXPANSION,COMPRESSION,ACCUMULATION,DISTRIBUTION,MANIPULATION} | ✔ | cerrado |
| regimeConfidence | decimal | ✔ | ∈ [0,1] |
| htf/mtf/ltf | object{regime,bias,volatilityState} | ✔ | por nivel |
| alignment | enum{ALIGNED,PARTIAL,CONFLICT} | ✔ | — |
| volatility | object{atr:price,atrRegime,realizedVol,…} | ✔ | atr `decimal` |
| session | object{session,killzone,inKillzone,inEfficiency} | ✔ | — |
| liquidity | object{spread:price,spreadState,availability,…} | ✔ | — |
| newsRisk | object{level,nextEvent?,blockActive} | ✔ | — |
| marketQuality | object{efficiencyRatio,qualityState,score} | ✔ | score ∈ [0,100] |
| contextScore | int32 | ✔ | ∈ [0,100] |
| gate | enum{PASS,FAIL} | ✔ | — |
| gateReason | string | ✔ | motivo exacto (XAI) |
| factorBreakdown | array<{factor,weight,points,condition}> | ✔ | Σpoints ≈ contextScore |
| vetoes | array<{vetoId,active,reason}> | ✔ | — |

- **Restricciones.** `gate=PASS ⇔ contextScore≥threshold ∧ ningún veto activo`.
- **Invariantes (⛔).** `Σ factorBreakdown.points == contextScore` (explicabilidad
  exacta). `gate=FAIL ⇒` consumidores no escanean.
- **Evolución.** Añadir un sub-detector = nuevo factor opcional (MINOR).
- **Ejemplo válido.** `{"regime":"TREND_UP","contextScore":86,"gate":"PASS",...}` con
  `Σpoints=86`. **Inválido.** `gate:"PASS"` con `contextScore:40` (viola invariante) o
  `regimeConfidence:1.4` (fuera de [0,1]) → **rechazado**.

---

## 4. C3 · `smart-money.v1`

- **Propósito.** Exponer las salidas tipadas de los detectores SMC (ENG-002) para el
  Trading Engine y el Replay.
- **Owner.** Quant Lead. **Producer.** ENG-002. **Consumers.** Trading, Replay.

**`SmartMoneyFeatureSet` (por símbolo/as-of):**
| Campo | Tipo | Req | Notas |
|-------|------|:---:|-------|
| symbol / asOf / dataVersion | symbol / timestampNs / string | ✔ | provenance |
| trendState | enum{BULLISH,BEARISH,RANGE} | ✔ | D05 |
| swings | array<{index,price,type∈{HH,HL,LH,LL},confirmIndex}> | ✔ | solo confirmados |
| events | array<{type∈{BOS,CHOCH,MSS},dir,level,index,displacementAtr}> | ✔ | D08–D10 |
| liquidityPools | array<{type∈{BSL,SSL},level:price,origin,strength,state∈{INTACT,SWEPT}}> | ✔ | D11–D16 |
| sweeps | array<{poolLevel,dir,penetration,index}> | ○ | D16 |
| inducement | object{level,state∈{TAKEN,PENDING,ABSENT}} | ○ | D17 |
| pois | array<POI{type∈{ORDER_BLOCK,MITIGATION,BREAKER,REJECTION},dir,zoneLo,zoneHi,mean,state,confidence}> | ✔ | D21–D24 |
| imbalances | array<{type∈{FVG,IFVG,BPR},dir,zoneLo,zoneHi,ce,state}> | ✔ | D18–D20 |
| dealingRange | object{low:price,high:price,refEvent} | ✔ | D25 |
| pricing | enum{PREMIUM,DISCOUNT,EQUILIBRIUM} | ✔ | D26–D28 |
| fib | object{origin:price,dest:price,span,levels:map,ote:{lo,hi,optimal}} | ✔ | D32/D29 |
| volumeConfirmation | enum{CONFIRMED,NOT_CONFIRMED,UNAVAILABLE} | ✔ | D30 |

- **Restricciones.** Todas las zonas `zoneLo ≤ zoneHi`; niveles de precio `decimal`;
  solo swings/velas **confirmados** (no-repaint).
- **Invariantes (⛔).** Un POI/sweep con `state` terminal no vuelve a un estado activo
  (no-repaint). `pricing` coherente con `dealingRange`.
- **Evolución.** Nuevo detector = nuevo campo/tipo opcional (MINOR); nuevos valores en
  enums de `type` con tolerancia `UNKNOWN`.
- **Ejemplo válido.** POI `{"type":"ORDER_BLOCK","dir":1,"zoneLo":"1.0840","zoneHi":"1.0855","state":"FRESH","confidence":0.8}`.
  **Inválido.** `zoneLo > zoneHi`, o un `FVG` con `zoneLo=zoneHi` (gap nulo) → **rechazado**.

---

## 5. C4 · `trade-intent.v1`

- **Propósito.** Intención de operar emitida por el Trading Engine hacia Risk y
  Execution (el "qué" de la operación).
- **Owner.** Quant + Execution. **Producer.** ENG-001. **Consumers.** Risk, Execution,
  Replay.

**`TradeIntent` (campos):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| intentId | uuid | ✔ | idempotencia de la intención |
| correlationId | uuid | ✔ | = decisionId del setup |
| decisionId | uuid | ✔ | enlaza DecisionRecord (C7) |
| symbol | symbol | ✔ | — |
| side | enum{BUY,SELL} | ✔ | — |
| orderType | enum{MARKET,LIMIT,STOP} | ✔ | — |
| entryPrice | price | ○ | requerido si LIMIT/STOP |
| stopLoss | price | ✔ | ⛔ obligatorio (nunca intent sin SL) |
| takeProfits | array<{price:price, sizePct:decimal}> | ✔ | `Σ sizePct ≤ 1`; ≥1 nivel |
| strategyId | string | ✔ | por estrategia |
| entryScore | int32 | ✔ | ∈ [0,100] |
| contextRef | hash | ✔ | ref al MarketContext usado (C2) |
| managementPlan | object{beAtR,trailingMode,partials[]} | ○ | plan propuesto (Execution lo ejecuta) |
| expiresAt | timestampNs | ○ | caducidad del setup |
| dataVersion / configHash | string / hash | ✔ | provenance (P0-D) |

- **Restricciones (⛔).** `stopLoss` presente y en el lado correcto respecto a `side`;
  `takeProfits` no vacío; RR implícito ≥ `min_rr` (validado por Risk).
- **Invariantes.** `intentId` único → **exactly-once** con Execution (una intención →
  ≤1 orden). El `managementPlan` es **propuesta**; Execution es dueño del estado.
- **Compatibilidad / evolución.** §1. Nuevos campos de gestión = opcionales (MINOR).
- **Ejemplo válido.**
```json
{ "intentId":"...","correlationId":"...","symbol":"XAUUSD","side":"BUY",
  "orderType":"LIMIT","entryPrice":"2385.20","stopLoss":"2380.10",
  "takeProfits":[{"price":"2395.00","sizePct":"0.5"},{"price":"2405.00","sizePct":"0.5"}],
  "strategyId":"smc-core","entryScore":88,"contextRef":"ctx-hash",
  "dataVersion":"xau-2026-07","configHash":"cfg-hash" }
```
- **Ejemplo inválido.** `stopLoss` ausente, o `side:"BUY"` con `stopLoss > entryPrice`
  (SL del lado equivocado), o `Σ sizePct = 1.4` → **rechazado**.

---

## 6. C5 · `risk.v1`

- **Propósito.** Petición y decisión de riesgo (pre-trade) + eventos de riesgo.
- **Owner.** Risk Lead. **Producer.** ENG-005 (decisión); Trading/Execution (petición).
  **Consumers.** Trading, Execution, Replay, Portfolio.
- **Mensajes.** `RiskRequest`, `RiskDecision`; eventos `RiskApproved`, `RiskRejected`,
  `RiskLimitBreached`, `KillSwitchTriggered`, `CooldownStarted`.

**`RiskDecision` (campos):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| requestId / correlationId | uuid | ✔ | enlaza al TradeIntent |
| decision | enum{APPROVE,REJECT} | ✔ | — |
| approvedSize | decimal | ○ | req si APPROVE; ≤ límites; `ROUND_DOWN` a lotStep |
| riskAmount | money | ○ | req si APPROVE |
| approvedSl / approvedTps | price / array | ○ | req si APPROVE |
| reasons | array<{code,detail,slack}> | ✔ | motivos + holgura de cada límite evaluado |
| primaryReason | string | ✔ | motivo principal (XAI) |
| engineState | enum{NORMAL,RESTRICTED,COOLDOWN,HALTED,LOCKED} | ✔ | §3 ENG-005 |
| riskConfigHash / fxSnapshotId | hash / string | ✔ | reproducibilidad monetaria |

- **Restricciones (⛔).** `decision=APPROVE ⇒ approvedSize>0 ∧ ningún veto`; importes en
  `decimal`/`money` (nunca float); `approvedSize` nunca excede límites.
- **Invariantes.** El riesgo manda: `APPROVE` imposible si hay veto activo. Determinismo
  monetario (misma entrada ⇒ misma decisión).
- **Evolución.** Nuevo tipo de límite = nuevo `reasons.code` (MINOR, con tolerancia
  `UNKNOWN` en consumidores).
- **Ejemplo válido.** `{"decision":"APPROVE","approvedSize":"0.25","riskAmount":{"amount":"50.00","currency":"USD"},"engineState":"NORMAL",...}`.
  **Inválido.** `decision:"APPROVE"` con un `reasons` que incluye un veto activo, o
  `approvedSize` en float `0.25000001` (debe ser `decimal` redondeado a `lotStep`) →
  **rechazado**.

---

## 7. C6 · `execution.v1`

- **Propósito.** Publicar el ciclo de vida de órdenes/posiciones del OMS (event
  sourcing). Contrato de eventos inmutables.
- **Owner.** Execution Lead. **Producer.** ENG-006. **Consumers.** Portfolio, Risk,
  Replay, XAI.
- **Eventos:** `OrderCreated`, `OrderValidated`, `OrderSent`, `OrderAcknowledged`,
  `OrderRejected`, `PartialFillReceived`, `OrderFilled`, `PositionOpened`,
  `StopLossSet`, `TakeProfitSet`, `BreakEvenMoved`, `TrailingUpdated`,
  `PartialCloseExecuted`, `PositionClosed`, `OrderCancelled`, `OrderExpired`,
  `OrderFailed`, `SafeHaltTriggered`, `RecoveryStarted`, `ReconciliationCompleted`,
  `TradeArchived`.

**Campos comunes de los eventos de orden:**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| tradeId / correlationId | uuid | ✔ | agregado raíz |
| clientOrderId (COID) | string | ✔ | estable; **exactly-once** |
| brokerOrderId (BOID) | string | ○ | tras ack |
| symbol / side / orderType | symbol/enum/enum | ✔ | — |
| qty | decimal | ✔ | >0; `ROUND_DOWN` a lotStep |
| price | price | ○ | según tipo |
| state | enum{CREATED,VALIDATED,RISK_APPROVED,QUEUED,SENT,ACKNOWLEDGED,PARTIALLY_FILLED,FILLED,MANAGED,PARTIALLY_CLOSED,CLOSED,ARCHIVED,REJECTED,CANCELLED,EXPIRED,FAILED,SAFE_HALT,RECOVERY} | ✔ | cerrado |
| brokerEventId | string | ○ | dedup de fills |
| mode | enum{LIVE,PAPER,BACKTEST} | ✔ | mismo contrato en 3 modos |

**`Fill` (PartialFillReceived/OrderFilled):** `fillId, brokerEventId, qty:decimal,
price:price, eventTime, slippage:decimal`.

- **Restricciones (⛔).** `Σ fills.qty ≤ order.qty`; transición de estado válida según la
  máquina de ENG-006 §3; `qty`/`price` en `decimal`.
- **Invariantes.** Un `COID` produce **≤1** orden; un `brokerEventId` se aplica **≤1**
  vez (exactly-once lógico). Estados terminales no vuelven a activos.
- **Compatibilidad hacia atrás.** Eventos inmutables → **upcasters** obligatorios (§1.3).
  Añadir un evento nuevo = MINOR.
- **Ejemplo válido.** `OrderFilled {"tradeId":"...","clientOrderId":"coid-1","brokerOrderId":"b-9","qty":"0.25","price":"2385.30","state":"FILLED","brokerEventId":"be-77","mode":"LIVE"}`.
- **Ejemplo inválido.** Dos `OrderFilled` con el mismo `brokerEventId` (viola
  exactly-once), o transición `ARCHIVED → SENT` (estado terminal→activo) → **rechazado**.

---

## 8. C7 · `decision-record.v1`

- **Propósito.** Esquema **común y unificado** de toda decisión del sistema (opere o no).
  Núcleo de la reproducibilidad y del Decision Replay. **Cierra P0-D (provenance).**
- **Owner.** CTO + Quant. **Producer.** todos los motores de decisión (MCE, Trading,
  Risk, Execution). **Consumer.** Decision Replay (ENG-009), audit (SEC-000).

**`DecisionRecord` (campos):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| decisionId | uuid | ✔ | idempotencia por `(symbol,bar,configHash)` |
| correlationId | uuid | ✔ | hilo end-to-end |
| producer | string | ✔ | motor emisor |
| symbol / timestamp | symbol / timestampNs | ✔ | — |
| **dataVersion** | string | ✔ | ⛔ snapshot MDE exacto (provenance) |
| **configHash** | hash | ✔ | ⛔ config/pesos exactos |
| **dnaHash** | hash | ○ | si aplica |
| decisionType | enum{CONTEXT_GATE,ENTRY_SCORE,RISK,EXECUTION} | ✔ | — |
| inputsSnapshotRef | string | ✔ | ref a las features/estado usados |
| factorBreakdown | array<{factor,weight,points,condition}> | ○ | si hay scoring |
| vetoes | array<{vetoId,active,reason}> | ○ | — |
| action | string | ✔ | resultado (`no_trade`/`enter_long`/`approve`/…) |
| primaryReason | string | ✔ | motivo exacto |
| outcomeRef | uuid | ○ | enlaza al Trade/resultado |

- **Restricciones (⛔).** `dataVersion` + `configHash` **siempre** presentes (sin
  provenance no hay reproducibilidad → **rechazado**). `decisionId` idempotente.
- **Invariantes.** Toda evaluación ⇒ exactamente un `DecisionRecord`; replay del
  `dataVersion`+`configHash` reconstruye la misma decisión (bit a bit).
- **Compatibilidad / evolución.** Añadir campos de contexto = opcionales (MINOR).
- **Ejemplo válido.** `{"decisionType":"ENTRY_SCORE","action":"no_trade","primaryReason":"score_below_threshold","dataVersion":"eurusd-2026-07","configHash":"cfg-abc"}`.
- **Ejemplo inválido.** Falta `dataVersion` o `configHash` (sin provenance), o dos
  registros con el mismo `decisionId` y distinto contenido → **rechazado**.

---

## 9. C8 · `explanation.v1`

- **Propósito.** Explicación estructurada de cualquier decisión (nunca "porque sí").
  Contrato consumido por XAI y la UI.
- **Owner.** ML Lead. **Producer.** todos (derivado del DecisionRecord). **Consumers.**
  Explainable AI (ENG-010), Dashboard.

**`Explanation` (campos):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| decisionId | uuid | ✔ | enlaza C7 |
| action / side / score / threshold | string/enum/int32/int32 | ✔ | — |
| detected | array<{feature,value,sourceDetector}> | ✔ | qué detectó |
| confirmed | array<{factor,points,condition}> | ✔ | qué confirmó |
| discarded | array<{factor,reason,expectedCondition}> | ✔ | qué descartó |
| weights | array<{factor,weight,pointsAwarded}> | ✔ | peso de cada criterio |
| rulesFired | array<string> | ✔ | reglas activadas |
| vetoesBlocked | array<{vetoId,reason}> | ✔ | reglas que bloquearon (vacío si ninguna) |
| primaryReason | string | ✔ | motivo exacto |
| narrativeText | string | ○ | narrativa (plantilla/LLM, fiel al registro) |

- **Restricciones (⛔).** Toda aserción de `narrativeText` **mapea** a un campo del
  DecisionRecord (fidelidad; prohibido factor inventado). `Σ weights.pointsAwarded ==
  score`.
- **Invariantes.** Cobertura 100 %: toda decisión tiene `Explanation` con los 7 bloques
  (detected/confirmed/discarded/weights/rulesFired/vetoesBlocked/primaryReason).
- **Compatibilidad / evolución.** §1. La narrativa LLM no cambia el contrato (es un
  campo derivado opcional).
- **Ejemplo válido.** `{"action":"no_trade","score":58,"threshold":70,"discarded":[{"factor":"fvg","reason":"absent"}],"vetoesBlocked":[{"vetoId":"news_window","reason":"GBP high-impact in 11m"}],"primaryReason":"veto:news_window"}`.
- **Ejemplo inválido.** `narrativeText` que cita un factor **no** presente en el
  DecisionRecord (viola fidelidad), o `Σ pointsAwarded ≠ score` → **rechazado**.

---

## 10. C9 · `market-dna.v1`

- **Propósito.** Perfil configurable por activo (adapta **filtros**, no reglas).
  Contrato de configuración versionada.
- **Owner.** Quant + Platform. **Producer.** Config/Platform. **Consumers.** MCE, SMC,
  Risk.

**`MarketDNA` (campos):**
| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| symbol / assetClass | symbol / enum{FX,METAL,INDEX,CRYPTO} | ✔ | — |
| sessionTimezone | string(IANA) | ✔ | zona con DST |
| dnaVersion / dnaHash | string / hash | ✔ | versionado (reproducibilidad) |
| volatility | object{typicalAtr:price, atrLow, atrHigh, atrExtreme, volOfVol} | ✔ | multiplicadores >0, crecientes |
| liquidity | object{behavior, depthClass, sessionLiquidityProfile, lowLiquidityWindows} | ✔ | — |
| efficiencyHours | array<{from,to,tz}> | ✔ | ventanas válidas |
| spread | object{typical:price, max:price} | ✔ | `typical ≤ max` |
| atr | object{period:int32, habitualValue:price, unit} | ✔ | period>0 |
| news | object{currencies:array, highImpactWeight, blockBefore, blockAfter} | ✔ | ventanas ≥0 |
| detectorSensitivity | map<string,decimal> | ✔ | **overrides de filtros** (equalLevelTol, sweepMinPenetration, displacementAtrMult, fvgMinSize, fibMinLegAtr…) |
| recommendedParams | map<string,any> | ○ | sugerencias por activo |

- **Restricciones (⛔).** `detectorSensitivity` solo puede overridear **parámetros de
  filtro** existentes; **no** puede introducir/alterar **reglas** (invariante DNA:
  adapta filtros, no lógica). `atrLow < atrHigh < atrExtreme`; `spread.typical ≤ max`.
- **Invariantes.** El `dnaHash` entra en `configHash`/DecisionRecord → una decisión sabe
  con qué DNA se evaluó (reproducibilidad). Sin auto-mutación en producción.
- **Compatibilidad / evolución.** Añadir una sensibilidad/param = clave nueva en el map
  (MINOR). Cambiar la semántica de un filtro = MAJOR (afecta a la detección).
- **Ejemplo válido.** `{"symbol":"XAUUSD","assetClass":"METAL","spread":{"typical":"0.20","max":"0.60"},"detectorSensitivity":{"equalLevelTol":"0.15","displacementAtrMult":"1.5"},...}`.
- **Ejemplo inválido.** `detectorSensitivity` que define una clave que **no** es un
  parámetro de filtro conocido (intento de "regla" nueva), o `atrHigh < atrLow` →
  **rechazado**.

---

## 11. Registro de congelación (Freeze Register)

| Contrato | Versión congelada | Owner | Estado | Test gate |
|----------|-------------------|-------|--------|-----------|
| C1 `market-data.v1` | 1.0.0 | Platform/Data | frozen-candidate | producer+consumer pendiente |
| C2 `market-context.v1` | 1.0.0 | Quant | frozen-candidate | pendiente |
| C3 `smart-money.v1` | 1.0.0 | Quant | frozen-candidate | pendiente |
| C4 `trade-intent.v1` | 1.0.0 | Quant/Exec | frozen-candidate | pendiente |
| C5 `risk.v1` | 1.0.0 | Risk | frozen-candidate | pendiente |
| C6 `execution.v1` | 1.0.0 | Execution | frozen-candidate | pendiente |
| C7 `decision-record.v1` | 1.0.0 | CTO/Quant | frozen-candidate | pendiente |
| C8 `explanation.v1` | 1.0.0 | ML | frozen-candidate | pendiente |
| C9 `market-dna.v1` | 1.0.0 | Quant/Platform | frozen-candidate | pendiente |

**Definición de "frozen" (⛔):** un contrato pasa de `frozen-candidate` a **`frozen`**
cuando (1) su esquema vive en `/contracts`, (2) tiene producer test + ≥1 consumer test
verdes, y (3) el gate de compatibilidad del Schema Registry está activo. Mientras sea
`frozen-candidate`, aún admite ajustes de forma **sin** RFC; una vez `frozen`, cualquier
cambio sigue §1.4/§1.5.

## 12. Control de cambios de este documento
- Cambios de contrato: **RFC + ADR** (owner + todos los consumers) → §1.5.
- Este documento es la **autoridad**; los esquemas en `/contracts` son su
  materialización generable. Ante discrepancia, **manda el esquema versionado** en el
  registry (y se corrige el documento).
- Trazabilidad: cada contrato ↔ los motores que lo producen/consumen ↔ sus tests
  (matriz BLD-003).

---

> **Versión 1.0 — `frozen-candidate` (🟡).** Autoridad oficial de los contratos
> inter-motor de ELYON QUANT. Congela C1–C9 en `.v1` para permitir el desarrollo
> paralelo de los motores. Promoción a **`frozen` (🟢)** y a `CORE ARCHITECTURE v1.0`
> GA según el checklist de la Architecture Review §9.2 (contract tests verdes + Schema
> Registry activo). Cambios vía RFC/ADR (§1.5).
