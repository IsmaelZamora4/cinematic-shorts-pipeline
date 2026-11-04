# Integración con Remotion y 3D

Lee esto solo si el plan usa Remotion. El proyecto auditado usa Remotion 4.0.540, React 19.3.1, `@remotion/three` 4.0.540, React Three Fiber 9.8.2 y Three.js 0.187.0; vuelve a revisar el manifiesto activo antes de confiar en estas versiones.

Usa las skills instaladas de Remotion y la documentación oficial como autoridad para las API vigentes. El patrón local validado deriva el movimiento del fotograma actual de Remotion dentro de `<ThreeCanvas>`; evita relojes reales, aleatoriedad sin semilla y el bucle en tiempo real `useFrame()` de React Three Fiber cuando se busca render determinista. Reutiliza geometría/materiales y separa datos de escena de audio/subtítulos. Confirma el comportamiento con la documentación de la versión instalada de `@remotion/three` antes de adoptarlo.

Planifica por separado un fotograma representativo de baja resolución y la composición final. La vista previa sirve para iterar, no demuestra que el render final quepa en la memoria de la GPU. Registra dimensiones, FPS, duración, rutas de recursos, concurrencia y destino de salida. Revisa una imagen fija o Studio antes de cualquier render de video solicitado.

Fuentes oficiales: [integración Three.js](https://www.remotion.dev/docs/three), [ThreeCanvas](https://www.remotion.dev/docs/three-canvas), [comando still](https://www.remotion.dev/docs/cli/still). Consulta la documentación de la versión instalada; las URL pueden cambiar.
