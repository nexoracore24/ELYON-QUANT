<!--
title: ELYON QUANT — Execution Engine Bible (OMS)
id: ENG-006 (Execution Engine / Order Management System)
owner: Execution Lead
reviewers: [CTO/Principal Architect, Risk Lead, Quant Lead, Platform/Data Lead, Security Lead, QA Lead]
status: draft
version: 0.1
last_updated: 2026-07-29
supersedes: amplía trading-engine-bible.md §27–§33 (gestión de entrada/salida)
-->

# ELYON QUANT — EXECUTION ENGINE BIBLE (OMS)

> **El Order Management System (OMS) oficial de ELYON QUANT.** No es un módulo que
> "envía órdenes": es el **responsable absoluto del ciclo de vida completo** de
> cada operación, desde su creación hasta su archivo. **Ninguna ejecución puede
> existir fuera del OMS** — toda orden pasa obligatoriamente por él. Es
> **determinista, auditable, idempotente y tolerante a fallos**, con **event
> sourcing** total.

Especificación de ingeniería de nivel institucional. Define arquitectura, máquina
de estados, contratos, eventos, gestión de fallos y pruebas que la implementación
debe cumplir y los tests deben verificar (BLD-003). No es teoría.

---

## 0. Preámbulo

### 0.1 Reglas obligatorias (invariantes ⛔)
1. **OMS único.** Toda ejecución pasa por el OMS. No existe envío de orden fuera de
   él (ni "atajos" a broker). El único que habla con el broker es el
   `execution-gateway` **a través** del OMS.
2. **Determinismo total.** Dado el mismo **log de eventos** (comandos + respuestas
   de broker), el OMS transita de estado de forma **idéntica**. La reproducción del
   log reconstruye el estado **bit a bit**.
3. **Idempotencia absoluta.** Un mismo comando **nunca** produce dos órdenes; un
   mismo evento de broker **nunca** se aplica dos veces (claves de idempotencia +
   dedup por `broker_event_id`).
4. **Event sourcing.** Toda acción genera **eventos inmutables**; el estado es una
   **proyección** del log. Nada se modifica en sitio; nada se borra.
5. **Sin pérdida de eventos.** Outbox + persistencia **antes** de confirmar;
   at-least-once + idempotencia → ningún evento se pierde ni se duplica.
6. **Sin duplicación de órdenes.** Garantizada por la máquina de estados (una sola
   transición `QUEUED→SENT`) + `client_order_id` estable + reconciliación.
7. **Reproducibilidad completa.** Replay del log → mismo estado (con el `Clock` y
   los precios del MDE inyectados).
8. **Fail-safe.** Ante fallo/duda: `SAFE_HALT` (no enviar nuevas) y **proteger** lo
   abierto; nunca "adivinar" el estado del broker → **reconciliar**.
9. **Multi-modo, multi-broker.** El **mismo núcleo OMS** opera en **live, paper y
   backtest** y contra **múltiples brokers**; solo cambia el *adapter* (ACL).

### 0.2 Reparto de responsabilidades
- **`execution` (módulo, OMS core):** máquina de estados, event store, idempotencia,
  ruteo lógico, gestión de posición, reconciliación, recovery. Determinista.
- **`execution-gateway` (servicio, Rust, baja latencia):** I/O real con el broker
  (FIX/API), sub-ms, sin lógica de negocio; traduce comandos del OMS ↔ protocolo del
  broker (ACL). Es "tonto y rápido"; el OMS es "inteligente y determinista".

### 0.3 Posición en el pipeline
```
Trading Engine (ENG-001) ── setup + RiskApproved(size, SL, TP) ──►  EXECUTION ENGINE (ENG-006)
                                                                     │  (OMS: ciclo de vida)
Risk Engine (ENG-005) ⇄ (bloqueante: sin RiskApproved no hay envío)  │
Market Data (ENG-000) ── precio oficial / conciliación ─────────────►│
                                                                     ▼
                           execution-gateway ──► Broker Connectivity (ENG-008) ──► Broker/Exchange
   Todo → event store inmutable → Decision Replay (ENG-009) + Explainable AI (ENG-010) + audit (SEC-000)
```

---

## 1. Arquitectura del OMS

```
   TradeIntent (del Trading Engine, ya RiskApproved)
        │
        ▼
   ┌──────────────────────────── EXECUTION ENGINE (OMS core) ───────────────────────────┐
   │                                                                                     │
   │  Order Validator ─► Idempotency Guard ─► Order Router ─► Fill Handler                │
   │        │                  │                    │             │                       │
   │        │                  │                    │             ▼                       │
   │        │                  │                    │        Position Manager (SL/TP/BE/  │
   │        │                  │                    │        trailing/parciales/cierres)  │
   │        │                  │                    ▼             │                       │
   │        │                  │            Broker Adapter (ACL) ─┼─► execution-gateway    │
   │        │                  │                    ▲             │                       │
   │        │                  │            Reconciliation ◄──────┘  (posición OMS ⇄ broker)│
   │        │                  │                    │                                      │
   │  Circuit Breaker ◄────────┴──── Recovery Engine ┴──── Retry/Timeout Policy            │
   │                                                                                       │
   │  ────────────── EVENT STORE (append-only, inmutable) — fuente de verdad ───────────── │
   └───────────────────────────────────────┬─────────────────────────────────────────────┘
                                            ▼
                         Eventos → bus → Portfolio/Risk/Replay/XAI/Analytics
```

**Componentes:**
- **Order Validator:** valida el `TradeIntent` (símbolo tratable, precios coherentes,
  `RiskApproved` presente y vigente, mercado abierto).
