# Hoja de ruta

## v0.2.1 — preparada

- Validación semántica de storyboards de ejemplos y plantilla mediante el validador compartido.
- Solapes de captions rechazados por defecto; excepción intencional requiere motivo.
- Enlaces Markdown inline/de referencia, destinos con espacios y exclusión de código.
- Convención de paquetes nuevos bajo `deliverables/<slug>/`.
- Tres paquetes ficticios, rúbrica cinematográfica y protocolo reproducible de benchmark.
- Skill conservada en `.agents/skills/cinematic-shorts-pipeline`; selector confirmado por el usuario.

**Verificación ejecutada 2026-11-04:** suite completa, 26 pruebas aprobadas; `validate-skill.py` aprobó; los tres storyboards de ejemplo y `templates/storyboard.json` pasaron `validate-storyboard.py`.

## v0.3 — cinematografía 3D

- Referencia operativa para óptica, 9:16, jerarquía, eje, mirada, movimiento, luz narrativa y revisión móvil.
- Contrato `cinematography` opcional en `schema_version: 1`, con coordenadas focales relativas y validación de tipos/rangos.
- Rúbrica y puertas separan intención de plan, still y secuencia reproducida.
- Simulación de cueva ampliada con ficha técnica por plano y decisiones correctivas; sin medios generados.

**Verificación de v0.3 (2026-11-04):** 29/29 pruebas aprobadas; `validate-skill.py` aprobó; los tres storyboards de ejemplo y `templates/storyboard.json` pasaron `validate-storyboard.py`.

## v0.4 — planificación de animación 3D

- Contrato `animation` opcional y compatible con `schema_version: 1`; storyboards v0.3 permanecen válidos.
- Validador comprueba estado/requisitos de rig, beats contiguos por plano, poses, peso/apoyos, root motion, mirada, manos/props y continuidad declarada.
- Guía y rúbrica separan planificación declarada de evidencia de rig, still y secuencia reproducida.
- Ejemplo de cueva documenta beats por plano; sin implementación de personaje ni producción audiovisual.

**Verificación de v0.4 (2026-11-04):** 33/33 pruebas aprobadas; `validate-skill.py` y los tres storyboards de ejemplo más `templates/storyboard.json` pasan sus validadores. La aprobación audiovisual sigue pendiente: no se generó ni reprodujo una secuencia.

## v0.5 — arquitectura y orquestación local

- Contrato independiente `pipeline_version: 1`, plantilla y ejemplo de cueva; rutas relativas confinadas a la raíz del proyecto.
- DAG de seis etapas, detección de dependencias/ciclos, olas deterministas y candidatos a paralelismo; concurrencia objetivo conservadora.
- CLI estándar de Python `validate`, `plan`, `dry-run` y `validate-manifest`; no invoca motores ni escribe artefactos.
- Contrato/validador de manifiesto con estados, intentos, dependencias, fingerprints y hash de salidas; reanudación selectiva queda diseñada, no ejecutada.
- Workflow de CI mínimo para pruebas y validadores; adaptadores audiovisuales siguen pendientes.

