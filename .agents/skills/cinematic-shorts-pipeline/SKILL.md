---
name: cinematic-shorts-pipeline
description: "Planifica y valida cortometrajes cinematográficos 3D y puede ejecutar TTS Kokoro local cuando se solicita explícitamente. Úsala para preparar paquetes de producción, validar el perfil productivo o narración productiva acotada; no genera escenas, renders ni videos."
---

# Flujo de cortometrajes cinematográficos

Crea un paquete de producción revisable a partir de una idea o guion. Esta skill planifica y valida; no autoriza por sí sola generar imágenes, audio, modelos 3D, renders ni video. Solo ejecuta TTS cuando la persona pide explícitamente el piloto local o la etapa productiva descrita abajo. Pregunta solo cuando una decisión creativa o técnica importante no pueda inferirse. Respeta la autorización explícita de producción y las instrucciones del proyecto destino.

## Flujo y puertas de aprobación

| Etapa | Entrada | Salida revisable | Aprobación para avanzar |
| --- | --- | --- | --- |
| Encargo | Idea, guion o petición | Brief con audiencia, formato, duración, idioma, tono, restricciones y entregables; incógnitas señaladas | Requisitos esenciales completos; hechos y restricciones entendidos |
| Historia | Brief aprobado | Logline, objetivo, obstáculo, riesgo, beats, giro y cierre | Causa/efecto legibles; hechos separados de ficción; sin decisión narrativa bloqueante |
| Estética y recursos | Historia aprobada | Hoja de estilo, criterios de personajes/entornos y lista de recursos candidatos | Estilo coherente; cada recurso usado tiene fuente/licencia/formato/rig verificados o queda marcado pendiente y fuera del plan aprobado |
| Storyboard | Historia y dirección aprobadas | JSON por plano; bloques opcionales `cinematography` y `animation` describen cámara/luz y actuación por beats | Validador pasa; intención de foco/corte y beats/continuidad revisada. Still 9:16 y secuencia tienen puertas separadas; rig sin verificar no queda aprobado para producción |
| Plan técnico | Storyboard aprobado | Motor y versiones, recursos/licencias, animación, voz/subtítulos, perfil y coste/limitaciones previstos | Ruta local viable; dependencias y medidas de recursos explícitas; sin incertidumbre crítica oculta |
| Control de calidad | Paquete anterior completo | Rúbrica con evidencia, resultados automáticos, revisión visual y evaluación humana separadas | Todos los fallos corregidos o aceptados expresamente; bloqueos listados; no afirmar checks sin ejecutarlos |

No saltes una puerta fallida. Un plan, vista previa, fotograma, narración o render no autoriza la siguiente etapa de producción.

Para cada storyboard ejecuta `python scripts/validate-storyboard.py ruta/al/storyboard.json`. `validate-skill.py` también valida estructura y cronología de todos los `examples/**/storyboard.json` y `templates/storyboard.json`, reutilizando el validador de storyboard. Para revisar la skill completa, ejecuta `python scripts/validate-skill.py .` y `python -m unittest discover -s tests -v`.

Los proyectos pueden versionar un `pipeline.json` con `pipeline_version: 1`, independiente del storyboard. Valida con `python scripts/pipeline.py validate ruta/al/pipeline.json`; inspecciona el DAG sin ejecutar ni escribir con `plan` o `dry-run`; valida un manifiesto existente con `validate-manifest pipeline.json run-manifest.json`. Consulta `references/pipeline-orchestration.md` antes de editar ese contrato.

Para probar el contrato neutral de voz, `python scripts/pipeline.py simulate-tts ruta/al/pipeline.json --backend mock` valida el pipeline, storyboard, dependencia narrativa y guion, y devuelve un WAV de silencio simulado y su procedencia **solo en memoria**. El comando siempre marca `simulation: true`, no revela el guion, no escribe archivos, no abre conexiones, no ejecuta procesos ni completa etapas productivas. No es un ejecutor TTS.

Para conservar el resultado simulado de forma explícita, ejecuta `python scripts/pipeline.py save-tts-simulation ruta/al/pipeline.json`. Este comando valida el mismo contrato y publica `mock-narration.wav` y `mock-provenance.json` en un directorio nuevo bajo `.pipeline/simulations/tts/`. Es una publicación local de artefactos simulados; no ejecuta etapas productivas, no cambia el manifiesto y no escribe en las rutas de salida declaradas por TTS.

