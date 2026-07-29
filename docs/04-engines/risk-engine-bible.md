<!--
title: ELYON QUANT — Risk Engine Bible
id: ENG-005 (Risk Engine — especificación técnica oficial de gestión de riesgo)
owner: Risk Lead
reviewers: [Quant Lead, CTO/Principal Architect, Execution Lead, Security Lead, QA Lead]
status: draft
version: 0.1
last_updated: 2026-07-29
supersedes: amplía trading-engine-bible.md §34 (gestión del riesgo)
-->

# ELYON QUANT — RISK ENGINE BIBLE

> **La autoridad suprema del sistema.** El Risk Engine controla **todas** las
> restricciones de riesgo **antes, durante y después** de cada operación. Es
> **completamente determinista, auditable y reproducible**: dado el mismo estado
> de cuenta, la misma configuración y la misma solicitud, produce **exactamente**
> la misma decisión. Ningún score, contexto o señal puede pasar por encima de un
> límite de riesgo: **un límite alcanzado es un veto (⛔).**

Especificación de ingeniería de nivel institucional. No es teoría: define
estructuras, algoritmos deterministas, estados, límites y pruebas que la
implementación debe cumplir y los tests deben verificar (trazabilidad BLD-003).

---

## 0. Preámbulo

### 0.1 Principios (invariantes ⛔)
1. **El riesgo manda.** El Risk Engine tiene la última palabra: puede **rechazar,
   recortar, pausar o cerrar** operativa aunque todo lo demás diga "sí".
2. **Determinismo total.** Sin reloj de pared en la lógica (el tiempo se inyecta
   como `Clock`), sin aleatoriedad, sin estado global mutable no versionado.
   Misma entrada ⇒ misma `RiskDecision`.
3. **Cero interpretación humana.** Toda regla es una condición numérica con
   parámetros; no existe "criterio del gestor".
4. **Todo configurable.** Cada umbral vive en un `RiskProfile` versionado
   (`risk_config_hash`), por cuenta/estrategia/símbolo.
5. **Todo registrado.** Cada evaluación —apruebe o rechace— emite un
   `RiskDecisionRecord` inmutable (Decision Replay ENG-009, audit ledger SEC-000).
6. **Explicable.** Cada decisión dice **exactamente** qué límite se evaluó, cuál
   bloqueó y con qué valores (Explainable AI ENG-010).
7. **Fail-safe.** Ante datos degradados, ambigüedad o error interno: **no aprobar**
   nuevas entradas y **proteger** lo abierto.

### 0.2 Aritmética monetaria determinista (crítico)
El dinero **no** se computa en coma flotante binaria. Reglas:
- **Tipo decimal de precisión fija** (`Decimal`) para equity, balance, riesgo,
  precios y tamaños. Precisión y escala definidas por `instrument_profile`/moneda.
- **Redondeo explícito y conservador:** el tamaño de posición se redondea **hacia
  abajo** al `lot_step` (`ROUND_DOWN`) → nunca se arriesga más de lo permitido.
  Los importes de riesgo se redondean **hacia arriba** al evaluar límites (peor
  caso). El modo de redondeo es parte de la config (`rounding_mode`).
- **Conversión de divisa determinista:** vía un `fx_snapshot` (tasas congeladas por
  barra/decisión, con `fx_snapshot_id`), nunca una tasa "en vivo" no reproducible.
- **Comparaciones con tolerancia definida** (`epsilon` decimal) para evitar
  desigualdades ambiguas.
- El `risk_config_hash` + `fx_snapshot_id` entran en el `RiskDecisionRecord` →
  **reproducibilidad bit a bit** (alineado con ENG-009).

### 0.3 Posición en el pipeline
```
Market Context (ENG-011, gate) → Smart Money (ENG-002) → Trading Engine (ENG-001, Entry Score)
        │ setup candidato (symbol, side, entry, SL, TP, strategy, context)
        ▼
   ┌────────────────── RISK ENGINE (ENG-005) ──────────────────┐
   │ PRE-TRADE:  evaluar límites → aprobar/rechazar + sizing    │  ⛔ bloqueante y síncrono
   │ IN-TRADE:   monitorizar exposición/drawdown/kill-switch    │  (event-driven)
   │ POST-TRADE: actualizar contadores, cooldown, profit lock   │
   └───────────────┬───────────────────────────────────────────┘
        approved?  │  size, risk_amount
          NO ──────┤ RiskRejected(reason)  → no se rutea (Trading Engine no envía a Execution)
          SÍ ──────▼ RiskApproved(size)    → Execution Engine (ENG-006) rutea la orden
   Todo → Decision Replay (ENG-009) + Explainable AI (ENG-010)
```
La relación `Trading/Execution → Risk` es **síncrona y bloqueante** por diseño
(arquitectura §03): **ninguna orden live se rutea sin `RiskApproved`.**

---

## 1. Arquitectura del motor

### 1.1 Módulo `risk` (Clean Architecture)
`domain` (agregados de riesgo, límites, invariantes) · `application`
(orquestación de las 3 fases, handlers) · `infrastructure` (estado persistente,
snapshots FX, publicación de eventos) · `interfaces` (API síncrona de pre-trade,
consumidores de eventos in/post-trade).

