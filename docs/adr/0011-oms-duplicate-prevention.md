# ADR-0011: Prevención de órdenes duplicadas en el OMS

- **Estado:** Accepted
- **Fecha:** 2026-08-25
- **Decisores:** CTO/Principal Architect, Execution Lead, Risk Lead
- **Relacionado:** ENG-006 §0.1, §4.3–4.5 · ADR-EXE-1, ADR-EXE-4, ADR-EXE-5,
  ADR-EXE-8 · [ADR-0006](0006-deterministic-computing.md)
- **Implementa:** `modules/execution/domain`

## Contexto

El bug más caro que puede tener un OMS es una **posición duplicada**. Duplica el
riesgo en silencio: no lanza ninguna alarma, la cuenta se ve bien, y el problema
solo aparece cuando el mercado se mueve en contra al doble de velocidad de la
esperada.

Y llega por una vía perfectamente banal: **un envío que da timeout tiene
resultado desconocido.** La orden puede estar descansando en el broker, puede
haberse ejecutado ya, o puede no haber llegado nunca. Los tres casos se ven
exactamente igual desde el lado del OMS.

Reintentar sobre esa duda es la reacción natural y es la equivocada.

## Decisión

### 1. Tres defensas independientes, no una

Ninguna basta sola, y ninguna depende de que las otras funcionen.

**a) La máquina de estados.** `QUEUED` es el **único** estado de origen de la
transición a `SENT`. No hay ningún otro camino. Un segundo envío no está
desaconsejado por una comprobación que alguien pueda olvidar: es una transición
que no existe en la tabla.

La tabla es **datos**, no código con ramas. Escrita como `Mapping[EventKind,
Mapping[OrderState, OrderState]]` se puede inspeccionar y verificar de forma
exhaustiva — hay un test que recorre toda la tabla y prueba que **ningún estado
terminal transita a uno activo**.

**b) `client_order_id` determinista.** Derivado del `correlation_id` vía UUIDv5,
nunca de un reloj ni de un generador aleatorio. Un reintento tiene que ser
reconociblemente la *misma* orden o el broker no puede deduplicarla; un id
derivado del tiempo haría que cada reintento pareciese una orden nueva.

**c) Preguntar antes de reenviar.**

```
query(client_order_id)
  existe  → adoptarla (la orden ya estaba puesta)
  ausente → reenviar, con el MISMO client_order_id
```

`query` **no es opcional ni una optimización**: es lo único que separa un envío
con timeout de una posición duplicada. Por eso está en el `Protocol` del adapter
como método obligatorio.

### 2. Un rechazo es un hecho; un timeout es una pregunta

`BrokerErrorKind` distingue explícitamente `TIMEOUT`/`UNAVAILABLE` (resultado
desconocido) de `REJECTED`/`INVALID` (hecho). Solo los primeros disparan
conciliación. Tratarlos igual llevaría a conciliar rechazos —ruido— o, mucho
peor, a reintentar timeouts a ciegas.

### 3. Cuando la duda persiste: parar, no cerrar

Si el broker no responde ni siquiera a `query`, el OMS **no puede saber la
verdad** y hace lo único siempre seguro: `halt()`. Deja de enviar órdenes nuevas
y **protege** lo abierto.

**Parar no es cerrar.** Cerrar posiciones durante una caída de conectividad es
operar a ciegas en el peor momento posible. Parar significa no asumir riesgo
nuevo mientras la situación no está clara.

### 4. El estado es un fold sobre el log

Event sourcing (ADR-EXE-1). El estado nunca se asigna; se reconstruye con
`Order.replay(request, events)`. Un proceso que muere a mitad de un envío vuelve,
reproduce, y sabe qué había hecho — porque **el evento `SENT` se persiste antes
de la llamada de red**, no después.

Escribir el evento después de la I/O lo perdería exactamente el fallo del que
existe para protegernos.

`Oms.order(coid)` reconstruye desde el log en cada consulta en vez de cachear.
A esta escala el coste es irrelevante y la propiedad es fuerte: la proyección es
*demostrablemente* una función del log, así que una divergencia entre ambos es
imposible, no meramente improbable.

### 5. Exactly-once lógico, no en el transporte

