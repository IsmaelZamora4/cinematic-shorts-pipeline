# La puerta bajo la roca · paquete de planificación

**Estado:** planificación solamente. No se produjeron imágenes, video, audio, voces, modelos 3D ni renders.

## Etapa 1 · Encargo

- **Formato:** Short vertical 9:16; duración objetivo 15 s.
- **Historia:** una exploradora descubre una puerta metálica oculta en una cueva.
- **Idioma de planificación:** español. Plataforma final concreta: no especificada.
- **Dirección requerida:** low-poly serio, proporciones adultas.
- **Hardware:** GTX 1650 con 4 GB de VRAM.
- **Límites:** solo paquete de planificación; sin medios, dependencias nuevas ni cambios en otros proyectos.
- **Supuesto técnico explícito:** 30 FPS para fijar el storyboard: 450 fotogramas totales. No es medición ni requisito del usuario.

**Puerta:** brief completo para proponer una historia y un plan técnico. El público objetivo se asume como audiencia general de Shorts de misterio; plataforma y asset 3D quedan sin seleccionar.

## Etapa 2 · Historia

**Logline:** Al explorar una cámara lateral, una cartógrafa encuentra una puerta de metal oculta bajo sedimento y oye tres golpes desde el otro lado.

- **Protagonista/objetivo:** exploradora que marca una ruta segura de regreso.
- **Obstáculo:** la forma regular está camuflada por roca y polvo; no se ve qué hay detrás.
- **Riesgo:** abrirla puede comprometer la ruta de salida y revelar una presencia desconocida.
- **Beats:** avanzar por una cámara legible → notar una línea recta impropia de la geología → limpiar y revelar puerta, bisagras y cierre → acercarse al mecanismo → oír respuesta desde dentro y apartarse.
- **Cierre:** la puerta sigue cerrada; los golpes responden a su contacto. Se conserva la incógnita.

**Puerta:** objetivo, obstáculo, riesgo, giro y cierre definidos; no se atribuyen hechos reales.

## Etapa 3 · Dirección artística y recursos

- Low-poly cinematográfico sobrio, anatomía adulta y equipo funcional de exploración; sin rasgos chibi ni silueta de fantasía.
- Cámara y composición vertical nativas. Mostrar suficiente cueva para orientar sin dejar áreas negras vacías.
- Jerarquía: exploradora y haz de linterna → seam metálico → puerta revelada → detalle del cierre.
- Cueva asimétrica con sedimento, grietas y escala humana. Metal oxidado y opaco; nada de emisión decorativa.
- Una luz principal motivada por la linterna y relleno ambiental muy tenue, suficiente para leer silueta y relieve.
- **Asset/rig:** personaje y cualquier modelo de puerta siguen sin seleccionarse. Antes de producción, verificar fuente, licencia exacta, formato, proporciones, rig y animaciones; no se descargó ningún recurso.

**Puerta:** reglas visuales coherentes. El asset permanece como pendiente, no como recurso aprobado.

## Etapa 4 · Storyboard

Storyboard JSON: [storyboard.json](storyboard.json). Cinco planos de 90 fotogramas cada uno; intervalos semiabiertos, contiguos, 30 FPS, total 450 fotogramas (15 s). Cada plano comunica lugar, indicio, revelación, proximidad o respuesta.

**Puerta:** pendiente del validador hasta ejecutar el comando. La validación estructural no prueba legibilidad artística.

## Etapa 5 · Plan técnico

- **Motor candidato:** Blender/Eevee, escena local simple con geometría de cueva y puerta de baja complejidad. No se verificó la versión instalada para este encargo; confirmar antes de implementar.
- **Composición:** 1080 × 1920, 30 FPS, 450 fotogramas. Probar primero composición y encuadre en 540 × 960 si se autoriza producción.
- **Presupuesto inicial propuesto:** una luz práctica principal con sombras contenidas, pocas luces dinámicas, materiales compartidos, texturas compactas y una sola figura en cuadro. GTX 1650/4 GB es restricción de diseño, no garantía de rendimiento; consumo y tiempo no medidos.
- **Animación planificada:** pasos lentos y apoyados; pausa y mirada hacia la seam; mano limpia sedimento; reacción breve a los golpes. Contacto de pies y continuidad requieren revisión temporal si más adelante se autoriza una secuencia.
- **Voz/subtítulos:** sin narración ni subtítulos previstos para esta versión; el sonido diegético de tres golpes es solo una nota de diseño, no audio producido.
- **Coste/recursos externos:** sin dependencias nuevas, servicios de pago ni descargas.

**Puerta:** ruta local y modesta propuesta; versión de Blender, asset/rig, memoria y tiempos siguen sin verificar. No iniciar producción hasta resolver el asset y confirmar las restricciones técnicas.

## Etapa 6 · Control de calidad

### Automático

- Validador de storyboard: pendiente de ejecución en esta entrega.
- Invariantes previstos: cinco IDs únicos, tiempos continuos 0–450, acciones y propósitos no vacíos, relación 9:16 declarada.

### Inspección de imágenes fijas

No ejecutada: no se generaron imágenes. En una futura revisión, comprobar que el primer plano tenga detalle narrativo en sombras, el segundo explique qué mira la protagonista, el tercero revele claramente la puerta y el detalle del cierre tenga jerarquía sin ocultar contexto.

### Evaluación humana y temporal

La lógica narrativa está definida para revisión; la calidad de composición/luz no puede aprobarse sin fotogramas. Fluidez, deslizamiento de pies, reacción, golpes sincronizados y continuidad no se pueden juzgar con este storyboard ni con capturas aisladas.

## Referencias de la skill aplicadas

- `references/storytelling.md`: logline, objetivo, obstáculo, giro y cierre.
- `references/art-direction.md`: estilo adulto low-poly, luz motivada y presupuesto para GPU de 4 GB.
- `references/cinematography.md`: propósito por plano, vertical nativo y timeline en intervalos semiabiertos.
- `references/animation-3d.md`: plan de pose/acción y cautela con rigs y assets sin verificar.
- `references/voice-and-captions.md`: voz opcional y no afirmar sincronización sin audio medido.
- `references/quality-control.md`: puertas y separación entre plan y evidencia de producción.
- `references/cinematic-quality-rubric.md`: lectura narrativa, jerarquía, luz y límites de imágenes estáticas.

La guía `references/remotion-integration.md` no se usó: el motor candidato de este plan es Blender, pendiente de verificar.