### 1.2 Componentes internos
```
                         ┌──────────── RiskProfile (config versionada) ───────────┐
                         │ límites por operación/día/semana/mes/símbolo/sesión/    │
                         │ estrategia/correlación/exposición/drawdown/funded/...   │
                         └───────────────────────────┬────────────────────────────┘
   TradeRequest ─┐                                    │
   AccountState ─┤        ┌───────────────────────────▼───────────────────────────┐
   MarketContext ┤───────►│  RISK EVALUATION PIPELINE (determinista, ordenado)     │
   fx_snapshot ──┤        │  1 Hard gates (kill-switch/cooldown/lockout/funded)    │
   Clock ────────┘        │  2 Límites agregados (día/sem/mes/exposición/corr.)    │
                          │  3 Límites de la operación (per-trade, símbolo, sesión,│
                          │    estrategia, RR, SL)                                 │
                          │  4 Position Sizing (+ margen)                          │
                          │  5 Resolución: APPROVE(size) / REJECT(reasons)         │
                          └───────────────┬────────────────────────────────────────┘
   RiskState Store ◄──────────────────────┤ (contadores por dimensión, HWM, streaks)
   (día/sem/mes/símbolo/estrategia/sesión) │
                                           ▼
                         RiskDecision + eventos → Execution / Decision Replay / XAI
     Monitores continuos (in-trade): Exposure · Drawdown · Kill-Switch · Correlation
```

### 1.3 Estado de riesgo (`RiskState`)
Contadores deterministas mantenidos por el motor, con **cadencia de reset**
definida y `Clock` inyectado:
- **Equity/Balance:** `equity`, `balance`, `peak_equity` (high-water mark).
- **Ventanas temporales:** PnL y nº de operaciones por **día**, **semana**, **mes**
  (con `boundary_timezone`, def. la del `instrument_profile`/cuenta).
- **Por dimensión:** riesgo abierto y realizado por **símbolo**, **estrategia**,
  **sesión**, **grupo de correlación**.
- **Rachas:** `consecutive_losses`, `consecutive_wins`.
- **Estado del motor:** `engine_state` (§3), `active_kill_switches`,
  `cooldown_until`, `lockout` flags.
Todo `RiskState` es **reconstruible** a partir del historial de operaciones
(event-sourcing) → auditable y reproducible.

---

## 2. Flujo de decisiones (pre-trade, ordenado y determinista)

El orden de evaluación es **fijo** (determinismo + explicabilidad: se reporta el
**primer** motivo de rechazo y **todos** los límites evaluados). Los vetos duros se
comprueban primero (barato, corta pronto); el sizing al final.

```
evaluate(TradeRequest req, AccountState acc, MarketContext ctx, cfg, clock):
  reasons = []
  # 1) HARD GATES (estado del motor)
  if engine.halted (kill-switch global activo):        return REJECT("kill_switch_active")
  if engine.locked (drawdown lockout):                 return REJECT("drawdown_lockout")
  if inCooldown(clock):                                return REJECT("cooldown_active")
  if fundedRuleViolatedPreemptively(acc, cfg.funded):  return REJECT("funded_rule")
  # 2) LÍMITES AGREGADOS (cuenta / ventanas / exposición / correlación)
  check dailyLoss, weeklyLoss, monthlyLoss             → reason si excede
  check maxTradesPerDay/Session/Strategy               → reason si excede
  check totalOpenRisk, maxNotional, maxLeverage, maxOpenPositions
  check correlationRisk(req.symbol, openPositions, cfg)
  # 3) LÍMITES DE LA OPERACIÓN
  check perSymbolRisk(req.symbol), perStrategyRisk(req.strategy), perSessionRisk(ctx.session)
  check minRR(req), maxSlDistance(req)
  if reasons not empty:                                return REJECT(reasons)   # ⛔ cualquier veto
  # 4) POSITION SIZING
  size, risk_amount = positionSizer(req, acc, ctx, cfg)   # ver §18
  if size < cfg.min_lot:                               return REJECT("size_below_min")
  if not marginSufficient(size, acc):                  return REJECT("insufficient_margin")
  # 5) RESOLUCIÓN
  return APPROVE(size, risk_amount, applied_adjustments)
```

- **Un solo veto basta** para rechazar; se registran **todos** los límites
  evaluados y su holgura (para explicabilidad y para el modo dinámico §19).
- **Recorte antes que rechazo (opcional, ⚙️ `allow_size_reduction`):** si el único
  problema es que el tamaño deseado excede un límite agregado, el motor puede
  **recortar** el tamaño al máximo permitido en vez de rechazar (nunca ampliar).

---

## 3. Estados del motor (máquina de estados)

```
                 ┌─────────────────────────────────────────────────────────┐
                 ▼                                                         │ reset ventana
        ┌────────────────┐  soft limit / racha           ┌──────────────┐  │ / cooldown_until
        │     NORMAL     │──────────────────────────────►│  RESTRICTED  │──┘ vencido
        │ (opera normal) │◄──────────────────────────────│ (riesgo↓,    │
        └───────┬────────┘   holgura recuperada           │  size↓)      │
                │                                          └──────┬───────┘
   N pérdidas   │ daily/weekly loss límite                        │ pérdida grave / límite duro
   consecutivas ▼                                                 ▼
        ┌────────────────┐                               ┌──────────────────┐
        │    COOLDOWN     │  cooldown vencido ──► NORMAL  │      HALTED       │ kill-switch
        │ (bloquea nuevas │──────────────────────────────│ (no nuevas; puede │
        │  entradas)      │                               │  proteger/cerrar) │
        └────────────────┘                               └─────────┬────────┘
                                                                    │ intervención / reset diario
        ┌────────────────┐  drawdown máx alcanzado                  │
        │     LOCKED      │◄─────────────────────────────────────────┘
        │ (lockout total; │  requiere revisión manual / reset de periodo (funded)
        │  solo cerrar)   │
        └────────────────┘
```
- **NORMAL:** operativa plena.
- **RESTRICTED:** cerca de límites o tras rachas → **riesgo reducido** (multiplicador
  dinámico §19), sin bloquear del todo.
- **COOLDOWN:** bloqueo temporal de nuevas entradas (§16); posiciones abiertas siguen
  gestionándose.
- **HALTED:** kill-switch activo (§15); no se abren entradas; según política se
  protege (BE) o se cierra lo abierto.
