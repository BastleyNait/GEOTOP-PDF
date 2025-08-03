from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import os
import uuid
import logging
import re
import boto3
from botocore.client import Config
from datetime import datetime
import qrcode
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.errors import PdfReadError
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from io import BytesIO
import time
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Configurar el registro
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('backblaze_uploader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('backblaze_uploader')

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))  # Clave secreta para mensajes flash

# Configuración para Backblaze B2 (S3 compatible) - usando variables de entorno
B2_ACCESS_KEY_ID = os.getenv('B2_ACCESS_KEY_ID')
B2_SECRET_ACCESS_KEY = os.getenv('B2_SECRET_ACCESS_KEY')
B2_BUCKET_NAME = os.getenv('B2_BUCKET_NAME')
B2_ENDPOINT = os.getenv('B2_ENDPOINT')
B2_REGION = os.getenv('B2_REGION')

# Verificar que todas las variables de entorno estén configuradas
required_env_vars = ['B2_ACCESS_KEY_ID', 'B2_SECRET_ACCESS_KEY', 'B2_BUCKET_NAME', 'B2_ENDPOINT', 'B2_REGION']
for var in required_env_vars:
    if not os.getenv(var):
        logger.error(f"Variable de entorno requerida no encontrada: {var}")
        raise ValueError(f"Variable de entorno requerida no encontrada: {var}")

# Configuración de carpetas
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB máximo

# Manejador de error para archivos demasiado grandes
@app.errorhandler(413)
def request_entity_too_large(error):
    """Maneja el error cuando el archivo es demasiado grande"""
    flash('El archivo es demasiado grande. El tamaño máximo permitido es 16MB.', 'error')
    return redirect(url_for('index'))

# Filtro para formatear timestamps como fechas
@app.template_filter('datetime')
def format_datetime(timestamp):
    try:
        if not timestamp:
            return "no existe"
        timestamp = float(timestamp)
        if timestamp > 1e12:  # si está en milisegundos
            timestamp = timestamp / 1000
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%d/%m/%Y %H:%M:%S')
    except (OSError, ValueError, TypeError):
        return f"Fecha inválida ({timestamp})"

def get_s3_client():
    """
    Crea y devuelve un cliente S3 para Backblaze B2
    """
    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=B2_ENDPOINT,
            aws_access_key_id=B2_ACCESS_KEY_ID,
            aws_secret_access_key=B2_SECRET_ACCESS_KEY,
            region_name=B2_REGION,
            config=Config(signature_version='s3v4')
        )
        return s3_client
    except Exception as e:
        logger.error(f"Error al crear cliente S3: {str(e)}")
        return None
