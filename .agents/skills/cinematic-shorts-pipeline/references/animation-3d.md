# Recursos 3D y animación

Elige Blender, Three.js/React Three Fiber en navegador o un híbrido medido según la escena y estructura existente. No migres recursos entre motores sin un beneficio que compense conversión y coordinación.

## Aceptación de recursos

Confirma formato (por ejemplo GLB/glTF), escala y ejes, materiales, tamaño de texturas, complejidad de malla, esqueleto, pesos, clips, licencia y fuente. Prueba el recurso exacto en una vista previa desechable cuando esté autorizado. La etiqueta “rig humanoide” no demuestra compatibilidad: verifica nombres/jerarquía de huesos y movimiento, o planifica retargeting.

Prefiere un rig coherente y actuación contenida a una gran cantidad de recursos. Proporciones adultas, ropa funcional, peso, apoyo del pie, movimiento secundario discreto y reacciones motivadas importan más que el detalle geométrico. En cuevas y espacios naturales, varía sección, techo, derrumbes, erosión y escala; evita módulos de túnel repetidos salvo necesidad narrativa.

## Plan de animación

Especifica pose/acción por plano, estado inicial/final, desplazamiento raíz, apoyos, mirada, manos/props, anticipación, reacción y transición. Indica si el movimiento es animado a mano, procedural, de un clip o retargeteado. Planifica limpieza y continuidad. Un ciclo de caminata por sí solo no garantiza actuación natural.

## Notas por motor

- **Blender:** conviene si son centrales la creación de escena, rigging, geometría procedural o secuencias offline. Empieza con fotogramas Eevee económicos; mide antes de fijar ajustes finales.
- **Remotion + Three.js/R3F:** conviene si importan la composición React direccionable por fotograma y la integración de subtítulos/audio. Mantén el 3D determinista desde el fotograma de composición y acota el trabajo por cuadro.
- **Híbrido:** úsalo solo con responsabilidades claras (por ejemplo, Blender produce secuencia y Remotion controla edición, audio y subtítulos). Registra dimensiones, FPS, alpha/color, almacenamiento y coste de regeneración.

Son criterios de selección, no promesas sobre disponibilidad de funciones/versiones. Verifica motores instalados y documentación oficial antes de implementar.
