# flight_extractor.py
import spacy
import re
import dateparser
import parsedatetime
from datetime import datetime, timedelta
from transformers import MarianMTModel, MarianTokenizer
from word2number import w2n

# Inicialización de modelos y parsedatetime
nlp = spacy.load("en_core_web_sm")
cal = parsedatetime.Calendar()

# Cargar modelo de traducción (es→en)
model_name = "Helsinki-NLP/opus-mt-es-en"
t_tokenizer = MarianTokenizer.from_pretrained(model_name)
t_model = MarianMTModel.from_pretrained(model_name)

# Cargar información de aeropuertos (desde CSV en GitHub)
import pandas as pd
_airports_url = 'https://raw.githubusercontent.com/datasets/airport-codes/refs/heads/main/data/airport-codes.csv'
df_airports = pd.read_csv(_airports_url)
df_filtered_airports = df_airports[df_airports["type"].isin(["small_airport", "medium_airport", "large_airport"])].copy()
df_iatacode = df_filtered_airports[["municipality", "iso_country", "iata_code"]].dropna()
df_iata = df_iatacode.rename(columns={
    "municipality": "city",
    "iso_country": "country",
    "iata_code": "iata_code"
})
df_iata.columns = ["city", "country", "iata_code"]
# Crear diccionario: clave = (city.lower(), country) → código IATA
airports = {(row["city"].strip().lower(), row["country"]): {"code": row["iata_code"]}
            for _, row in df_iata.iterrows()}


def translate_text(text):
    """Traduce el texto de español a inglés."""
    inputs = t_tokenizer(text, return_tensors="pt", padding=True, truncation=True)
    translated_tokens = t_model.generate(**inputs)
    return t_tokenizer.decode(translated_tokens[0], skip_special_tokens=True)


def parse_date_str(date_str):
    """Convierte una cadena de fecha a formato YYYY-MM-DD, manejando fechas relativas."""
    base_date = datetime.now()
    settings = {'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': base_date}
    parsed_date = dateparser.parse(date_str, languages=["en", "es"], settings=settings)
    if not parsed_date:
        time_struct, parse_status = cal.parse(date_str, sourceTime=base_date)
        if parse_status:
            parsed_date = datetime(*time_struct[:6])
    if not parsed_date:
        today = datetime.today()
        weekdays = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                    "friday": 4, "saturday": 5, "sunday": 6}
        match = re.search(r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday) of next week', date_str.lower())
        if match:
            target_weekday = weekdays[match.group(1)]
            days_until_target = (target_weekday - today.weekday() + 7) % 7 + 7
            parsed_date = today + timedelta(days=days_until_target)
    return parsed_date.strftime("%Y-%m-%d") if parsed_date else None


def extract_locations(text):
    """
    Extrae origen y destino usando reglas basadas en dependencias y entidades.
    Se buscan preposiciones "from", "to" y verbos como "arrive" o "depart".
    Solo se consideran entidades de tipo GPE o LOC.
    """
    doc = nlp(text)
    origin, destination = None, None

    # Regla 1: Buscar "from" para origen
    for token in doc:
        if token.lower_ == "from" and token.dep_ == "prep":
            for child in token.children:
                if child.dep_ == "pobj" and child.ent_type_ in ("GPE", "LOC"):
                    origin = child.text
                    break
        if origin:
            break

    # Regla 2: Si no se encontró, buscar verbos "depart" o "leave" con "from"
    if not origin:
        for token in doc:
            if token.lemma_ in ("depart", "leave"):
                for child in token.children:
                    if child.dep_ == "prep" and child.lower_ == "from":
                        for sub in child.children:
                            if sub.dep_ == "pobj" and sub.ent_type_ in ("GPE", "LOC"):
                                origin = sub.text
                                break
                if origin:
                    break

    # Regla 3: Buscar "to" para destino
    for token in doc:
        if token.lower_ == "to" and token.dep_ == "prep":
            for child in token.children:
                if child.dep_ == "pobj" and child.ent_type_ in ("GPE", "LOC"):
                    destination = child.text
                    break
        if destination:
            break

    # Regla 4: Buscar destino a partir de verbo "arrive" (ej. "arrive in")
    if not destination:
        for token in doc:
            if token.lemma_ == "arrive":
                for child in token.children:
                    if child.dep_ == "prep" and child.lower_ == "in":
                        for sub in child.children:
                            if sub.dep_ == "pobj" and sub.ent_type_ in ("GPE", "LOC"):
                                destination = sub.text
                                break
                if destination:
                    break

    # Fallback: usar orden de entidades detectadas
    loc_entities = [ent for ent in doc.ents if ent.label_ in ("GPE", "LOC")]
    if (not origin or not destination) and len(loc_entities) >= 2:
        if not origin:
            origin = loc_entities[0].text
        if not destination:
            destination = loc_entities[1].text
    elif not origin and len(loc_entities) == 1:
        # Si hay solo una, se decide según contexto (por ejemplo, si se menciona "arrive", lo asigna a destino)
        if any(tok.lemma_ in ("arrive", "fly", "go") for tok in doc):
            destination = loc_entities[0].text
        else:
            origin = loc_entities[0].text

    return origin, destination