- **LOCKED:** lockout por drawdown (§14) o violación de cuenta fondeada (§17);
  requiere reset de periodo o intervención.
Transiciones con **histéresis** (`state_hysteresis`) para evitar parpadeo alrededor
de umbrales. Todo cambio de estado emite evento y se registra.

---

## 4. Parámetros configurables (`RiskProfile` — extracto)

> Todos versionados (`risk_config_hash`), por cuenta/estrategia/símbolo. Valores por
> defecto de diseño; se calibran en Backtesting (ENG-004). `⛔` = si se alcanza,
> veta; `⚙️` = ajusta comportamiento.

| Grupo | Parámetro | Def. | Descripción |
|-------|-----------|------|-------------|
| Operación | `risk_per_trade_pct` / `_max_pct` | 0.5 % / 1.0 % | Riesgo base / máximo por operación |
| Operación | `min_rr` | 2.0 | RR mínimo para aprobar ⛔ |
| Operación | `max_sl_atr` | 3.0 | SL máximo (en ATR); si mayor, rechaza/refina ⛔ |
| Diario | `max_daily_loss_pct` | 3.0 % | Pérdida diaria máxima ⛔ |
| Diario | `max_trades_per_day` | 5 | Nº máximo de operaciones/día ⛔ |
| Diario | `daily_profit_lock_pct` | 4.0 % | Objetivo diario → protege/reduce ⚙️ |
| Semanal | `max_weekly_loss_pct` | 6.0 % | Pérdida semanal máxima ⛔ |
| Mensual | `max_monthly_loss_pct` | 10.0 % | Pérdida mensual máxima ⛔ |
| Símbolo | `max_risk_per_symbol_pct` / `max_positions_per_symbol` | 1.0 % / 1 | Riesgo/posiciones por símbolo ⛔ |
| Sesión | `max_risk_per_session_pct` / `max_trades_per_session` | 1.5 % / 3 | Por sesión (Asia/LDN/NY) ⛔ |
| Estrategia | `max_risk_per_strategy_pct` / `max_open_per_strategy` | 2.0 % / 3 | Por estrategia ⛔ |
| Correlación | `max_correlated_risk_pct` / `correlation_threshold` | 1.5 % / 0.7 | Riesgo correlacionado agregado ⛔ |
| Exposición | `max_total_open_risk_pct` | 3.0 % | Riesgo total abierto ⛔ |
| Exposición | `max_open_positions` / `max_leverage` / `max_notional_pct` | 5 / perfil / — | Cotas de exposición ⛔ |
| Drawdown | `max_drawdown_pct` | 10 % | Lockout total ⛔ |
| Drawdown | `trailing_drawdown_pct` | — | DD desde HWM (cuentas fondeadas) ⛔ |
| Kill-switch | `killswitch_daily_loss_pct` / `killswitch_consec_losses` | 3 % / 4 | Disparadores ⛔ |
| Cooldown | `cooldown_after_losses` / `cooldown_duration` | 3 / 60 min | Cooldown tras rachas ⚙️ |
| Funded | `funded_ruleset` | — | Reglas de prop firm (§17) ⛔ |
| Sizing | `sizing_model` / `lot_step` / `min_lot` / `max_lot` | structure_risk / perfil | Cálculo de tamaño |
| Dinámico | `risk_scaling` | ver §19 | Multiplicadores por contexto/racha/DD ⚙️ |
| Capital | `equity_base` / `compounding` | equity / true | Base de cálculo y compounding ⚙️ |
| Estado | `state_hysteresis` | perfil | Anti-parpadeo de estados |

---

## 5. Riesgo por operación (per-trade)

**Definición.** El riesgo de una operación es la pérdida máxima si toca el SL:
`trade_risk = size × sl_distance_price × value_per_unit` (en divisa de la cuenta,
vía `fx_snapshot`). Debe cumplir `trade_risk ≤ risk_per_trade_pct × equity_base`.
**Reglas.** (a) `min_rr`: `RR = tp_distance / sl_distance ≥ min_rr` ⛔. (b)
`max_sl_atr`: si `sl_distance > max_sl_atr × ATR` → rechazar o exigir refinamiento
LTF ⛔. (c) El **SL define el tamaño** (§18), nunca al revés.
**Reset.** N/A (por operación).
**Casos válidos.** Riesgo 0.5 %, RR 2.5, SL 1.4·ATR → OK.
**Casos inválidos.** RR 1.6 (< min_rr) → REJECT. SL 3.5·ATR (> max) → REJECT.
**Edge cases.** SL muy pequeño → tamaño enorme; acotado por `max_lot`, exposición
(§13) y margen. `sl_distance = 0` (SL en el precio) → REJECT (inválido).
**Pseudocódigo.**
```
def perTradeCheck(req, cfg, equityBase):
    rr = req.tp_distance / req.sl_distance
    if req.sl_distance <= 0: reject("invalid_sl")
    if rr < cfg.min_rr: reject("rr_below_min")
    if req.sl_distance > cfg.max_sl_atr * req.atr: reject("sl_too_wide")
```

## 6. Riesgo diario

**Definición.** Suma del PnL realizado del día + riesgo abierto vivo no puede
implicar una pérdida diaria > `max_daily_loss_pct × equity_base_day_start`. También
`trades_today < max_trades_per_day`.
**Reset.** En el límite de día del `boundary_timezone` (definido, DST-correcto).
Se congela `equity_base_day_start` al inicio del día.
**Casos válidos.** Pérdida acumulada 1.8 %, nueva op arriesga 0.5 % → 2.3 % < 3 % OK.
**Casos inválidos.** Pérdida 2.8 % + nueva 0.5 % = 3.3 % > 3 % → REJECT + posible
kill-switch (§15). `trades_today = 5` → REJECT.
**Edge cases.** Operación que cruza medianoche → cuenta en el día de apertura; el
reset no altera posiciones abiertas, solo los contadores nuevos. Gap que ya supera
el límite al abrir mercado → HALTED preventivo.
**Pseudocódigo.**
```
def dailyCheck(state, req, cfg):
    projected = state.realized_today - (state.open_risk + req.trade_risk)
    if -projected > cfg.max_daily_loss_pct * state.equity_base_day_start: reject("daily_loss_limit")
    if state.trades_today >= cfg.max_trades_per_day: reject("daily_trade_count")
```

