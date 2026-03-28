import streamlit as st
import anthropic
import requests
from bs4 import BeautifulSoup
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Klucze - działają lokalnie i na Streamlit Cloud
if "ANTHROPIC_API_KEY" in st.secrets:
    os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]

def wyslij_email(do: str, temat: str, tresc: str) -> bool:
    try:
        gmail = st.secrets.get("GMAIL_EMAIL") or os.environ.get("GMAIL_EMAIL")
        haslo = st.secrets.get("GMAIL_HASLO") or os.environ.get("GMAIL_HASLO")
        if not gmail or not haslo:
            return False
        wiadomosc = MIMEText(tresc, "plain", "utf-8")
        wiadomosc["Subject"] = temat
        wiadomosc["From"] = gmail
        wiadomosc["To"] = do
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(gmail, haslo)
            s.send_message(wiadomosc)
        return True
    except Exception as e:
        st.error(f"Błąd wysyłania: {e}")
        return False

BAZA = "pamiec.db"

# ============================================================
# BAZA DANYCH
# ============================================================

def inicjuj_baze():
    conn = sqlite3.connect(BAZA)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS firmy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            nazwa TEXT,
            analiza TEXT,
            data_analizy TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_url TEXT,
            typ TEXT,
            tresc TEXT,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

def zapisz_firme(url, nazwa, analiza):
    conn = sqlite3.connect(BAZA)
    conn.execute("""
        INSERT INTO firmy (url, nazwa, analiza, data_analizy) VALUES (?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET analiza=excluded.analiza, data_analizy=excluded.data_analizy
    """, (url, nazwa, analiza, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def pobierz_firme(url):
    conn = sqlite3.connect(BAZA)
    wynik = conn.execute("SELECT * FROM firmy WHERE url=?", (url,)).fetchone()
    conn.close()
    return wynik

def zapisz_content(url, typ, tresc):
    conn = sqlite3.connect(BAZA)
    conn.execute("INSERT INTO content (firma_url, typ, tresc, data) VALUES (?, ?, ?, ?)",
                 (url, typ, tresc, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def pobierz_wszystkie_firmy():
    conn = sqlite3.connect(BAZA)
    wynik = conn.execute("SELECT url, nazwa, data_analizy FROM firmy ORDER BY data_analizy DESC").fetchall()
    conn.close()
    return wynik

def pobierz_content_firmy(url):
    conn = sqlite3.connect(BAZA)
    wynik = conn.execute(
        "SELECT typ, tresc, data FROM content WHERE firma_url=? ORDER BY data DESC", (url,)
    ).fetchall()
    conn.close()
    return wynik

# ============================================================
# AGENCI
# ============================================================

def pobierz_strone(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:6000]
    except Exception as e:
        return f"Błąd: {e}"

def agent_badacz(url):
    istniejaca = pobierz_firme(url)
    if istniejaca:
        return istniejaca[3], True  # analiza, z_cache
    tekst = pobierz_strone(url)
    klient = anthropic.Anthropic()
    odpowiedz = klient.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Przeanalizuj tę stronę firmową:

{tekst}

Zwróć:
1. FIRMA: nazwa i czym się zajmuje
2. OFERTA: główne produkty/usługi
3. KLIENT DOCELOWY: do kogo kierują ofertę
4. USP: co ich wyróżnia
5. TON KOMUNIKACJI: jak piszą
6. SŁABE PUNKTY: czego brakuje"""}]
    )
    analiza = odpowiedz.content[0].text
    nazwa = url.replace("https://", "").replace("www.", "").split("/")[0]
    zapisz_firme(url, nazwa, analiza)
    return analiza, False

def agent_copywriter(analiza, typ):
    klient = anthropic.Anthropic()
    odpowiedz = klient.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Na podstawie analizy firmy stwórz {typ}.

ANALIZA FIRMY:
{analiza}

Zasady formatowania:
- Pisz czysty tekst, bez markdown (bez **, bez #, bez ---)
- Nie zaczynaj od "Temat:" ani żadnego nagłówka
- Listy pisz jako zwykłe zdania lub z myślnikiem i spacją, bez pogrubień
- Zacznij bezpośrednio od treści wiadomości

Pisz po polsku. Użyj konkretnych informacji z analizy."""}]
    )
    return odpowiedz.content[0].text

# ============================================================
# INTERFEJS
# ============================================================

inicjuj_baze()

st.title("Agent z Pamięcią")
zakładka1, zakładka2 = st.tabs(["Generuj content", "Historia"])

with zakładka1:
    url = st.text_input("URL firmy:")
    typ = st.selectbox("Co wygenerować:", ["cold email sprzedażowy", "post na LinkedIn", "ofertę współpracy B2B"])

    if st.button("Generuj"):
        if not url:
            st.error("Podaj URL.")
        else:
            with st.spinner("Agent Badacz analizuje..."):
                analiza, z_cache = agent_badacz(url)

            if z_cache:
                st.info("Firma już w bazie - używam zapisanej analizy (0 tokenów za analizę)")
            else:
                st.success("Nowa firma - analiza zapisana w bazie")

            with st.expander("Pokaż analizę firmy"):
                st.write(analiza)

            with st.spinner("Agent Copywriter tworzy content..."):
                content = agent_copywriter(analiza, typ)
                zapisz_content(url, typ, content)

            st.session_state["content"] = content

    if "content" in st.session_state:
        st.subheader("Gotowy content:")
        st.markdown(st.session_state["content"])
        st.download_button("Pobierz", st.session_state["content"], file_name="content.txt")

        st.divider()
        st.subheader("Wyślij emailem")
        adres_email = st.text_input("Adres email odbiorcy:")
        temat_email = st.text_input("Temat:", value="Propozycja współpracy")
        if st.button("Wyślij email"):
            if not adres_email:
                st.error("Podaj adres email.")
            else:
                if wyslij_email(adres_email, temat_email, st.session_state["content"]):
                    st.success(f"Email wysłany do: {adres_email}")
                else:
                    st.error("Brak konfiguracji email w Secrets (GMAIL_EMAIL, GMAIL_HASLO)")

with zakładka2:
    firmy = pobierz_wszystkie_firmy()
    if not firmy:
        st.info("Brak historii. Wygeneruj coś w zakładce 'Generuj content'.")
    else:
        st.subheader(f"Analizowane firmy: {len(firmy)}")
        for url, nazwa, data in firmy:
            with st.expander(f"{nazwa} — {data}"):
                st.write(f"URL: {url}")
                wyniki = pobierz_content_firmy(url)
                if wyniki:
                    for typ, tresc, data_c in wyniki:
                        st.markdown(f"**{typ}** ({data_c})")
                        st.write(tresc)
                        st.divider()