Para un piloto de voz real con Kokoro local, usa explícitamente `python scripts/pipeline.py run-tts-kokoro ruta/al/pipeline-kokoro.json --python RUTA_ABSOLUTA_A_PYTHON --model RUTA_ABSOLUTA_AL_MODELO_ONNX --voices RUTA_ABSOLUTA_A_VOCES`. La configuración debe declarar `provider: kokoro`, `voice: em_alex`, `language: es` y WAV mono PCM16 a 24 kHz. El worker fijo recibe los bytes del guion por stdin, fuerza `CPUExecutionProvider`, impone un timeout de 300 s y no reintenta. El piloto acepta hasta 8 KiB de guion, 300 s de audio y 16 MiB de WAV. Publica WAV y procedencia verificados bajo `.pipeline/tts-local-runs/run-<id>/`; no usa las salidas mock ni las salidas productivas y no completa la etapa ni actualiza manifiestos. El comando requiere un runtime local compatible y no instala ni descarga dependencias. No afirma aislamiento del sistema de archivos o de red ni límites de CPU/RAM.

Para ejecutar narración productiva —solo tras solicitud explícita— usa `python scripts/pipeline.py run-productive-tts CONFIG --python RUTA_ABSOLUTA_A_PYTHON --model RUTA_ABSOLUTA_AL_MODELO_ONNX --voices RUTA_ABSOLUTA_A_VOCES`. Requiere el perfil productivo Kokoro completo documentado en `references/pipeline-orchestration.md`; `examples/tts-contract/pipeline.json` es neutral/Piper y no se puede pasar sin adaptar. El comando materializa y verifica Stage 01 (`validated-plan`), sintetiza Stage 03 mediante el mismo worker Kokoro y publica WAV/provenance en outputs declarados más un manifiesto v1 bajo `workspace.manifest_dir/run-<id>/` (por defecto, `.pipeline/runs/`). Solo Stage 01 y Stage 03 pueden quedar `completed`; las etapas audiovisuales permanecen `pending`. Exige `em_alex`, `es`, WAV mono PCM16 a 24 kHz y runtime local compatible indicado por rutas absolutas. Una invocación, sin retry, resume ni adopción de outputs huérfanos. No descarga modelos ni instala dependencias.

No confundir comandos:

- `validate`, `plan`, `dry-run`, `validate-manifest` y `simulate-tts`: validación/inspección o mock en memoria; read-only y sin worker productivo.
- `save-tts-simulation`: publica WAV silencioso mock y provenance, nunca voz real.
- `run-tts-kokoro`: piloto real standalone; no completa etapa ni actualiza manifiesto.
- `run-productive-tts`: única ejecución productiva actual; completa como máximo Stage 01 y Stage 03 mediante manifiesto v1.

Los captions usan intervalos `[start_ms, end_ms)`: solapes rechazados por defecto. Un solape intencional requiere `overlap_exception` no vacío en el caption que empieza después; documenta su motivo.

Para enlazar voz y planos, `narrative_timing` es opcional en `schema_version: 1`: etiqueta el reloj como estimado o grabado y anota beats, pausas y cortes con anclas referidas a escenas. Con grabación real, usa su duración como reloj y vuelve a revisar captions. Las anclas no demuestran sincronía; no declares alineación palabra a palabra sin evidencia de alineador o revisión manual.

Guarda paquetes nuevos en `deliverables/<slug>/` con un brief/plan y `storyboard.json`. Usa un slug estable y conserva los entregables existentes; no muevas ni sobrescribas paquetes anteriores al crear uno nuevo.

## Estado de capacidad

Implementado: guías de planificación, contratos opcionales de cinematografía/animación, rúbrica cualitativa, planificador DAG local, contratos de pipeline/manifiesto y ejecución productiva local de TTS Phase 4B. `plan` y `dry-run` son read-only; no ejecutan etapas ni prueban runtime. `run-productive-tts` es una excepción limitada a Stage 01/03 y escribe sus artefactos/manifiesto verificados. `validate-manifest` valida un manifiesto existente, pero no crea, reanuda ni vuelve a ejecutar.