## 7. Riesgo semanal

**Definición.** Pérdida realizada de la semana ≤ `max_weekly_loss_pct`.
**Reset.** Inicio de semana (lunes, `boundary_timezone`).
**Casos válidos/inválidos.** Análogos a §6 con ventana semanal.
**Edge cases.** Semana con festivos → la ventana es de calendario, no de días
operados. **Pseudocódigo.** Igual patrón que §6 con `realized_week` y umbral semanal.

## 8. Riesgo mensual

**Definición.** Pérdida realizada del mes ≤ `max_monthly_loss_pct`.
**Reset.** Primer día del mes (`boundary_timezone`).
**Edge cases.** Mes con menor nº de días; DST dentro del mes → límites por
calendario. **Nota:** día ≤ semana ≤ mes deben ser coherentes
(`daily ≤ weekly ≤ monthly`); el motor **valida la coherencia** de la config al
cargar el `RiskProfile` (si `max_daily > max_weekly` → error de configuración ⛔).

## 9. Riesgo por símbolo

**Definición.** Riesgo abierto agregado en un símbolo ≤ `max_risk_per_symbol_pct` y
`open_positions(symbol) < max_positions_per_symbol`.
**Reset.** Dinámico (según posiciones vivas).
**Casos válidos.** XAUUSD con 0 posiciones, nueva 0.5 % → OK.
**Casos inválidos.** EURUSD ya con 1 posición y `max_positions_per_symbol=1` → REJECT.
**Edge cases.** Hedging (posiciones opuestas): política `netting` vs `hedging`
(⚙️ `symbol_exposure_mode`); en netting, opuestas reducen exposición neta; en
hedging, suman riesgo bruto. **Pseudocódigo.** suma `open_risk[symbol] + req.trade_risk`
y compara.

## 10. Riesgo por sesión

**Definición.** Riesgo/nº de operaciones dentro de la **sesión** actual (Asia/
London/NY, provista por el Market Context Engine, ENG-011 §5.F) ≤
`max_risk_per_session_pct` / `max_trades_per_session`.
**Reset.** Al cambiar de sesión (frontera de killzone/sesión del MCE).
**Casos válidos.** London KZ, 1 op previa, nueva dentro de límite → OK.
**Casos inválidos.** 3 operaciones ya en NY con `max_trades_per_session=3` → REJECT.
**Edge cases.** Solape London-NY → la sesión efectiva la define el MCE (una sola);
determinista. Fuera de sesión → normalmente el gate del MCE ya bloqueó.

## 11. Riesgo por estrategia

**Definición.** Cada `strategy_id` tiene su presupuesto: riesgo abierto ≤
`max_risk_per_strategy_pct`, `open(strategy) < max_open_per_strategy`, y su propio
sub-límite de pérdida diaria (`max_daily_loss_per_strategy_pct`, opcional).
**Reset.** Diario para el sub-límite; dinámico para el abierto.
**Casos válidos.** Estrategia A con 1.0 % abierto, nueva 0.5 % ≤ 2.0 % → OK.
**Casos inválidos.** Estrategia A ya en su tope de pérdida diaria → REJECT (esa
estrategia), otras siguen operando.
**Edge cases.** Varias estrategias sobre el mismo símbolo → interactúa con §9
(riesgo por símbolo) y §12 (correlación). El límite **más restrictivo** manda.

## 12. Riesgo por correlación

**Definición.** El riesgo no se cuenta por instrumento aislado sino por **grupo de
correlación**. Dado un mapa/matriz de correlación (`correlation_matrix`, calibrada
en ENG-004), instrumentos con `|ρ| ≥ correlation_threshold` forman un grupo; el
**riesgo agregado del grupo** ≤ `max_correlated_risk_pct`. Posiciones muy
correlacionadas **suman** riesgo (misma apuesta); inversamente correlacionadas y en
sentido opuesto pueden **compensar** (según `correlation_mode`).
**Grupos típicos.** USD-pairs (EURUSD/GBPUSD…), Gold↔DXY (inversa), índices US
(NAS100/US30), cripto (BTC/ETH).
**Reset.** Dinámico.
**Casos válidos.** Long EURUSD 0.5 % + long GBPUSD 0.5 %, `ρ=0.8` → grupo 1.0 % ≤
1.5 % → OK.
**Casos inválidos.** Añadir long AUDUSD 0.5 % → grupo 1.5 %+ → REJECT (misma apuesta
USD).
**Edge cases.** Correlación cambiante (regímenes) → la matriz se **versiona** y se
congela por decisión (`correlation_snapshot_id`) para determinismo; su
recalibración es un acto de config (RFC), no en vivo. Correlación desconocida para
un par → tratar como grupo propio (conservador).
**Pseudocódigo.**
```
def correlationCheck(state, req, matrix, cfg):
    group = correlatedGroup(req.symbol, state.open_positions, matrix, cfg.threshold)
    agg = aggregatedDirectionalRisk(group + [req], cfg.correlation_mode)
    if agg > cfg.max_correlated_risk_pct * equityBase: reject("correlated_risk")
```

## 13. Exposición máxima

