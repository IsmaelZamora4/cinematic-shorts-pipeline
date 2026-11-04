# Dirección cinematográfica 3D y storyboard

Planifica cada plano para que un lector pueda reconstruir la atención del público, la acción, la información y el corte sin adivinar coordenadas de una escena que aún no existe. Un plan escrito puede aprobar intención y continuidad; solo fotogramas y reproducción pueden aprobar su resultado visual.

## Ficha por plano

Conserva los campos base de `schema_version: 1`: ID, intervalo, propósito, acción, encuadre, cámara y luz. Añade opcionalmente `cinematography` para convertir intención en una ficha revisable:

```json
{
  "subject_or_clue": "Exploradora y cuerda cortada",
  "shot_size": "medium",
  "angle": "eye-level",
  "focal_point": {"x": 0.67, "y": 0.43},
  "continuity": {
    "axis_id": "explorer-to-cave-interior",
    "screen_direction": "left-to-right",
    "gaze_direction": "screen-right"
  },
  "camera": {
    "intent": "Llevar la mirada desde la exploradora a la cuerda.",
    "movement": "Paneo corto a la derecha; frena al revelar la cuerda.",
    "lens_intent": "Perspectiva moderada; conservar proporciones y espacio."
  },
  "narrative_light_source": "Linterna frontal de la exploradora",
  "visible_information": ["Mano detenida", "Cuerda cortada", "Pared húmeda"],
  "transition_reason": "Cortar al detalle cuando la mirada llega a la cuerda."
}
```

El bloque es optativo y no cambia campos existentes. El validador exige campos conocidos, enums, textos no vacíos y coordenadas focales numéricas entre 0 y 1. El origen del cuadro es arriba a la izquierda; `x` crece hacia la derecha y `y` hacia abajo. Son proporciones del encuadre, no coordenadas 3D. `axis_id` nombra una relación narrativa compartida, no un eje calculado. Los valores describen intención aproximada; no sustituyen una cámara configurada ni una revisión de imagen.

## Tamaño, ángulo y perspectiva

- Elige tamaño por la información que debe leerse: `extreme-wide`/`wide` para geografía y escala; `full` para postura y desplazamiento; `medium` para interacción; `close-up` para reacción; `extreme-close-up`/`insert` para pista o detalle. No cambies tamaño si no cambia la información dominante.
- Elige ángulo por relación dramática y visibilidad: nivel de ojos para una relación neutral; bajo para peso/amenaza; alto para vulnerabilidad o geografía; cenital para patrón espacial; inclinado (`dutch`) solo cuando desorientación sea intención explícita.
- Describe lente como intención, no como milímetros universales: distancia focal, distancia de cámara y tamaño del sensor/viewport cambian juntos la perspectiva aparente. Gran angular cercano exagera rasgos y profundidad; teleobjetivo comprime distancia. Para retrato adulto, empieza con perspectiva moderada y cámara separada; revisa bordes, manos y cara en el encuadre final.
- Si el adaptador requiere FOV, especifica primero intención amplia/media/estrecha: amplio para incluir geografía, estrecho para aislar/comprimir; anota qué debe entrar en cuadro. Fija un valor numérico solo cuando renderer, sensor/viewport y escala estén definidos; inspecciona el 9:16 final para detectar distorsión. No cambies FOV durante la toma salvo que el cambio tenga propósito narrativo.
- Planifica primero la cámara en el viewport 9:16. No compongas en horizontal para recortar después. Si aún no hay escala de escena, registra intención (p. ej., “perspectiva moderada, sin distorsión de bordes”), no FOV ni posiciones de mundo inventadas.
- Revisión: dibuja el cuadro vertical con sujeto y pista en su escala prevista. Confirma que se leen relación espacial, acción y dirección de mirada sin depender de un paneo que no está especificado.

## Composición vertical y jerarquía

- Define un solo primer punto de atención por beat. Nómbralo como sujeto/pista focal y coloca su centro aproximado en `focal_point`; usa el texto de acción para indicar qué lo conecta con el personaje.
- Asigna zonas distintas al personaje y a la pista cuando deban leerse a la vez. Evita que cabeza, mano, haz de linterna o silueta oculten el objeto. Usa mirada, postura, borde de luz o líneas del entorno para guiar la lectura entre ambos.
- Usa profundidad vertical con intención: primer plano para pista/contexto, medio para acción y fondo para origen/destino. No llenes el alto con roca oscura que no aporta escala, geografía, amenaza ni pausa deliberada.
- Declara qué información debe permanecer visible y despejada. Mantén caras, manos, pista y dirección de movimiento dentro del cuadro; reserva la interfaz y subtítulos según la plataforma objetivo. Si la zona segura exacta no se conoce, anota esa incertidumbre y revisa en la interfaz de destino antes de aprobar.
- Contrasta el sujeto y la pista con el fondo por silueta, valor, color o luz motivada; evita sumar adornos que compitan con el punto focal.
- Revisión a tamaño de teléfono: mira cada still completo, en orientación vertical y a tamaño de visualización real. Pregunta “¿qué miré primero?”, “¿qué hizo el personaje?” y “¿qué pista entendí?”. Si la respuesta necesita leer la ficha, reajusta tamaño, separación, luz o encuadre.

## Eje y continuidad