- **Idempotency Guard:** genera/verifica `client_order_id` + `idempotency_key`; evita
  duplicados.
- **Order Router:** decide destino (broker/venue), aplica retry/timeout, envía por el
  adapter. (SOR — smart order routing — cuando hay varios venues.)
- **Broker Adapter (ACL):** traduce al protocolo del broker (uno por broker/modo).
- **Fill Handler:** procesa acks y fills (parciales/múltiples), agrega y valida.
- **Position Manager:** gestiona la posición viva (SL/TP/BE/trailing/parciales/cierres).
- **Reconciliation:** compara estado OMS ⇄ broker de forma continua/periódica.
- **Recovery Engine:** reconstruye estado tras reinicio/desconexión y converge a la
  verdad del broker.
- **Circuit Breaker:** corta el ruteo ante fallos/latencia del broker → `SAFE_HALT`.
- **Retry/Timeout Policy:** política determinista (con `Clock` inyectado).
- **Event Store:** log append-only inmutable; **fuente de verdad** de todo el OMS.

---

## 2. Modelo de dominio

- **`Order`** (orden de broker): `order_id`(interno), `client_order_id`(COID),
  `broker_order_id`(BOID), `correlation_id`, `symbol`, `side`, `type`(market/limit/
  stop), `qty`, `price?`, `tif`, `state`, `idempotency_key`, `attempt`.
- **`Fill` / ExecutionReport:** `fill_id`, `broker_event_id`, `order_id`, `qty`,
  `price`, `event_time`, `liquidity_flag?`.
- **`Position`:** `position_id`, `correlation_id`, `symbol`, `net_qty`, `avg_price`,
  `sl`, `tp[]`, `be_moved`, `trailing_state`, `realized_pnl`, `state`.
- **`Trade` (agregado raíz):** enlaza `decision_id` (ENG-009) → orden(es) de
  entrada → posición → órdenes de gestión/cierre. Es la **unidad del ciclo de vida**
  y del event sourcing. Su `correlation_id` hila **todo** (decisión→ejecución→fills→
  posición→eventos).

---

## 3. Estados internos, máquina de estados y lifecycle

### 3.1 Lifecycle completo (happy path)
```
CREATED
  ↓  validación estructural OK
VALIDATED
  ↓  RiskApproved(size, SL, TP) presente y vigente (⛔ bloqueante)
RISK_APPROVED
  ↓  encolada con COID + idempotency_key
QUEUED
  ↓  enviada al broker vía gateway (una sola vez)
SENT
  ↓  ack del broker (BOID asignado)
ACKNOWLEDGED
  ↓  primer fill parcial
PARTIALLY_FILLED   ←──┐ (múltiples fills se agregan aquí)
  ↓  fill total       │
FILLED  ──────────────┘
  ↓  posición abierta → gestión activa
MANAGED   (SL/TP/BE/trailing/parciales)
  ↓  cierre parcial (scale-out)
PARTIALLY_CLOSED  ←──┐ (varios parciales)
  ↓  cierre total     │
CLOSED  ──────────────┘
  ↓  conciliada, PnL final sellado
ARCHIVED (terminal, inmutable)
```

### 3.2 Estados excepcionales
- **REJECTED:** rechazo en validación, por Risk, o por el broker (`OrderRejected`).
  Terminal (o re-evaluable como nuevo `Trade`).
- **CANCELLED:** cancelación (usuario/sistema/estrategia) antes de fill total.
- **EXPIRED:** vence por `tif`/GTD o por caducidad del setup (ENG-001 §27).
- **FAILED:** error irrecuperable de envío/estado (tras agotar política de recovery).
- **SAFE_HALT:** parada de emergencia (circuit breaker / kill-switch de Risk /
  contexto crítico del MCE): **no** se envían nuevas; las posiciones se **protegen**.
- **RECOVERY:** estado transitorio tras reinicio/desconexión: el Trade queda en
  reconciliación hasta converger con el broker (luego vuelve al estado real).

### 3.3 Máquina de estados (transiciones y guardas)
```
                    ┌───────────── SAFE_HALT ◄───────── (circuit breaker / kill-switch)
                    │                   │  reanudar (manual/condiciones)
                    ▼                   ▼
 CREATED ─► VALIDATED ─► RISK_APPROVED ─► QUEUED ─► SENT ─► ACKNOWLEDGED
    │           │  reject       │ reject      │        │ timeout/err     │
    │           ▼               ▼             │        ▼                 ▼
    │        REJECTED        REJECTED         │   (retry/reconcile)  PARTIALLY_FILLED ─► FILLED
    │                                         │        │                                  │
    │                                         │        ▼                                  ▼
    │                                    CANCELLED / EXPIRED                            MANAGED
    │                                                                                     │
    │                                                              cierre parcial ◄───────┤
    │                                                              PARTIALLY_CLOSED ──► CLOSED ─► ARCHIVED
    │
    └─ error irrecuperable en cualquier envío ─► FAILED ─► (RECOVERY → estado real | ARCHIVED)
  * Desde SENT/ACKNOWLEDGED/…: cualquier reinicio/desconexión ─► RECOVERY ─► (reconcilia) ─► estado verdadero.
```

**Guardas (⛔):**
- `VALIDATED → RISK_APPROVED` **exige** `RiskApproved` vigente (no caducado); sin él
  → REJECTED. (Contrato bloqueante con ENG-005.)
- `QUEUED → SENT` ocurre **exactamente una vez** por `client_order_id` (idempotencia).
- Ningún estado terminal (`ARCHIVED/REJECTED/…`) transita a activo (inmutabilidad del
  desenlace).
- Toda transición **emite un evento** y se persiste **antes** de actuar (event
  sourcing + no pérdida).

