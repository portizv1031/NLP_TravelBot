# main.py
import json
from threading import Thread
from flask import Flask, request, jsonify
from flask_cors import CORS

from config import SQLALCHEMY_DATABASE_URI
from db_model import db, FlightOrder
from entity_extractor import extract_flight_info
from telegram_utils import send_message
from speech_utils import transcribe_voice

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Diccionario que asocia cada campo faltante a una pregunta
question_mapping = {
    "from": "Please provide your departure city:",
    "to": "Please provide your destination city:",
    "departure_date": "Please provide your departure date:",
    "return_date": "Please provide your return date:",
    # "stay_duration": "Please provide the duration of your stay in days:",
    "num_people": "How many passengers/tickets do you need?",
    "airline": "Do you have a preferred airline? (Leave blank if none):"
}

@app.route("/chatbot", methods=["POST"])
def chatbot():
    try:
        data = request.json
        uid = str(data["message"]["from"]["id"])
        text = data["message"].get("text", "")

        # Si se recibe el comando /start, enviar mensaje de bienvenida y detener procesamiento adicional
        if text.strip() == "/start":
            welcome_text = "Welcome! Please provide your flight details."
            send_message(uid, welcome_text)
            return jsonify({"status": "ok"})
        # else:
        #     return jsonify({"status": "ok"})

        # Si se recibe un mensaje de voz, transcribirlo y usar la transcripción como texto
        if "voice" in data["message"]:
            file_id = data["message"]["voice"]["file_id"]
            try:
                text = transcribe_voice(file_id)
            except Exception as e:
                send_message(uid, f"Error transcribing voice message: {str(e)}")
                return jsonify({"status": "error"})

        # Buscar un pedido pendiente (estado 'pending' o 'processing') para este uid
        order = FlightOrder.query.filter(FlightOrder.uid == uid,
                                         FlightOrder.state.in_(["pending", "processing"])).first()

        if not order:
            order = FlightOrder(uid=uid, flight_info=json.dumps({f: None for f in ['from', 'to', 'departure_date', 'return_date', 'stay_duration', 'num_people', 'airline']}), state='pending')
            db.session.add(order)
            db.session.commit()

        info = json.loads(order.flight_info)
        pending_fields = [k for k, v in info.items() if v is None]
        new_info = extract_flight_info(text, pending_fields, info)

        for key in pending_fields:
            if new_info.get(key) is not None:
                info[key] = new_info[key]

        order.flight_info = json.dumps(info)
        db.session.commit()

        # Revisar campos faltantes en el pedido
        info = json.loads(order.flight_info)
        pending_fields = [k for k, v in info.items() if v is None]

        if pending_fields:
            response_text = question_mapping.get(pending_fields[0], f"Please provide {pending_fields[0]}")
            order.state = "processing"
        else:
            response_text = f"You booked a flight from {info['from']} to {info['to']} on {info['departure_date']}."
            order.state = "complete"

        db.session.commit()
        # Enviar la respuesta a Telegram mediante /sendMessage
        send_message(uid, response_text)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_flask():
    app.run(host="0.0.0.0", port=5555)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    thread = Thread(target=run_flask)
    thread.start()