- Antes del primer corte, define un eje simple entre personaje, pista y destino. Asigna el mismo `axis_id` a planos que mantienen esa relación. Describe su orientación en términos visibles (por ejemplo, “exploradora a la izquierda, túnel a la derecha”), no con coordenadas de mundo.
- Mantén dirección de pantalla del desplazamiento y de la mirada entre planos. Si la exploradora entra hacia la derecha, el contraplano debe conservar hacia dónde avanza o establecer claramente que cambió de lado.
- En un diálogo o mirada-pista, enlaza la mirada del plano del personaje con la posición del objeto en el siguiente. No hagas que la mirada salte de lado sin mostrar giro, cambio de eje o plano neutral.
- No cruces el eje de 180° sin motivo visible. Para cruzarlo, muestra un movimiento que atraviesa el eje, usa un plano neutral sobre el eje, o corta a una vista que vuelva a establecer geografía antes de continuar.
- Anota dirección principal por plano (`left-to-right`, `right-to-left`, `toward-camera`, `away-from-camera` o `static`) y dirección de mirada. Si la acción está quieta, usa `static`; no inventes movimiento para llenar la ficha.
- Revisión: dibuja planta simple de personaje, pista, cámara y eje; compara entrada/salida de cuadro y miradas plano a plano. El diagrama es orientación, no bloqueo 3D.

## Movimiento de cámara y transición

- Da a cada movimiento una causa y un destino: seguir un paso, acompañar un giro, revelar una pista o acercarse a una reacción. Escribe punto inicial, dirección, punto de llegada y qué debe descubrir el público.
- Especifica qué inicia y detiene el movimiento (acción, mirada, cambio de información). “Paneo lento” sin motivación, recorrido ni llegada no basta.
- Mantén el movimiento lo bastante simple para que la acción siga siendo dominante. Si la cámara puede estar fija mientras actor o luz revelan la pista, prefiere el plano fijo.
- Justifica cada corte por cambio de escala/información, respuesta, dirección de mirada, elipsis o contraste. Registra la razón en `transition_reason`; conserva dirección y gesto durante el corte cuando se espere continuidad.
- En empalmes de movimiento, compara dirección, velocidad aparente, lado de pantalla y fase de acción. Si no se pueden evaluar aún, marca continuidad temporal como pendiente hasta reproducir una secuencia.

## Luz narrativa

- Para cada plano nombra la fuente visible o inferible (`narrative_light_source`), su ubicación/dirección relativa, qué revela y qué deja oculto. Linterna debe apuntar desde el personaje y responder a su orientación; no ilumines objetos distantes sin explicar rebote u otra fuente.
- Separa función de luz principal y relleno. La principal modela sujeto/pista; rebote o ambiente tenue conserva información en sombra sin borrar la dirección de la fuente.
- Usa borde de silueta solo si cabe una causa (rebote de pared, abertura, agua u otra fuente). Evita rim light arbitraria que parezca desconectada de la cueva.
- Mantén detalle suficiente para reconocer silueta, superficie y pista. La oscuridad puede ocultar identidad, pero no debe borrar la acción necesaria para entender el misterio.
- Revisión: sigue visualmente la luz desde su fuente hasta el objeto; comprueba que punto focal recibe prioridad, que negro conserva contorno y que luces altas no borran textura importante. La descripción solo permite revisar intención; exposición y legibilidad requieren still.

## Errores comunes y corrección

| Falla observable | Corrección de planificación | Revisión |
| --- | --- | --- |
| Negro vacío sin información | Recupera silueta/contexto con rebote motivado o recorta el encuadre; conserva negro solo si su vacío crea expectativa deliberada | Still de teléfono: ¿el vacío señala un destino o solo elimina lectura? |
| Personaje visible, mirada ambigua | Nombra la pista, alinea mirada/gesto/haz y despeja la línea visual | Still: identifica primero el punto focal sin consultar texto |
| Objeto importante no destaca | Aísla su silueta, aumenta tamaño o contraste y reduce competencia del fondo | Still: objeto identificable al primer vistazo |
| Luz sin fuente o sin dirección | Declara fuente, orientación, rebote y objeto que revela | Still: sigue el recorrido fuente-sujeto-pista |
| Jerarquía débil en 9:16 | Un foco por beat; separa actor y pista verticalmente; revisa bordes e interfaz | Still en viewport 9:16 a tamaño real |
| Eje o mirada invertidos | Reestablece el mismo `axis_id`/dirección o añade giro/puente visible | Secuencia: sigue izquierda/derecha y línea de mirada a través del corte |
| Cámara se mueve sin razón | Nombra disparador y destino o fija cámara | Secuencia: verifica que acción sigue dominando |

## Tiempo y puerta

Usa intervalos semiabiertos `[start_frame, end_frame)`, tasa única, primer plano desde 0, sin huecos ni solapes, y `total_frames` igual al último final. La validación automática comprueba el contrato y cronología; no aprueba composición, luz, puesta en escena, continuidad percibida ni calidad cinematográfica.

Antes de aprobar storyboard, recorre cada plano sin narración: sujeto primero, acción, información nueva, fuente de luz y conexión con corte siguiente deben poder describirse sin contradicción. La aprobación visual espera stills; la aprobación de movimiento espera secuencia reproducida. Registra esos estados por separado.