### 3.4 Invariantes de estado
- El estado del `Trade` es **siempre** una proyección determinista del log de
  eventos (reconstruible).
- La suma de fills = qty de la posición (conservación); divergencia → RECOVERY.
- SL/TP siempre consistentes con la posición viva (nunca "huérfanos").

---

## 4. Order Routing

### 4.1 Order Router
- **Objetivo.** Llevar una orden `QUEUED` al broker/venue correcto **exactamente una
  vez**, gestionando retry/timeout sin duplicar.
- **SOR (multi-venue):** si hay varios venues para el símbolo, selecciona por
  `routing_policy` (mejor precio/latencia/coste); determinista dado el snapshot.
- **Regla (⛔):** una orden solo pasa `QUEUED→SENT` **una vez**; los reintentos
  reutilizan el **mismo** `client_order_id` (no crean orden nueva).

### 4.2 Broker Adapter (ACL)
- **Objetivo.** Traducir el modelo canónico del OMS ↔ protocolo del broker
  (MT5/IB/Binance/paper/sim). **Uno por broker/modo.** Aísla al OMS de las
  peculiaridades del broker (ENG-008).
- **Contrato.** `place(order) → ack{BOID}` | `error`; `cancel(order)`; `modify(sl/tp)`;
  `query(COID|BOID) → estado`; stream de `ExecutionReport`.
- **Regla.** El adapter **no** decide nada de negocio; solo traduce y reporta.

### 4.3 Idempotency Keys · Client/Broker Order IDs · Correlation IDs
- **`client_order_id` (COID):** identidad **estable y determinista** de la orden,
  derivada del `Trade`/intento (p.ej. `hash(correlation_id, leg, attempt_group)`).
  **Se reutiliza en los reintentos** → el broker deduplica por COID.
- **`idempotency_key`:** clave del **comando** (place/cancel/modify); el gateway y el
  broker garantizan "aplicar una sola vez".
- **`broker_order_id` (BOID):** id asignado por el broker en el ack; se **mapea** al
  COID.
- **`correlation_id`:** hilo único `decision_id → orden(es) → fills → posición →
  eventos` (trazabilidad total para Replay/XAI/audit).

### 4.4 Duplicate Prevention
- **En el envío:** Idempotency Guard rechaza reenviar un COID ya `SENT` salvo por la
  ruta de recovery (que **consulta** antes de reenviar).
- **En la recepción:** dedup de `ExecutionReport` por `broker_event_id` → un fill
  nunca se aplica dos veces.
- **Regla clave anti-duplicado (⛔):** ante **timeout de ack**, el OMS **no reenvía a
  ciegas**: primero `query(COID)`; si la orden existe en el broker → la **adopta**;
  solo si **no** existe → reenvía con el **mismo** COID. Esto elimina las órdenes
  duplicadas por timeout.

### 4.5 Retry Policy y Timeout Policy
- **Timeouts:** `send_timeout`, `ack_timeout`, `fill_timeout` (por broker/modo).
- **Retry:** número máximo (`max_retries`), backoff exponencial con tope; el
  *scheduling* usa el **`Clock` inyectado** (determinista en backtest). Cada reintento
  **reutiliza COID/idempotency_key**.
- **Al agotar retries:** `query`+`reconcile`; si sigue sin certeza → `FAILED` +
  `RECOVERY`.
- **Determinismo:** en live el reloj es real pero las **decisiones** (reintentar,
  adoptar, fallar) son función determinista del estado + eventos; en backtest el
  `Clock` reproduce los mismos tiempos → mismas transiciones.
- **Pseudocódigo (envío seguro).**
```
def sendOrder(order):
    if guard.alreadySent(order.coid): return                 # idempotencia
    emit(OrderSent(order)); persist()                        # evento ANTES de I/O
    ack = adapter.place(order, key=order.idempotency_key)
    on timeout:
        state = adapter.query(order.coid)                    # NO reenviar a ciegas
        if state.exists: adopt(state)                        # adoptar (dedup)
        elif attempts < max_retries: schedule(retry, backoff(attempts, clock))
        else: emit(OrderFailed(order)); enterRecovery(order)
```

---

## 5. Fills (ejecuciones)

### 5.1 Partial fills / Multiple fills / Fill aggregation
- Una orden puede llenarse en **varios** `ExecutionReport` (parciales/múltiples). El
  Fill Handler **agrega**: `filled_qty += fill.qty`; recalcula `avg_price` ponderado.
- Estados: `ACKNOWLEDGED → PARTIALLY_FILLED (…) → FILLED` cuando `filled_qty == qty`.
- **Determinismo:** los fills se aplican en **orden de `event_time`**; empates por
  `broker_event_id` estable.

### 5.2 Fill validation · Price validation · Spread validation
- **Fill validation:** `broker_event_id` no visto, `order_id` conocido, `qty>0`,
  `filled_qty ≤ qty` (sobre-fill → RECOVERY/alerta).
- **Price validation:** el precio del fill se contrasta con el **precio oficial del
  MDE** (ENG-000) en ese `event_time`; desvío > `price_sanity_tol` → alerta/quarantine
  (posible dato erróneo del broker).
- **Spread validation:** al **enviar**, se re-verifica el spread (fuente MDE); si
  `spread_state=blowout` → no enviar (coherente con el veto de Risk/MCE).

### 5.3 Slippage control
- `slippage = fill_price − expected_price` (con signo por lado). Política
  `slippage_policy`:
  - `≤ max_slippage` → aceptar.
  - `> max_slippage` → según config: **rechazar** (si aún cancelable), **aceptar y
    marcar**, o **cerrar inmediato**. Todo registrado y explicable.