def extract_flight_info(text, pending_fields, info):
    """
    Extrae información de vuelo a partir de la consulta.
    Se traduce el texto a inglés y se utilizan spaCy y regex para detectar:
      - Origen (from) y Destino (to)
      - Fechas (departure_date y return_date)
      - Duración de estadía (stay_duration)
      - Número de personas (num_people)
      - Aerolínea (airline)
    """
    # Traducir el texto para unificar el procesamiento
    text = translate_text(text)
    doc = nlp(text)

    # origin, destination = extract_locations(text)

    origin, destination, airline = None, None, None
    origin_country, destination_country = None, None
    origin_code, destination_code = None, None
    departure_date, return_date = None, None
    num_people = 1
    stay_duration = None
    airline = None

    locations, dates = [], []
    for ent in doc.ents:
        if ent.label_ == "GPE":
            locations.append(ent.text.strip())  # Cities/Countries
        elif ent.label_ == "ORG":
            airline = ent.text.strip()  # Airline
        elif ent.label_ == "DATE":
            dates.append(ent.text.strip())  # Dates

    # Extract duration
    if 'stay_duration' in pending_fields:
        duration_match = re.search(r'(\d+|\w+)\s*(days|day|días|día)', text, re.IGNORECASE)
        if duration_match:
            duration_str = duration_match.group(1)
            try:
                stay_duration = int(duration_str)
            except ValueError:
                word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                               "eight": 8, "nine": 9, "ten": 10}
                stay_duration = word_to_num.get(duration_str.lower(), None)

    # Assign origin and destination
    if len(locations) >= 2:
        if 'from' in pending_fields:
            origin = locations[0]
        if 'to' in pending_fields:
            destination = locations[1]
    elif len(locations) == 1:
        if 'to' in pending_fields:
            destination = locations[0]
        elif 'from' in pending_fields:
            origin = locations[0]

    # Retrieve country + IATA code
    if origin:
        match = [(city, country) for (city, country) in airports if city == origin.lower()]
        if match:
            origin_country = match[0][1]
            origin_code = airports[match[0]]["code"]

    if destination:
        match = [(city, country) for (city, country) in airports if city == destination.lower()]
        if match:
            destination_country = match[0][1]
            destination_code = airports[match[0]]["code"]

    # Extract dates
    if dates:
        parsed_dates = [parse_date_str(date_str) for date_str in dates]
        if 'departure_date' in pending_fields and parsed_dates:
            departure_date = parsed_dates.pop(0)
        if 'return_date' in pending_fields and parsed_dates:
            return_date = parsed_dates.pop(0)
    elif 'departure_date' in pending_fields:
        departure_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")  # Default to next day

    # Extract number of people
    if 'num_people' in pending_fields:
        ticket_keywords = {
            "ticket", "tickets", "passage", "passages",
            "person", "people", "individual", "individuals",
            "adult", "adults", "child", "children", "kid", "kids", "flight", "flights"
        }
        total = 0
        for token in doc:
            if token.like_num:
                try:
                    number = int(token.text)
                except ValueError:
                    try:
                        number = w2n.word_to_num(token.text)
                    except Exception:
                        continue

                # Define a token 2 places before-after window
                window = doc[max(0, token.i - 2): min(len(doc), token.i + 3)]

                if any(w.lower_ in ticket_keywords for w in window):
                    total += number
        if total:
            num_people = total

    return {
        "from": origin,
        "from_country": origin_country,
        "from_code": origin_code,
        "to": destination,
        "to_country": destination_country,
        "to_code": destination_code,
        "departure_date": departure_date,
        "return_date": return_date,
        "stay_duration": stay_duration,
        "num_people": num_people,
        "airline": airline
    }
