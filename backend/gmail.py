"""Read-only Gmail scan (IMAP + app password kept in the macOS Keychain) that updates the tracker."""
import email
import imaplib
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

import yaml
from sqlmodel import select

from . import tracker
from .config import DATA
from .models import Application, InboxItem, Job, session

CONF = DATA / "gmail.yaml"  # only your address; the app password lives in the Keychain
KEYRING_SERVICE = "jobprep-gmail"
SEARCH = ('newer_than:{days}d (subject:(application OR applying OR applied OR interview OR assessment OR offer '
          'OR "next steps" OR candidacy OR update) OR from:(greenhouse.io OR lever.co OR ashbyhq.com OR myworkday.com '
          'OR smartrecruiters.com OR icims.com OR hackerrank.com OR codesignal.com))')

# order matters: a rejection that mentions "interview" is still a rejection
RULES = [
    ("offer", ["pleased to offer", "offer letter", "extend an offer", "extend you an offer", "offer of employment"]),
    ("rejected", ["unfortunately", "not to move forward", "not moving forward", "will not be moving forward",
                  "other candidates", "decided to pursue", "no longer under consideration", "not been selected",
                  "not selected", "position has been filled", "regret to inform"]),
    ("oa", ["online assessment", "coding assessment", "technical assessment", "hackerrank", "codesignal", "codility",
            "coding challenge", "hirevue", "take-home", "assessment invitation", "complete the assessment"]),
    ("interview", ["schedule an interview", "interview invitation", "invite you to interview", "schedule a call",
                   "phone screen", "next round", "interview with", "availability for an interview", "like to interview"]),
    ("applied", ["thank you for applying", "thanks for applying", "application received", "received your application",
                 "application has been received", "application was submitted", "application has been submitted",
                 "we have received your", "thank you for your application", "thank you for your interest"]),
]


def classify(subject: str, body: str) -> str | None:
    text = f"{subject} {body}".lower()
    for kind, phrases in RULES:
        if any(p in text for p in phrases):
            return kind
    return None


def _norm(s: str) -> str:
    return re.sub(r"\b(inc|llc|corp|corporation|co|ltd|careers|recruiting|talent|team|jobs|hr|the)\b|[^a-z0-9]", "", s.lower())


def match_company(sender: str, subject: str, body: str, companies: dict[str, list[str]]) -> str | None:
    """Return the job_id whose company is named in the sender, subject or opening of the body (longest name wins)."""
    name, addr = parseaddr(sender)
    domain = addr.split("@")[-1].split(".")[-2] if "@" in addr and addr.count(".") else ""
    head = f"{name} {subject} {body[:1500]}"
    best, best_len = None, 0
    for company, job_ids in companies.items():
        n = _norm(company)
        if len(n) < 2:
            continue
        hit = (n == _norm(domain) or n == _norm(name)
               or (len(n) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(company.lower())}(?![a-z0-9])", head.lower())))
        if hit and len(n) > best_len:
            best, best_len = job_ids[0], len(n)
    return best


def guess_company(sender: str, subject: str) -> str:
    name, addr = parseaddr(sender)
    m = re.search(r"(?:at|to|with|from)\s+([A-Z][\w&.\- ]{1,40}?)(?:[!,.:]|\s+-|\s+for|$)", subject)
    if m:
        return m[1].strip()
    if name and not re.search(r"no.?reply|notifications?|greenhouse|lever|workday|ashby", name, re.I):
        return re.sub(r"\s*(careers|recruiting|talent|jobs|hr)\b.*", "", name, flags=re.I).strip()
    return addr.split("@")[-1] if "@" in addr else ""


# --- credentials ------------------------------------------------------------

def address() -> str | None:
    return (yaml.safe_load(CONF.read_text()) or {}).get("email") if CONF.exists() else None


def _password(addr: str) -> str | None:
    import keyring
    return keyring.get_password(KEYRING_SERVICE, addr)


def connect(addr: str, app_password: str) -> None:
    import keyring
    pw = app_password.replace(" ", "")
    _login(addr, pw).logout()  # raises if wrong
    keyring.set_password(KEYRING_SERVICE, addr, pw)
    CONF.parent.mkdir(exist_ok=True)
    CONF.write_text(yaml.safe_dump({"email": addr}))


def disconnect() -> None:
    import keyring
    if (addr := address()):
        try:
            keyring.delete_password(KEYRING_SERVICE, addr)
        except keyring.errors.PasswordDeleteError:
            pass
    CONF.unlink(missing_ok=True)


def _login(addr: str, pw: str) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(addr, pw)
    return m


# --- scan -------------------------------------------------------------------

def _text(msg: email.message.Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    plain, html = "", ""
    for p in parts:
        ctype = p.get_content_type()
        if p.get_content_maintype() == "multipart" or p.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = p.get_payload(decode=True) or b""
            text = payload.decode(p.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        if ctype == "text/plain":
            plain += text
        elif ctype == "text/html":
            html += re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", text))
    return re.sub(r"\s+", " ", plain or html).strip()


def ingest(messages: list[dict]) -> dict:
    """messages: [{message_id, sender, subject, date, body}] -> store, classify, and update the tracker."""
    with session() as s:
        apps = s.exec(select(Application)).all()
        companies: dict[str, list[str]] = {}
        for a in apps:
            if a.status != "dismissed":
                companies.setdefault(s.get(Job, a.job_id).company, []).append(a.job_id)
        known = {x.message_id for x in s.exec(select(InboxItem)).all()}
    counts = {"scanned": len(messages), "job_emails": 0, "updated": 0, "to_review": 0}
    for m in messages:
        if m["message_id"] in known:
            continue
        kind = classify(m["subject"], m["body"])
        if not kind:
            continue
        counts["job_emails"] += 1
        job_id = match_company(m["sender"], m["subject"], m["body"], companies)
        item = InboxItem(message_id=m["message_id"], received_at=m["date"], sender=m["sender"][:200],
                         subject=m["subject"][:300], snippet=m["body"][:500], kind=kind,
                         company_guess=guess_company(m["sender"], m["subject"]), job_id=job_id,
                         state="applied_to_tracker" if job_id else "new")
        if job_id:
            tracker.set_status(job_id, kind, "gmail", forward_only=True)
            counts["updated"] += 1
        else:
            counts["to_review"] += 1
        with session() as s:
            s.add(item)
            s.commit()
    return counts


def scan(days: int = 14) -> dict:
    addr = address()
    pw = addr and _password(addr)
    if not pw:
        return {"error": "Gmail not connected"}
    m = _login(addr, pw)
    try:
        m.select('"[Gmail]/All Mail"', readonly=True)  # read-only: nothing is marked read or changed
        query = SEARCH.format(days=days).replace('"', '\\"')  # Gmail search syntax inside an IMAP quoted string
        typ, data = m.search(None, "X-GM-RAW", f'"{query}"')
        ids = data[0].split()[-300:] if typ == "OK" else []
        messages = []
        for i in ids:
            typ, raw = m.fetch(i, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not isinstance(raw[0], tuple):
                continue
            msg = email.message_from_bytes(raw[0][1])
            try:
                date = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                date = datetime.now(timezone.utc)
            messages.append({"message_id": msg.get("Message-ID") or f"imap:{i.decode()}",
                             "sender": str(make_header(decode_header(msg.get("From", "")))),
                             "subject": str(make_header(decode_header(msg.get("Subject", "")))),
                             "date": date, "body": _text(msg)[:4000]})
    finally:
        m.logout()
    return ingest(messages)