- Slippage extremo dispara señal a Risk (recalcular) y puede abrir circuit breaker.

### 5.4 Fill reconciliation
- El OMS reconcilia **continuamente** su posición con la del broker: `net_qty` OMS vs
  broker, órdenes vivas, SL/TP. Divergencia → `RECOVERY` + alerta; se converge a la
  **verdad del broker** (el broker es la autoridad del estado real de la cuenta).

### 5.5 Latency measurement
- Se mide y registra: `send→ack`, `ack→first_fill`, `fill_stream_lag`. Alimenta SLOs
  de ejecución (OPS-006), el circuit breaker y el análisis de calidad de ejecución.

---

## 6. Position Management

Gestión de la posición viva (estado `MANAGED`). El **Trading Engine propone** (§27–§33
de ENG-001) y el **Risk Engine autoriza** (ENG-005); el **OMS ejecuta y es la fuente
de verdad** del estado de la posición.

- **Apertura.** Al `FILLED`, se crea/actualiza `Position` con `avg_price`, `net_qty`;
  se **colocan SL y TP** como órdenes protectoras en el broker (o gestionadas por el
  OMS si el broker no las soporta nativamente). Evento `PositionOpened`.
- **SL (Stop Loss).** Orden stop protectora; **nunca se amplía** (`never_widen_sl`,
  ENG-001 §29); solo se reduce (BE/trailing). Cambios → evento `StopLossSet`.
- **TP (Take Profit).** Uno o varios niveles (parciales); `TakeProfitSet`.
- **Break Even.** Al disparador (`be_at_r`/estructura), mover SL a entrada+offset;
  `BreakEvenMoved`. El OMS valida que el movimiento sea monótono a favor.
- **Trailing Stop.** Por estructura/ATR/FVG (ENG-001 §32); el OMS aplica solo
  movimientos **monótonos**; `TrailingUpdated`.
- **Parciales (scale-out).** Cierre parcial en TP1/TP2 (ENG-001 §33) → orden de cierre
  parcial → `PartialCloseExecuted`; estado `PARTIALLY_CLOSED`.
- **Cierre manual.** Orden del usuario/operador → cierre de la posición (total o
  parcial); mismos contratos e idempotencia.
- **Cierre automático.** Por SL/TP alcanzado, trailing, cierre inteligente (POI opuesto
  ENG-001 §30), fin de sesión (funded), o kill-switch de Risk.
- **Gestión tras desconexión.** Si el feed/broker se cae con posición abierta: el OMS
  **no asume**; entra en `RECOVERY`, consulta al broker el estado real de la posición
  y de las órdenes protectoras (SL/TP), y **reconstruye**. Si las protectoras no están
  en el broker, las **re-coloca** (idempotente). Prioridad: **la posición nunca queda
  sin SL**.
- **Recuperación de estado.** Ver §7.4 (Recovery Engine) y §8 (event sourcing):
  reconstrucción desde el log + reconciliación con el broker.

**Regla (⛔):** toda modificación de la posición (SL/TP/BE/trailing/parcial/cierre) es
un **evento inmutable**; el estado de la posición es la proyección del log.

---

## 7. CQRS + Event Sourcing (patrón arquitectónico núcleo)

El OMS se diseña con **CQRS + Event Sourcing donde aporta ventajas claras**
(separación comando/consulta y auditoría/reconstrucción), sin sobre-aplicarlo a
lecturas triviales.

### 7.1 Por qué aquí sí (justificación)
El OMS es el caso de uso **ideal** para ES: necesita auditoría total, idempotencia,
reconstrucción tras fallo y consultas temporales. Un modelo CRUD mutable perdería el
"cómo se llegó" a cada estado. Por eso el **event store es la fuente de verdad** y el
estado es una **proyección**.

### 7.2 Lado de **Comandos** (write)
- **Comandos** (intención de cambio): `PlaceOrder`, `CancelOrder`, `ModifyOrder`,
  `SetStopLoss`, `SetTakeProfit`, `MoveBreakEven`, `UpdateTrailing`, `ClosePartial`,
  `ClosePosition`, `Reconcile`, `Recover`.
- **Handler:** valida invariantes del agregado `Trade` → **añade eventos** al store →
  aplica la transición. **Un solo escritor por stream** (`correlation_id`) con
  **concurrencia optimista por versión** → consistencia fuerte dentro del agregado.
- Nada muta en sitio: cambiar un SL = evento `StopLossSet`, no un UPDATE.

### 7.3 Lado de **Consultas** (read)
- **Read models / proyecciones** construidas desde los eventos (eventualmente
  consistentes), optimizadas por caso de uso: `OpenPositions`, `OrderBlotter`,
  `FillLedger`, `TradeHistory`, `ExecutionQualityStats`.
- Las consultas **nunca** tocan el write-side ni el broker; leen proyecciones.

### 7.4 Consistencia
- **Fuerte** dentro del agregado (`Trade`), **eventual** en los read models y entre
  agregados. Las decisiones críticas (¿enviar? ¿duplicado?) usan el **write-side**
  (fuerte), no una proyección.

---

## 8. Transactional Outbox Pattern

**Problema.** Persistir un evento **y** publicarlo al bus son dos operaciones; un
fallo entre ambas pierde o duplica eventos (*dual-write*).

**Solución (⛔).** En **la misma transacción** que añade el evento al event store, se
inserta una fila en la tabla **`outbox`**. Un **relay** lee el outbox y publica al bus,
marcando cada fila como publicada. Garantías:
- **Atómico:** o se guardan evento + outbox, o nada → **ningún evento se pierde entre
  persistencia y publicación**.
