# 07 — Naming Convention

> Los nombres siguen el **lenguaje ubicuo** del dominio. La técnica se adapta al
> negocio, no al revés. Consistencia > preferencia personal.

## 1. Reglas generales

- **Inglés** para todo el código, identificadores y nombres de recursos técnicos
  (la documentación de negocio puede ser en español).
- Nombres que **revelan intención**; sin abreviaturas salvo las universales
  (`id`, `url`, `db`, `ohlcv`, `pnl`).
- Nada de sufijos vacíos tipo `Manager`, `Helper`, `Util`, `Data`, `Info` en el
  dominio. Un nombre de dominio describe un concepto del negocio.
- Booleans en forma de predicado: `is_active`, `has_expired`, `can_trade`.
- Evitar acoplar nombres a tecnología en el dominio (`UserRepository`, no
  `UserPostgresDao` — eso es el nombre de la *implementación* en infra).

## 2. Por lenguaje

### Python (backend)
| Elemento | Convención | Ejemplo |
|----------|-----------|---------|
| Módulo/paquete/archivo | `snake_case` | `strategy_lab`, `place_order.py` |
| Clase / Aggregate / VO | `PascalCase` | `Order`, `RiskProfile`, `Money` |
| Función / método / variable | `snake_case` | `calculate_pnl`, `unrealized_pnl` |
| Constante | `UPPER_SNAKE_CASE` | `MAX_LEVERAGE`, `DEFAULT_TIMEFRAME` |
| Enum / miembros | `PascalCase` / `UPPER_SNAKE` | `OrderSide.BUY` |
| Privado | prefijo `_` | `_apply_fill` |
| Interfaz/Port | nombre del rol (sin `I`) | `OrderRepository`, `BrokerGateway` |

### TypeScript (frontend)
| Elemento | Convención | Ejemplo |
|----------|-----------|---------|
| Archivo componente | `PascalCase.tsx` | `StrategyCard.tsx` |
| Archivo util/hook | `camelCase.ts` / `useX.ts` | `formatMoney.ts`, `useOrders.ts` |
| Componente / Tipo / Interface | `PascalCase` | `OrderTable`, `type Strategy` |
| Variable / función | `camelCase` | `activeOrders`, `submitOrder` |
| Constante global | `UPPER_SNAKE_CASE` | `API_BASE_URL` |
| Carpeta | `kebab-case` | `strategy-lab/` |

### Rust (execution-gateway)
| Elemento | Convención | Ejemplo |
|----------|-----------|---------|
| Módulo/archivo | `snake_case` | `order_router.rs` |
| Struct/Enum/Trait | `PascalCase` | `OrderRouter`, `Venue` |
| Función/variable | `snake_case` | `route_order` |
| Constante | `UPPER_SNAKE_CASE` | `MAX_INFLIGHT` |

## 3. Nombres de dominio (DDD)

| Concepto | Patrón | Ejemplos |
|----------|--------|----------|
| Aggregate / Entity | sustantivo del negocio | `Order`, `Backtest`, `Portfolio` |
| Value Object | sustantivo, inmutable | `Money`, `Price`, `Symbol`, `Quantity` |
| Domain Event | **pasado** `<Aggregate><VerboPasado>` | `OrderPlaced`, `RiskRejected` |
| Command | **imperativo** `<Verbo><Aggregate>` | `PlaceOrder`, `RunBacktest` |
| Command Handler | `<Command>Handler` | `PlaceOrderHandler` |
| Query | `Get/List/Find<X>` | `GetPortfolioPnL`, `ListStrategies` |
| Domain Service | `<Concepto>Service`/`Policy` | `PositionSizingService`, `RiskPolicy` |
| Repository (port) | `<Aggregate>Repository` | `OrderRepository` |
| Gateway (port) | `<Externo>Gateway` | `BrokerGateway`, `MarketDataGateway` |
| Factory | `<Aggregate>Factory` | `OrderFactory` |
| DTO | `<X>Request`/`<X>Response`/`<X>Dto` | `PlaceOrderRequest` |

## 4. Base de datos (PostgreSQL)

- Esquema = nombre del módulo: `iam`, `execution`, `portfolio`…
- Tablas: `snake_case` **plural**: `orders`, `risk_limits`.
- Columnas: `snake_case` singular: `created_at`, `tenant_id`.
- PK: `id` (UUID v7). FK: `<entidad>_id`: `order_id`.
- Timestamps: `created_at`, `updated_at` (UTC). Soft-delete: `deleted_at`.
- Índices: `ix_<tabla>_<cols>`; únicos: `uq_<tabla>_<cols>`; FK: `fk_<t>_<t>`.
- Migraciones: `NNNN_verbo_descripcion.py` (`0007_add_orders_status_index.py`).

## 5. APIs REST

- Recursos en **plural, kebab-case**: `/api/v1/strategies`, `/api/v1/risk-limits`.
- Jerarquía por pertenencia: `/portfolios/{id}/positions`.
- Verbos HTTP para acciones CRUD; acciones de dominio como sub-recurso:
  `POST /orders/{id}/cancel`.
- Versionado en la ruta: `/api/v1/...`.
- Campos JSON en `camelCase` (contrato de cara al frontend TS).
- Query params `camelCase`; paginación `?page=&pageSize=` o cursor.

## 6. Eventos (Kafka / AsyncAPI)

- Topic: `<dominio>.<agregado>.<evento>` en `kebab`/`dot`:
  `execution.order.placed`, `risk.limit.breached`.
- Envelope estándar: `eventId`, `eventType`, `occurredAt`, `tenantId`,
  `aggregateId`, `version`, `payload`.
- Nombre del tipo de evento = nombre del Domain Event (`OrderPlaced`).

## 7. Infraestructura y recursos cloud

- Recursos: `elyon-<entorno>-<servicio>-<recurso>`:
  `elyon-prod-platform-api`, `elyon-staging-market-data-db`.
- Namespaces K8s: `<entorno>` o `<dominio>` según aislamiento.
- Variables de entorno: `UPPER_SNAKE_CASE` con prefijo `ELYON_`:
  `ELYON_DB_URL`, `ELYON_KAFKA_BROKERS`.

## 8. Git

- Ramas: `type/scope-descripcion-corta`:
  `feat/execution-place-order`, `fix/risk-leverage-check`.
- Ver [Git Strategy](08-git-strategy.md) para *conventional commits* y detalle.