ex = 490
ey = 665
qr_size = 55
# qr_size = 58
def add_qr_to_pdf(input_pdf_path, output_pdf_path, qr_url, x=ex, y=ey):
    """
    Añade un código QR a un PDF existente en la posición especificada.
    
    CONFIGURACIÓN DE POSICIÓN DEL QR:
    ================================
    Actualmente configurado para: PRIMERA PÁGINA (OPCIÓN 1)
    
    Para cambiar la posición del QR:
    1. PRIMERA PÁGINA (ACTUAL): Mantener OPCIÓN 1 activa, comentar OPCIÓN 2 y 3
    2. ÚLTIMA PÁGINA (SIMPLE): Comentar OPCIÓN 1, descomentar OPCIÓN 2
    3. ÚLTIMA PÁGINA (PERSONALIZABLE): Comentar OPCIÓN 1 y 2, descomentar OPCIÓN 3
    
    NOTA: Solo afecta al PDF que se sube a la nube, NO al PDF en blanco.
    
    Args:
        input_pdf_path: Ruta al archivo PDF original
        output_pdf_path: Ruta donde se guardará el PDF con el QR
        qr_url: URL para generar el código QR
        x: Posición X del código QR en el PDF (desde la izquierda) - Solo para OPCIÓN 1
        y: Posición Y del código QR en el PDF (desde abajo) - Solo para OPCIÓN 1
    """
    qr_img_path = None
    try:
        # Generar el código QR
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=0,
        )
        qr.add_data(qr_url)
        qr.make(fit=True)
        
        # Crear una imagen QR con fondo transparente
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.convert("RGBA")
        
        # Hacer que el fondo sea transparente (convertir el blanco a transparente)
        datas = qr_img.getdata()
        new_data = []
        for item in datas:
            # Si el pixel es blanco (255, 255, 255), hacerlo transparente
            if item[0] == 255 and item[1] == 255 and item[2] == 255:
                new_data.append((255, 255, 255, 0))  # RGBA con alpha=0 (transparente)
            else:
                new_data.append(item)
        
        qr_img.putdata(new_data)
        
        # Guardar la imagen temporal
        qr_img_path = os.path.join(UPLOAD_FOLDER, f"qr_{uuid.uuid4()}.png")
        qr_img.save(qr_img_path, format="PNG")
        
        # Obtener las dimensiones del PDF original para usar el mismo pagesize
        page_size = letter  # Valor por defecto
        try:
            with open(input_pdf_path, 'rb') as file:
                reader = PdfReader(file)
                if len(reader.pages) > 0:
                    page = reader.pages[0]
                    mediabox = page.mediabox
                    page_width = float(mediabox.width)
                    page_height = float(mediabox.height)
                    page_size = (page_width, page_height)
                    logger.info(f"Usando dimensiones del PDF original: {page_width} x {page_height} puntos")
        except Exception as e:
            logger.warning(f"No se pudieron extraer dimensiones del PDF, us ando Letter: {str(e)}")
        
        # Crear un PDF temporal con el código QR usando las mismas dimensiones
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=page_size)
        can.drawImage(qr_img_path, ex, ey, qr_size, qr_size, mask='auto')  # mask='auto' para respetar la transparencia
        can.save()
        
        # Mover al inicio del BytesIO
        packet.seek(0)
        
        # Leer el PDF original
        with open(input_pdf_path, "rb") as input_file:
            existing_pdf = PdfReader(input_file)
            output = PdfWriter()
            
            # Añadir el código QR a cada página
            for i in range(len(existing_pdf.pages)):
                page = existing_pdf.pages[i]

                # OPCIÓN 1: QR en la primera página (ACTUAL - ACTIVO)
                # Solo añadir el QR a la primera página
                if i == 0:
                    # Crear un nuevo PDF con el QR
                    watermark = PdfReader(packet)
                    page.merge_page(watermark.pages[0])
            
                # OPCIÓN 2: QR en la última página (COMENTADO - LISTO PARA ACTIVAR)
                # Descomenta las siguientes líneas y comenta la OPCIÓN 1 para usar esta funcionalidad
                # if i == len(existing_pdf.pages) - 1:  # Última página
                #     # Crear un nuevo PDF con el QR
                #     watermark = PdfReader(packet)
                #     page.merge_page(watermark.pages[0])
                
                # OPCIÓN 3: QR en la última página con posición personalizable (COMENTADO)
                # Esta opción permite cambiar fácilmente la posición del QR en la última página
                # Posiciones comunes (ajustar según las dimensiones del PDF):
                # - Esquina superior derecha: x=460, y=750
                # - Esquina inferior derecha: x=460, y=50
                # - Esquina inferior izquierda: x=50, y=50
                # - Centro inferior: x=280, y=50
                # if i == len(existing_pdf.pages) - 1:  # Última página
                #     # Crear un nuevo PDF con el QR en posición personalizada
                #     packet_custom = BytesIO()
                #     can_custom = canvas.Canvas(packet_custom, pagesize=page_size)  # Usar las mismas dimensiones
                #     # Cambiar las coordenadas x, y según la posición deseada:
                #     can_custom.drawImage(qr_img_path, x, y, width=qr_size, height=qr_size, mask='auto')  # Usar tamaño consistente
                #     can_custom.save()
                #     packet_custom.seek(0)
                #     watermark_custom = PdfReader(packet_custom)
                #     page.merge_page(watermark_custom.pages[0])
                
                output.add_page(page)
            
            # Guardar el resultado
            with open(output_pdf_path, "wb") as output_stream:
                output.write(output_stream)
        
        return True
    except Exception as e:
        logger.error(f"Error al añadir QR al PDF: {str(e)}")
        return False
    finally:
        # Eliminar la imagen temporal del QR
        if qr_img_path and os.path.exists(qr_img_path):
            try:
                os.remove(qr_img_path)
            except Exception as e:
                logger.warning(f"No se pudo eliminar la imagen temporal del QR: {str(e)}")

