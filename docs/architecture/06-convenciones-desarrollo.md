# 06 — Convenciones de Desarrollo

> Objetivo: que cualquier ingeniero, en cualquier módulo, encuentre el código
> donde espera y lo entienda sin preguntar. La consistencia es una *feature*.

## 1. Principios de Clean Code

- **Funciones pequeñas**, una responsabilidad, nombres reveladores de intención.
- **Sin comentarios que expliquen *qué* hace el código** (que lo diga el código);
  comentarios solo para el *por qué* y decisiones no obvias.
- **Sin *magic numbers*/strings**: constantes con nombre o value objects.
- **Fail fast**: validar en la frontera (DTOs Pydantic), asumir dominio válido.
- **Inmutabilidad por defecto** en value objects; entidades cambian solo por
  métodos de dominio (nunca *setters* públicos anémicos).
- **Ley de Demeter**: no encadenar a través de estructuras internas ajenas.

## 2. SOLID aplicado

- **S**RP: cada clase/módulo, una razón para cambiar (guía los bounded contexts).
- **O**CP: extensiones vía nuevos adaptadores (nuevo broker = nuevo ACL, no un
  `if`).
- **L**SP: los adaptadores cumplen el contrato del *port* sin sorpresas.
- **I**SP: *ports* pequeños y específicos por caso de uso, no interfaces "gordas".
- **D**IP: el dominio define *ports*; la infraestructura los implementa. Las
  dependencias se inyectan en el *composition root* (`main.py`).

## 3. Modelado táctico DDD

- **Aggregate Root** es la única puerta de entrada a su grafo; protege
  invariantes. Transacción = un agregado.
- **Value Objects** para conceptos sin identidad (Money, Price, Symbol); validan
  en construcción y son inmutables.
- **Domain Events** en pasado (`OrderFilled`), emitidos por el agregado.
- **Domain Services** solo para lógica que no pertenece a un único agregado.
- **Repositories** devuelven/persisten agregados completos; su interfaz vive en
  `domain/ports`, su implementación en `infrastructure/persistence`.
- **Application Services / Handlers** orquestan; no contienen reglas de negocio.

## 4. CQRS ligero

- **Commands** mutan estado, devuelven poco (id/void); un handler por comando.
- **Queries** no mutan; pueden leer *read models*/proyecciones optimizadas, sin
  pasar por agregados.
- Separación física en `application/commands` y `application/queries`.

## 5. Manejo de errores

- Jerarquía de errores de dominio en `shared_kernel/errors`
  (`DomainError` → `ValidationError`, `NotFoundError`, `ConflictError`,
  `PermissionError`, `RiskViolationError`…).
- El dominio lanza **errores de dominio**, nunca `HTTPException`.
- La capa `interfaces` traduce error de dominio → código HTTP/gRPC en un único
  *error handler* central. Mapeo consistente y documentado.
- Respuestas de error siguen **RFC 7807 (Problem Details)**.
- **Nunca** silenciar excepciones; **nunca** exponer *stack traces* al cliente.

## 6. Logging, tracing y métricas

- **Logs estructurados** (JSON) con `structlog`; nada de `print`.
- **Correlación**: cada request/evento lleva `trace_id`, `tenant_id`,
  `request_id`; se propagan por OpenTelemetry.
- **Niveles**: `ERROR` (acción requerida), `WARN` (anómalo recuperable),
  `INFO` (hitos de negocio), `DEBUG` (diagnóstico, off en prod).
- **Nunca** loguear secretos, credenciales de broker ni PII sin enmascarar.
- Cada caso de uso relevante emite una **métrica** (contador/histograma) y un
  **span**.

## 7. Configuración

- **12-Factor**: config por entorno vía variables de entorno, tipada con
  Pydantic Settings. Nada de valores hardcodeados por entorno en el código.
- Secretos **nunca** en el repo ni en `.env` versionado (ver [seguridad](11-seguridad.md)).
- Feature flags para cambios de comportamiento en runtime.

## 8. Asincronía y concurrencia

- Async por defecto en I/O (FastAPI, httpx, drivers async).
- El dominio es **síncrono y puro**; la asincronía vive en `application`/`infra`.
- Idempotencia obligatoria en consumidores de eventos y endpoints de mutación
  (claves de idempotencia).

## 9. Dependencias y librerías

- Añadir dependencia = decisión con dueño; evaluar mantenimiento, licencia,
  tamaño y alternativas. Preferir stdlib y lo ya presente.
- Versiones **fijadas** (lockfiles) y auditadas (Trivy/Grype) en CI.
- Actualizaciones vía Renovate/Dependabot con revisión.

## 10. Documentación de código

- Docstrings en interfaces públicas (ports, application services) explicando
  contrato e invariantes.
- Cada módulo tiene un `README.md` con su propósito, agregados y eventos.
- Decisiones estructurales → **ADR** en `docs/adr/`.
- El **lenguaje ubicuo** manda: los nombres en el código = los del glosario.

## 11. Definition of Done (DoD)

Una tarea está *hecha* cuando:
1. Cumple el criterio de aceptación y el lenguaje ubicuo.
2. Tiene tests (unit + los de integración/contrato que apliquen) y pasan.
3. Cobertura del dominio afectado no baja del umbral acordado.
4. Lint, type-check y linters de arquitectura en verde.
5. Observabilidad añadida (logs/métricas/spans) donde aplique.
6. Documentación/README/ADR actualizados si cambió algo estructural.
7. Revisado y aprobado por PR (ver [Git Strategy](08-git-strategy.md)).
8. Sin secretos ni vulnerabilidades nuevas detectadas.