- **At-least-once + idempotencia:** el relay puede reintentar; los consumidores
  deduplican por `event_id` (**inbox**) → sin duplicados efectivos.
- **Orden por agregado:** el outbox preserva el orden por `correlation_id`.
```
BEGIN TX
   append(event_store, event)          # fuente de verdad
   insert(outbox, event)               # misma TX
COMMIT
--- (asíncrono) ---
relay: for row in outbox where not published: publish(bus,row); mark_published(row)
```

---

## 9. Saga / Process Manager

Los procesos **largos** del OMS se coordinan como **sagas / process managers**
**durables** (Temporal, stack ENG-004/ARC): estado persistente, pasos idempotentes,
**timeouts**, **reintentos** y **compensaciones**. El process manager **reacciona a
eventos** y **emite comandos**; no contiene I/O directo.

| Saga | Pasos | Compensación / timeout |
|------|-------|------------------------|
| **OpenPosition** | send entry → ack → fills → colocar SL+TP → `PositionOpened` | si falla antes de fill: cancel; si falla tras fill sin SL: **re-colocar SL** o cerrar (⛔ nunca posición sin SL) |
| **Partial (scale-out)** | disparador TP1 → `ClosePartial` → confirmar → mover BE | reintento idempotente; si el parcial no confirma: reconciliar |
| **Break Even** | disparador `be_at_r` → `MoveBreakEven` (monótono) → confirmar | si el broker rechaza: reintento; si imposible: alerta + proteger |
| **Trailing Stop** | por vela: recomputar → `UpdateTrailing` (solo aprieta) | idempotente; nunca afloja |
| **Close** | `ClosePosition` → confirmar cierre → sellar PnL → `Archived` | reintento; si divergencia: reconciliar |
| **Recovery** | detectar fallo → `query` broker → reconciliar → converger estado real | timeout de reconciliación → SAFE_HALT + alerta |

**Reglas (⛔):** cada paso es **idempotente** (reutiliza claves); toda saga tiene
**timeout** y **compensación**; el estado de la saga es durable (sobrevive reinicios).

---

## 10. Exactly-Once Semantics (a nivel lógico)

La entrega *exactly-once* real es imposible en sistemas distribuidos; ELYON QUANT
logra **exactly-once lógico/efectivo**: **una intención de orden se ejecuta una sola
vez**, incluso tras reinicios o reintentos.

Mecanismos combinados:
- **Idempotency key / COID estable:** los reintentos reutilizan la misma clave → el
  broker deduplica.
- **Query-before-resend:** ante timeout, se **consulta** antes de reenviar (§4.4).
- **Dedup de eventos:** `broker_event_id` procesados en un **dedup store** → un fill
  nunca se aplica dos veces.
- **At-least-once + handlers idempotentes** (outbox/inbox).
- **Máquina de estados:** `QUEUED→SENT` una sola vez por COID.
- **Persistencia previa** (evento antes de I/O) → tras un crash, se sabe si ya se
  envió.

**Invariante verificable (⛔):** para todo `intent_id`, `count(órdenes ejecutadas) ≤
1`; `count(aplicaciones de un broker_event_id) ≤ 1` (tests T-dup, T-restart).

---

## 11. Circuit Breakers (independientes por dependencia)

Cuatro **circuit breakers independientes** (estados `CLOSED → OPEN → HALF_OPEN`, con
umbrales de tasa de error/latencia/fallos consecutivos). Independientes = el fallo de
uno no ciega a los demás.

| Breaker | Se abre por | Efecto en la ejecución |
|---------|-------------|------------------------|
| **Broker** | rechazos/errores/latencia del broker o gateway | **detener ruteo** de nuevas órdenes a ese broker → `SAFE_HALT` (scope broker); **proteger** posiciones; reconciliar |
| **Market Data** | feed del MDE degradado/sin precio fiable | **no abrir** nuevas (sin precio no hay decisión válida); mantener/proteger abiertas con último precio conocido + alerta; **no** confiar en disparos de SL/TP internos |
| **Red (Network)** | pérdida/latencia de red | degradar, encolar comandos, reconciliar al recuperar; evitar reenvíos ciegos |
| **Riesgo** | Risk Engine no responde/incoherente | **fail-safe: ninguna orden nueva** (sin `RiskApproved` no hay envío, ⛔); no afecta a la protección de lo abierto |

- **HALF_OPEN:** sondas limitadas; si tienen éxito → `CLOSED`; si fallan → `OPEN`.
- Cada apertura/cierre es un **evento** (auditable) y alimenta Health Monitoring (§12).

---

## 12. Health Monitoring

Salud por dependencia (broker, market data, riesgo, red, gateway) y **salud agregada**
del OMS, en tres estados. Derivada de circuit breakers (§11) + heartbeats + SLOs de
latencia.

| Estado | Definición | Efecto en la ejecución |
|--------|------------|------------------------|
| **HEALTHY** | dependencias OK, latencia dentro de SLO | Operación **normal** |
| **DEGRADED** | alguna dependencia con latencia alta / errores parciales / breaker HALF_OPEN | **Restringido**: reducir/pausar nuevas entradas (según dependencia), reforzar protección, priorizar gestión sobre apertura |
| **UNAVAILABLE** | dependencia crítica caída / breaker OPEN | `SAFE_HALT` (scope afectado): **no nuevas órdenes**, **proteger** abiertas, entrar en RECOVERY/reconciliación |

- El mapeo salud→comportamiento es **determinista** y configurable.
- La salud se **publica** (evento + métrica) y es visible en el Dashboard (DES-006) y
  para Risk (que puede endurecer límites en DEGRADED).

---

## 13. Dead Letter Queue (DLQ)