def create_blank_pdf_with_qr(qr_url, output_path, original_pdf_path=None):
    """
    Crea un PDF en blanco con un QR en la posición específica del certificado.
    Usa exactamente los mismos parámetros del QR que el certificado (con fondo transparente).
    Si se proporciona original_pdf_path, extrae las dimensiones exactas de ese PDF.
    """
    qr_img_path = None
    try:
        # Determinar el tamaño de página
        page_size = A4  # Valor por defecto
        
        # Si se proporciona el PDF original, extraer sus dimensiones exactas
        if original_pdf_path and os.path.exists(original_pdf_path):
            try:
                with open(original_pdf_path, 'rb') as file:
                    reader = PdfReader(file)
                    if len(reader.pages) > 0:
                        # Obtener las dimensiones de la primera página
                        page = reader.pages[0]
                        mediabox = page.mediabox
                        # Convertir de puntos PDF a tupla (ancho, alto)
                        page_width = float(mediabox.width)
                        page_height = float(mediabox.height)
                        page_size = (page_width, page_height)
                        logger.info(f"Dimensiones extraídas del PDF original: {page_width} x {page_height} puntos")
            except Exception as e:
                logger.warning(f"No se pudieron extraer dimensiones del PDF original: {str(e)}, usando A4")
        
        # Generar el código QR exactamente igual que en add_qr_to_pdf
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=0,
        )
        qr.add_data(qr_url)
        qr.make(fit=True)
        
        # Crear una imagen QR con fondo transparente (igual que en el certificado)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.convert("RGBA")
        
        # Hacer que el fondo sea transparente (convertir el blanco a transparente)
        datas = qr_img.getdata()
        new_data = []
        for item in datas:
            # Si el pixel es blanco (255, 255, 255), hacerlo transparente
            if item[0] == 255 and item[1] == 255 and item[2] == 255:
                new_data.append((255, 255, 255, 0))  # RGBA con alpha=0 (transparente)
            else:
                new_data.append(item)
        
        qr_img.putdata(new_data)
        
        # Guardar la imagen temporal
        qr_img_path = os.path.join(os.path.dirname(output_path), f"temp_qr_blank_{uuid.uuid4()}.png")
        qr_img.save(qr_img_path, format="PNG")
        
        try:
            # Crear PDF en blanco con las dimensiones exactas del original
            can = canvas.Canvas(output_path, pagesize=page_size)
            
            # Usar exactamente las mismas coordenadas y tamaño que en add_qr_to_pdf
            # Variables globales: ex=498, ey=622, qr_size=52
            
            # Dibujar el QR en el PDF con transparencia (igual que en el certificado)
            can.drawImage(qr_img_path, ex, ey, qr_size, qr_size, mask='auto')
            can.save()
            
            logger.info(f"PDF en blanco con QR creado exitosamente: {output_path}")
            return True
            
        finally:
            # Limpiar archivo temporal del QR
            if qr_img_path and os.path.exists(qr_img_path):
                try:
                    os.remove(qr_img_path)
                except Exception as e:
                    logger.warning(f"No se pudo eliminar QR temporal: {str(e)}")
                    
    except Exception as e:
        logger.exception(f"Error al crear PDF en blanco con QR: {str(e)}")
        return False

