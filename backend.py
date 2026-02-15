from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime
import os
import psycopg
import csv
import io
import tempfile

from openai import OpenAI


app = FastAPI(
    title="Emzirme Danışmanlığı Chatbotu",
    description="Anneler ve ebelik öğrencileri için destekleyici rehber chatbot",
    version="1.0"
)

# ✅ CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://busrahizliol-svg.github.io",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    print("⚠️ OPENAI_API_KEY tanımlı değil. PDF RAG çalışmaz.")
client = OpenAI(api_key=OPENAI_API_KEY)

# ✅ DB
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL tanımlı değil.")
    return psycopg.connect(DATABASE_URL, autocommit=True)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL,
                    session_id TEXT,
                    user_type TEXT NOT NULL,
                    question TEXT NOT NULL,
                    matched_card TEXT,
                    is_emergency BOOLEAN NOT NULL
                );
            """)

@app.on_event("startup")
def on_startup():
    try:
        init_db()
        print("✅ DB hazır.")
    except Exception as e:
        print("⚠️ DB bağlantısı yok, uygulama yine de açılıyor:", repr(e))

def log_event(session_id, user_type, question, matched_card, is_emergency):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events (ts, session_id, user_type, question, matched_card, is_emergency)
                    VALUES (NOW(), %s, %s, %s, %s, %s)
                    """,
                    (session_id, user_type, question, matched_card, bool(is_emergency))
                )
    except Exception as e:
        print("⚠️ log_event DB yazamadı:", repr(e))


class Message(BaseModel):
    question: str
    user_type: Literal["anne", "ogrenci"] = "anne"
    session_id: Optional[str] = None
    vector_store_id: Optional[str] = None  # ✅ PDF kaynağı (opsiyonel)


CARDS = [
    {
        "id": "sut_saklama",
        "keywords": ["sakla", "saklama", "buzdolabi", "buzdolab", "dondurucu", "kac gun", "kac saat", "derece"],
        "answer_anne": (
            "Sağılmış anne sütü uygun hijyen koşullarında sağılmışsa; "
            "oda sıcaklığında (20–25°C) yaklaşık 4 saat, "
            "buzdolabında (+4°C) 3–4 gün, "
            "derin dondurucuda (−18°C) 6 aya kadar saklanabilir."
        ),
        "answer_ogrenci": (
            "Rehberlere göre sağılmış anne sütü; oda sıcaklığında ~4 saat, "
            "buzdolabında 3–4 gün, derin dondurucuda 6 aya kadar saklanabilir."
        ),
    },
    {
        "id": "sut_sagma",
        "keywords": ["sagma", "sagim", "pompa"],
        "answer_anne": (
            "Süt sağarken ellerinin temiz olması ve pompa/kabın temiz olması önemlidir. "
            "Genelde 15–20 dakika sürebilir; kişiye göre değişir."
        ),
        "answer_ogrenci": (
            "Sağımda hijyen, uygun ekipman ve annenin rahatlığı önemlidir. "
            "Süre genelde 15–20 dk olmakla birlikte bireyseldir."
        ),
    },
]

def normalize(s: str) -> str:
    s = s.lower().strip()
    tr_map = str.maketrans({"ç":"c","ğ":"g","ı":"i","İ":"i","ö":"o","ş":"s","ü":"u"})
    s = s.translate(tr_map)
    for ch in ['"', "'", ".", ",", "?", "!", ":", ";", "(", ")", "[", "]", "{", "}", "-", "_", "/","\\"]:
        s = s.replace(ch, " ")
    return " ".join(s.split())
def answer_from_pdf(question: str, user_type: str, vector_store_id: str):

    system_instructions = (
        "Sen emzirme danışmanlığı asistanısın. "
        "Yanıtını SADECE verilen PDF içeriğine dayanarak ver. "
        "PDF'de cevap yoksa aynen şu cümleyi yaz: Bu PDF’de bu bilgi yok."
    )

    tone = "sade ve destekleyici" if user_type == "anne" else "akademik ve kanıta dayalı"

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            instructions=system_instructions + f" Yanıt tonu: {tone}",
            input=question,
            tools=[{
                "type": "file_search",
                "vector_store_ids": [vector_store_id]
            }],
        )

        text = resp.output_text

        if not text:
            return None

        text = text.strip()

        if len(text) < 3:
            return None

        return text

    except Exception as e:
        print("PDF cevap hatası:", repr(e))
        return None

@app.get("/")
def root():
    return {"message": "Merhaba, ben emzirme konusunda sana yardımcı olmak için buradayım."}


# ✅ PDF Upload Endpoint
@app.post("/pdf/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY tanımlı değil.")

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Lütfen PDF dosyası yükleyin.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        uploaded = client.files.create(file=open(tmp_path, "rb"), purpose="assistants")
        vs = client.vector_stores.create(name=f"emzirme_pdf_{datetime.utcnow().isoformat()}")

        client.vector_stores.files.create_and_poll(
            vector_store_id=vs.id,
            file_id=uploaded.id,
        )

        return {"vector_store_id": vs.id, "file_id": uploaded.id, "filename": file.filename}
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@app.post("/chat")
def chat(message: Message):
    text = normalize(message.question)
    matched_card = None
    is_emergency = False

    # 1) İlaç/doz engeli
    if "ilac" in text or "doz" in text:
        matched_card = "MEDICATION_BLOCK"
        log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
        return {"answer": "İlaç dozu/tedavi önerisi veremem. Emzirme döneminde ilaç için sağlık profesyoneline danışmalısın."}

    # 2) Acil durum
    acil_tetikler = ["38", "ates", "ateş", "siddetli", "şiddetli", "kan", "kanli", "kanlı", "titreme"]
    for tetik in acil_tetikler:
        if tetik in text:
            matched_card = "EMERGENCY"
            is_emergency = True
            log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
            return {"answer": "Bu bir acil duruma işaret ediyor olabilir 🚨 Lütfen en yakın sağlık kuruluşuna başvur."}

    # ✅ 2.5) PDF varsa önce PDF
    if message.vector_store_id:
        pdf_answer = answer_from_pdf(message.question, message.user_type, message.vector_store_id)
        if pdf_answer:
            matched_card = "PDF_RAG"
            log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
            return {"answer": pdf_answer}

    # 3) Kartlar
    for card in CARDS:
        for keyword in card["keywords"]:
            if keyword in text:
                matched_card = card["id"]
                log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
                return {"answer": card["answer_ogrenci"] if message.user_type == "ogrenci" else card["answer_anne"]}

    # 4) Fallback
    matched_card = "FALLBACK"
    log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
    return {"answer": "Seni anlıyorum 💙 Sorunu biraz daha detaylandırabilir misin? Belirtiler artarsa sağlık kuruluşuna başvur."}


@app.get("/stats")
def stats():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM events")
                total = cur.fetchone()[0]
        return {"total_questions": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/export.csv")
def export_csv():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ts, session_id, user_type, matched_card, is_emergency, question
                FROM events
                ORDER BY id ASC
            """)
            rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ts", "session_id", "user_type", "matched_card", "is_emergency", "question"])
    writer.writerows(rows)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=analytics.csv"}
    )