**Verificación de v0.5 (2026-11-04):** 61 pruebas ejecutadas; 60 aprobadas y 1 omitida porque el entorno Windows no permitió crear symlinks. `validate-skill.py`, los cuatro storyboards, las dos configuraciones del pipeline y `dry-run` pasaron. `validate-manifest` pasó mediante pruebas unitarias. GitHub Actions terminó correctamente en el commit `862bb4896f539177aefef46c15ab9053106da547` (PR #2); el squash integrado en `main` es `b78e3ea347cde94f68c9b010f276ea4ea0106406`.

## v0.6 — Quality Assurance & Security Hardening (integrada en `main` mediante PR #3)

- Endurecer aceptación de manifiestos completos: rechazar adaptadores pendientes, herramientas no verificadas, dependencias incompletas y artefactos/fingerprints inconsistentes.
- Convertir errores previsibles de JSON profundo y relaciones de aspecto malformadas en diagnósticos controlados; fijar el límite de 128 dígitos por componente de `aspect_ratio`.
- Verificar de forma automatizada que `validate`, `plan`, `dry-run` y `validate-manifest` no escriben ni borran archivos, no lanzan procesos y no abren conexiones.
- Actualizar `actions/checkout` a v7.0.1 y `actions/setup-python` a v7.1.0; ambos manifiestos oficiales declaran Node.js 24 y el workflow los fija por SHA completo.
- Sustituir `ubuntu-latest` por una matriz pequeña `ubuntu-24.04`/`ubuntu-26.04`, mantener `contents: read` y validar también dry-run y la plantilla storyboard.
- Registrar hallazgos, severidad, evidencia y riesgos pendientes en `references/pipeline-security.md`.

**Verificación local (2026-11-04):** 87 pruebas ejecutadas en Python 3.12 y 3.13; 85 aprobadas y 2 omitidas porque este entorno Windows no permite crear symlinks. `validate-skill.py`, los cuatro storyboards, las dos configuraciones del pipeline y ambos `dry-run` pasaron. `validate-manifest` y la garantía de solo lectura de las cuatro operaciones CLI se cubrieron en la suite. Se fija un máximo explícito de 128 niveles JSON y se rechazan sustitutos Unicode sin pareja; tildes, eñes y pares sustitutos emoji válidos se conservan. `git diff --check` pasó. El workflow y los comandos se revisaron localmente; no hay parser YAML ni `actionlint` instalado, así que la sintaxis no recibió validación automatizada local. **Pendiente de evidencia remota:** ambos jobs de GitHub Actions deben terminar correctamente antes de declarar compatibles las imágenes 24.04/26.04; no se hizo push ni se abrió PR en esta fase.

## v0.7 — Benchmarking & Storytelling (integrada en `main` mediante PR #4)

- Convertir criterios de hook, progresión causal, función de plano, giro/cierre y claridad vertical en un protocolo reusable con evidencia por beat/corte, sin métricas de retención inventadas.
- Separar metadatos reportados del piloto Gengar, inferencias y datos pendientes; sus 40.53 s, 1080×1920, 30 FPS y ~−14.4 LUFS no son objetivos universales.
- Definir audio como reloj cuando hay grabación; anotar estimaciones, medidas y revisiones manuales sin afirmar sync palabra-a-palabra sin evidencia.
- Añadir `narrative_timing` opcional a `schema_version: 1` para beats, pausas y cortes enlazados a escenas existentes; storyboards anteriores siguen válidos.
- Incluir un ejemplo original de estación nocturna para demostrar el método; no generar imagen, audio o video.

**Verificación local y CI (2026-11-04):** localmente se ejecutaron 93 pruebas; 91 aprobadas y 2 omitidas en Windows por permisos de symlink. `validate-skill.py`, los cuatro storyboards de `examples/` y la plantilla, las dos configuraciones del pipeline, ambos `dry-run` y `git diff --check` pasaron. La suite conserva los storyboards anteriores y cubre el bloque `narrative_timing` válido/inválido, referencias temporales, cortes de transición y voz que cruza un corte; prueba que las cuatro operaciones CLI siguen siendo de solo lectura. GitHub Actions ejecutó las 93 pruebas sin omisiones ni fallos en Ubuntu 24.04 y 26.04 para el commit `69cb9c8134a8703a48d249fa6823b2a504b1a3d8` (run [41921199387](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/actions/runs/41921199387)); el squash integrado en `main` es `530188cc37c570939b58e5dfbc64190afb7df0f7`. No se creó ni reprodujo audio/video; no hay evidencia audiovisual de ritmo, sync, continuidad temporal ni claridad real.

## v0.8 — Production Adapters: fase 1, contratos TTS

- El validador acepta exactamente una etapa narrativa: `03-kokoro-narration` (heredada) o `03-tts-narration` (neutral). Conserva los IDs declarados, los contratos `pipeline_version: 1` y `manifest_version: 1`, y la validación de fingerprints/manifiestos anteriores.
- El contrato neutral valida proveedor conocido (`kokoro` o `piper`), voz, idioma, parámetros WAV acotados, recurso de guion explícito y dos salidas futuras: WAV y JSON de procedencia.
- `voiceover-script` debe ser recurso requerido, confinado, UTF-8 válido, no vacío y de hasta 1 MiB. Su hash participa en los fingerprints. No se usa texto de captions.
- Se conserva el ejemplo legado de cueva y la plantilla original. `examples/tts-contract/` demuestra la configuración neutral; declara proveedor no verificado y adaptadores pendientes.
- Sin ejecución TTS productiva, WAV ni sidecar publicados, reintentos o recuperación. Disponibilidad, licencias y versiones no se infieren de la allowlist.

**Estado:** integrado mediante PR #6 y squash `694d4c9de26b840af7794d08273f6d9b218a009c`.

## v0.8 — fase 2A: simulación TTS en memoria

- Comando explícito `simulate-tts --backend mock`, separado de las cuatro órdenes originales de planificación/validación.
- Validar pipeline, storyboard, dependencia TTS directa de `01-validate-project` y guion UTF-8 explícito; limitar lectura a 1 MiB y calcular el hash de los mismos bytes capturados.
- Construir y verificar en memoria un segundo de silencio RIFF/WAVE PCM de 16/24 bits o IEEE float de 32 bits. Límite WAV: 512 KiB. Resumen JSON sin el texto privado del guion.
- Procedencia en memoria con `simulation: true`, backend `mock`, proveedor solicitado, proveedor productivo no ejecutado, parámetros efectivos y hashes. No publicar archivos, llamar servicios, ejecutar herramientas ni completar etapas.
- No imponer límites CPU/RAM/VRAM ni timeout; no afirmar aislamiento de red ni resistencia TOCTOU. El contrato y los fingerprints v1 permanecen intactos.

**Estado:** integrada mediante PR #7 y squash `cfa7698bd165bedbd27d478859cdbddd3a7478a7`. Sin TTS real ni publicación de artefactos productivos.

## v0.8 — fase 2B: publicación local segura de simulación

- Añadir `save-tts-simulation CONFIG`, separado de `simulate-tts`, con destino fijo `.pipeline/simulations/tts/run-<uuid>/` y archivos `mock-narration.wav` y `mock-provenance.json`.
- Reutilizar la validación y el mock aprobados; abrir archivos en modo exclusivo, verificar WAV/JSON/hashes releídos y reportar éxito solo con ambos artefactos completos.
- Rechazar symlinks, junctions y reparse points identificables en componentes existentes. La limpieza no es recursiva: elimina únicamente archivos cuya identidad se verificó y conserva e informa parciales inciertos.
- Mantener `pipeline_version: 1`, `manifest_version: 1`, fingerprints, salidas productivas y estados `pending`; los comandos originales y `simulate-tts` siguen siendo de solo lectura.
- El modelo de amenazas se limita a proyectos locales confiables sin sustitución hostil concurrente de directorios. No se promete TOCTOU completo, aislamiento del sistema de archivos ni atomicidad transaccional WAV/JSON. No se aplican límites CPU/RAM/VRAM ni timeout.
- La CI conserva Ubuntu 24.04/26.04 y añade Windows 2025. En Windows se ejecutan junctions reales y comprobaciones genéricas de reparse; los symlinks reales solo se omiten con motivo explícito si falta el privilegio. Las pruebas Linux usan symlinks reales.

**Estado:** PR #8 fusionado mediante squash `bfd71cc0ac7cbf969768a4b40e80c2661738026f`. La matriz de GitHub Actions en Ubuntu 24.04, Ubuntu 26.04 y Windows 2025 aprobó. En Windows 11/Python 3.12, las pruebas de junction y detección genérica de reparse pasaron; los casos de symlink que requieren privilegios se omitieron explícitamente con `WinError 1314`. La fase publica únicamente artefactos simulados y no integra TTS productivo al DAG.

## Plan anterior a las fases productivas (histórico)

El plan original proponía integrar el piloto Kokoro al DAG con persistencia/huellas antes de los adaptadores audiovisuales. La integración TTS quedó implementada en Phase 4B (v0.10). No se implementaron Blender, Remotion, FFmpeg ni QA audiovisual; cualquier asset/modelo y licencia se debe verificar antes de una fase futura.

## v0.9 — Phase 3B.2: Kokoro Local Integration

- Añadir `run-tts-kokoro CONFIG --python ... --model ... --voices ...`, limitado a `kokoro` / `em_alex` / `es` y WAV mono PCM16 a 24 kHz.
- Validar pipeline, storyboard, dependencia narrativa y los mismos bytes del guion antes de ejecutar. El worker Python fijo recibe el texto por stdin, ejecuta una inferencia CPU con `CPUExecutionProvider`, timeout de 300 s y sin reintentos.
- Verificar WAV real, señal, clipping, duración, tamaño de hasta 16 MiB, límite de 300 s y SHA-256 de guion/audio/modelo/voces; registrar versiones, proveedor y mediciones disponibles.
- Publicar exclusivamente en `.pipeline/tts-local-runs/run-<uuid>/`, separado del mock y de las salidas productivas. El registro marca `simulation: false`, `execution_mode: local-standalone`, `productive_stage_completed: false`; no modifica manifiestos ni estados `pending`.
- Mantener las cuatro órdenes originales, `simulate-tts`, `save-tts-simulation`, contratos/fingerprints v1 y los runners Ubuntu 24.04/26.04/Windows 2025.

**Fuera de alcance:** integración del worker en el DAG productivo, escritura en outputs declarados, Piper, otras voces/idiomas, remuestreo, límites de CPU/RAM/VRAM, aislamiento de red, reintentos y recuperación.

## v0.10 y estado actual

### v0.10 — Phase 4: Productive local TTS

#### Phase 4A — diseño de ejecución (completada)

- Aprobada la pseudentrada semántica `pipeline-config` para Stage 01; algoritmo de fingerprints y contratos `pipeline_version: 1` / `manifest_version: 1` preservados.
- Aprobado registro estructurado de versiones runtime observadas y SHA-256 del worker, modelo y voces; `availability: verified` no reemplaza la comprobación del worker.
- Definidos elegibilidad Kokoro, recibo `validated-plan`, checkpoints y fronteras de publicación/fallo.

#### Phase 4B — ejecución productiva v0.10 (completada)

- `run-productive-tts` implementa Stage 01 y Stage 03 Kokoro mediante worker existente. Publica y relee validated-plan, WAV, provenance y manifest v1; solo Stage 01/03 pueden completarse. Outputs no se sobrescriben.
- Un intento; sin retries, resume ni adopción de artefactos huérfanos. 02/04/05/06 permanecen pending.
- Aprobada auditoría/correcciones; PR #12 fusionado mediante merge commit `aba0b4335062655291488b2ec128ee05922aff16`.
- 176 pruebas automatizadas ejecutadas localmente: 173 aprobadas y 3 skips por permisos de symlink Windows (`WinError 1314`). CI del head PR `cb323a0e419ee1a16912890a6e778b49afb89929` pasó en Ubuntu 24.04, Ubuntu 26.04 y Windows 2025. CI usa mocks y no hace inferencia real.
- Una inferencia Kokoro real, solo en Windows local; no se hizo inferencia real en Ubuntu. Runtime, versiones, hashes y tiempos en [evidencia de Phase 4B](productive-tts-execution-design.md).

#### Phase 4C — Documentation & Release Readiness v0.10

- Actualizar orquestación, seguridad, instrucciones de skill, README y roadmap para describir contratos/controles reales de Phase 4B.
- Corregir afirmaciones históricas que describían TTS productivo como no implementado.
- Mantener esta fase documental: sin cambios de código, audiovisual, runtime, assets ni generación de medios.
- La fase actualiza la documentación de v0.10; no cambia el estado de los adaptadores audiovisuales ni afirma generación completa de video.

### Trabajo todavía pendiente

El runner productivo actual solo cubre Stage 01/03 Kokoro. No existe ejecución Blender para escenas, Remotion para composición, FFmpeg para finalizar video, QA audiovisual, alineador de subtítulos, descargador/verificador de licencias, retries, resume ni orquestación audiovisual general. La rúbrica y benchmarks siguen siendo protocolos, no resultados medidos. La generación completa de cortos verticales no está implementada.

## v0.10 — Phase 5A: Blender Productive Adapter Architecture (completada)

- El diseño documental aprobado define cómo integrar productivamente Blender en Stage 02, preservando Phase 4B, `pipeline_version: 1`, `manifest_version: 1` y sus fingerprints.
- Define un ejecutor Stage 02 en el orquestador compartido y un worker Blender fijo, con paquete ZIP único que contiene escenas `.blend`, secuencias PNG verificables, índice y provenance.
- El diseño se fusionó mediante PR #15. Stage 02 continúa `pending`; no existe adaptador Blender productivo. No se ejecutó Blender, render ni generación audiovisual durante Phase 5A.
- Diseño actualizado para Phase 5B: Cycles CPU, perfil MVP coherente 540×960/12 FPS/[0,24), 24 PNG, `run-productive-scenes CONFIG --blender ABSOLUTE_PATH`, validación ZIP read-only en streaming y scene spec JSON v1 estricto.
- Criterio de validación de PNG para Phase 5B: decodificar completamente IDAT/scanlines con límites; rechazar corrupción, truncamiento y errores reales de decodificación aunque las cabeceras sean válidas.
- El diseño incluye contrato de recursos, seguridad/headless, límites, compatibilidad TTS, manifiesto v1, pruebas mock multiplataforma y gates para Phase 5B/5C.
- Contrato aprobado: `docs/blender-productive-adapter-design.md`.

## v0.10 — Phase 5B.1: Compatibility & Productive Blender Profile (completada)

- PR #16 fusionado en el merge commit `bd8f128d10117b9abaf478e4b2f5f2318aed1752`.
- El perfil Stage 01 + Stage 02 valida el DAG completo, mantiene Stage 03/04/05/06 pending y verifica semánticamente `validated-plan`.
- El CLI `run-productive-scenes CONFIG --blender ABSOLUTE_PATH` falla de forma controlada mientras el adaptador real no está disponible; no invoca Blender ni escribe estado de ejecución.

## v0.10 — Phase 5B.2: Strict Blender Scene Specification v1 (completada)

- PR #17 integrado mediante merge commit `6bd1868987cd918ace261d5a734b5ca8bb49b0ad`.
- El parser UTF-8 estricto, limitado a 64 KiB, valida el contrato cerrado `blender-scene-spec` v1: tipos, rangos, IDs, referencias, timeline y movimiento de cámara.
- Se incluye un ejemplo coherente de pipeline, storyboard y scene spec; los cambios al recurso invalidan la semántica del `validated-plan` mediante los fingerprints v1 existentes.
- 212 pruebas automatizadas ejecutadas localmente en Windows/Python 3.12: 209 aprobadas y 3 omitidas por permisos de symlink (`WinError 1314`). CI pasó en Ubuntu 24.04, Ubuntu 26.04 y Windows 2025.
- Stage 02 permanece pending. No se implementaron worker, preflight Blender, render, decodificador PNG ni publicación ZIP; no se ejecutó Blender ni se generaron medios.

**Criterio para Phase 5B.3:** validar la trayectoria completa de cámara entre keyframes. La interpolación lineal independiente de posición y target puede producir posiciones coincidentes o un vector de dirección inválido en frames intermedios; el worker/verificador deberá detectar y rechazar esos casos antes de renderizar.

## v0.10 — Phase 5B.3: Blender Runtime Preflight & Fixed Worker (completada)

- Phase 5B.3 se integró en `main` mediante merge commit `93f2cbce8ce20a84b8e661651813ff749b9d51f6`.
- Se implementa preflight explícito y local de Blender 5.2.x; comprueba ruta absoluta, archivo ejecutable sin symlink/reparse point y versión observada, sin búsqueda en PATH ni instalación.
- Se añade un worker fijo que acepta solo request acotado y `scene-spec v1`, configura `blender-scene-policy-v1` con Cycles CPU y staging exclusivo, y prepara `scene.blend` más 24 PNG.
- El proceso padre supervisa timeout/cancelación y verifica el recibo y los outputs de staging. Continúa siendo autoridad de hashes, provenance y manifiesto; este incremento no publica ni permite completar Stage 02.
- `run-productive-scenes` mantiene un fallo controlado tras el preflight: todavía no lanza el worker desde el flujo productivo.
- Pruebas mock cubren preflight, request, cámara (incluida distancia mínima analítica), supervisión y cambios concurrentes de inputs. No se ejecuta Blender ni se generan medios reales.
- Fuera de alcance de este incremento: decodificación PNG completa (5B.4), ZIP/publicación (5B.5) e integración productiva con gate local independiente (5B.6).

## v0.10 — Phase 5B.4: Strict PNG RGB8 Decoder & Validation (completada)

- Phase 5B.4 se integró en `main` mediante merge commit `e9c81aac72b6834026df932c622c3f667b314d52`.
- Se añade un decodificador PNG read-only, de biblioteca estándar, para RGB8 no entrelazado a 540×960 y límite de 32 MiB por archivo.
- Verifica estructura y CRC de chunks, zlib completo/checksum, 960 scanlines exactas y filtros 0–4 mediante reconstrucción incremental con dos filas de trabajo; no conserva el raster completo.
- Acepta únicamente chunks auxiliares explícitos `cHRM`, `gAMA`, `pHYs`, `sRGB`, `tEXt` y `tIME`, con límites, orden y validación estructural; rechaza los demás chunks no definidos para este perfil.
- Se mantiene independiente del worker, ZIP y publicación. Stage 02 sigue pending y no se habilita `run-productive-scenes`; no se ejecutan Blender ni renders.
- Validación final de Phase 5B.4 después de auditoría en Windows/Python 3.12: 251 pruebas; 247 aprobadas y 4 omitidas por permisos de symlink. CI del commit aprobado pasó en los tres sistemas. `validate-skill.py` y `git diff --check` pasan. No se ejecutó Blender.

## v0.10 — Phase 5B.5A: Strict Scene Package ZIP Validator (completada)

- Integrada en `main` mediante merge commit `41406c2b42ebe7ce449c7977dc02dfd6f60017ad` (PR #21).

- Antes de codificar se documenta el schema cerrado v1 para `package.json` y `provenance.json`, incluidos IDs, hashes, runtime observado, inputs, política efectiva y timeline MVP.
- Se fija el contrato ZIP classic single-disk sin ZIP64, prefijos/comentarios, encryption, data descriptors ni extra fields; admite STORE/DEFLATE y metadata regular coherente de Windows/Unix.
- El módulo `scene_package_validator.py` implementa validación read-only de 27 miembros: preflight acotado del directorio, comparación de headers local/central, rutas e inventario exactos, límites previos y durante lectura, CRC/tamaño/SHA-256 y JSON UTF-8 estricto sin claves duplicadas o desconocidas.
- Los 24 PNG se decodifican incrementalmente mediante el decoder 5B.4 reutilizado; el `.blend` se consume y hashea completo y solo se comprueba su cabecera/versionado, sin afirmar que Blender pueda abrirlo.
- La API acepta un `ScenePackageContext` externo para comparar IDs, run, fingerprints, hashes de inputs, identidad observada del runtime, seed autorizada del Scene Spec validado y SHA-256 del ZIP declarado por el manifiesto. Sin ese contexto, el resultado acredita únicamente integridad y consistencia interna.
- Las pruebas usan ZIP/PNG sintéticos y cubren corrupción de miembros/CRC, metadata inconsistente, rutas, duplicados, esquemas JSON, contradicciones semánticas, límites y contexto. No ejecutan Blender ni producen medios.
- Esta fase no crea ZIP productivo, no publica, no integra el validador con `validate-manifest`, no habilita `run-productive-scenes` y no completa Stage 02.
- El constructor/publicación exclusiva de Phase 5B.5B se implementa en una rama separada para auditoría independiente. El gate local Blender 5.2.3 de Phase 5B.6 sigue pendiente.

## v0.10 — Phase 5B.5B: ZIP Builder, Exclusive Publication & Stage 02 Integration (en auditoría)

- El perfil productivo permite Stage 02 `implemented` solo con Scene Spec v1, DAG/outputs exactos y Blender declarado `implemented/verified`; conserva el perfil heredado `pending` y Kokoro Phase 4B.
- El padre reutiliza Stage 01 y el worker fijo, construye ZIP classic de 27 miembros desde staging sin cargar los medios completos en memoria, valida con contexto externo, publica exclusivamente, relee el ZIP y persiste Stage 02 `completed` solo tras verificación.
- `validate-manifest` comprueba el paquete de escenas en streaming y sin escrituras, extracción ni procesos. Stage 03/04/05/06 permanecen `pending`; no hay retry/resume ni adopción de huérfanos.
- La suite sintética no acredita ejecución real de Blender ni openability del `.blend`. Phase 5B.6 reserva el gate local opt-in con Blender 5.2.3.