def upload_to_backblaze(file_path, original_filename=None, folder="certificados"):
    """
    Sube un archivo a Backblaze B2 usando la API S3 compatible y devuelve la URL pública.
    Si el archivo es un PDF, añade un código QR antes de subirlo.
    """
    logger.info(f"Iniciando carga de archivo: {file_path} en carpeta: {folder}")
    
    # Obtener la extensión del archivo
    extension = os.path.splitext(file_path)[1].lower()
    
    # Asegurar que la extensión sea segura
    if not extension or len(extension) > 5:
        extension = ".pdf"
    
    # Usar el nombre original del archivo si está disponible, sino generar uno único
    if original_filename:
        # Eliminar la extensión del nombre original si existe
        base_name = os.path.splitext(original_filename)[0]
        # Limpiar el nombre para que sea seguro para URL
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', base_name)
        # Crear un nombre de archivo seguro con la extensión correcta
        unique_filename = f"{safe_name}{extension}"
    else:
        # Si no hay nombre original, generar un UUID
        unique_id = str(uuid.uuid4()).replace("-", "")
        unique_filename = f"{unique_id}{extension}"
    
    # Si se especificó una carpeta, usar una versión segura
    if folder:
        safe_folder = re.sub(r'[^a-zA-Z0-9]', '_', folder)
        file_key = f"{safe_folder}/{unique_filename}"
    else:
        file_key = unique_filename
    
    # Eliminar cualquier carácter especial o espacio
    file_key = re.sub(r'[^a-zA-Z0-9_./]', '_', file_key)
    # Asegurarse de que no haya espacios
    file_key = file_key.replace(' ', '_')
    logger.debug(f"Nombre de archivo generado: {file_key}")
    
    pdf_with_qr_path = None
    try:
        # Obtener cliente S3
        s3_client = get_s3_client()
        if not s3_client:
            return None, "Error al conectar con Backblaze B2"
        
        # Determinar el tipo de contenido basado en la extensión
        content_type = "application/pdf"
        if extension.lower() == '.jpg' or extension.lower() == '.jpeg':
            content_type = "image/jpeg"
        elif extension.lower() == '.png':
            content_type = "image/png"
        
        # Generar la URL pública anticipadamente para el código QR
        public_url = f"{B2_ENDPOINT}/{B2_BUCKET_NAME}/{file_key}"
        
        # Si es un PDF, añadir el código QR
        upload_file_path = file_path  # Por defecto, usar el archivo original
        if extension.lower() == '.pdf':
            # Crear un archivo temporal para el PDF con QR usando el mismo nombre para mantener consistencia
            pdf_with_qr_path = os.path.join(UPLOAD_FOLDER, f"qr_{unique_filename}")
            
            # Añadir el código QR al PDF
            qr_added = add_qr_to_pdf(file_path, pdf_with_qr_path, public_url)
            
            if qr_added:
                # Usar el nuevo archivo con QR para subir
                upload_file_path = pdf_with_qr_path
                logger.debug(f"QR añadido al PDF exitosamente: {pdf_with_qr_path}")
            else:
                # Si hubo un error al añadir el QR, usar el archivo original
                logger.warning("No se pudo añadir el QR al PDF, usando archivo original")
        
        # Pequeño retraso para asegurar que el archivo esté listo
        time.sleep(0.2)
        
        # Subir el archivo
        with open(upload_file_path, 'rb') as file_data:
            s3_client.upload_fileobj(
                file_data, 
                B2_BUCKET_NAME, 
                file_key,
                ExtraArgs={
                    'ContentType': content_type
                }
            )
        
        logger.debug("Archivo subido exitosamente")
        logger.info(f"URL pública generada: {public_url}")
        
        return public_url, None
    
    except Exception as e:
        error_msg = f"Error al subir a Backblaze B2: {str(e)}"
        logger.exception(error_msg)
        return None, error_msg
    finally:
        # Eliminar el archivo temporal con QR si existe
        if pdf_with_qr_path and os.path.exists(pdf_with_qr_path) and pdf_with_qr_path != file_path:
            try:
                # Pequeño retraso antes de intentar eliminar
                time.sleep(0.2)
                os.remove(pdf_with_qr_path)
                logger.debug(f"Archivo temporal con QR eliminado: {pdf_with_qr_path}")
            except Exception as e:
                logger.warning(f"No se pudo eliminar el archivo temporal con QR: {str(e)}")

def list_files_in_bucket(prefix=None):
    """
    Lista todos los archivos en el bucket de Backblaze B2.
    Si se proporciona un prefijo, solo muestra los archivos que comienzan con ese prefijo.
    """
    try:
        # Obtener cliente S3
        s3_client = get_s3_client()
        if not s3_client:
            return None, "Error al conectar con Backblaze B2"
        
        # Parámetros para listar objetos
        params = {'Bucket': B2_BUCKET_NAME}
        if prefix:
            params['Prefix'] = prefix
        
        # Listar objetos
        response = s3_client.list_objects_v2(**params)
        
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                # Usar solo la información básica de list_objects_v2 sin head_object
                file_key = obj['Key']
                
                files.append({
                    'name': file_key,
                    'id': obj['ETag'].strip('"'),
                    'size': obj['Size'],
                    'upload_timestamp': int(obj['LastModified'].timestamp() * 1000),  # Convertir a milisegundos
                    'url': f"{B2_ENDPOINT}/{B2_BUCKET_NAME}/{file_key}"
                })
        
        return files, None
    except Exception as e:
        error_msg = f"Error al listar archivos: {str(e)}"
        logger.exception(error_msg)
        return None, error_msg

