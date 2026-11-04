# Planificación de animación 3D

Esta guía y el bloque opcional `scene.animation` describen intención de actuación y requisitos de rig sin acoplarlos a Blender, Remotion ni un asset concreto. El bloque no cambia `schema_version: 1`; storyboards v0.3 que no lo incluyen siguen siendo válidos.

La validación automática comprueba forma, enumeraciones y tiempos relativos. No demuestra que exista un rig, que sus huesos correspondan, que un pie esté realmente apoyado ni que un contacto se vea natural. No generar medios ni empezar adaptadores durante planificación.

## 1. Selección y comprobación de rig

Antes de planear una actuación ligada a un recurso, anota identificador/fuente y verifica el asset exacto: formato y versión, escala/ejes, jerarquía y nombres de huesos, pesos, clips, controles, licencia y límites de uso. Un rig etiquetado “humanoide” no prueba compatibilidad.

El contrato registra:

- `rig.status`: `unselected`, `unverified` o `verified`.
- `rig.profile`: tipo de actuación requerida, por ejemplo cuerpo adulto bípedo con control de raíz, pies, mirada y manos.
- `rig.required_controls`: capacidades abstractas `root`, `gaze`, `foot_ik`, `hand_ik`, `prop_contact`.

`verified` requiere evidencia independiente en el paquete (asset exacto, rig, controles y revisión aprobada). El validador solo acepta el literal: nunca certifica el recurso. Sin rig seleccionado, usa `unselected` y escribe requisitos; no inventes nombres de hueso ni posiciones de mundo.

## 2. Beats de actuación y tiempo

Divide la escena en `animation.beats` con offsets enteros relativos a `scene.start_frame`. El primer beat comienza en 0, el último termina en la duración completa del plano y los beats son contiguos, sin solapes. Una pausa también es un beat: escribe acción de sostener, peso, mirada y estado de manos.

Cada beat declara:

- `action`, `pose_in`, `pose_out`: qué cambia y desde/hasta qué estado observable.
- `anticipation`: preparación que precede a una acción; usa `none` si no aplica.
- `weight_intent`: dónde se sostiene o transfiere el peso y qué efecto tiene en el cuerpo.
- `root_motion`: `stationary`, `in-place-turn`, `travel` o `combined`; indica dirección de pantalla e intención, sin distancia ni coordenadas 3D no calibradas.
- `support`: estado de pie izquierdo/derecho (`planted`, `support`, `swing`, `airborne`, `offscreen`, `not-applicable`) y transferencia de peso.
- `gaze`: objetivo identificable y dirección al inicio/final. Indica si ojos/cabeza anticipan al torso o a la mano dentro de `action`/poses.
- `hands`: lista posiblemente vacía con lado, intención, objetivo y estado de contacto.
- `prop_interaction`: opcional; prop, mano, acción, contacto y respuesta observable del objeto.

Los offsets deben quedar dentro del intervalo del plano. La validación rechaza huecos, solapes y finales fuera de la duración; estructura válida no significa actuación plausible.

## 3. Apoyos, peso y root motion

- Separa swing de stance/support por beat. Nombra pie que recibe peso, pie que despega y pie que termina plantado. Si ambos pies sostienen durante pausa, dilo; no mantengas un pie “swing” a través de un beat de reposo.
- Planifica anticipación antes de viajar, cambio de peso sobre la base y asentamiento después del paso o giro. Una descripción como “camina despacio” no define apoyo ni peso.
- Usa `stationary` para pausa sin raíz desplazada; `in-place-turn` para giro sin avance; `travel` para desplazamiento; `combined` cuando giro y avance son parte del mismo beat. `screen_direction` debe ser `static` en los dos primeros y señalar dirección en los dos últimos.
- Relaciona root motion con dirección de pantalla y eje de cinematografía del mismo plano. Explica desplazamiento relativo (“hacia el túnel”, “cruza a la derecha”); deja metros/vector local para después de fijar escena y escala.
- Revisión de plan: recorre beats en orden; verifica que cada despegue tenga apoyo precedente y que la pose de salida encaje con la siguiente. El deslizamiento real exige secuencia reproducida.

