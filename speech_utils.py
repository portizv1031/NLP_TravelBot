import os
import whisper
import tempfile
from telegram_utils import get_file_info, download_file

# Carga el modelo de Whisper (puedes elegir "base", "small", etc. dependiendo de tus recursos)
whisper_model = whisper.load_model("small")

def transcribe_voice(file_id):
    """
    Dado el file_id del mensaje de voz de Telegram, descarga el archivo y
    utiliza Whisper para transcribirlo.
    """
    # Obtener información del archivo
    file_info = get_file_info(file_id)
    if not file_info.get("ok"):
        raise Exception("Error obtaining file info from Telegram")
    file_path = file_info["result"]["file_path"]
    # Descargar el archivo
    file_content = download_file(file_path)
    # Guardar el archivo temporalmente
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
        tmp.write(file_content)
        temp_filename = tmp.name

    # Transcribir el audio usando Whisper
    result = whisper_model.transcribe(temp_filename)
    os.remove(temp_filename)
    return result["text"].strip()
