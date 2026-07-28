# 02 — Módulos y Bounded Contexts (DDD Estratégico)

## 1. Mapa de subdominios

Clasificamos los dominios por su valor estratégico. Esto guía **dónde
invertir** talento y **qué comprar vs. construir**.

| Tipo | Subdominio | Módulo | Estrategia |
|------|-----------|--------|-----------|
| **Core** (ventaja competitiva) | Backtesting de alta fidelidad | `backtesting` | Construir, equipo senior |
| **Core** | Ejecución & OMS | `execution` (+ `execution-gateway`) | Construir, equipo senior |
| **Core** | Gestión de riesgo | `risk` | Construir, equipo senior |
| **Core** | Plataforma de datos de mercado | `market_data` (+ `market-data-ingestor`) | Construir |
| **Core** | Autoría de estrategias | `strategy_lab` | Construir |
| **Supporting** | Portfolio & posiciones | `portfolio` | Construir |
| **Supporting** | Analítica & reporting | `analytics` | Construir + herramientas OSS |
| **Supporting** | Marketplace | `marketplace` | Construir (fase 2) |
| **Supporting** | Notificaciones & alertas | `notifications` | Construir ligero |
| **Generic** (comprar/usar OSS) | Identidad y accesos | `iam` | Keycloak/Auth0 + capa fina |
| **Generic** | Facturación & suscripciones | `billing` | Stripe + capa fina |
| **Generic** | Auditoría & cumplimiento | `audit` (transversal) | Ledger append-only |

---

## 2. Ficha de cada bounded context

### 2.1 `iam` — Identity & Access Management (Generic)
- **Responsabilidad:** usuarios, organizaciones/tenants, roles, permisos
  (RBAC + ABAC), sesiones, API keys, MFA.
- **Agregados:** `User`, `Organization`, `Membership`, `ApiKey`, `Role`.
- **Publica:** `UserRegistered`, `OrganizationCreated`, `MembershipRevoked`.
- **Lenguaje ubicuo:** *Tenant, Principal, Role, Scope, Grant*.

### 2.2 `billing` — Billing & Subscriptions (Generic)
- **Responsabilidad:** planes, suscripciones, uso medido, facturas, pagos
  (integración Stripe vía ACL).
- **Agregados:** `Subscription`, `Plan`, `Invoice`, `UsageRecord`.
- **Publica:** `SubscriptionActivated`, `SubscriptionCanceled`, `PaymentFailed`.
- **Consume:** eventos de uso de otros módulos (backtests ejecutados, órdenes).

### 2.3 `market_data` — Market Data Platform (Core)
- **Responsabilidad:** ingesta, normalización y almacenamiento de datos de
  mercado (tick, OHLCV, order book, fundamentales). Catálogo de instrumentos.
- **Agregados:** `Instrument`, `DataFeed`, `Candle`, `BarSeries`.
- **Publica:** `MarketTickReceived`, `BarClosed`, `InstrumentListed`.
- **Nota:** el *hot path* de streaming vive en `market-data-ingestor`; este
  módulo gestiona catálogo, calidad de datos y acceso histórico.

### 2.4 `strategy_lab` — Strategy Authoring (Core)
- **Responsabilidad:** definición, versionado, parámetros, validación y ciclo
  de vida de estrategias (borrador → validada → publicada → retirada).
- **Agregados:** `Strategy`, `StrategyVersion`, `ParameterSet`.
- **Publica:** `StrategyVersionCreated`, `StrategyValidated`, `StrategyPublished`.
- **Lenguaje ubicuo:** *Signal, Indicator, Rule, Parameter, Universe*.

### 2.5 `backtesting` — Backtesting Engine (Core)
- **Responsabilidad:** orquestar simulaciones históricas **deterministas y
  reproducibles**; gestionar jobs, resultados y artefactos.
- **Agregados:** `Backtest`, `BacktestRun`, `SimulationResult`.
- **Publica:** `BacktestRequested`, `BacktestCompleted`, `BacktestFailed`.
- **Nota:** coordina; el cómputo pesado corre en `quant-workers` (Temporal).

### 2.6 `execution` — Execution & Order Management (Core)
- **Responsabilidad:** ciclo de vida de órdenes (OMS), ruteo (SOR), sesiones de
  trading, conciliación de fills. Modo *paper* y *live*.
- **Agregados:** `Order`, `TradingSession`, `ExecutionReport`, `Fill`.
- **Publica:** `OrderPlaced`, `OrderFilled`, `OrderRejected`, `OrderCanceled`.
- **Consume:** `RiskApproved` / `RiskRejected` (obligatorio antes de rutear).
- **Nota:** el envío/recepción real a broker vive en `execution-gateway`.

### 2.7 `risk` — Risk Management (Core)
- **Responsabilidad:** riesgo **pre-trade** (límites de posición, exposición,
  apalancamiento, buying power) y **post-trade** (drawdown, VaR), *kill-switch*.
- **Agregados:** `RiskProfile`, `RiskLimit`, `ExposureSnapshot`.
- **Publica:** `RiskApproved`, `RiskRejected`, `RiskLimitBreached`, `KillSwitchTriggered`.
- **Regla dura:** ninguna orden `live` se rutea sin aprobación de `risk`.