**Definición.** Cotas globales simultáneas: riesgo total abierto ≤
`max_total_open_risk_pct`; `open_positions ≤ max_open_positions`; nocional total ≤
`max_notional_pct × equity`; apalancamiento efectivo ≤ `max_leverage`.
**Reset.** Dinámico.
**Casos válidos.** 4 posiciones, riesgo total 2.5 %, nueva 0.5 % = 3.0 % ≤ 3 % → OK
(justo en el límite; `epsilon` decide el borde de forma determinista).
**Casos inválidos.** Riesgo total ya 3.0 % → cualquier nueva → REJECT.
**Edge cases.** Apalancamiento por margen del broker vs límite interno → manda el
**más restrictivo**. Nocional en divisa distinta → convertir con `fx_snapshot`.

## 14. Gestión de drawdown

**Definición.** `drawdown = (peak_equity − equity) / peak_equity`. Dos modos:
- **Estático:** DD desde el balance inicial del periodo.
- **Trailing:** DD desde el **high-water mark** (`peak_equity`), usado en cuentas
  fondeadas (§17). `max_drawdown_pct` / `trailing_drawdown_pct`.
Al alcanzarse → `engine_state = LOCKED` (lockout total): **solo cerrar**, no abrir.
**Reset.** Según política: reset de periodo (mensual) o intervención manual; en
funded, según reglas de la firma.
**Casos válidos.** DD 6 % < 10 % → NORMAL (o RESTRICTED si `dd_soft` cruzado).
**Casos inválidos.** DD 10 % → LOCKED, kill-switch de cuenta.
**Edge cases.** `peak_equity` se actualiza solo al alza (monótono); un depósito/
retirada ajusta la base (`equity_adjustment_event`) de forma determinista y
registrada. Drawdown intradía (flotante) vs de cierre → ambos vigilados; el trailing
de funded suele mirar equity flotante (peor caso).
**Pseudocódigo.**
```
def drawdownMonitor(state, cfg):
    dd = (state.peak_equity - state.equity) / state.peak_equity
    if dd >= cfg.max_drawdown_pct: transition(LOCKED); killSwitch("drawdown")
    elif dd >= cfg.dd_soft_pct: transition(RESTRICTED)
```

## 15. Kill-Switch

**Definición.** Mecanismo de parada de emergencia. **Disparadores (⛔):**
- Pérdida diaria ≥ `killswitch_daily_loss_pct`.
- Drawdown ≥ `max_drawdown_pct` (§14).
- `consecutive_losses ≥ killswitch_consec_losses`.
- Contexto crítico del MCE: `manipulation=extreme` / `atr_regime=extreme` /
  `news.block_active` (ENG-011).
- Anomalía operativa (desfase de estado, feed degradado, slippage excesivo — ENG-001
  §38).
- **Manual** (operador / back-office).
**Alcance (`killswitch_scope`):** `global` | `symbol` | `strategy`.
**Comportamiento (`killswitch_action`):** `block_new` (mínimo), `move_to_be`
(proteger), `flatten` (cerrar todo). Por defecto: **bloquear nuevas + proteger a BE**.
**Reset.** Manual o al reset de periodo (según config); nunca auto-reset silencioso.
**Casos válidos (activación correcta).** 4 pérdidas seguidas → HALTED global.
**Casos inválidos (no debe activar).** 2 pérdidas + 1 ganancia → racha rota, no
activa. **Edge cases.** Varios disparadores simultáneos → un solo evento
`KillSwitchTriggered` con **todas** las causas listadas. Kill-switch por símbolo no
debe frenar otros símbolos.
**Pseudocódigo.**
```
def killSwitchCheck(state, ctx, cfg):
    causes = []
    if state.daily_loss_pct >= cfg.ks_daily: causes += "daily_loss"
    if state.consecutive_losses >= cfg.ks_consec: causes += "consec_losses"
    if ctx.manipulation==EXTREME or ctx.news.block_active: causes += "context_critical"
    if anomaly(state): causes += "operational_anomaly"
    if causes: trigger(KillSwitch(scope=cfg.scope, action=cfg.action, causes))
```

## 16. Cooldown tras pérdidas

**Definición.** Tras `cooldown_after_losses` pérdidas consecutivas (o tras tocar un
límite blando), el motor entra en **COOLDOWN**: bloquea **nuevas** entradas durante
`cooldown_duration` (tiempo del `Clock`) o `cooldown_bars`. Las posiciones abiertas
se siguen gestionando.
**Reset.** Al vencer `cooldown_until` (una ganancia no lo cancela salvo
`cooldown_reset_on_win`).
**Casos válidos.** 3 pérdidas seguidas → cooldown 60 min → nuevas entradas
rechazadas con `reason=cooldown_active`.
**Casos inválidos.** 2 pérdidas → no activa. **Edge cases.** Cooldown solapando con
kill-switch → prevalece el más restrictivo (HALTED > COOLDOWN). El cooldown se mide
con `Clock` inyectado (determinismo en backtest).
**Pseudocódigo.**
```
def cooldownCheck(state, cfg, clock):
    if state.consecutive_losses >= cfg.cooldown_after_losses and not state.cooldown_active:
        state.cooldown_until = clock.now() + cfg.cooldown_duration; transition(COOLDOWN)
    if state.cooldown_active and clock.now() < state.cooldown_until: reject("cooldown_active")
```

## 17. Límites para cuentas fondeadas (funded / prop firm)

**Definición.** Un `funded_ruleset` modela las reglas de una firma de fondeo como
**perfil de restricciones adicional** (encima de las anteriores). Reglas típicas
(⛔, todas configurables por firma):
- **Max daily loss** (a menudo % del **saldo inicial del día** o absoluto).
- **Max total drawdown**: **estático** (desde balance inicial) o **trailing** (desde
  HWM) — §14.
- **Profit target** (fase de reto): objetivo a alcanzar sin violar límites.
- **Min trading days**: nº mínimo de días operados.
- **Consistency rule**: ningún día puede aportar > X % del beneficio total.
- **News restriction**: prohibido operar dentro de ventana de noticias de alto
  impacto (integra con MCE `news.block_active`).
