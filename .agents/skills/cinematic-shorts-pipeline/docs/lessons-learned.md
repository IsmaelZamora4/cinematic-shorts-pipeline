# Hallazgos de la auditoría del proyecto

Resumen de solo lectura de archivos VideoIA existentes, 2026-11-04. Orienta valores iniciales, pero no autoriza editar ni producir dentro de ese repositorio.

- La dirección actual busca exploración low-poly seria y contenida, proporciones adultas, ropa funcional, geología asimétrica y luz motivada. En el proyecto se consideró que el Ranger KayKit era infantil/de fantasía para este objetivo; permanece como prueba técnica de carga/animación, no como dirección final.
- Un piloto breve propone tres planos: establecer el lugar, mostrar un hallazgo legible y terminar con reacción/decisión. La pista debe verse y los subtítulos no deben taparla.
- Remotion local fija 4.0.540, React 19.3.1, R3F 9.8.2 y Three.js 0.187.0. Sus notas derivan el movimiento del fotograma de composición y evitan `useFrame()` en animación destinada al render determinista.
- El proyecto usa Kokoro local con `em_alex` por defecto. Sus marcas de palabra actuales son aproximadas; medir duración real y confirmar alineación antes de afirmar sincronía final.
- Las notas locales de Blender sugieren vistas Eevee económicas, salidas pequeñas durante desarrollo y medición explícita del presupuesto de GPU. La caminata procedural existente es una prueba técnica, no actuación terminada.
- Las instrucciones del proyecto limitan stills, renders, APIs de pago, descargas, publicación y cambios en proyectos ajenos.

## Evidencia estática del piloto Quaternius

**Procedencia y límite:** observaciones reportadas por el usuario a partir de capturas estáticas. Las capturas no están incluidas en este paquete ni se inspeccionaron independientemente durante esta auditoría. Ver el registro y criterios operativos en [la rúbrica cinematográfica](../references/cinematic-quality-rubric.md).

- El modelo Quaternius tiene proporciones adultas que encajan mejor con la estética buscada.
- El primer plano deja oscuridad sin información narrativa.
- El segundo plano no explica claramente qué observa el protagonista.
- El tercero no destaca el descubrimiento.
- Hace falta iluminación motivada y una jerarquía visual clara.
- Cada plano debe aportar una acción o información narrativa.
- Las imágenes fijas no permiten evaluar fluidez, deslizamiento de pies ni continuidad temporal.

Estos hallazgos guían revisión futura; no prueban compatibilidad de rig, licencia, movimiento natural ni calidad final.