def get_folders_structure():
    """
    Obtiene la estructura de carpetas basada en los archivos existentes en el bucket.
    Retorna un diccionario con carpetas y sus archivos.
    """
    try:
        files, error = list_files_in_bucket()
        if error:
            return None, error
        
        folders = {}
        
        for file in files:
            file_path = file['name']
            
            # Separar la ruta en partes
            path_parts = file_path.split('/')
            
            if len(path_parts) > 1:
                # Archivo está en una carpeta
                folder_path = '/'.join(path_parts[:-1])
                filename = path_parts[-1]
                
                # Crear la carpeta aunque esté vacía
                if folder_path not in folders:
                    folders[folder_path] = {
                        'name': folder_path,
                        'files': [],
                        'subfolders': set()
                    }
                
                # Filtrar archivos .folder_placeholder pero mantener la carpeta
                if filename == '.folder_placeholder':
                    continue
                
                # Añadir archivo a la carpeta
                file_copy = file.copy()
                file_copy['filename'] = filename
                folders[folder_path]['files'].append(file_copy)
                
                # Procesar subcarpetas
                folder_parts = folder_path.split('/')
                for i in range(len(folder_parts)):
                    parent_path = '/'.join(folder_parts[:i+1])
                    if parent_path not in folders:
                        folders[parent_path] = {
                            'name': parent_path,
                            'files': [],
                            'subfolders': set()
                        }
                    
                    if i > 0:
                        parent_parent = '/'.join(folder_parts[:i])
                        folders[parent_parent]['subfolders'].add(parent_path)
            else:
                # Archivo en la raíz
                filename = file_path
                
                # Filtrar archivos .folder_placeholder
                if filename == '.folder_placeholder':
                    continue
                
                if 'root' not in folders:
                    folders['root'] = {
                        'name': 'root',
                        'files': [],
                        'subfolders': set()
                    }
                file_copy = file.copy()
                file_copy['filename'] = file_path
                folders['root']['files'].append(file_copy)
        
        # Convertir sets a listas para JSON serialization
        for folder in folders.values():
            folder['subfolders'] = list(folder['subfolders'])
        
        return folders, None
    except Exception as e:
        error_msg = f"Error al obtener estructura de carpetas: {str(e)}"
        logger.exception(error_msg)
        return None, error_msg

def create_folder(folder_path):
    """
    Crea una carpeta virtual en Backblaze B2 subiendo un archivo placeholder.
    """
    try:
        # Limpiar y validar el nombre de la carpeta
        folder_path = folder_path.strip()
        if not folder_path:
            return False, "El nombre de la carpeta no puede estar vacío"
        
        # Limpiar caracteres especiales
        folder_path = re.sub(r'[^a-zA-Z0-9_\-/]', '_', folder_path)
        
        # Asegurar que no termine con /
        folder_path = folder_path.rstrip('/')
        
        # Obtener cliente S3
        s3_client = get_s3_client()
        if not s3_client:
            return False, "Error al conectar con Backblaze B2"
        
        # Crear un archivo placeholder para la carpeta
        placeholder_key = f"{folder_path}/.folder_placeholder"
        
        # Verificar si la carpeta ya existe
        try:
            s3_client.head_object(Bucket=B2_BUCKET_NAME, Key=placeholder_key)
            return False, "La carpeta ya existe"
        except:
            pass  # La carpeta no existe, podemos crearla
        
        # Subir el archivo placeholder
        s3_client.put_object(
            Bucket=B2_BUCKET_NAME,
            Key=placeholder_key,
            Body=b'',
            ContentType='text/plain'
        )
        
        logger.info(f"Carpeta creada: {folder_path}")
        return True, None
        
    except Exception as e:
        error_msg = f"Error al crear carpeta: {str(e)}"
        logger.exception(error_msg)
        return False, error_msg

def delete_folder(folder_path):
    """
    Elimina una carpeta y todos sus archivos de Backblaze B2.
    """
    try:
        # Obtener cliente S3
        s3_client = get_s3_client()
        if not s3_client:
            return False, "Error al conectar con Backblaze B2"
        
        # Listar todos los objetos en la carpeta
        response = s3_client.list_objects_v2(
            Bucket=B2_BUCKET_NAME,
            Prefix=f"{folder_path}/"
        )
        
        if 'Contents' not in response:
            return False, "La carpeta no existe o está vacía"
        
        # Eliminar todos los objetos
        objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
        
        if objects_to_delete:
            s3_client.delete_objects(
                Bucket=B2_BUCKET_NAME,
                Delete={'Objects': objects_to_delete}
            )
        
        logger.info(f"Carpeta eliminada: {folder_path}")
        return True, None
        
    except Exception as e:
        error_msg = f"Error al eliminar carpeta: {str(e)}"
        logger.exception(error_msg)
        return False, error_msg

def move_file(old_path, new_path):
    """
    Mueve un archivo de una ubicación a otra en Backblaze B2.
    """
    try:
        # Obtener cliente S3
        s3_client = get_s3_client()
        if not s3_client:
            return False, "Error al conectar con Backblaze B2"
        
        # Copiar el archivo a la nueva ubicación
        copy_source = {'Bucket': B2_BUCKET_NAME, 'Key': old_path}
        s3_client.copy_object(
            CopySource=copy_source,
            Bucket=B2_BUCKET_NAME,
            Key=new_path
        )
        
        # Eliminar el archivo original
        s3_client.delete_object(
            Bucket=B2_BUCKET_NAME,
            Key=old_path
        )
        
        logger.info(f"Archivo movido de {old_path} a {new_path}")
        return True, None
        
    except Exception as e:
        error_msg = f"Error al mover archivo: {str(e)}"
        logger.exception(error_msg)
        return False, error_msg