Implementado también: contrato declarativo TTS en `pipeline_version: 1`, compatible con la etapa heredada `03-kokoro-narration`, con etapa neutral `03-tts-narration`, guion UTF-8 explícito, formato WAV acotado y salidas previstas de audio/procedencia. La fase 2A añade exclusivamente `simulate-tts --backend mock`: construye un segundo de silencio WAV RIFF en memoria (PCM de 16/24 bits o IEEE float de 32 bits), verifica cabeceras, duración, tamaño y hashes y presenta un resumen JSON sin texto del guion. La fase 2B añade `save-tts-simulation`, que guarda WAV y procedencia simulados en un directorio exclusivo separado de las rutas productivas, verifica los bytes y hashes publicados y evita sobrescribir resultados existentes. La fase 3B.2 añade `run-tts-kokoro`, un piloto local explícito con voz real que publica únicamente bajo `.pipeline/tts-local-runs/`; no cambia los contratos ni fingerprints v1, no modifica salidas productivas ni marca etapas completadas. `simulate-tts` y `save-tts-simulation` conservan su semántica mock. Consulta `references/pipeline-orchestration.md`.

Las etapas audiovisuales 02/04/05/06 y sus adaptadores siguen pendientes. `simulate-tts` produce WAV mock efímero; `save-tts-simulation` publica mock; `run-tts-kokoro` publica narración real standalone; `run-productive-tts` publica TTS productivo y completa solo Stage 01/03. Estos comandos tienen destinos/estados distintos. El ejecutor es para proyecto local confiable: no promete protección TOCTOU completa, aislamiento de filesystem/red ni atomicidad transaccional de WAV+provenance+manifest. No impone límites de CPU/RAM/VRAM; timeout de 300 s. No hay ejecución Blender, composición Remotion, finalización FFmpeg, QA audiovisual temporal ni pipeline completo de generación de video. Licencias y procedencia de assets deben revisarse por tarea.

## Contexto local

Al trabajar en VideoIA, lee primero `AGENTS.md` y las instrucciones locales pertinentes. El entorno de referencia conocido es Windows 11, GTX 1650 de 4 GB, 16 GB de RAM, Blender local, Remotion/React/Three.js/R3F, FFmpeg, Kokoro TTS y paquetes de recursos CC0 opcionales. Es contexto, no garantía de que un binario, modelo, paquete, licencia o versión siga instalado. Prioriza flujos locales/gratuitos y vistas previas modestas; verifica las versiones activas antes de aplicar patrones del framework.

Reutiliza las skills instaladas de Remotion en vez de copiar sus manuales. Consulta las guías de buenas prácticas, markup, 3D, subtítulos, Studio o render solo si la tarea lo requiere. El render requiere petición explícita. No instales dependencias, descargues recursos, uses servicios de pago, publiques ni modifiques proyectos ajenos sin autorización.

Usa la [rúbrica cinematográfica](references/cinematic-quality-rubric.md) para separar validación automática, inspección de fotogramas fijos y juicio humano. Las imágenes estáticas no prueban calidad temporal de animación.

## Recursos

- Narrativa: [references/storytelling.md](references/storytelling.md)
- Identidad visual: [references/art-direction.md](references/art-direction.md)
- Diseño de planos: [references/cinematography.md](references/cinematography.md)
- Personajes, entorno y animación: [references/animation-3d.md](references/animation-3d.md)
- Integración Remotion/R3F: [references/remotion-integration.md](references/remotion-integration.md)
- TTS, audio y subtítulos: [references/voice-and-captions.md](references/voice-and-captions.md)
- Puertas de revisión: [references/quality-control.md](references/quality-control.md)
- Rúbrica cinematográfica: [references/cinematic-quality-rubric.md](references/cinematic-quality-rubric.md)
- Arquitectura y uso del pipeline: [references/pipeline-orchestration.md](references/pipeline-orchestration.md)
- Seguridad y validación reproducible del pipeline: [references/pipeline-security.md](references/pipeline-security.md)
- Auditoría de fuentes: [docs/sources.md](docs/sources.md)
- Skills candidatas: [docs/skill-candidates.md](docs/skill-candidates.md)
- Hallazgos locales: [docs/lessons-learned.md](docs/lessons-learned.md)
- Prompts iniciales: [docs/prompt-library.md](docs/prompt-library.md)
- Pendientes: [docs/roadmap.md](docs/roadmap.md)
- Descubrimiento local de Codex: [docs/codex-discovery.md](docs/codex-discovery.md)
