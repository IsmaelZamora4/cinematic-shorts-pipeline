# Phase 5C.1 — diseño de composición audiovisual productiva

**Estado:** arquitectura Phase 5C.1 aprobada e integrada; implementación dividida por fases incrementales.
**Base de diseño:** `pipeline_version: 1`, `manifest_version: 1`, Scene Spec v1 y algoritmo de fingerprints vigentes.

## 1. Objetivo y límites

Diseñar un tercer perfil productivo que ejecute Stage 01, Kokoro Stage 03, Blender Stage 02 y Remotion Stage 04 bajo un `run_id`, un `validated-plan` y un manifiesto v1. Los runners `run-productive-tts` y `run-productive-scenes` conservan elegibilidad, argumentos, outputs y comportamiento.

El MVP valida una sola escena de 540×960, 12 FPS y 24 frames `[0,24)`: duración visual exacta de dos segundos. No acredita producción general, clips de 30–60 segundos, aprobación artística ni las etapas FFmpeg/QA.

## 2. Estado observado y restricciones existentes

- `run-productive-tts` completa Stage 01 y `03-tts-narration` (`provider: kokoro`), y publica WAV más `narration-provenance.json`. Su salida productiva es mono PCM16 little-endian a 24 kHz; el máximo documentado es 16 MiB y 300 s. El run real de Phase 4B duró 5,056 s, con 121 344 muestras.
- `run-productive-scenes` completa Stage 01/02 y publica `scene-package` ZIP más manifiesto v1. El gate real contiene 24 PNG RGB8 de 540×960 a 12 FPS, intervalo `[0,24)`, un `.blend`, índice y provenance. Su cabecera/hash no prueban openability ni calidad artística.
- El `pipeline.json` del gate declara etapa narrativa heredada `03-kokoro-narration` pendiente y Stage 04 `composite.mp4` pendiente; el perfil TTS productivo exige `03-tts-narration` neutral y rechaza Stage 02 productiva.
- `validate_config()` tiene restricciones cruzadas que mantienen separados ambos perfiles. `_is_productive_scenes_config()` exige las otras etapas pendientes. `_is_productive_tts_config()` reconoce el perfil Kokoro. `validate_manifest()` exige que cada perfil independiente deje pendientes las etapas audiovisuales incompatibles.
- `validate_manifest()` valida hashes de WAV/provenance como artefactos, pero no comprueba su semántica Kokoro. El perfil combinado deberá validar ambos contextualmente. La validación actual de manifiesto no ejecuta procesos ni crea temporales.

Por tanto, los resultados históricos Blender y Kokoro no se pueden unir en un manifiesto nuevo: pertenecen a configuraciones, inputs/huellas y `run_id` distintos. La ejecución combinada generará ambos outputs de nuevo. El WAV real documentado de 5,056 s excede la ventana visual actual de dos segundos y deberá bloquear Stage 04.

Fuentes de contrato: [orquestación](../references/pipeline-orchestration.md), [voz y captions](../references/voice-and-captions.md), [integración Remotion](../references/remotion-integration.md), `scripts/pipeline.py`, y evidencia de ejecución de Phase 4B y del cuarto gate Phase 5B.6.

## 3. Perfiles y clasificación

Clasificar los tres perfiles por firma completa, no por un único `adapter_status`:

| Perfil | Etapas productivas permitidas | Estados obligatorios restantes |
| --- | --- | --- |
| Kokoro independiente | 01, 03 | 02, 04, 05, 06 `pending` |
| Blender independiente | 01, 02 | Narración declarada, 04, 05, 06 `pending` |
| Audiovisual combinado | 01, 03, 02; 04 cuando esté implementado | 05, 06 `pending` en Phase 5C |

Añadir clasificación inequívoca, por ejemplo `_productive_profile_kind()`, con valores `kokoro`, `scenes`, `audiovisual` o `None`. Rechazar perfiles parciales, ambiguos y estados mezclados. Hacer que cada runner exija su clase exacta. No relajar la validación de los dos perfiles existentes para aceptar el combinado.