def delete_file(file_name):
    """
    Elimina un archivo del bucket de Backblaze B2.
    """
    try:
        # Obtener cliente S3
        s3_client = get_s3_client()
        if not s3_client:
            return False, "Error al conectar con Backblaze B2"
        
        # Eliminar el objeto
        s3_client.delete_object(
            Bucket=B2_BUCKET_NAME,
            Key=file_name
        )
        
        logger.info(f"Archivo {file_name} eliminado exitosamente")
        return True, None
    
    except Exception as e:
        error_msg = f"Error al eliminar archivo: {str(e)}"
        logger.exception(error_msg)
        return False, error_msg

@app.route('/')
def index():
    return render_template('index.html')

def merge_pdfs(pdf_paths, output_path):
    """
    Combina múltiples archivos PDF en uno solo.
    
    Args:
        pdf_paths: Lista de rutas a los archivos PDF a combinar
        output_path: Ruta donde se guardará el PDF combinado
    
    Returns:
        bool: True si la combinación fue exitosa, False en caso contrario
    """
    try:
        pdf_writer = PdfWriter()
        
        for pdf_path in pdf_paths:
            try:
                with open(pdf_path, 'rb') as pdf_file:
                    pdf_reader = PdfReader(pdf_file)
                    
                    # Verificar que el PDF no esté corrupto
                    if len(pdf_reader.pages) == 0:
                        logger.warning(f"PDF vacío o corrupto: {pdf_path}")
                        continue
                    
                    # Agregar todas las páginas del PDF actual
                    for page_num in range(len(pdf_reader.pages)):
                        page = pdf_reader.pages[page_num]
                        pdf_writer.add_page(page)
                    
                    logger.info(f"PDF agregado exitosamente: {pdf_path} ({len(pdf_reader.pages)} páginas)")
                    
            except (PdfReadError, Exception) as e:
                logger.error(f"Error al leer PDF {pdf_path}: {str(e)}")
                continue
        
        # Verificar que tengamos al menos una página
        if len(pdf_writer.pages) == 0:
            logger.error("No se pudieron agregar páginas de ningún PDF")
            return False
        
        # Escribir el PDF combinado
        with open(output_path, 'wb') as output_file:
            pdf_writer.write(output_file)
        
        logger.info(f"PDFs combinados exitosamente en: {output_path} ({len(pdf_writer.pages)} páginas totales)")
        return True
        
    except Exception as e:
        logger.error(f"Error al combinar PDFs: {str(e)}")
        return False

