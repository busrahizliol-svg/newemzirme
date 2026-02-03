from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime
import os
import psycopg
import csv
import io


app = FastAPI(
    title="Emzirme Danışmanlığı Chatbotu",
    description="Anneler ve ebelik öğrencileri için destekleyici rehber chatbot",
    version="1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL tanımlı değil. PowerShell'de önce $env:DATABASE_URL=... yazmalısın."
        )
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
    # conn context manager commit eder
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
        # Log yazamasak da chatbot çalışsın
        print("⚠️ log_event DB yazamadı:", repr(e))


class Message(BaseModel):
    question: str
    user_type: Literal["anne", "ogrenci"] = "anne"
    session_id: Optional[str] = None

# === KANITA DAYALI YANIT KARTLARI (Anne modu) ===

CARDS = [
    {
        "id": "sut_saklama",
        "keywords": ["sakla", "saklama", "buzdolabi", "buzdolab", "dondurucu", "kac gun", "kac saat", "derece"],
        "answer_anne": (
            "Sağılmış anne sütü uygun hijyen koşullarında sağılmışsa; "
            "oda sıcaklığında (20–25°C) yaklaşık 4 saat, "
            "buzdolabında (+4°C) 3–4 gün, "
            "derin dondurucuda (−18°C) 6 aya kadar saklanabilir. "
            "Kullanımdan önce görünüm ve koku kontrolü yapılmalıdır."
        ),
        "answer_ogrenci": (
            "Rehberlere göre sağılmış anne sütü; oda sıcaklığında ~4 saat, "
            "buzdolabında 3–4 gün, derin dondurucuda 6 aya kadar saklanabilir. "
            "Saklama süresi hijyen ve ortam koşullarına bağlı değişebilir."
        ),
    },
    {
        "id": "sut_sagma",
        "keywords": ["sagma", "sagim", "pompa"],
        "answer_anne": (
            "Süt sağarken ellerinin temiz olması ve kullanılan pompa ya da kabın temiz ve kuru olması önemlidir. "
            "Genellikle her iki memeden süt sağımı 15–20 dakika sürebilir; bu süre anneden anneye değişebilir. "
            "Rahat bir ortamda, acele etmeden sağım yapmak süt akışını kolaylaştırabilir."
        ),
        "answer_ogrenci": (
            "Anne sütü sağımında hijyen, uygun ekipman ve annenin rahatlığı süt akışını etkiler. "
            "Sağım süresi genellikle 15–20 dakika olmakla birlikte bireysel farklılıklar göz önünde bulundurulmalıdır."
        ),
    },
    {
        "id": "emzirme_suresi",
        "keywords": ["kac dakika", "emzirme suresi", "kac dk"],
        "answer_anne": (
            "Emzirmenin tek ve kesin bir süresi yoktur. "
            "Ancak birçok bebek bir memeyi genellikle 10–20 dakika arasında aktif olarak emer. "
            "Önemli olan bebeğin aktif emmesi ve kendiliğinden memeyi bırakmasıdır."
        ),
        "answer_ogrenci": (
            "Emzirme süresi rehberlerde dakika bazlı sınırlandırılmaz. "
            "Etkin emme, yutma ve memenin boşalması temel göstergelerdir."
        ),
    },
    {
        "id": "pozisyon_kavrama",
        "keywords": ["pozisyon", "kavrama", "dogru emme"],
        "answer_anne": (
            "Emzirirken bebeğin vücudunun sana dönük olması, başı ve gövdesinin aynı hizada tutulması önemlidir. "
            "Bebeğin ağzının geniş açılması ve meme başının çevresindeki koyu alanın büyük kısmını kavraması doğru emzirmenin göstergesidir."
        ),
        "answer_ogrenci": (
            "Doğru emzirme pozisyonunda bebeğin baş, boyun ve gövdesi aynı hizada olmalıdır. "
            "Etkili kavrama danışmanlığın temel basamaklarındandır."
        ),
    },
    {
        "id": "meme_ucu",
        "keywords": ["catlak", "meme ucu", "agri", "aci"],
        "answer_anne": (
            "Emzirme sırasında hafif hassasiyet olabilir; ancak şiddetli ağrı normal değildir. "
            "Meme ucu ağrısı çoğunlukla bebeğin memeyi yeterince iyi kavrayamamasına bağlı olabilir. "
            "Pozisyonu gözden geçirmek ve meme ucunun havalanmasına izin vermek rahatlama sağlayabilir."
        ),
        "answer_ogrenci": (
            "Meme ucu ağrısı ve çatlaklar sıklıkla yanlış kavrama ile ilişkilidir. "
            "Danışmanlıkta öncelikle emzirme pozisyonu değerlendirilmelidir."
        ),
    },
]


def normalize(s: str) -> str:
    s = s.lower().strip()
    tr_map = str.maketrans({
        "ç": "c", "ğ": "g", "ı": "i", "İ": "i", "ö": "o", "ş": "s", "ü": "u"
    })
    s = s.translate(tr_map)

    # Noktalama ve özel karakterleri boşluğa çevir
    for ch in ['"', "'", ".", ",", "?", "!", ":", ";", "(", ")", "[", "]", "{", "}", "-", "_", "/","\\"]:
        s = s.replace(ch, " ")

    # Çoklu boşlukları tek boşluk yap
    s = " ".join(s.split())
    return s


@app.get("/")
def root():
    return {"message": "Merhaba, ben emzirme konusunda sana yardımcı olmak için buradayım."}
@app.post("/chat")
def chat(message: Message):
    text = normalize(message.question)
    matched_card = None
    is_emergency = False

    # 1️⃣ İlaç / doz engeli
    if "ilac" in text or "ilaç" in text or "doz" in text:
        matched_card = "MEDICATION_BLOCK"
        log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
        return {
            "answer": (
                "Bu konuda ilaç dozu veya tedavi önerisi veremem. "
                "Emzirme döneminde ilaç kullanımı için mutlaka bir sağlık profesyoneline danışmalısın."
            )
        }

    # 2️⃣ Acil durum taraması
    acil_tetikler = [
        "38", "38.", "ates", "ateş",
        "siddetli", "şiddetli",
        "kan", "kanli", "kanlı",
        "cok halsiz", "çok halsiz",
        "dayanamiyorum", "dayanamıyorum",
        "titreme", "usume", "üşüme"
    ]

    for tetik in acil_tetikler:
        if tetik in text:
            matched_card = "EMERGENCY"
            is_emergency = True
            log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
            return {
                "answer": (
                    "Anlattıkların acil bir duruma işaret ediyor olabilir 🚨 "
                    "Lütfen vakit kaybetmeden en yakın sağlık kuruluşuna başvur."
                )
            }

    # 3️⃣ Kart eşleştirme
    for card in CARDS:
        for keyword in card["keywords"]:
            if keyword in text:
                matched_card = card["id"]
                log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
                if message.user_type == "ogrenci":
                    return {"answer": card["answer_ogrenci"]}
                else:
                    return {"answer": card["answer_anne"]}

    # 4️⃣ Varsayılan cevap
    matched_card = "FALLBACK"
    log_event(message.session_id, message.user_type, message.question, matched_card, is_emergency)
    return {
        "answer": (
            "Seni anlıyorum 💙 Sorunu biraz daha detaylandırabilir misin? "
            "Eğer belirtiler artarsa bir sağlık kuruluşuna başvurmanı öneririm."
        )
    }

from fastapi import HTTPException

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

