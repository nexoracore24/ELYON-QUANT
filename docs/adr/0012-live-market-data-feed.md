# ADR-0012: Feed de datos en vivo — sondeo, silencio y concurrencia

- **Estado:** Accepted
- **Fecha:** 2026-09-04
- **Decisores:** CTO/Principal Architect, Execution Lead
- **Relacionado:** [ADR-0006](0006-deterministic-computing.md) ·
  [ADR-0010](0010-backtest-fidelity.md) ·
  [ADR-0011](0011-oms-duplicate-prevention.md)
- **Implementa:** `modules/session/domain/live.py`,
  `modules/execution/infrastructure/mt5_feed.py`

## Contexto

Hasta aquí una sesión recorría un fichero de velas de principio a fin, en un
hilo, con un final conocido. En vivo cambian dos cosas, y la segunda es fácil de
pasar por alto:

1. **Los ticks llegan en vez de iterarse.** Un feed puede pararse, repetirse o
   morir.
2. **Dos hilos tocan la sesión.** El feed la muta; la superficie de control la
   lee para contestar al móvil.

Además, MetaTrader 5 **no empuja datos**. No hay callback ni suscripción: se
pregunta `symbol_info_tick` y contesta, haya cambiado algo o no. Todo lo demás
se deriva de ese hecho del venue.

## Decisión

### 1. Sondeo, y se asume la pérdida de ticks

A 250 ms se pierden ticks en un mercado rápido. Se acepta **porque se decide
sobre velas confirmadas**: un tick perdido puede mover ligeramente un máximo o
un mínimo, pero no puede cambiar qué vela cerró ni cuándo. La misma frontera que
ADR-0010 usa para el backtest — la decisión vive en la vela confirmada — es la
que hace tolerable el muestreo aquí.

Si algún día se decide intrabar, esta decisión hay que reabrirla.

### 2. Deduplicación por contenido, no por confianza en el venue

El terminal devuelve el mismo tick una y otra vez cuando nada ha cambiado.
Plegarlo dos veces inflaría el volumen y el conteo de ticks de la vela — es
decir, contaminaría una entrada del motor de decisión. Se deduplica en dos
sitios a propósito: en el adaptador (`time_msc`, bid, ask crudos) y en el runner
(`event_time_ns`, bid, ask ya normalizados), porque el runner acepta cualquier
`TickFeed`, no solo el de MT5.

### 3. El silencio se interroga, no se interpreta

Un fin de semana y un socket muerto devuelven exactamente lo mismo: nada. El
feed **no lo infiere del silencio**; le pregunta al terminal y distingue cuatro
casos: terminal inalcanzable, símbolo desconocido (con el aviso del sufijo de
Exness), símbolo fuera de Market Watch, y mercado probablemente cerrado.

`ensure_symbol()` falla en el arranque con el motivo. Un símbolo ausente de
Market Watch devuelve `None` en todas las llamadas de precio, que es
indistinguible de una conexión muerta hasta que alguien lo comprueba — y ese
alguien lo comprueba a las 3am.

### 4. Los estados del feed tienen nombre

`STARTING · LIVE · STALLED · DISCONNECTED · STOPPED`. Un feed que se cae no es
una excepción: es un martes. Modelarlo como error obliga a cada llamante a
inventarse una política; modelarlo como estado hace que la política sea una y
esté escrita.

`STALLED` y `DISCONNECTED` son estados distintos porque las causas lo son:
silencio prolongado frente a un fallo positivo de la conexión.

### 5. Al desconectar: parar el motor, no cerrar posiciones

`halt_on_disconnect=True` por defecto. Un motor que no puede ver precios no
debería abrir riesgo nuevo. Pero **conserva lo que tiene**: cerrar a ciegas
durante un corte es operar en el peor momento posible, y es la misma postura que
ADR-0011 ya fija para el OMS — `halt` protege, no liquida.

Y sigue reportando. Un runner callado es indistinguible de uno muerto.

### 6. Un solo lock, y toda lectura pasa por él

`LiveRunner.read(view)` es la única forma de mirar la sesión desde fuera del
hilo del feed. Sin eso, un snapshot tomado a mitad de un `append` describe un
estado que nunca existió: una posición a medio escribir, una lista de velas a
medio ampliar. El panel en vivo (`live_panel_for`) lee por ahí; leer la sesión
directamente sería la ruta obvia y la incorrecta.

Los callbacks (`on_outcome`) corren **fuera** del lock: un notificador lento que
lo retuviera pararía el feed, y un feed que deja de leer es un feed que se
pierde la vela que estaba esperando.

### 7. La configuración rechaza lo incoherente

`stall_after_seconds` debe superar `poll_interval_seconds`, o el feed se
declararía parado entre dos sondeos. Se valida en `__post_init__`, no en la
documentación.

### 8. Un solo camino de código

En vivo se entra por `session.on_tick`, exactamente igual que en backtest. Las
velas del fichero son calentamiento y el feed continúa la misma serie. Un camino
«live» aparte sería un camino que el backtest nunca ejercita, y por tanto el
único que no está probado justo donde hay dinero.

## Alternativas descartadas

- **Esperar una API push de MT5.** No existe. Diseñar como si existiera produce
  un adaptador que finge un contrato que el venue no ofrece.
- **Sondear más rápido para no perder ticks.** No elimina la pérdida, solo la
  reduce, a cambio de carga y de la ilusión de completitud. La garantía real la
  da la vela confirmada, no la frecuencia.
- **Tratar el silencio como desconexión.** Convertiría cada fin de semana en una
  alarma, y las alarmas que suenan siempre no se leen.
- **Cerrar posiciones al perder el feed.** Es liquidar a ciegas en el peor
  momento; ya descartado en ADR-0011 por la misma razón.
- **Copiar la sesión para leerla sin lock.** Una copia tomada sin lock es
  exactamente el snapshot incoherente que se quiere evitar.
- **`asyncio` en vez de un hilo.** Obligaría a async a todo el motor, que es
  síncrono y determinista a propósito. Un hilo y un lock es menos código y menos
  contagio.

## Consecuencias

**Positivas.** El motor puede correr indefinidamente contra un terminal real, el
móvil ve la salud del feed antes que ningún número, y una desconexión deja al
sistema en un estado seguro y declarado. `ReplayFeed` ejercita el mismo runner,
el mismo lock y el mismo manejo de desconexión sin broker delante, con
`fail_after` para inyectar el corte.

**Negativas / coste asumido.** Se pierden ticks entre sondeos, lo que hace que
máximos y mínimos intrabar sean aproximados; es intrascendente para decisiones
sobre velas confirmadas y sería inaceptable para cualquier lógica intrabar
futura. Y el lock serializa lecturas con el hilo del feed: si algún día el
snapshot se vuelve caro, tocará una proyección con doble buffer.

**Deuda registrada.** Un solo símbolo por runner. Varios instrumentos —lo que
SMT Divergence necesita— requieren un feed multi-símbolo y una decisión sobre
cómo se alinean sus relojes. No hay persistencia del stream: un reinicio
recalienta desde el fichero de velas, no desde donde se quedó.