La nueva configuración conserva un único ID narrativo, `03-tts-narration`, por ser el contrato productivo neutral usado por Phase 4B. Stage 03 declara exactamente Kokoro y sus dos outputs existentes. Stage 02 conserva Scene Spec v1, worker fijo y ZIP existente. Stage 04 declara `depends_on: [02-blender-scenes, 03-tts-narration]`, inputs exactos de la sección 5, `tools: [remotion, ffprobe]` y sus dos outputs de la sección 8. Queda `pending` hasta su PR de implementación; Stage 05/06 siguen `pending` en todo Phase 5C.

El Stage 01 `validated-plan` y `config_fingerprint` describen la configuración completa del DAG combinado. Stage 02/03 usan sus inputs y `tool_versions` actuales. Stage 04 incluye hashes de todos los recursos consumidos y versiones observadas de Remotion/ffprobe en el algoritmo actual `stage_fingerprint`. No cambiar algoritmo ni fingerprints históricos.

Comando previsto: `run-productive-composition CONFIG --python ABSOLUTE_PATH --model ABSOLUTE_PATH --voices ABSOLUTE_PATH --blender ABSOLUTE_PATH --node ABSOLUTE_PATH --ffprobe ABSOLUTE_PATH`. Rutas runtime explícitas; cada proceso usa worker fijo. Los comandos independientes no cambian.

## 4. Ejecución y checkpoints

**Ajuste de implementación para Phase 5C.3:** el validador de manifiestos v1 del perfil combinado exige Stage 04/05/06 `pending`. Por ello, este incremento registra los fallos en la etapa que los experimenta y conserva Stage 04 `pending`, aunque algunos casos de fallo abajo indiquen Stage 04 `blocked`. No se amplía ni relaja el contrato v1.

Aunque Stage 02 y 03 solo dependan de Stage 01 en el DAG actual, ejecutarlos en este orden serial:

```text
01 validate → 03 Kokoro → validar duración/audio → 02 Blender → 04 Remotion
```

La voz suele fallar o revelar incompatibilidad temporal con menos coste que el render Blender. Validar el WAV medido antes de iniciar Blender evita renderizar una escena cuando la composición ya está bloqueada. El orden es una política del runner combinado; el DAG conserva dependencias y huellas declaradas, y los runners independientes no cambian de orden.

| Punto de fallo | Estados y outputs permitidos |
| --- | --- |
| Preflight/runtime/rutas/colisión | Sin `run_id` publicado ni outputs productivos; salida controlada. |
| Stage 01 | 01 `failed`; etapas posteriores `blocked` o `pending` sin artefactos. |
| Stage 03 | 01 `completed`, 03 `failed`, 02 `pending`, 04 `blocked`; ningún WAV parcial se marca reutilizable. |
| Duración o audio inválido | 01/03 conservan su checkpoint; 02 `pending`, 04 `blocked`; Blender no se ejecuta. Nueva invocación requiere run y destinos nuevos. |
| Stage 02 | 01/03 `completed`, 02 `failed`, 04 `blocked`; ZIP parcial/huérfano no se adopta. |
| Stage 04 | 01/03/02 `completed`, 04 `failed` o `blocked` según si comenzó el worker; no hay artefactos 04 reutilizables. |
| Crash tras publicar y antes del checkpoint | El manifiesto conserva último checkpoint durable; salida no referenciada queda huérfana y requiere inspección manual. |

Un checkpoint cambia a `completed` solo después de verificar runtime, inputs sin cambios, outputs releídos, provenance y hashes, publicación exclusiva y validación final del manifiesto. Un fallo no activa retries, resume ni adopción automática. La publicación de archivos y el checkpoint del manifiesto no son una transacción única. Conservar evidencia parcial para inspección; eliminar solo archivos cuya identidad el proceso pueda demostrar que creó.

## 5. Entradas y validación contextual

Stage 04 declara como inputs, exactamente:

- `validated-plan` y `storyboard` para validar configuración, escenas y captions actuales.
- `scene-package` para índice, provenance y frames verificados.
- `narration` y `narration-provenance` del Stage 03 de este mismo manifiesto y `run_id`.

