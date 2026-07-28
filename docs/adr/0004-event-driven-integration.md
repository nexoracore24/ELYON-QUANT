# ADR-0004: Integración event-driven entre bounded contexts

- **Estado:** Accepted
- **Fecha:** 2026-07-28
- **Decisores:** Arquitectura / CTO
- **Relacionado:** [03-dependencias](../architecture/03-dependencias-entre-modulos.md)

## Contexto

Los módulos deben integrarse sin acoplarse. Una integración síncrona punto a
punto entre todos crea un grafo frágil (un fallo se propaga, un cambio obliga a
desplegar a todos). Pero algunos caminos sí requieren respuesta inmediata y
consistencia fuerte (p.ej. aprobación de riesgo antes de rutear una orden).

## Opciones consideradas

1. **Todo síncrono (llamadas directas)** — simple de razonar, pero acoplamiento
   temporal y fallos en cascada; mala escalabilidad.
2. **Todo asíncrono (eventos para todo)** — máximo desacoplamiento, pero
   complejidad de consistencia eventual incluso donde no aporta.
3. **Híbrido: eventos por defecto, síncrono donde la consistencia lo exige.**

## Decisión

**Event-driven por defecto** para integración entre bounded contexts (Kafka/
Redpanda, eventos de dominio, Schema Registry con compatibilidad BACKWARD).
**Llamada in-process síncrona** solo cuando el caso de uso requiere consistencia
fuerte inmediata (p.ej. `execution → risk`). Consistencia garantizada con
**Outbox/Inbox**, **idempotencia** obligatoria y **Sagas (Temporal)** para
procesos multi-módulo.

## Consecuencias

- **Positivas:** bajo acoplamiento, escalabilidad, aislamiento de fallos,
  extensibilidad (nuevos consumidores sin tocar productores), base para CQRS.
- **Negativas / trade-offs:** consistencia eventual que hay que diseñar
  explícitamente; complejidad de *tooling* (bus, registry, DLQs); *debugging*
  distribuido → exige observabilidad de primera (tracing).
- **Reglas asociadas:** todo consumidor es idempotente; todo cambio de estado
  con efecto externo usa Outbox; los eventos versionan con compatibilidad
  hacia atrás.