Toda orden o evento que **no pueda procesarse** (tras agotar reintentos, mensaje
"veneno", error de broker desconocido, evento incoherente) se envía a la **DLQ** —
**nunca se pierde en silencio**.

- **Contenido:** payload original, `correlation_id`, tipo, `error`, `attempts`,
  `first_seen`, `last_error_time`, breaker/estado en el momento.
- **Distinciones:** DLQ (fallo de **procesamiento**) ≠ `QUARANTINE` del MDE (fallo de
  **validación de dato**) ≠ estado `FAILED` de una orden (desenlace del ciclo).
- **Tratamiento:** alerta inmediata; **reproceso** manual o automático (idempotente,
  reutiliza claves); análisis post-mortem; **auditable**. Métrica `dlq_depth`
  vigilada (una DLQ creciente es un incidente).
- **Regla (⛔):** de la DLQ **no** se ejecutan órdenes automáticamente sin pasar de
  nuevo por validación + Risk (evita ejecutar intenciones caducadas).

---

## 14. Observabilidad

- **Logging estructurado** (JSON): cada log lleva `correlation_id`, `trade_id`,
  `coid`, `state`, `tenant_id`; **nunca** credenciales de broker ni PII. Niveles
  ERROR/WARN/INFO/DEBUG (§ ENGX/Coding Standards).
- **Métricas** (Prometheus): tasas y latencias por etapa (send/ack/fill), colas,
  breakers, DLQ, reconciliaciones.
- **Trazas distribuidas** (OpenTelemetry): un *trace* por `Trade` que abarca
  `decisión (ENG-001/009) → orden → gateway → broker → fill → posición`, con *spans*
  por comando/saga.
- **Correlation IDs:** el `correlation_id` (y `decision_id`) se propaga por logs,
  métricas, trazas y eventos → hilo único de extremo a extremo.
- **Latencia extremo a extremo:** `decision_time → order_sent → ack → first_fill →
  position_open` (y `signal→fill`), con p50/p95/p99 por broker/símbolo.
- **KPIs del motor:** `fill_rate`, `reject_rate`, `slippage` (distribución), latencias
  p99, **`order_duplication_rate` (objetivo 0)**, `reconciliation_divergences`,
  `dlq_depth`, `circuit_breaker_trips`, `recovery_count`, `time_in_SAFE_HALT`,
  `events_lost` (objetivo 0). Alimentan SLOs (OPS-006) y el Dashboard.

---

> *Continúa en las siguientes secciones: Failure Management (escenarios), Modos de
> ejecución (live/paper/backtest), Integración con todos los motores, Batería de casos
> de prueba, Garantías, y el ADR final con las decisiones de arquitectura adoptadas.*

---

## 15. Failure Management (escenarios)

Todos los fallos se gestionan de forma **determinista y fail-safe**, apoyados en los
circuit breakers (§11), health (§12), DLQ (§13), sagas (§9) y recovery (§16).

| Fallo | Detección | Respuesta del OMS |
|-------|-----------|-------------------|
| **Broker offline** | heartbeat perdido / errores | Broker breaker `OPEN` → `SAFE_HALT` (scope broker), proteger posiciones, RECOVERY al volver (reconciliar) |
| **Red lenta** | latencia > SLO | Network breaker `HALF_OPEN`/`OPEN`; `DEGRADED`; encolar; **no** reenviar a ciegas |
| **Timeout (ack/fill)** | `ack_timeout`/`fill_timeout` | `query(COID)` → adoptar si existe; si no, retry con mismo COID; agotado → `FAILED`+RECOVERY |
| **Orden duplicada** | COID ya `SENT` / `broker_event_id` repetido | Idempotency Guard/dedup → **ignorar** (no crea 2ª orden, no aplica 2º fill) |
| **Fill perdido** | posición broker ≠ OMS en reconciliación | RECOVERY: `query` fills → aplicar los faltantes (por `broker_event_id`) → converger |
| **Fill fuera de orden** | `event_time` menor que el último aplicado | reordenar por `event_time`; el agregado es conmutativo por `broker_event_id` → estado final idéntico |
| **Reintentos** | política §4.5 | bounded, backoff, `Clock` inyectado, **mismo COID** → sin duplicados |
| **Circuit breaker** | §11 | corta por dependencia; SAFE_HALT del scope afectado |
| **Recovery Engine** | reinicio/desconexión/divergencia | reconstruir desde event store + reconciliar con broker (§16) |

**Principio (⛔):** ante **cualquier** duda sobre el estado real, el OMS **no
adivina**: consulta al broker y **reconcilia**; mientras tanto, `SAFE_HALT` para
nuevas y **protección** de lo abierto.

---

## 16. Recovery Engine y reconstrucción de estado

- **Al reiniciar la plataforma:** el estado del OMS se **reconstruye reproduciendo el
  event store** (proyección del agregado `Trade`) → estado idéntico al previo al
  reinicio (event sourcing).
- **Reconciliación con el broker:** tras reconstruir, el Recovery Engine **consulta**
  al broker (órdenes vivas, posiciones, fills recientes) y **converge**: el **broker es
  la autoridad** del estado real de la cuenta; el OMS ajusta su proyección y **re-emite
  eventos de reconciliación** (nunca sobrescribe, añade).
- **Protección primero:** si una posición viva no tiene SL en el broker → se **coloca**
  inmediatamente (idempotente) antes de nada.
- **Determinismo:** dado el mismo event store + misma respuesta de `query`, la
  recuperación produce el **mismo** estado (test T-restart / T-reconstruction).

---

## 17. Modos de ejecución (live / paper / backtest)