Antes de componer:

1. Validar config, storyboard, semántica de `validated-plan`, dependencias, estado y huellas actuales.
2. Abrir ZIP sin extraer durante la fase read-only; validar estructura/contexto, 27 miembros, CRC, SHA-256, límites y PNG RGB8 completos con el validador contextual existente. Comparar `project_id`, `run_id`, escena, huellas Stage 01/02 y hash exterior con el manifiesto.
3. Leer WAV con límite actual; comprobar RIFF/WAVE, PCM16 mono 24 kHz, alineación de muestras, tamaño, duración, señal no silenciosa y hash. Analizar estrictamente provenance v1: `record_type`, `run_id`, proyecto/etapa, hash de guion, WAV y `validated-plan`, runtime, worker, modelo, voces y parámetros efectivos. Hashes sin coherencia semántica no bastan.
4. Comparar duración medida con storyboard y timeline; verificar captions/anclas según sección 7.
5. Solo tras todo lo anterior, extraer los PNG requeridos en staging exclusivo comprobando nombres, límites, identidad y ausencia de symlinks/reparse. Remotion no abre ni evalúa `.blend`.

`validate-manifest` contextualiza 01–04 por perfil: valida el recibo 01, vuelve a validar ZIP 02, WAV/provenance 03 y la salida/provenance 04 por hashes y semántica. Permanece read-only, streaming cuando aplique, sin extracción, escrituras, temporales ni procesos externos. `ffprobe` se ejecuta únicamente dentro del runner productivo autorizado; su resultado validado queda resumido y hasheado en provenance. La lectura posterior del manifiesto no vuelve a ejecutar `ffprobe`.

## 6. Reloj y política de duración

Reloj canónico: muestras enteras de 24.000 Hz. El WAV empieza en muestra 0. A 12 FPS, cada frame ocupa 2.000 muestras y 24 frames ocupan `[0,48.000)`, exactamente dos segundos.

La política de `voiceover.duration_ms` es conservadora: convertir muestras PCM a milisegundos usa techo entero, `(muestras × 1000 + 23 999) // 24 000` (equivalente a dividir `muestras × 1000` entre 24 000 con techo). Por ejemplo, 43 200 muestras son 1 800 ms y 43 201 muestras son 1 801 ms. El storyboard debe declarar al menos ese milisegundo redondeado; no se redondea hacia abajo ni se retimea el WAV.

- Si el WAV tiene más de 48.000 muestras, Stage 04 se bloquea **antes de ejecutar Blender**. No recorta voz ni acorta audio.
- Si tiene menos, Remotion completa la composición hasta 48.000 muestras con silencio final declarado. No modifica WAV publicado ni provenance Kokoro.
- No duplica PNG, congela el último frame, cambia FPS, velocidad, pitch o duración de fuente, ni retimea implícitamente.
- Índice del frame `f` empieza en muestra `2.000 × f`. La composición entrega exactamente los frames fuente 0–23 y duración lógica `48.000 / 24.000 = 2 s`.

El ejemplo Kokoro real de 121.344 muestras no es elegible para este MVP. Adaptarlo requiere nueva duración visual en un contrato futuro; no es una conversión que Phase 5C haga automáticamente.

### AAC y duración efectiva del contenedor

Separar duración lógica (video 24 frames = 2 s) de muestras codificadas y duración presentada en MP4. Perfil propuesto Stage 04: MP4, H.264 `yuv420p`, 540×960, 12/1 FPS, 24 frames; AAC-LC mono a 48 kHz. El WAV 24 kHz se remuestrea a 48 kHz de forma declarada, sin alterar tiempo ni pitch. Provenance registra muestras WAV originales, silencio añadido, parámetros de remuestreo, encoder, priming/padding, duración lógica, duración de cada stream y duración del contenedor.

Criterios propuestos para `ffprobe` después del render:

