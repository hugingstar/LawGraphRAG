from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.annotate import annotate_text
from app.db import get_session

app = FastAPI(title="SafetyLawAdvisor")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze")
def analyze(text: str = Form(...), session: Session = Depends(get_session)):
    citations = annotate_text(session, text)
    return {
        "text": text,
        "citations": [
            {
                "law_name": c.law_name,
                "article_label": f"제{c.article_no}조" + (f"의{c.article_no_sub}" if c.article_no_sub else ""),
                "title": c.title,
                "start": c.start,
                "end": c.end,
                "reason": c.reason,
                "url": c.url,
            }
            for c in citations
        ],
    }
