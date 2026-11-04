---
name: cinematic-shorts-pipeline
description: "Planifica y valida cortometrajes cinematográficos 3D: narrativa, storyboard, dirección artística, plan técnico, voz, subtítulos y control de calidad. Úsala al preparar un paquete de producción; no autoriza por sí sola generar contenido audiovisual."
---

# Flujo de cortometrajes cinematográficos

Crea un paquete de producción revisable a partir de una idea o guion. Esta skill planifica y valida; no autoriza generar imágenes, audio, modelos 3D, renders ni video. Pregunta solo cuando una decisión creativa o técnica importante no pueda inferirse. Respeta la autorización explícita de producción y las instrucciones del proyecto destino.

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

Los captions usan intervalos `[start_ms, end_ms)`: solapes rechazados por defecto. Un solape intencional requiere `overlap_exception` no vacío en el caption que empieza después; documenta su motivo.

Guarda paquetes nuevos en `deliverables/<slug>/` con un brief/plan y `storyboard.json`. Usa un slug estable y conserva los entregables existentes; no muevas ni sobrescribas paquetes anteriores al crear uno nuevo.

## Estado de capacidad

Implementado: guías de planificación, contratos opcionales de cinematografía y animación, rúbrica cualitativa y ejemplos de paquete. La validación de storyboard comprueba estructura y tiempos declarados; no valida cámara, actuación, existencia/compatibilidad de rigs ni calidad visual. Los resultados de benchmarking, disponibilidad de assets, licencia y compatibilidad se verifican por tarea. Esta skill no tiene generadores de medios, motor de render, alineador TTS/subtítulos, evaluación temporal automática ni escáner legal.

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
- Auditoría de fuentes: [docs/sources.md](docs/sources.md)
- Skills candidatas: [docs/skill-candidates.md](docs/skill-candidates.md)
- Hallazgos locales: [docs/lessons-learned.md](docs/lessons-learned.md)
- Prompts iniciales: [docs/prompt-library.md](docs/prompt-library.md)
- Pendientes: [docs/roadmap.md](docs/roadmap.md)
- Descubrimiento local de Codex: [docs/codex-discovery.md](docs/codex-discovery.md)