- Video: codec H.264, pixel format `yuv420p`, dimensiones exactas, `avg_frame_rate` y `r_frame_rate` `12/1`, 24 frames contados, inicio presentado en 0 y duración lógica 2 s, tolerancia máxima de un tick de video.
- Audio: AAC-LC, mono, 48.000 Hz, inicio presentado en 0. Tras aplicar priming/edit list, la duración lógica debe representar exactamente 96.000 muestras a 48 kHz. Padding codificado final máximo: dos access units AAC de 1.024 muestras cada una (42,667 ms total); debe quedar informado por `ffprobe` y representado por el contenedor. Rechazar si el padding no puede determinarse.
- Contenedor: MP4 válido; duración reportada entre 2 s y 2 s + 42,667 ms. Tamaño máximo propuesto: 64 MiB. Provenance JSON estricto, máximo 64 KiB.

Estos límites son un contrato candidato; el gate real debe comprobar que la versión fijada de Remotion, AAC encoder y `ffprobe` reporta las muestras/edits así. Si no puede verificarse la duración presentada y padding con esos límites, no marcar Stage 04 `completed`; revisar el perfil antes de ampliar tolerancias.

## 7. Captions, anchors y pausas

Captions siguen intervalos semiabiertos `[start_ms,end_ms)`. Cuantizar hacia afuera al conjunto de frames que intersectan el intervalo, con aritmética entera:

```text
start_frame = floor(start_ms × 12 / 1000)
end_frame_exclusive = ceil(end_ms × 12 / 1000)
```

Un caption más corto que un frame aparece durante un frame, documentado en provenance. Captions consecutivos que ocupen el mismo frame después de cuantizar colisionan; rechazar Stage 04 y pedir edición revisada, sin fusionarlos, descartarlos ni cambiar texto/timing. Rechazar inicios negativos, intervalos vacíos o cualquier caption que exceda 2 s/48.000 muestras.

Con audio real, `narrative_timing.clock_source` debe ser `recording`; anchors `beat`/`pause` deben estar medidos o revisados manualmente. `estimated` no autoriza captions sincronizados con la voz real: se corrigen antes del nuevo run o se omiten captions del render. Las pausas describen silencio narrativo, no silencio total; el WAV no se corta ni modifica. El contrato no da alineación de palabra a palabra: conservar texto/intervalos suministrados, sin inventar timings ni afirmar precisión por palabra.

## 8. Entrada, salida y frontera Stage 04/05

### Stage 04

Mantener el ID `composite` y ruta configurable bajo `.pipeline/work/`, con extensión `.mp4`. Añadir output `composition-provenance` JSON bajo la misma raíz. Ambos son outputs de Stage 04 en el manifiesto v1 y se publican exclusivamente.

Provenance v1 cerrado: `record_type`, run/proyecto/stage IDs; hashes de `validated-plan`, storyboard, ZIP, WAV, provenance de voz, worker y lockfile; parámetros de composición; versiones observadas; frames/captions/anchors cuantizados; muestras de entrada/silencio/remuestreo; datos de `ffprobe`, codecs, inicio/duraciones/padding/tamaño y SHA-256 de MP4. Sin guion, texto de captions, rutas absolutas ni datos del host innecesarios.

El padre vuelve a verificar inputs/config/runtime antes y después del worker, valida respuesta acotada, llama `ffprobe` fijo con salida limitada, valida MP4/provenance en staging, publica ambos con creación exclusiva, los relee y verifica hashes/provenance. Solo entonces guarda el checkpoint 04.

### Stage 05

Mantener output `final-video` en `.pipeline/artifacts/final.mp4` y añadir `finalization-provenance` JSON bajo `.pipeline/work/`. Su responsabilidad futura: remux final sin recodificar, usando stream copy H.264/AAC, metadatos permitidos explícitos y `faststart`; verificar que frame count, timestamps presentados, codec y hashes de streams permanecen equivalentes. Provenance de Stage 05 registra hashes de entrada/salida, `run_id`, versiones/hash de FFmpeg/worker, opciones de remux, codec, dimensiones, FPS, frames, duración, padding/timestamps y tamaño. Límite propuesto: MP4 64 MiB, provenance JSON 64 KiB; outputs en manifiesto, con creación exclusiva y relectura. Si el destino necesita otro codec/resolución, se decide y documenta una recodificación explícita en una fase posterior; no se añade por defecto a Phase 5C.