- **Weekend/overnight holding**: prohibición de mantener posiciones el fin de
  semana / overnight (según firma).
- **Lot/leverage caps** específicos.
**Comportamiento.** El motor evalúa el `funded_ruleset` como **hard gate** (fase 1
del flujo, §2). Violar una regla de funded suele implicar `LOCKED` (protege la
cuenta de romper el reto/fondeo).
**Casos válidos.** DD trailing 3 % < 5 % de la firma, fuera de noticias, viernes con
cierre antes del corte → OK.
**Casos inválidos.** Intento de abrir el viernes tras `weekend_cutoff` → REJECT.
Beneficio del día que rompería la `consistency_rule` → REJECT/recorte.
**Edge cases.** Trailing drawdown que "congela" el HWM tras alcanzar profit target
(regla de algunas firmas) → modelar `hwm_lock_after_target`. Reset diario a la hora
de corte de la firma (`funded_reset_time`, DST-correcto). **Determinismo:** todo el
ruleset es config versionada; ninguna regla se infiere.
**Pseudocódigo.**
```
def fundedCheck(state, req, ctx, rules, clock):
    if rules.news_restriction and ctx.news.block_active: reject("funded_news")
    if rules.no_weekend_holding and afterCutoff(clock, rules.weekend_cutoff): reject("funded_weekend")
    if trailingDD(state) >= rules.max_trailing_dd: lock("funded_drawdown")
    if breaksConsistency(state, req, rules): reject("funded_consistency")
    ... (max_daily_loss, lot caps, etc.)
```

---

## 18. Position Sizing

**Definición.** El tamaño se **deriva del SL** (nunca al revés), de forma
determinista:
```
risk_amount = risk_pct_effective × equity_base            # ver §19 y §20
size_raw    = risk_amount / (sl_distance_price × value_per_unit × fx_rate)
size        = clamp( roundDown(size_raw, lot_step), min_lot, max_lot_effective )
```
- `value_per_unit` y `fx_rate` del `instrument_profile`/`fx_snapshot`.
- `max_lot_effective = min(max_lot, límite por exposición §13, límite por margen)`.
- **Modelos (`sizing_model`):** `structure_risk` (def., SL estructural),
  `atr_risk` (SL = k·ATR), `fixed_lot` (tamaño fijo, para pruebas). El modelo
  cambia el SL, **no** la fórmula de riesgo.
**Casos válidos.** equity 10 000, riesgo 0.5 % = 50; SL 20 pips, value 10/pip/lot →
`size = 50/(20×10) = 0.25 lots` → redondeado a `lot_step`.
**Casos inválidos.** `size < min_lot` → REJECT (`size_below_min`).
**Edge cases.** SL diminuto → `size_raw` enorme → recortado por `max_lot_effective`
(y si el recorte deja el riesgo real < deseado, se acepta; nunca se supera). Divisa
de cotización ≠ cuenta → `fx_rate` del snapshot. `value_per_unit` variable (índices)
→ del perfil. **Determinismo:** redondeo y epsilon fijos → mismo `size` siempre.
**Pseudocódigo.**
```
def positionSizer(req, acc, ctx, cfg):
    risk_pct = effectiveRiskPct(cfg, ctx, acc)                 # §19
    risk_amt = risk_pct * equityBase(acc, cfg)                 # §20
    unit_risk = req.sl_distance * req.value_per_unit * fx(req, snapshot)
    size = clamp(roundDown(risk_amt / unit_risk, cfg.lot_step),
                 cfg.min_lot, maxLotEffective(acc, cfg))
    return size, actualRisk(size, unit_risk)
```

## 19. Gestión dinámica del riesgo

**Definición.** `risk_pct_effective = risk_per_trade_pct × Π(multiplicadores)`,
acotado a `[risk_min_pct, risk_per_trade_max_pct]`. Multiplicadores **deterministas**
por factor (⚙️ `risk_scaling`):
| Factor | Regla | Efecto |
|--------|-------|--------|
| **Context Score** (ENG-011) | `≥ high` → ×`ctx_high`; marginal → ×`ctx_low` | sube en contexto excelente, baja en marginal |
| **Entry Score** (ENG-001) | alta convicción (≥ `high_conviction`) → ×`conv_high` | premia setups A+ |
| **Racha** | `consecutive_losses` → ×`streak_down`; wins → neutral/leve | reduce tras pérdidas |
| **Drawdown** | DD > `dd_soft` → ×`dd_reduce` | protege capital en DD |
| **Estado motor** | `RESTRICTED` → ×`restricted_factor` | reduce cerca de límites |
| **Volatilidad** | `atr_regime=high` → ×`vol_reduce` | menos tamaño en alta vol |
**Regla dura (⛔):** los multiplicadores solo pueden **subir** el riesgo dentro de
`risk_per_trade_max_pct`; ningún multiplicador rompe los límites de §5–§17.
**Casos válidos.** Contexto 88 + convicción 90 → ×1.2 → 0.6 % (≤ max 1 %).
**Casos inválidos.** Cálculo que daría 1.4 % → **capado** a 1.0 %.
**Edge cases.** Multiplicadores que se cancelan (contexto alto pero DD alto) →
producto determinista; el resultado se capa a `[min,max]`.

## 20. Gestión del capital

**Definición.** Define la **base** sobre la que se calcula el riesgo y la evolución
del capital:
- `equity_base ∈ {equity, balance, initial_balance, custom_hwm}` — base del %.
- **Compounding** (`compounding=true`): el riesgo % se aplica sobre el equity actual
  (crece/decrece con resultados). **Fixed base** (`false`): sobre un capital fijo del
  periodo (típico en cuentas fondeadas y para comparabilidad de backtest).
