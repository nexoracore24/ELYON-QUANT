# 03 — Dependencias entre Módulos

## 1. Principios

1. **Fronteras explícitas**: un módulo solo se comunica con otro por su
   **capa de aplicación pública** (interfaces/ports) o por **eventos**. Jamás
   accediendo a su `domain`, `infrastructure` ni a su base de datos.
2. **Database-per-module**: cada módulo posee su esquema. Prohibido `JOIN`
   entre esquemas de módulos distintos. Los datos ajenos se obtienen por
   consulta a la API interna del módulo o por proyección desde eventos.
3. **Dependencias acíclicas** (DAG): el grafo de dependencias no puede tener
   ciclos. Se verifica en CI.
4. **Estabilidad**: un módulo solo puede depender en compilación de módulos
   **más estables** que él (`shared_kernel` es el más estable). Todo lo demás
   se desacopla vía eventos.
5. **Sync para consistencia fuerte, async por defecto**: se usa llamada
   in-process síncrona solo cuando el caso de uso requiere consistencia
   transaccional inmediata; en el resto, eventos.

---

## 2. Tipos de dependencia permitidos

| Tipo | Dirección | Cuándo | Riesgo de acoplamiento |
|------|-----------|--------|------------------------|
| **Shared Kernel** | Todos → `shared_kernel` | VOs universales (Money, Symbol) | Bajo (estable) |
| **Llamada in-process** | A → interfaz `application` de B | Consulta/comando con consistencia fuerte | Medio (contrato explícito) |
| **Evento de dominio** | A publica → B consume | Reacción, integración, proyecciones | Bajo |
| **ACL a terceros** | Módulo → adaptador externo | Broker, data provider, Stripe | Aislado por diseño |

Prohibido: import directo de `otro_modulo.domain.*` o
`otro_modulo.infrastructure.*`; acceso a tablas de otro esquema.

---

## 3. Grafo de dependencias (compile-time)

Flechas = "depende en compilación de". Nótese que es acíclico y que casi todo
converge en `shared_kernel`. El resto de relaciones son **runtime vía eventos**
(no crean dependencia de compilación).

```
                         shared_kernel
                              ▲
        ┌──────────┬──────────┼──────────┬──────────┬─────────┐
        │          │          │          │          │         │
       iam      market_data strategy_lab risk    portfolio  billing
        ▲          ▲          ▲           ▲          ▲
        │          │          │           │          │
        └──────────┴────┬─────┴─────┬─────┴──────────┘
                        │           │
                   backtesting   execution
                                     │ (in-process, síncrono, bloqueante)
                                     ▼
                                   risk

   analytics, notifications, marketplace:
        dependen SOLO de shared_kernel en compilación;
        todo lo demás lo reciben por EVENTOS (runtime).
```

### Dependencias de compilación declaradas (whitelist)

| Módulo | Puede importar (compile-time) | Integra por eventos (runtime) |
|--------|-------------------------------|-------------------------------|
| `iam` | shared_kernel | — |
| `market_data` | shared_kernel | — |
| `strategy_lab` | shared_kernel, market_data (ports) | market_data |
| `backtesting` | shared_kernel, strategy_lab (ports), market_data (ports) | strategy_lab, market_data |
| `risk` | shared_kernel, portfolio (ports) | portfolio, execution |
| `execution` | shared_kernel, risk (ports), strategy_lab (ports) | market_data, portfolio, risk |
| `portfolio` | shared_kernel | execution |
| `billing` | shared_kernel, iam (ports) | *(uso de todos)* |
| `marketplace` | shared_kernel, iam (ports), billing (ports) | strategy_lab, portfolio |
| `analytics` | shared_kernel | *(casi todos)* |
| `notifications` | shared_kernel | *(casi todos)* |

> `execution → risk` es la única dependencia **síncrona bloqueante** por diseño:
> ninguna orden `live` sale sin `RiskApproved`. Se implementa como llamada a la
> interfaz de aplicación de `risk`, no como evento (necesitamos la respuesta ya).

---

## 4. Contratos como fuente de verdad

Toda comunicación cruza un contrato versionado en `/contracts`:

- **API pública** → OpenAPI 3.1 (`contracts/openapi/`).
- **Eventos de dominio** → AsyncAPI + esquemas Avro/Protobuf en Schema Registry
  (`contracts/asyncapi/`, `contracts/avro/`).
- **gRPC interno** (futuro, al extraer servicios) → `contracts/proto/`.

Los tipos de `packages/py/elyon-contracts` y `packages/ts/api-client` se
**generan** desde estos contratos. Cambiar un contrato es un acto deliberado
(revisión + versionado + compatibilidad hacia atrás). Ver
[testing de contratos](09-testing-strategy.md#contract-testing).

---

## 5. Consistencia de datos entre módulos

- **Outbox Pattern**: al cambiar estado, el módulo escribe el evento en una
  tabla `outbox` **en la misma transacción** que su agregado; un *relay*
  publica al bus. Garantiza "al menos una vez" sin *dual write*.
- **Inbox / Idempotencia**: todo consumidor registra los `event_id` procesados;
  reprocesar un evento no tiene efecto (idempotente).
- **Sagas**: procesos multi-módulo (p.ej. *place order* → riesgo → ruteo →
  fill → actualizar portfolio) se orquestan con **Temporal**, con pasos de
  compensación ante fallo.
- **Read Models / Proyecciones**: `analytics` y las vistas de UI mantienen
  proyecciones propias alimentadas por eventos (CQRS), evitando consultar en
  caliente a los módulos *core*.

---

## 6. Verificación automática (fitness functions)

Estas reglas **se testean en CI**, no son buena voluntad:

- `import-linter` (Python): capas y whitelist de dependencias entre módulos.
- Prohibición de import de `*.domain`/`*.infrastructure` ajenos.
- Detección de ciclos en el grafo de módulos.
- Detección de `JOIN` cruzando esquemas (revisión de migraciones + tests).
- Verificación de compatibilidad de esquemas de eventos (Schema Registry:
  compatibilidad `BACKWARD`).

Un PR que viole una frontera **no compila el pipeline**. La arquitectura se
protege con automatización, no con revisiones manuales.