Así Stage 04 compone y codifica una vez; Stage 05 prepara el MP4 para entrega sin volver a codificar. CI prueba el contrato con fixtures y un ffprobe simulado; el gate local prueba el encoder/probe reales. No afirmar éxito Stage 05 hasta esa verificación futura.

## 9. Worker, runtime, seguridad y recursos

- Crear worker fijo y lockfile propio para Node, Remotion, `@remotion/renderer`, React/React DOM y dependencias usadas, incluido `@remotion/three`/Three.js solo si se necesita. Fijar versiones exactas compatibles y su árbol; verificar esas versiones contra documentación oficial antes de implementar. Remotion 4.0.540 en la referencia local no equivale a instalación comprobada. No instalar ni descargar durante una ejecución productiva.
- El operador indica Node y `ffprobe` absolutos; preflight verifica archivos locales, versiones y hashes del worker/lockfile. Runtime y assets existentes de Kokoro/Blender se comprueban con sus preflights actuales. `availability: verified` no sustituye preflight.
- Proceso padre llama worker fijo con argv construido por código, `shell=False`, timeout y límites de stdout/stderr. Nada de `npx`, comandos del JSON, scripts/React del proyecto, imports dinámicos controlados por inputs, `eval` ni código de usuario. El worker solo acepta una solicitud JSON acotada y paths de staging validados.
- Captions validados y request pasan por archivo estructurado de staging, no argv ni logs. Confinamiento, inspección de symlinks/junctions/reparse, identidad de archivos, staging exclusivo, ausencia de overwrite y cambios concurrentes de inputs siguen las medidas existentes.
- Límite fijo propuesto: 10 min de pared para Stage 04, concurrencia 1 y MP4 64 MiB. Los presupuestos `limits` actuales de RAM/VRAM no son límites de SO. No afirmar cuota de memoria o red; si el equipo no permite límites OS, registrarlo como presupuesto, no como garantía.
- Staging conserva request, respuesta acotada, `ffprobe` JSON, logs redactados y paths de parciales como evidencia. No conservar script, WAV completo o captions en logs.

## 10. Pruebas, gates y fases incrementales

Todo incremento de código requiere pruebas positivas/negativas en Windows, Ubuntu 24.04 y 26.04; CI solo mocks, sin motores o medios productivos. Los fixtures deben incluir package ZIP/PNG, WAV PCM y provenance cerrados, MP4/ffprobe JSON controlados; comprobar read-only, huellas, publish/no-overwrite, fallos, límites, concurrencia y regresión de ambos runners independientes.

| Fase / PR | Alcance y aceptación |
| --- | --- |
| 5C.2 — perfil y validación | Clasificación estricta de tres perfiles; combined acepta 01/02/03 y 04 pending o implemented según PR; 05/06 pending. `validate-manifest` contextual para 01–04. Legacy configs y manifiestos v1 siguen pasando. Sin runners nuevos. |
| 5C.3 — runner combinado 01/03/02 | Un run/manifest/plan; Kokoro primero, duración se valida antes de Blender, Scene Package contextual; checkpoints por etapa. Mock prueba la matriz de fallos y ausencia de adopción/retry. Stage 04 sigue pending. |
| 5C.4 — contrato y preflight Remotion | WAV/provenance, timeline, captions, worker/runtime bloqueados, staging, límites y respuesta acotada. Fixtures positivos y negativos; ningún render real en CI. |
| 5C.5 — composición y publicación 04 | Worker Remotion fijo, MP4 + provenance, ffprobe dentro del runner, publicación exclusiva, relectura/hash/checkpoint y `validate-manifest` read-only. CI multiplataforma con worker/probe simulados. |
| Gates reales 5C | Autorización explícita para ejecutar Kokoro, Blender y Remotion en un proyecto aislado de dos segundos; una invocación sin retries. Registrar versions, hashes, duración/padding y outputs. Gate técnico separado de apertura `.blend` y aprobación humana de imagen, voz, captions y secuencia. No ejecutar FFmpeg/QA hasta sus fases. |