El **mismo núcleo OMS** (máquina de estados, event store, idempotencia, sagas)
funciona en los tres modos; **solo cambia el `Broker Adapter`**:
- **Live:** adapter real (MT5/IB/Binance vía ENG-008).
- **Paper:** adapter simulado que genera fills deterministas desde el **precio oficial
  del MDE** (ENG-000), con modelo de spread/slippage configurable. Sin dinero real.
- **Backtest:** adapter histórico que genera fills contra el `dataset_id` versionado en
  **event-time**, con el mismo modelo de spread/slippage → **reproducible**.
- **Compatibilidad funded:** cualquier modo respeta el `funded_ruleset` de Risk
  (ENG-005 §17) — el OMS ejecuta lo que Risk autoriza.

**Ventaja clave:** como el OMS es **idéntico** en los tres modos, un backtest ejercita
**el mismo código de ejecución** que producción → paridad y confianza.

---

## 18. Integración con todos los motores

| Motor | Interacción |
|-------|-------------|
| **Market Data (ENG-000)** | precio **oficial** para price validation, slippage y conciliación; SSOT (el OMS no lee del broker para "precio de mercado") |
| **Market Context (ENG-011)** | contexto crítico (manipulación/news/vol extrema) puede abrir el Market Data/Risk breaker → SAFE_HALT |
| **Smart Money (ENG-002)** | provee las **anclas** (SL=mecha del sweep/POI; TP=pools/Fibonacci) que el OMS coloca y gestiona |
| **Trading (ENG-001)** | **propone** el `TradeIntent` (entrada, SL, TP, gestión §27–§33); el OMS **ejecuta** y es la fuente de verdad del estado |
| **Scoring (ENG-001 §26)** | no habla directo con el OMS; su resultado llega vía el `TradeIntent` (y modula riesgo) |
| **Risk (ENG-005)** | **bloqueante**: sin `RiskApproved` no hay `SENT`; kill-switch/DD ordenan `SAFE_HALT`/`flatten`; fills/exposición vuelven a Risk |
| **Broker Connectivity (ENG-008)** | los `Broker Adapter` (ACL) viven aquí; el gateway realiza el I/O real |
| **Decision Replay (ENG-009)** | cada evento del OMS (inmutable, con `correlation_id`) se registra → ciclo de vida reproducible paso a paso |
| **Explainable AI (ENG-010)** | cada acción de ejecución es explicable (por qué se envió/reintentó/adoptó/rechazó/halt) |
| **Backtesting (ENG-004)** | usa el OMS en modo backtest (mismo core) → resultados de ejecución reproducibles y realistas |
| **Portfolio/Analytics (ENG-007)** | consume `PositionOpened/Closed`, fills y PnL sellado para posiciones y métricas |

---

## 19. Batería de casos de prueba (deterministas)

- **T1 Ejecución perfecta:** intent → VALIDATED → RISK_APPROVED → SENT → ACK → FILLED →
  SL/TP colocados → MANAGED → CLOSED → ARCHIVED; eventos completos y ordenados.
- **T2 Broker lento:** ack tarda > `ack_timeout` → `query` → adopta → **una** orden
  (sin duplicado).
- **T3 Broker caído:** breaker Broker `OPEN` → SAFE_HALT, posiciones protegidas; al
  volver → RECOVERY reconcilia.
- **T4 Slippage extremo:** fill fuera de `max_slippage` → política aplica (reject/
  accept+mark/close) + evento + alerta a Risk.
- **T5 Fill parcial / múltiples:** varios `ExecutionReport` → agregación correcta,
  `avg_price` ponderado, PARTIALLY_FILLED→FILLED.
- **T6 Duplicados:** COID reenviado / `broker_event_id` repetido → ignorados
  (`order_duplication_rate=0`).
- **T7 Reintentos:** timeout → retries con mismo COID/backoff (`Clock` simulado) →
  sin duplicar.
- **T8 Recovery:** divergencia OMS/broker → reconciliación converge a la verdad del
  broker.
- **T9 Reinicio de plataforma:** reconstruir desde event store → estado idéntico
  (proyección) + reconciliación.
- **T10 Desconexión durante operación:** posición abierta sin SL en broker → al
  reconectar, RECOVERY **re-coloca SL** antes que nada.
- **T11 Reconstrucción completa del estado:** replay del event store (dos veces) →
  estado bit a bit idéntico.
- **T12 Exactly-once:** un `intent_id` con 3 reintentos + 1 reinicio → **1** orden
  ejecutada.
- **T13 Outbox sin pérdida:** crash entre commit y publish → el relay publica al
  reanudar (0 eventos perdidos).
- **T14 Saga compensación:** fallo tras fill sin SL → saga OpenPosition compensa
  (re-coloca SL / cierra).
- **T15 DLQ:** evento de broker irreconciliable → va a DLQ (no se pierde) + alerta.
- **T16 Circuit breaker (4):** simular fallo de broker/MDE/red/riesgo → cada breaker
  abre de forma **independiente** con el efecto esperado.
- **T17 Health:** dependencia DEGRADED/UNAVAILABLE → comportamiento de ejecución
  correcto (restringir/halt).

---

## 20. Garantías y reglas obligatorias (checklist)

- ✅ **OMS único:** ninguna ejecución fuera del OMS.
- ✅ **Determinismo total:** estado = proyección determinista del event store (T9,T11).
- ✅ **Idempotencia absoluta / exactly-once lógico:** 1 intención → ≤1 orden; 1
  `broker_event_id` → ≤1 aplicación (T6,T7,T12).