- **High-water mark** (`peak_equity`) para trailing DD (§14) y profit lock (§21).
- **Ajustes de capital** (depósitos/retiradas) → `equity_adjustment_event`
  determinista que reajusta bases y HWM, registrado.
**Casos válidos.** Compounding: tras +5 %, el 0.5 % de riesgo es mayor en valor
absoluto. **Edge cases.** Retirada que baja el equity bajo el HWM → el trailing DD no
se penaliza por la retirada (se ajusta la referencia). Multi-estrategia →
`capital_allocation` reparte presupuesto de riesgo por estrategia (§11).

## 21. Gestión de beneficios (profit management)

**Definición.** Reglas que **protegen** el beneficio:
- **Daily profit lock:** al alcanzar `daily_profit_lock_pct`, el motor pasa a
  `RESTRICTED` (riesgo reducido) o **deja de abrir** (`profit_lock_action`),
  protegiendo el día verde.
- **Trailing profit protection:** una vez en beneficio del periodo, no permitir que
  el equity retroceda más de `profit_giveback_pct` desde el pico (lockout blando).
- **Escalado de beneficios (coordinación con ENG-001 §33):** el Risk Engine **valida**
  los cierres parciales/BE/trailing que propone el Trading Engine, garantizando que no
  violen límites; no los inventa, los **autoriza**.
- **Aumento de riesgo por beneficio:** opcional, subir el riesgo dentro de `max_pct`
  cuando el periodo va en positivo (`profit_scaling`), siempre capado.
**Casos válidos.** +4 % en el día → profit lock → nuevas entradas restringidas.
**Casos inválidos.** Intentar aumentar riesgo por beneficio por encima de
`risk_per_trade_max_pct` → capado. **Edge cases.** Consistency rule de funded (§17)
puede **forzar** dejar de operar para no romper el reparto de beneficio.

## 22. Reglas de bloqueo de nuevas entradas (consolidado)

Una **nueva entrada se bloquea (⛔)** si se cumple **cualquiera**:
1. `engine_state ∈ {HALTED, LOCKED}` (kill-switch / drawdown lockout).
2. `cooldown_active`.
3. Límite alcanzado: diario/semanal/mensual (§6–§8), por símbolo/sesión/estrategia
   (§9–§11), correlación (§12), exposición (§13).
4. Violación de `funded_ruleset` (§17).
5. RR < `min_rr` o SL > `max_sl_atr` (§5).
6. `size < min_lot` o margen insuficiente (§18).
7. Contexto crítico del MCE (manipulación/news/vol extrema) — coordinado con §15.
8. Datos degradados / fail-safe (§0.1.7).
Cada bloqueo se registra con su(s) `reason_code(s)` (explicabilidad).

---

## 23. Casos válidos / inválidos (nivel motor)

**Válidos (APPROVE).**
- Contexto excelente, 1ª operación del día, RR 3.0, riesgo 0.6 % (dinámico), sin
  límites cerca → `RiskApproved(size=0.25)`.
- Segunda operación correlacionada que, recortada al máximo del grupo, cabe →
  `APPROVE` con `size` recortado (`allow_size_reduction`).

**Inválidos (REJECT/estado).**
- Pérdida diaria 2.9 % + nueva 0.5 % > 3 % → `REJECT(daily_loss_limit)`.
- 4ª pérdida consecutiva → `KillSwitch` → `HALTED` → toda nueva `REJECT`.
- DD 10 % → `LOCKED`.
- Viernes tras corte en cuenta fondeada → `REJECT(funded_weekend)`.
- RR 1.5 → `REJECT(rr_below_min)`.

## 24. Edge cases (transversales)

- **Frontera de periodo** (día/sem/mes) con posición abierta: los resets afectan
  contadores nuevos, no posiciones vivas; `equity_base_*_start` se congela en la
  frontera.
- **Gap de apertura** que ya supera un límite → HALTED preventivo antes de evaluar.
- **Config incoherente** (`daily > weekly > monthly` o `min_lot > max_lot`) →
  **error de validación** al cargar el `RiskProfile` ⛔ (no arranca con config mala).
- **Depósito/retirada** intradía → `equity_adjustment_event` determinista.
- **fx_snapshot** ausente para una divisa → fail-safe: `REJECT` (no adivinar tasa).
- **Empate en el borde** de un límite → resuelto por `epsilon` decimal fijo (mismo
  resultado siempre).
- **Reloj**: todo "tiempo" viene del `Clock` inyectado → en backtest el tiempo es el
  de la barra (determinismo total, sin `now()` real).

## 25. Diagramas de flujo

### 25.1 Pre-trade (aprobación + sizing)
```
TradeRequest
   │
   ▼
[HALTED/LOCKED/COOLDOWN/funded?]──sí──► REJECT(reason) ──► registrar + XAI
   │no
   ▼
[límites agregados: día/sem/mes/expo/corr excedidos?]──sí──► REJECT(reasons)
   │no
   ▼
[límites de operación: símbolo/sesión/estrategia/RR/SL?]──sí──► REJECT(reasons)
   │no
   ▼
positionSizer → size
   │
   ▼
[size<min_lot o margen insuficiente?]──sí──► REJECT
   │no
   ▼
APPROVE(size, risk_amount) ──► Execution ; registrar + XAI
```

### 25.2 Ciclo de vida (3 fases)
```
 PRE-TRADE ──approve──► IN-TRADE ───────────────► POST-TRADE
   │reject             │ monitor:                 │ on close:
   ▼                   │  exposición/DD/kill-sw    │  update PnL día/sem/mes
 (fin)                 │  → puede HALT/protect     │  update rachas → cooldown?
                       │  valida BE/trailing/parc. │  update HWM → profit lock?
                       └──────────────► (repite)   │  registrar resultado
                                                   ▼
                                          RiskState actualizado (auditable)
```

