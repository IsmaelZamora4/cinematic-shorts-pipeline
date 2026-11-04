# Control de calidad y puertas

Registra cada comprobación como **aprobada**, **fallida**, **sin revisar** o **no aplicable**. Un plan no demuestra que una implementación funcione.

## Puertas de planificación

- **Encargo:** plataforma/formato, duración, idioma, tono, entregables, restricciones y autorización anotados.
- **Historia:** objetivo, obstáculo, riesgo, giro y final claros; hechos separados de la ficción.
- **Storytelling — plan:** hook presenta pregunta/premisa; cada beat cambia acción o conocimiento; progresión causal conduce a revelación/cierre; cada plano tiene función y la voz/pausa/corte se relacionan con el beat. Registra evidencia escrita y corrección; sin puntos ni puntuación global arbitraria.
- **Audio — automático:** si existe `narrative_timing`, validar origen declarado del reloj, procedencia de anclas, referencias a escenas, orden, no solape, cortes puntuales y límites de `voiceover.duration_ms`. Esto valida consistencia declarada, no existencia de grabación ni sincronía.
- **Audio — reproducción:** con pista real, reproducir y revisar duración, pausas, acción visual y cortes; registra archivo/intervalo/método. Sin grabación, mantener tiempos como estimados. No aprobar alineación palabra por palabra sin alineador o revisión manual con evidencia.
- **Narrativa — still:** un fotograma puede probar legibilidad de una acción/pista concreta, no el ritmo, causalidad o claridad total de la historia.
- **Narrativa — secuencia:** reproducción completa verifica progresión, cambio de información, continuidad espacial y causalidad, así como si cada corte y cierre llegan en el momento previsto.
- **Estética/recursos:** reglas visuales coherentes; fuente/licencia/compatibilidad exactas comprobadas o incertidumbre declarada.
- **Storyboard — automático:** JSON, campos, enums/rangos opcionales, IDs, tiempos, captions y cobertura total pasan validadores.
- **Storyboard — intención cinematográfica:** por plano se puede identificar primer foco, acción, información nueva, fuente de luz y razón del corte; continuidad de eje, dirección y mirada no se contradice. Esto aprueba el plan escrito, no su imagen.
- **Animación — intención:** rig/status/controles requeridos declarados; beats cubren el plano sin huecos; poses, peso, apoyos, root motion, mirada, manos/props y entradas/salidas están descritos; continuidad textual entre planos revisada. Un rig `unselected` o `unverified` mantiene la implementación bloqueada.
- **Animación — automático:** el contrato opcional tiene tipos/enums válidos, beats relativos contiguos dentro de cada escena y controles/estados reconocidos. No prueba rig, movimiento o continuidad perceptual.
- **Storyboard — still:** solo se aprueba con fotogramas revisados en viewport 9:16 a tamaño de teléfono; foco, geografía, perspectiva, luz y pistas son legibles.
- **Storyboard — movimiento:** solo se aprueba con secuencia reproducida; eje, dirección de pantalla, movimiento de cámara y continuidad temporal comprobados.
- **Actuación — movimiento:** solo se aprueba tras reproducir secuencia; peso/apoyo, root motion, anticipación, mirada, contactos de manos/props y estados de entrada/salida se evalúan entre cortes. Still y JSON no lo sustituyen.
- **Plan técnico:** motor/versión y ruta de recursos verificados; audio, subtítulos y perfil descritos; sin dependencias externas no autorizadas.

## Puertas de producción (solo si se solicita producir)

- Revisa fotogramas fijos a tamaño móvil para cada plano antes del render completo.
- Comprueba recortes geométricos, siluetas, materiales/texturas, exposición, sombras, jerarquía focal, continuidad de cámara y legibilidad del hallazgo en stills por plano.
- Reproduce la secuencia para revisar dirección de pantalla, eje/mirada, movimientos motivados, empalmes, acción y continuidad temporal. No apruebes movimiento desde stills.
- Revisa en secuencia apoyos y transferencia de peso, deslizamiento, raíz, anticipación, seguimiento de mirada, contacto/agarre/liberación de manos y respuesta de props; compara pose y estado a través de cada corte.
- Verifica duración real de voz y subtítulos; escucha inteligibilidad, picos/clipping y conflictos de mezcla.
- Mide tiempo de render y memoria en la máquina destino; limita concurrencia y calidad inicial cuando sea necesario.
- Tras el render solicitado, inspecciona fotogramas representativos y reproduce el archivo; valida lectura, dimensiones, FPS, duración, audio y subtítulos. Informa comandos y resultados observados.

Los scripts incluidos validan el empaquetado de la skill, los datos del storyboard, el DAG del pipeline y manifiestos existentes. No ejecutan etapas de producción, renderizan, inspeccionan calidad audiovisual, verifican licencias ni juzgan la narrativa.

## Puertas de seguridad del pipeline

- **Automática:** `validate`, `plan`, `dry-run` y `validate-manifest` no deben crear, eliminar ni modificar archivos; no deben invocar procesos externos ni abrir conexiones. La suite compara snapshots de rutas/contenidos y bloquea aperturas de escritura, operaciones comunes del sistema de archivos, procesos y sockets.
- **Automática:** un manifiesto con estado `completed` solo se acepta si el adaptador de etapa está implementado, las herramientas están declaradas verificadas y sus adaptadores están implementados; dependencias, salidas, hashes y fingerprints deben coincidir.
- **Pendiente de ejecución:** las declaraciones `availability: verified` no prueban que el ejecutable real exista ni que sea compatible. La ejecución y reanudación permanecen fuera del alcance; deben volver a validar entradas, versiones, límites y salidas al implementarse.
- **CI:** la matriz fija Ubuntu 24.04 y 26.04. El soporte remoto se declara aprobado solo después de que ambos jobs terminen con éxito. La suite de Windows puede omitir la prueba de symlink si el sistema no concede ese permiso.
