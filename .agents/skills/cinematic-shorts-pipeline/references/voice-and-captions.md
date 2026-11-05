# Voz, sonido y subtítulos

Mantén intercambiable el proveedor de voz: describe entradas y salidas en vez de acoplar el plan a una marca. Registra idioma, voz, proveedor/runtime, ejecución local o remota, costes/consentimiento, texto, pronunciación, formato/tasa de muestreo y duración medida. Kokoro local es la opción actual del proyecto auditado, no una dependencia universal.

No llames servicios de voz de pago, envíes guiones/audio privado ni descargues voces/modelos sin autorización explícita. Verifica la licencia del modelo antes de adoptarlo. Prefiere un proveedor local cuando cumpla los requisitos de calidad y ejecución.

## Recurso TTS declarativo (pipeline v0.8, fase 1)

La etapa neutral `03-tts-narration` requiere un recurso `voiceover-script` explícito, independiente de captions. El archivo debe ser UTF-8 válido, no vacío y de hasta 1 MiB. El validador no extrae ni reconstruye el guion desde captions. El bloque `tts` declara un proveedor conocido por el contrato, una voz, un idioma y WAV acotado; el nombre del proveedor no demuestra que esté instalado, verificado, licenciado o disponible para ejecutarse.

La etapa heredada `03-kokoro-narration` y las configuraciones existentes siguen siendo válidas en modo planificación sin el nuevo recurso. La fase 1 no ejecuta TTS ni crea WAV o procedencia. Consulta [el contrato de pipeline](pipeline-orchestration.md#contrato-narrativo-tts-v08-fase-1) para el esquema y los límites exactos.

## Tiempo y mezcla

Estima la duración de voz para planificar y usa el audio real para fijar los tiempos finales. Conserva pausas intencionales. Atenúa música bajo la voz cuando haga falta; revisa clipping, inteligibilidad, canales y fundidos. No afirmes que el tiempo por palabra es exacto salvo que un alineador lo mida o se compruebe manualmente.

### Audio como reloj narrativo

Cuando hay grabación real, su duración y pausas gobiernan el montaje: ajusta o redistribuye acciones y cortes alrededor de esa pista. No comprimas, estires ni cambies el ritmo de voz para hacerla caber sin declararlo. Antes de grabar, la duración es una estimación y todos sus anclajes deben llevar `basis: "estimated"`.

El bloque opcional `narrative_timing` de `schema_version: 1` anota `clock_source` (`estimated` o `recording`) y una lista ordenada de `anchors`. Cada ancla tiene un `id`, un `scene_id`, un `kind` (`beat`, `pause` o `cut`), `start_ms`, `end_ms` y `basis` (`estimated`, `measured` o `manually_reviewed`). Los beats y pausas son intervalos semiabiertos positivos; un corte es un punto con inicio y fin iguales. El beat/pausa debe solapar en tiempo la escena referenciada, con tolerancia de un fotograma; no se exige que termine dentro de esa escena, porque la voz puede continuar sobre un cambio de plano. Las pausas declaran silencio narrativo, no silencio total de ambiente.

Los anclajes beat/pausa no pueden solaparse entre sí. Un marcador puntual de corte sí puede coincidir con o caer dentro de un beat/pausa activo: el sonido puede cruzar el cambio de imagen sin dividirse. El corte referencia la escena saliente y debe coincidir, con tolerancia de un fotograma, con el límite compartido con la siguiente escena. No se marca como corte una salida final sin plano posterior.

La línea `voiceover.duration_ms` alimenta los límites del reloj. `clock_source: "recording"` requiere anclajes medidos o revisados manualmente; `estimated` requiere anclajes estimados. El validador comprueba forma, orden, referencias y límites, pero no escucha ni comprueba la grabación. Es una declaración de procedencia, no evidencia. No se representa alineación de palabra a palabra en este contrato; no la afirmes sin alineador o revisión manual y evidencia asociada.

Conserva captions en milisegundos semiabiertos `[start_ms, end_ms)`. Sus intervalos pueden seguir al audio, pero no son prueba automática de sincronización. Tras sustituir estimaciones por una grabación, actualiza/revisa captions con la grabación real y conserva las excepciones de solape solo cuando sean intencionales.

## Plan de subtítulos

Guarda subtítulos como registros estructurados con texto e inicio/fin en milisegundos, más confianza/procedencia opcional según el framework. Exige intervalos positivos y límites dentro de la duración medida de audio/video. Usa unidades breves, alto contraste y posición estable; como base, no más de dos líneas. Revisa tildes, puntuación, saltos, zona segura, atribución y obstrucciones en cada plano.

Los intervalos de captions son semiabiertos `[start_ms, end_ms)`: captions consecutivos pueden tocarse, pero los solapes se rechazan por defecto. Para una superposición deliberada (por ejemplo, dos hablantes durante un breve cruce), añade `overlap_exception` con una razón no vacía al caption que empieza después. No hay excepciones usadas en los ejemplos actuales; evita solapar texto si tapa información visual.

La skill de captions de Remotion instalada define el tipo `Caption` y los flujos actuales de visualización/importación. Reutilízala para implementar; no dupliques aquí sus recetas completas.