@app.route('/upload', methods=['POST'])
def upload_file():
    logger.info("Solicitud de carga de archivo recibida")
    
    # Verificar si se enviaron archivos
    if 'files' not in request.files:
        logger.warning("No se seleccionaron archivos")
        flash('No se seleccionaron archivos', 'error')
        return redirect(url_for('index'))
    
    files = request.files.getlist('files')
    
    # Filtrar archivos vacíos
    valid_files = [f for f in files if f.filename != '']
    
    if not valid_files:
        logger.warning("No se seleccionaron archivos válidos")
        flash('No se seleccionaron archivos válidos', 'error')
        return redirect(url_for('index'))
    
    # Obtener la carpeta de destino del formulario
    target_folder = request.form.get('target_folder', 'certificados').strip()
    if target_folder == 'root':
        target_folder = 'certificados'  # Usar certificados como carpeta por defecto
    
    # Obtener el índice del archivo que debe tener el QR
    qr_file_index = int(request.form.get('qr_file_index', 0))
    logger.info(f"Índice del archivo para QR: {qr_file_index}")
    
    temp_files = []
    final_pdf_path = None
    
    try:
        logger.info(f"Procesando {len(valid_files)} archivo(s)")
        
        # Reorganizar archivos para que el archivo con QR esté primero
        if len(valid_files) > 1 and 0 <= qr_file_index < len(valid_files):
            # Mover el archivo seleccionado para QR al inicio
            qr_file = valid_files.pop(qr_file_index)
            valid_files.insert(0, qr_file)
            logger.info(f"Archivo reorganizado: '{qr_file.filename}' movido a la primera posición para QR")
        
        # Guardar todos los archivos temporalmente (ya reorganizados)
        for i, file in enumerate(valid_files):
            temp_filename = f"{uuid.uuid4()}_{i}_{file.filename}"
            temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
            file.save(temp_filepath)
            temp_files.append(temp_filepath)
            logger.info(f"Archivo {i+1} guardado temporalmente: {temp_filepath} ({'CON QR' if i == 0 else 'sin QR'})")
        
        # Si hay múltiples archivos, combinarlos
        if len(valid_files) > 1:
            # Crear nombre para el archivo combinado
            first_filename = valid_files[0].filename
            base_name = os.path.splitext(first_filename)[0]
            combined_filename = f"{base_name}_combinado.pdf"
            final_pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"combined_{uuid.uuid4()}.pdf")
            
            # Combinar los PDFs
            if merge_pdfs(temp_files, final_pdf_path):
                logger.info(f"PDFs combinados exitosamente en: {final_pdf_path}")
                original_filename = combined_filename
            else:
                logger.error("Error al combinar PDFs")
                flash('Error al combinar los archivos PDF', 'error')
                return redirect(url_for('index'))
        else:
            # Solo un archivo, usar directamente
            final_pdf_path = temp_files[0]
            original_filename = valid_files[0].filename
        
        logger.info(f"Archivo final para subir: {final_pdf_path}")
        logger.info(f"Nombre original del archivo: {original_filename}")
        logger.info(f"Carpeta de destino: {target_folder}")
        
        # Subir a Backblaze B2 con el nombre original y carpeta especificada
        cloud_url, error = upload_to_backblaze(final_pdf_path, original_filename=original_filename, folder=target_folder)
        
        if cloud_url:
            # Crear PDF en blanco con QR ANTES de eliminar los archivos temporales
            # para que create_blank_pdf_with_qr pueda acceder al archivo original
            blank_pdf_filename = f"blank_{os.path.splitext(original_filename)[0]}.pdf"
            blank_pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], blank_pdf_filename)
            
            # Generar PDF en blanco con QR usando las dimensiones exactas del PDF final
            blank_pdf_created = create_blank_pdf_with_qr(cloud_url, blank_pdf_path, final_pdf_path)
            
            # Pequeño retraso para asegurar que los archivos no estén en uso
            time.sleep(0.5)
            
            # Limpiar todos los archivos temporales
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                        logger.debug(f"Archivo temporal eliminado: {temp_file}")
                    except Exception as e:
                        logger.warning(f"No se pudo eliminar el archivo temporal {temp_file}: {str(e)}")
            
            # Si se creó un archivo combinado separado, también eliminarlo
            if len(valid_files) > 1 and final_pdf_path and os.path.exists(final_pdf_path):
                try:
                    os.remove(final_pdf_path)
                    logger.debug(f"Archivo combinado temporal eliminado: {final_pdf_path}")
                except Exception as e:
                    logger.warning(f"No se pudo eliminar el archivo combinado temporal: {str(e)}")
            
            if blank_pdf_created:
                logger.info(f"PDF en blanco creado localmente: {blank_pdf_path}")
            else:
                logger.error("Error al crear PDF en blanco con QR")
                blank_pdf_filename = None
            
            files_count = len(valid_files)
            success_message = f'¡{files_count} archivo(s) combinado(s) y subido(s) con éxito!' if files_count > 1 else '¡Archivo subido con éxito!'
            logger.info(f"Archivos procesados exitosamente: {files_count} archivo(s)")
            flash(success_message, 'success')
            return render_template('success.html', url=cloud_url, filename=original_filename, blank_pdf=blank_pdf_filename if blank_pdf_created else None)
        else:
            logger.error(f"Error al subir el archivo: {error}")
            flash(f'Error al subir el archivo: {error}', 'error')
            return redirect(url_for('index'))
    except Exception as e:
        logger.exception(f"Error en el proceso de carga: {str(e)}")
        flash(f'Error en el proceso de carga: {str(e)}', 'error')
        return redirect(url_for('index'))
    finally:
        # Asegurarse de que todos los archivos temporales se eliminen si ocurre una excepción
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    logger.debug(f"Archivo temporal eliminado en finally: {temp_file}")
                except Exception:
                    # Si no se puede eliminar, simplemente lo registramos
                    pass
        
        # También limpiar el archivo combinado si existe
        if final_pdf_path and len(temp_files) > 1 and os.path.exists(final_pdf_path):
            try:
                os.remove(final_pdf_path)
                logger.debug(f"Archivo combinado eliminado en finally: {final_pdf_path}")
            except Exception:
                pass

@app.route('/files')
@app.route('/files/<path:folder_path>')
def list_files(folder_path=None):
    """
    Muestra una lista de archivos organizados por carpetas.
    Si se especifica folder_path, muestra solo esa carpeta.
    """
    folders, error = get_folders_structure()
    
    if error:
        flash(f'Error al listar archivos: {error}', 'error')
        return redirect(url_for('index'))
    
    # Si se especifica una carpeta, filtrar
    current_folder = None
    if folder_path:
        current_folder = folders.get(folder_path)
        if not current_folder:
            flash('Carpeta no encontrada', 'error')
            return redirect(url_for('list_files'))
    
    return render_template('files.html', 
                         folders=folders, 
                         current_folder=current_folder,
                         current_path=folder_path)