## 26. Casos de prueba (deterministas)

- **T1 sizing:** equity 10 000, riesgo 0.5 %, SL 20 pips → `size=0.25` exacto
  (redondeo definido).
- **T2 veto diario:** realized −2.9 %, nueva 0.5 % → `REJECT(daily_loss_limit)`.
- **T3 kill-switch:** 4 pérdidas consecutivas → `HALTED`; siguiente request `REJECT`.
- **T4 drawdown lockout:** DD cruza 10 % → `LOCKED`, solo cierres.
- **T5 correlación:** long EUR+GBP+AUD (ρ alto) supera `max_correlated_risk` → 3ª
  `REJECT(correlated_risk)`.
- **T6 cooldown:** 3 pérdidas → cooldown 60 min → rechazos hasta `cooldown_until`
  (con `Clock` simulado).
- **T7 funded:** intento overnight fin de semana → `REJECT(funded_weekend)`; DD
  trailing > límite firma → `LOCKED`.
- **T8 dinámico:** contexto 88 + convicción 90 → riesgo capado a `max_pct`.
- **T9 determinismo/money:** dos ejecuciones mismo estado+config+fx → misma
  `RiskDecision` y mismo `size` (Decimal, sin drift de float).
- **T10 config inválida:** `max_daily > max_weekly` → falla al cargar (no arranca).
- **T11 reproducibilidad:** reconstruir `RiskState` desde el historial de trades →
  idéntico al estado en vivo (event-sourcing).

## 27. Integración

### 27.1 Con el **Market Context Engine** (ENG-011)
- El `MarketContext` (regime, context_score, volatilidad, sesión, news,
  manipulación) es **entrada** del riesgo dinámico (§19) y del kill-switch (§15).
- El **límite por sesión** (§10) usa la sesión del MCE. `manipulation=extreme` /
  `news.block_active` / `atr_regime=extreme` disparan kill-switch/bloqueo.

### 27.2 Con el **Smart Money Engine** (ENG-002)
- Provee las **anclas** del SL (mecha del sweep D16, borde del POI D21/D24) y del TP
  (pools de liquidez D11–D15, proyecciones Fibonacci D32) → base del `sl_distance`,
  `RR` y sizing. La **invalidación** de detectores es la cota dura del SL
  (`never_widen_sl`).

### 27.3 Con el **Scoring Engine** (ENG-001 §26)
- El **gate de riesgo es posterior** al Entry Score: un setup con score alto **puede
  ser rechazado** por riesgo (el riesgo tiene prioridad ⛔). El `Entry Score` /
  `high_conviction` modula el multiplicador de riesgo dinámico (§19), nunca al revés.

### 27.4 Con el **Execution Engine** (ENG-006)
- **Contrato bloqueante:** Execution **solo** rutea con `RiskApproved(size)`. El
  Risk Engine fija `size`, `SL`, `TP(s)` autorizados. Divergencias de fill/slippage
  vuelven al riesgo (recalcular exposición); fills parciales ajustan el riesgo real;
  el kill-switch puede ordenar `flatten`/`protect` a Execution.

### 27.5 Con el **Backtesting Engine** (ENG-004)
- El Risk Engine se ejecuta **idéntico** en backtest y en vivo (mismo código, `Clock`
  y `fx_snapshot` inyectados) → resultados reproducibles. El backtesting **calibra**
  límites/multiplicadores y la `correlation_matrix`, y reporta métricas de riesgo
  (drawdown, exposición, distribución de tamaños) por régimen.

### 27.6 Con el **Decision Replay Engine** (ENG-009)
- Cada `RiskDecision` (con límites evaluados, holguras, `risk_config_hash`,
  `fx_snapshot_id`, estado del motor) se persiste y es **reproducible paso a paso**;
  el replay muestra por qué se aprobó/rechazó/recortó.

### 27.7 Con **Explainable AI** (ENG-010)
- Toda decisión de riesgo es **explicable**: qué límites se evaluaron, su holgura,
  cuál bloqueó y con qué valores; nunca "rechazado porque sí". Si un modelo ML
  informa el riesgo (p.ej. sizing por volatilidad predicha), entra como **factor
  explicable**, jamás como override opaco.

---

## 28. Garantías y reglas obligatorias (checklist)

- ✅ **Determinismo:** `Clock`/`fx_snapshot` inyectados, aritmética Decimal, redondeo
  y epsilon fijos → misma entrada, misma decisión (T9).
- ✅ **Cero interpretación humana:** toda regla es numérica y configurable.
- ✅ **Configurable:** todo umbral en `RiskProfile` versionado (`risk_config_hash`),
  con validación de coherencia al cargar (T10).
- ✅ **Registrado:** cada decisión → `RiskDecisionRecord` inmutable (ENG-009) + audit
  ledger (SEC-000).
- ✅ **Reproducible:** `RiskState` reconstruible por event-sourcing (T11).
- ✅ **Verificable por tests unitarios:** cada límite/estado/transición tiene su caso
  (T1–T11) + property-based (p.ej. "el riesgo aprobado nunca excede `max_pct`",
  "el tamaño nunca supera límites de exposición").
- ✅ **El riesgo manda:** cualquier límite ⛔ vence a cualquier score/contexto.

**Relaciones:** consume ENG-011 (contexto), ENG-002 (anclas), ENG-001 (score/setup);
gobierna ENG-006 (ruteo); se calibra en ENG-004; se registra/explica en ENG-009/010;
ancla al audit ledger SEC-000.

> **Versión 0.1 — Borrador (🟨).** Especificación oficial del Risk Engine. Aprobación
> (🟩) requiere revisión de Risk Lead, Quant Lead, CTO, Execution y QA; prerrequisito
> del gate D4. Cambios de límites/multiplicadores vía RFC + calibración en ENG-004.