### 2.8 `portfolio` — Portfolio & Positions (Supporting)
- **Responsabilidad:** *position keeping*, cálculo de PnL (realizado/no
  realizado), balances, valuación de cartera.
- **Agregados:** `Portfolio`, `Position`, `Balance`, `PnLSnapshot`.
- **Publica:** `PositionOpened`, `PositionClosed`, `PortfolioRevalued`.
- **Consume:** `OrderFilled` de `execution`.

### 2.9 `analytics` — Analytics & Reporting (Supporting)
- **Responsabilidad:** métricas de performance (Sharpe, Sortino, drawdown,
  win-rate), *tearsheets*, atribución, comparativas.
- **Agregados:** `PerformanceReport`, `Metric`, `Tearsheet`.
- **Modelo:** principalmente **lectura** (CQRS); consume eventos y proyecta.

### 2.10 `marketplace` — Strategy Marketplace (Supporting)
- **Responsabilidad:** publicar, descubrir, suscribir y monetizar estrategias;
  *revenue share* con creadores; ratings.
- **Agregados:** `Listing`, `Subscription` (a estrategia), `Payout`, `Review`.
- **Publica:** `StrategyListed`, `StrategySubscribed`, `PayoutIssued`.

### 2.11 `notifications` — Notifications & Alerts (Supporting)
- **Responsabilidad:** entrega multicanal (email, push, webhook, in-app) de
  alertas y eventos relevantes; preferencias; plantillas.
- **Agregados:** `NotificationChannel`, `AlertRule`, `NotificationPreference`.
- **Consume:** casi todos los eventos de dominio del sistema.

### 2.12 `audit` — Audit & Compliance (Generic, transversal)
- **Responsabilidad:** *ledger* append-only e inmutable de acciones sensibles
  (órdenes, cambios de riesgo, accesos), trazabilidad regulatoria.
- **Modelo:** *write-once*, particionado temporal; nunca se borra.

---

## 3. Context Map (relaciones entre contextos)

Notación DDD: `U` = Upstream, `D` = Downstream, `ACL` = Anti-Corruption Layer,
`OHS` = Open Host Service, `PL` = Published Language.

```
                      ┌────────────┐
                      │    iam     │  (OHS/PL: identidad y tenant)
                      └─────┬──────┘
        provee contexto de tenant a todos (U)
                            │
   ┌──────────────┐   ┌─────▼──────┐   ┌───────────────┐
   │ market_data  │──►│ strategy_  │──►│  backtesting  │
   │  (U, PL)     │   │   lab (U)  │   │   (D)         │
   └──────┬───────┘   └─────┬──────┘   └──────┬────────┘
          │                 │                 │ resultados
          │ precios (U)     │ estrategia (U)  ▼
          │            ┌────▼─────────────────────────┐
          └───────────►│         execution            │
                       │  (OMS, coordinador)          │
                       └───┬───────────────┬──────────┘
             comando (D)   │ pide riesgo   │ fills (U)
                  ┌────────▼───┐      ┌─────▼──────┐
                  │    risk    │      │ portfolio  │
                  │  (U, dura) │      │   (D)      │
                  └────────────┘      └─────┬──────┘
                                            │ posiciones/PnL (U)
   ┌────────────┐   ┌──────────────┐   ┌────▼──────┐   ┌──────────────┐
   │  billing   │   │ marketplace  │   │ analytics │   │ notifications│
   │ (consume   │   │ (consume     │   │ (consume  │   │ (consume     │
   │  uso)      │   │  publish/subs│   │  todo, R) │   │  casi todo)  │
   └────────────┘   └──────────────┘   └───────────┘   └──────────────┘

              execution ──► execution-gateway ──► Brokers/Exchanges  (ACL)
              market_data ──► market-data-ingestor ──► Data providers (ACL)
```

Relaciones destacadas:
- **`iam`** es *Open Host Service*: publica el "lenguaje" de tenant/principal
  que todos consumen.
- **`execution` ↔ `risk`**: relación de *Customer/Supplier* con contrato duro;
  `risk` es *upstream* y bloqueante.
- **Conectividad a brokers y proveedores de datos**: siempre tras un **ACL**
  (adaptadores en `infrastructure/external`) para que un cambio de MT5/IB/Binance
  no contamine el dominio.
- **`analytics`, `notifications`, `billing`, `marketplace`**: consumidores
  *event-driven* → bajo acoplamiento, escalan y fallan de forma aislada.

---

## 4. Lenguaje ubicuo (extracto)

Se mantiene un glosario vivo en `docs/domain/ubiquitous-language.md`. Ejemplos:

- **Order**: intención de comprar/vender un instrumento con parámetros.
- **Fill / Execution Report**: confirmación (parcial o total) de una orden.
- **Position**: exposición neta a un instrumento en una cuenta.
- **Signal**: salida de una estrategia que puede derivar en órdenes.
- **Kill-switch**: mecanismo de parada de emergencia de toda la operativa.
- **Tearsheet**: informe estandarizado de performance de una estrategia.

Nota crítica: la palabra **"Subscription"** significa cosas distintas en
`billing` (plan de pago) y en `marketplace` (suscripción a una estrategia). Son
**conceptos diferentes en contextos diferentes** — no se comparten; eso es DDD
bien hecho.