Entrega at-least-once + dedup por `broker_event_id` en el agregado
(ADR-EXE-4). Un execution report reentregado se aplica una vez. Intentar
exactly-once en el transporte es un problema mucho más difícil con un modo de
fallo mucho peor.

### 6. El broker es la autoridad

Donde el OMS y el broker discrepan, el OMS está equivocado por definición: no es
él quien tiene la posición (ADR-EXE-8). `RECONCILED` aterriza el agregado en el
estado que diga el broker.

**Excepción: el over-fill.** Si el broker reporta más cantidad de la pedida, eso
no es un estado a adoptar — es una discrepancia que tiene que ver un humano. Va
a la DLQ y el OMS para. Promediarlo en código sería esconder el único síntoma.

### 7. Circuit breakers por dependencia

Uno por dependencia, no uno global (ADR-EXE-5). Una caída de market data no
puede impedir **cerrar** una posición, y un breaker compartido no distingue: el
componente menos fiable acabaría decidiendo qué puede hacer todo lo demás.

Un fallo durante el sondeo `HALF_OPEN` reabre de inmediato — la dependencia
acaba de decir que sigue rota, y mandar los sondeos restantes a un agujero
conocido no ayuda. Y la recuperación exige **más de una** respuesta buena: una
sola respuesta con suerte no es recuperación.

### 8. Outbox contra el dual-write

El evento se persiste y se encola en el outbox en el mismo paso (ADR-EXE-2).
Guardar estado y publicar como dos operaciones separadas significa que un crash
entre ambas deja al sistema en un estado del que nadie se enteró. Un publish
fallido **se conserva**: el evento ya es un hecho duradero, y la entrega puede
ser lenta pero no silenciosa.

### 9. La DLQ exige un motivo

`DeadLetterQueue.add()` rechaza un motivo vacío. Una cola de muertos cuyas
entradas no se pueden explicar es descartar eventos con pasos extra, y un OMS
que descarta eventos pierde posiciones.

## Alternativas descartadas

- **Reintentar con backoff sin consultar.** Es la respuesta intuitiva y la que
  produce el bug. Con dedup por COID en el broker *casi* funciona — y «casi» no
  es aceptable para el modo de fallo que tiene.
- **No reintentar nunca ante un timeout.** Elimina el duplicado y crea el
  problema contrario: setups perdidos por un hipo de red, sin forma de saber si
  se perdieron.
- **Id de orden con timestamp o aleatorio.** Rompe la deduplicación del broker:
  cada reintento parece una orden nueva.
- **Un único circuit breaker global.** El componente menos fiable decide por
  todos, incluida la capacidad de cerrar riesgo.
- **Cerrar posiciones al perder conectividad.** Operar a ciegas en el peor
  momento. `halt` protege; no liquida.
- **Cachear la proyección del agregado.** Abre la puerta a que estado y log
  diverjan, que es precisamente lo que el event sourcing existe para impedir.
- **Exactly-once en el transporte.** Mucho más difícil y con peor degradación
  que at-least-once + dedup idempotente.

## Consecuencias

**Positivas.** Un envío con timeout coloca la orden exactamente una vez en los
cuatro escenarios que hay (llegó / no llegó / broker caído / doble envío), y hay
un test por cada uno que cuenta las colocaciones reales en el venue. El mismo
núcleo corre en live, paper y backtest, así que el código que decide reintentar,
adoptar o fallar **es el mismo código** que el backtest ejercita.

**Negativas / coste asumido.** Cada timeout cuesta un round-trip extra de
`query` antes de poder actuar, lo que añade latencia justo cuando el enlace ya
va mal. Es un intercambio aceptado: latencia frente a una posición duplicada no
es una decisión difícil.

Además, el OMS **para** ante la duda, y eso significa que una caída de
conectividad puede dejar setups sin operar. Es deliberado.

**Deuda registrada.** El OMS todavía no gestiona el ciclo de vida de la posición
tras el fill (BE, trailing, parciales — ENG-006 §6); ni Saga durable
(ADR-EXE-3, requiere Temporal); ni el `execution-gateway` en Rust separado
(ADR-EXE-7). El `PaperBroker` es un doble de test, no un simulador de
microestructura.
