# 12 — Escalabilidad

> Diseñamos para escalar por **diseño**, pero optimizamos por **medición**.
> Primero la arquitectura no bloquea el escalado; el tuning llega con datos de
> observabilidad, no con corazonadas.

## 1. Dimensiones de escala

ELYON QUANT crece en ejes distintos que se escalan de forma independiente:

| Eje | Presión | Estrategia |
|-----|---------|-----------|
| **Tenants / usuarios** | Multi-tenant SaaS | Stateless + escalado horizontal, aislamiento por tenant |
| **Símbolos / feeds** | Ingesta de mercado | Particionado por símbolo, `market-data-ingestor` escalable |
| **Backtests** | Cómputo bursátil | `quant-workers` elásticos, paralelismo masivo |
| **Órdenes / segundo** | Ejecución en picos | `execution-gateway` de baja latencia, particionado por venue |
| **Consultas analíticas** | Reporting | OLAP (ClickHouse) + read models (CQRS) |
| **Eventos** | Integración interna | Kafka particionado, consumidores escalables |

## 2. Servicios sin estado (stateless)

- `platform-api`, `quant-workers` y los frontends son **stateless**: el estado
  vive en Postgres/Redis/S3/Kafka. Escalado **horizontal** trivial (más réplicas
  + HPA).
- Sesión y contexto en tokens/Redis, no en memoria del proceso.
- Trabajo idempotente para poder reintentar y re-balancear sin daño.

## 3. Escalado de datos

- **PostgreSQL**: réplicas de lectura para *queries*; *connection pooling*
  (PgBouncer); **particionado** por tiempo/tenant en tablas grandes.
  Sharding por tenant cuando un solo primario no baste (Fase 4).
- **Series temporales** (`market_data`): TimescaleDB (hypertables, compresión,
  retención por tiers) y/o **ClickHouse** para analítica masiva.
- **CQRS + Read Models**: las lecturas de UI/analytics no golpean los agregados
  *core*; se sirven de proyecciones optimizadas alimentadas por eventos.
- **Caching por niveles**: Redis (datos calientes, rate-limit, locks), CDN
  (estáticos y respuestas cacheables), caches locales con invalidación por
  evento. *Cache-aside* como patrón por defecto.
- **Tiering de almacenamiento**: caliente (BD) → templado (ClickHouse/parquet) →
  frío (S3/Glacier) para históricos.

## 4. Escalado del event backbone

- **Kafka/Redpanda** particionado por clave de negocio (`tenant_id`,
  `symbol`, `aggregate_id`) para paralelismo con orden garantizado por clave.
- Consumidores en *consumer groups* que escalan hasta el nº de particiones.
- **Backpressure**, *dead-letter queues* y reintentos con *backoff*.
- Outbox/inbox para no perder ni duplicar eventos bajo carga.

## 5. Escalado del cómputo (backtesting/ML)

- **Temporal** orquesta jobs; `quant-workers` escalan elásticamente (incluso en
  *spot instances* por ser tolerantes a fallo).
- Paralelización de backtests por partición de parámetros/símbolos
  (*embarrassingly parallel*); resultados a S3.
- Aislamiento de recursos por tenant/plan (cuotas) para evitar *noisy neighbors*.

## 6. Escalado de la ejecución (hot path)

- `execution-gateway` en **Rust**: sub-ms, sin GC, conexiones persistentes a
  venues, *sharding* por broker/venue.
- Colas de órdenes con prioridad; *backpressure* hacia el cliente si un venue se
  satura.
- Ubicación cercana al broker (co-location/región) para minimizar RTT.

## 7. Multi-tenancy y aislamiento

- **Aislamiento lógico** por defecto (RLS + `tenant_id`), con **cuotas y
  rate-limits por plan**.
- Clientes *enterprise*: opción de aislamiento reforzado (esquema/DB dedicada o
  *cell* dedicada).
- **Cell-based architecture** (Fase 4): particionar tenants en *cells*
  independientes para acotar el *blast radius* y escalar linealmente.

## 8. Resiliencia (escalar también significa no caerse)

- **Timeouts, retries con backoff+jitter, circuit breakers, bulkheads** en todo
  ACL a broker/proveedor externo.
- **Degradación elegante**: si `analytics` cae, la ejecución sigue; los caminos
  críticos no dependen de los no críticos.
- **Idempotencia** en todos los caminos de mutación.
- **SLOs + error budgets** por servicio; autoscaling guiado por SLIs (latencia,
  saturación), no solo CPU.
- **Chaos engineering** para validar los mecanismos anteriores (ver
  [testing](09-testing-strategy.md)).

## 9. Rendimiento y coste

- **Medir siempre**: perfiles, *load tests* (k6), tracing distribuido para
  encontrar cuellos reales antes de optimizar.
- Presupuesto de latencia por caso de uso (SLO) y por *hop*.
- Eficiencia de coste: autoscaling fino, *spot* para cargas tolerantes, tiering
  de datos, apagar lo que no se usa. La escala no puede quebrar la unidad
  económica.

## 10. Camino de escalado (resumen ejecutivo)

1. **Fase 1-2**: monolito modular stateless + Postgres/Timescale + Kafka; escala
   vertical/horizontal simple. Suficiente para miles de tenants.
2. **Fase 3**: read models y ClickHouse para analítica; réplicas y particionado;
   caches por niveles.
3. **Fase 4**: extracción de servicios core, sharding por tenant, multi-región y
   *cells*. Cada paso se justifica con métricas, no con anticipación especulativa.

> La clave: **las fronteras de módulo y el event-driven de hoy son lo que hará
> barato el escalado de mañana.** No sobre-diseñamos; dejamos las costuras
> preparadas.