## 4. Mirada, manos y props

- Nombra un objetivo de mirada visible (“línea de agua”, “fibras cortadas”, “cierre”), no solo “mira”. La dirección de salida debe motivar foco/corte siguiente. Planifica secuencia ojos → cabeza → torso si esa anticipación aporta lectura; no la des por ejecutada.
- Especifica lado y objetivo de cada mano: apoyarse, detenerse, alcanzar, tocar, agarrar, sostener o soltar. Si ninguna mano actúa, lista vacía expresa la decisión.
- Usa para la mano estados `none`, `approach`, `touch`, `grasp`, `release`, `maintain`. `prop_interaction` es opcional y solo se añade si hay aproximación/contacto activo; sus estados son `approach`, `touch`, `grasp`, `release`, `maintain`. Separa acción, estado y respuesta del prop; “toca la cuerda” no indica si se mueve o permanece tensa.
- Planifica precontacto, contacto y salida en beats contiguos cuando el objeto deba responder. Define qué cambia en el prop y qué continúa en mano, mirada y peso. No simules contacto por cercanía del prop al personaje.
- Revisión de plan: busca objetivo, lado, estado de contacto y consecuencia en cada interacción. Revisión física/táctil requiere stills para lectura espacial y secuencia para sincronía y mantenimiento de contacto.

## 5. Transiciones y continuidad de actuación

Cada `animation.continuity` declara estado de entrada y salida del plano. Para el primer plano, registra que no hay entrada previa; para el final, registra que no hay toma siguiente. Entre planos adyacentes compara:

- pose de salida/entrada, balance del peso y pie de apoyo;
- ubicación/dirección de raíz y fase del paso;
- objetivo de mirada y lado de pantalla;
- mano/prop: precontacto, contacto, agarre o liberación preservados;
- reacción y ritmo que justifican el corte.

No reinicies pose, root motion, mirada o estado de prop solo porque cambió el ID de plano. Si la acción cruza el corte, documenta empalme y estado compartido. Si hay salto de tiempo o elipsis, escribe la razón y el nuevo estado en la entrada del siguiente plano.

La revisión cruzada de texto puede encontrar contradicciones; solo reproducción confirma continuidad temporal, peso, apoyo, seguimiento de ojos/manos y contacto.

## 6. Método de animación y ruta de producción

En el plan técnico, marca si la intención se resuelve con clips, keyframes, procedimiento o retargeting; explica qué se conserva entre planos y qué requisito del rig lo permite. No prescribas nombres de API, nodos ni implementaciones de motor en este contrato.

- Blender puede encajar si rig/actuación son centrales; primero verifica versión, recurso y ruta local.
- Remotion/Three.js/R3F puede encajar cuando la escena debe ser determinista por frame; verifica versión y rig antes de elegir.
- Híbrido solo con responsabilidad y transferencia de estado descritas.

Son criterios de elección, no compatibilidad confirmada ni autorización para producir. No instales dependencias ni elijas candidato hasta resolver las puertas técnicas.

## 7. Revisión por evidencia

- **Automático:** JSON, campos/enums, offsets contiguos y cobertura temporal del plano. No juzga movimientos ni rigs.
- **Still:** pose, escala, mano/prop y línea de mirada pueden evaluarse espacialmente; no prueba apoyo, peso, anticipación ni contacto temporal.
- **Secuencia:** reproduce y revisa anticipación, transferencia, planta/despegue, raíz, mirada, contacto y estado de entrada/salida entre cortes. Si no existe secuencia, estado temporal `sin revisar`.

Registra estado, comando o archivo y evidencia por separado. No conviertas `rig.status: verified` declarado ni un validador verde en prueba audiovisual.