@app.route('/delete/<path:file_name>')
def delete_file_route(file_name):
    """
    Elimina un archivo del bucket.
    """
    logger.info(f"Solicitud para eliminar archivo: {file_name}")
    
    success, error = delete_file(file_name)
    
    if success:
        flash('Archivo eliminado con éxito', 'success')
    else:
        flash(f'Error al eliminar el archivo: {error}', 'error')
    
    return redirect(url_for('list_files'))

@app.route('/download_blank/<filename>')
def download_blank_pdf(filename):
    """
    Descarga el PDF en blanco con QR.
    """
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True, download_name=filename)
        else:
            flash('Archivo no encontrado', 'error')
            return redirect(url_for('index'))
    except Exception as e:
        logger.exception(f"Error al descargar PDF en blanco {filename}: {str(e)}")
        flash('Error al descargar archivo', 'error')
        return redirect(url_for('index'))

@app.route('/create_folder', methods=['POST'])
def create_folder_route():
    """
    Crea una nueva carpeta.
    """
    from flask import jsonify
    
    folder_name = request.form.get('folder_name', '').strip()
    parent_folder = request.form.get('parent_folder', '').strip()
    
    if not folder_name:
        # Verificar si es una petición AJAX
        if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded' and 'XMLHttpRequest' in str(request.headers.get('User-Agent', '')):
            return jsonify({'success': False, 'error': 'El nombre de la carpeta no puede estar vacío'}), 400
        flash('El nombre de la carpeta no puede estar vacío', 'error')
        return redirect(url_for('list_files'))
    
    # Construir la ruta completa de la carpeta
    if parent_folder and parent_folder != 'root':
        folder_path = f"{parent_folder}/{folder_name}"
    else:
        folder_path = folder_name
    
    success, error = create_folder(folder_path)
    
    # Verificar si es una petición AJAX (fetch)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'fetch' in str(request.headers.get('User-Agent', '')):
        if success:
            return jsonify({'success': True, 'message': f'Carpeta "{folder_name}" creada exitosamente'})
        else:
            return jsonify({'success': False, 'error': error}), 400
    
    # Respuesta tradicional para formularios HTML
    if success:
        flash(f'Carpeta "{folder_name}" creada exitosamente', 'success')
    else:
        flash(f'Error al crear carpeta: {error}', 'error')
    
    # Redirigir a la carpeta padre o a la lista principal
    if parent_folder and parent_folder != 'root':
        return redirect(url_for('list_files', folder_path=parent_folder))
    else:
        return redirect(url_for('list_files'))

@app.route('/delete_folder/<path:folder_path>')
def delete_folder_route(folder_path):
    """
    Elimina una carpeta y todos sus archivos.
    """
    success, error = delete_folder(folder_path)
    
    if success:
        flash('Carpeta eliminada exitosamente', 'success')
    else:
        flash(f'Error al eliminar carpeta: {error}', 'error')
    
    # Redirigir a la carpeta padre
    parent_path = '/'.join(folder_path.split('/')[:-1])
    if parent_path:
        return redirect(url_for('list_files', folder_path=parent_path))
    else:
        return redirect(url_for('list_files'))

@app.route('/move_file', methods=['POST'])
def move_file_route():
    """
    Mueve un archivo de una carpeta a otra.
    """
    old_path = request.form.get('old_path')
    new_folder = request.form.get('new_folder', '').strip()
    
    if not old_path:
        flash('Archivo no especificado', 'error')
        return redirect(url_for('list_files'))
    
    # Extraer el nombre del archivo
    filename = old_path.split('/')[-1]
    
    # Construir la nueva ruta
    if new_folder and new_folder != 'root':
        new_path = f"{new_folder}/{filename}"
    else:
        new_path = filename
    
    success, error = move_file(old_path, new_path)
    
    if success:
        flash('Archivo movido exitosamente', 'success')
    else:
        flash(f'Error al mover archivo: {error}', 'error')
    
    return redirect(url_for('list_files'))

@app.route('/api/folders')
def api_get_folders():
    """
    API endpoint para obtener la lista de carpetas (para AJAX).
    """
    from flask import jsonify
    
    folders, error = get_folders_structure()
    
    if error:
        return jsonify({'error': error}), 500
    
    # Devolver la estructura completa de carpetas para que el JavaScript pueda procesarla
    return jsonify({'folders': folders})

if __name__ == '__main__':
    logger.info("Iniciando la aplicación Flask")
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)