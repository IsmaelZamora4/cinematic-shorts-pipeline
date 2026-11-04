# Voz, sonido y subtítulos

Mantén intercambiable el proveedor de voz: describe entradas y salidas en vez de acoplar el plan a una marca. Registra idioma, voz, proveedor/runtime, ejecución local o remota, costes/consentimiento, texto, pronunciación, formato/tasa de muestreo y duración medida. Kokoro local es la opción actual del proyecto auditado, no una dependencia universal.

No llames servicios de voz de pago, envíes guiones/audio privado ni descargues voces/modelos sin autorización explícita. Verifica la licencia del modelo antes de adoptarlo. Prefiere un proveedor local cuando cumpla los requisitos de calidad y ejecución.

## Tiempo y mezcla

Estima la duración de voz para planificar y usa el audio real para fijar los tiempos finales. Conserva pausas intencionales. Atenúa música bajo la voz cuando haga falta; revisa clipping, inteligibilidad, canales y fundidos. No afirmes que el tiempo por palabra es exacto salvo que un alineador lo mida o se compruebe manualmente.

## Plan de subtítulos

Guarda subtítulos como registros estructurados con texto e inicio/fin en milisegundos, más confianza/procedencia opcional según el framework. Exige intervalos positivos y límites dentro de la duración medida de audio/video. Usa unidades breves, alto contraste y posición estable; como base, no más de dos líneas. Revisa tildes, puntuación, saltos, zona segura, atribución y obstrucciones en cada plano.

Los intervalos de captions son semiabiertos `[start_ms, end_ms)`: captions consecutivos pueden tocarse, pero los solapes se rechazan por defecto. Para una superposición deliberada (por ejemplo, dos hablantes durante un breve cruce), añade `overlap_exception` con una razón no vacía al caption que empieza después. No hay excepciones usadas en los ejemplos actuales; evita solapar texto si tapa información visual.

La skill de captions de Remotion instalada define el tipo `Caption` y los flujos actuales de visualización/importación. Reutilízala para implementar; no dupliques aquí sus recetas completas.