Cada PR conserva solo su alcance y no se mezcla con el siguiente. Si un gate falla, actualizar diseño/implementación por PR; no rerun automático ni adopción de outputs fallidos. Un gate de integración de dos segundos solo prueba el contrato, no un corto completo.

## 11. Riesgos y cuestiones abiertas

- La regla de dos segundos limita la voz a 48.000 muestras; la ejecución Kokoro documentada de 5,056 s queda bloqueada. Extender timeline requiere una decisión posterior sobre Scene Spec/storyboard y nueva política explícita.
- Remotion/Node/React no tienen lockfile de esta skill; versiones candidatas deben probarse antes de fijar el worker.
- El perfil de codec y tolerancia AAC/MP4 deben demostrarse con el encoder real y `ffprobe`. Si no pueden comprobarse priming y padding como se especifica, el gate debe fallar y el diseño revisarse.
- El remux Stage 05 y equivalencia exacta de streams deben probarse en su propia fase; hasta entonces Stage 05 permanece pendiente.
- CI y hashes prueban consistencia/integridad de artefactos, no calidad artística, inteligibilidad, sincronía perceptual, portabilidad universal ni licencia de assets.

## Phase 5C.4 — contrato y preflight (implementado en rama, pendiente de auditoría)

- `pipeline.py` define el reloj fijo 540×960/12 FPS/24 frames, valida WAV mono PCM16/24 kHz hasta 48 000 muestras y declara el silencio final como metadato entero; no cambia los bytes de narración.
- El contrato compara `voiceover.duration_ms` con las muestras medidas, requiere `clock_source: recording` cuando hay captions/anchors, limita anchors a evidencia medida o revisada y cuantiza captions hacia afuera con división entera. Caption texto/tiempos originales se conservan; colisión tras cuantizar y timeline excedido fallan.
- El validador contextual cruza run/proyecto, hashes del plan/storyboard/scene package/WAV y fingerprints Stage 01/02 usando provenance existente. El validador ZIP contextual existente conserva la autoridad sobre miembros y PNG. Stage 04/05/06 siguen `pending`; no se añaden outputs ni se publica MP4.
- Request/response v1 usan JSON estricto, campos cerrados, paths de input fijos, máximos 128 KiB/64 KiB, 24 frames y MP4 hasta 64 MiB. Staging usa creación exclusiva dentro de `.pipeline/work/remotion-staging/`; sin publicación Stage 04.
- Preflight requiere Node y `ffprobe` por rutas absolutas, detecta symlink/reparse en todos los componentes, hashea binarios/worker/lockfile, comprueba package-lock npm v3 y versiones por banderas fijas `--version`/`-version`, con 15 s y 16 KiB de salida. No usa `PATH`, `npx`, shell ni ejecuta composición. El futuro worker tendrá timeout de 600 s.
- Worker `workers/remotion/package.json`/`package-lock.json`: `remotion`, `@remotion/bundler` y `@remotion/renderer` 4.0.540; React y React DOM 19.3.1. npm lock v3 resuelto en modo offline con caché existente; no se instalaron paquetes ni se creó `node_modules`. Todos los paquetes `@remotion/*` mantienen una sola versión conforme a [Version mismatch](https://cloudrun.remotion.dev/docs/version-mismatch). React 19 requiere Remotion 4 según [React 19](https://www.remotion.dev/docs/react-19).
- Runtime Node admitido por política: LTS mayores 22, 24 o 26; Remotion documenta mínimo Node 16, mientras [Node.js Releases](https://nodejs.org/en/about/previous-releases) marca 22/24/26 LTS y recomienda LTS en producción. Worker declara `>=22 <27`; preflight rechaza majors no-LTS del rango.
- `ffprobe` debe ser >=4.1.0; se registra versión y SHA-256 observados. [Documentación de Remotion](https://www.remotion.dev/docs) documenta mínimo 4.1 para FFmpeg/ffprobe. La lectura y política AAC/MP4 aún requieren gate real de 5C.5.
- Versiones localmente observadas: Node 24.16.0 y ffprobe 9.0.2. Ningún Remotion, ffprobe sobre medios, FFmpeg, Kokoro o Blender se ejecutó. CI valida contratos con fixtures y mocks; no acredita compatibilidad de instalación/render en los tres sistemas.
- El lockfile es reproducible y cerrado, pero su instalación y ejecución multiplataforma quedan para el gate de dependencias/runtime de Phase 5C.5. El staging Worker/Remotion fijo todavía no renderiza; esta fase no habilita Stage 04.

Fuentes oficiales consultadas para Phase 5C.4: [Remotion system requirements](https://www.remotion.dev/docs), [Remotion version mismatch](https://www.remotion.dev/docs/miscellaneous/version-mismatch), [Remotion React 19](https://www.remotion.dev/docs/react-19), [Node.js release support](https://nodejs.org/en/about/previous-releases), [ffprobe](https://ffmpeg.org/ffprobe.html).

## Phase 5C.5 — composición y publicación Stage 04

**Estado de implementación:** completo en rama Phase 5C.5; pendiente de CI y auditoría independiente. No mergear ni ejecutar el gate real hasta autorización posterior.

- El runner combinado habilita Stage 04 solo con el perfil exacto y exige `--node`/`--ffprobe` absolutos. Orden efectivo: Stage 01 → Stage 03 Kokoro → validación de audio/duración → Stage 02 Blender → Stage 04 Remotion. Los runners TTS y Blender independientes no cambian.
- `workers/remotion/` tiene worker MJS fijo, raíz de composición y lock npm v3: Remotion/bundler/renderer 4.0.540, React/React DOM 19.3.1. Acepta únicamente los cinco nombres fijos en staging. Renderiza 24 PNG y narration WAV padded en staging, sin evaluar código de usuario, importar el `.blend`, usar `eval` ni decidir publicación.
- Stage 04 ejecuta ffprobe en el padre, valida MP4 H.264/yuv420p 540×960 a 12 FPS/24 frames y AAC-LC mono a 48 kHz, incluidos timestamps, muestras presentadas, priming/padding y límite de 64 MiB. Provenance enlaza run/proyecto, hashes de inputs, fingerprints, runtimes, parámetros y resumen/sha256 de ffprobe.
- MP4/provenance se validan en staging, publican con creación exclusiva, se releen y validan antes del checkpoint. El par de archivos y el manifiesto no es una transacción común; outputs huérfanos no se adoptan. Fallos mantienen Stage 04 sin `completed`. Staging y evidencia parcial permanecen para inspección; limpieza limita a outputs publicados cuya identidad se posee.
- `validate-manifest` valida por hashes y datos de ffprobe guardados, sin procesos, extracción ZIP, temporales ni escrituras. Stage 05/06 siguen `pending`.
- P3-A cerrado: techo entero conservador de PCM-a-ms documentado; pruebas para 43 200 → 1800 ms, 43 201 → 1801 ms, 48 000 → 2000 ms y storyboard más corto.
- P3-B cerrado antes de conectar el helper: rechazo de rutas absolutas Windows/POSIX/UNC, traversal, componentes vacíos/punto, separadores mixtos, nombres reservados con extensión y escapes symlink/reparse; las rutas de request siguen fijas.
- Remotion no está instalado en esta máquina de auditoría; no se instalaron paquetes ni se ejecutó Node worker, Kokoro, Blender, Remotion, ffprobe sobre medios o FFmpeg. Mocks verifican el flujo padre, no compatibilidad de instalación ni salida codificada. Falta el gate local autorizado para confirmar AAC/MP4, muestras presentadas, priming/padding y ejecución real multiplataforma.

Remotion documenta `sampleRate` como opción de `renderMedia()` desde v4.0.448; el worker usa `sampleRate: 48000` para la salida AAC [Audio Sample Rate](https://www.remotion.dev/docs/sample-rate). `@remotion/media` ofrece otra API de audio recomendada en releases actuales, pero el worker fijo sigue usando `Audio` de Remotion 4.0.540 y ese render no se ha validado con dependencias instaladas [Audio component](https://www.remotion.dev/docs/media/audio).