- ✅ **Event sourcing:** eventos inmutables; nada se modifica/borra.
- ✅ **Sin pérdida de eventos:** transactional outbox + inbox (T13).
- ✅ **Sin duplicación de órdenes:** máquina de estados + COID + query-before-resend
  (T2,T6).
- ✅ **Reproducibilidad completa:** replay → mismo estado (T11).
- ✅ **Auditoría completa:** `correlation_id` de extremo a extremo; audit ledger
  (SEC-000).
- ✅ **Fail-safe/recovery:** SAFE_HALT + reconciliación; posición nunca sin SL (T3,T10).
- ✅ **Compatible funded / multi-broker / paper / backtest / replay:** mismo core,
  adapter intercambiable (§17).
- ✅ **DLQ:** nada se pierde en silencio (T15). **Circuit breakers** independientes +
  **Health** (T16,T17). **Observabilidad** con KPIs y `order_duplication_rate=0`.

---

## 21. ADR — Decisiones de arquitectura del Execution Engine

> Formato Nygard resumido (contexto · decisión · alternativas · consecuencias). Se
> promoverán a `docs/adr/` como ADR formales al aprobar el gate.

### ADR-EXE-1 · Event Sourcing + CQRS para el OMS
- **Contexto.** Se exige auditoría total, idempotencia, reconstrucción tras fallo y
  reproducibilidad.
- **Decisión.** El **event store es la fuente de verdad**; el estado es una proyección;
  separar comandos (write) de consultas (read models).
- **Alternativas.** CRUD mutable (pierde el "cómo"); solo logging (no reconstruye
  estado).
- **Consecuencias.** + Auditoría/replay/idempotencia nativas; − complejidad de
  proyecciones y consistencia eventual en read models (aceptada; lo crítico usa el
  write-side fuerte).

### ADR-EXE-2 · Transactional Outbox (no dual-write, no 2PC)
- **Contexto.** Persistir y publicar eventos sin perderlos.
- **Decisión.** Outbox en la misma transacción + relay + inbox idempotente.
- **Alternativas.** Publicar directo tras commit (pierde eventos en crash); 2PC/XA
  (frágil, acoplado, lento).
- **Consecuencias.** + Cero pérdida, at-least-once controlado; − latencia de relay y
  necesidad de dedup en consumidores.

### ADR-EXE-3 · Saga / Process Manager durable (Temporal)
- **Contexto.** Procesos largos (apertura, parciales, BE, trailing, cierre, recovery)
  con pasos, timeouts y compensaciones.
- **Decisión.** Orquestación con **process managers durables**.
- **Alternativas.** Coreografía pura por eventos (difícil de razonar/compensar); lógica
  ad-hoc en código (frágil ante reinicios).
- **Consecuencias.** + Estado durable, compensación y timeouts explícitos; −
  dependencia de un motor de workflows.

### ADR-EXE-4 · Exactly-once lógico (idempotency + query-before-resend)
- **Contexto.** Reintentos/reinicios no deben duplicar órdenes.
- **Decisión.** COID estable + dedup por `broker_event_id` + **consultar antes de
  reenviar**.
- **Alternativas.** Confiar en dedup del broker (insuficiente/variable); reintento ciego
  (duplica).
- **Consecuencias.** + `order_duplication_rate=0`; − un `query` extra por timeout.

### ADR-EXE-5 · Circuit breakers independientes por dependencia
- **Contexto.** Broker, Market Data, Red y Riesgo fallan de formas distintas.
- **Decisión.** Un breaker por dependencia, con efecto específico en la ejecución.
- **Alternativas.** Un breaker global (un fallo ciega todo; no distingue causa).
- **Consecuencias.** + Aislamiento de fallos y respuesta precisa; − más estado que
  gestionar.

### ADR-EXE-6 · OMS core agnóstico de broker y modo + adapters (ACL)
- **Contexto.** Multi-broker y multi-modo (live/paper/backtest) con paridad.
- **Decisión.** Núcleo OMS único; **solo cambia el `Broker Adapter`**.
- **Alternativas.** Un OMS por broker/modo (duplica lógica, diverge, mata la paridad
  backtest≡live).
- **Consecuencias.** + Paridad y reutilización; − el adapter debe normalizar bien las
  diferencias de cada broker.

### ADR-EXE-7 · `execution-gateway` (Rust) separado del OMS
- **Contexto.** El I/O con el broker exige baja latencia; la lógica exige determinismo.
- **Decisión.** Gateway "tonto y rápido" (Rust, sub-ms) separado del OMS "inteligente y
  determinista".
- **Alternativas.** OMS monolítico haciendo I/O (acopla latencia y lógica; difícil de
  testear determinista).
- **Consecuencias.** + Latencia y determinismo desacoplados, testabilidad; − una
  frontera de red/servicio más que operar.

### ADR-EXE-8 · Broker como autoridad del estado real (reconciliación)
- **Contexto.** Tras fallos, OMS y broker pueden divergir.
- **Decisión.** El **broker es la verdad** del estado de la cuenta; el OMS converge por
  reconciliación (añadiendo eventos, sin sobrescribir).
- **Alternativas.** OMS autoritativo (arriesga operar sobre un estado irreal).
- **Consecuencias.** + Seguridad frente a divergencias; − necesidad de `query`/
  reconciliación robusta y de resolver conflictos de forma determinista.

---

> **Versión 0.1 — Borrador (🟨).** Especificación oficial del Execution Engine / OMS de
> ELYON QUANT. Aprobación (🟩) requiere revisión de Execution Lead, CTO, Risk, Quant,
> Platform y QA; prerrequisito del gate D4. Cambios de política (retry/timeout,
> circuit breakers, sagas, exactly-once) vía RFC + validación con la batería T1–T17 y
> golden datasets (ENG-004).
