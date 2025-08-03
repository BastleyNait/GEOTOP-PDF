# GEOTOP Flask Uploader

Aplicación Flask para subir y gestionar archivos PDF con códigos QR en Backblaze B2.

## Despliegue en Render

### Pasos para desplegar:

1. **Subir el código a GitHub:**
   - Crear un repositorio en GitHub
   - Subir todos los archivos del proyecto
   - **IMPORTANTE:** No subir el archivo `.env` (ya está en .gitignore)

2. **Configurar en Render:**
   - Ir a [render.com](https://render.com)
   - Conectar tu cuenta de GitHub
   - Crear un nuevo "Web Service"
   - Seleccionar tu repositorio
   - Render detectará automáticamente que es una aplicación Python

3. **Variables de entorno en Render:**
   Configurar las siguientes variables de entorno en el dashboard de Render:
   ```
   B2_ACCESS_KEY_ID=tu_access_key_id
   B2_SECRET_ACCESS_KEY=tu_secret_access_key
   B2_BUCKET_NAME=GEOTOP-1
   B2_ENDPOINT=https://s3.us-east-005.backblazeb2.com
   B2_REGION=us-east-005
   FLASK_SECRET_KEY=tu_clave_secreta_segura
   ```

4. **Configuración automática:**
   - Render usará el `Procfile` para saber cómo ejecutar la app
   - Instalará las dependencias desde `requirements.txt`
   - Usará Python 3.8.18 según `runtime.txt`

### Archivos importantes para el despliegue:

- `Procfile`: Define cómo ejecutar la aplicación
- `requirements.txt`: Lista de dependencias
- `runtime.txt`: Versión de Python
- `render.yaml`: Configuración específica de Render
- `.gitignore`: Archivos a ignorar en el repositorio

### Notas:

- La aplicación se ejecutará en el puerto que Render asigne automáticamente
- El modo debug está deshabilitado para producción
- Asegúrate de configurar todas las variables de entorno antes del despliegue